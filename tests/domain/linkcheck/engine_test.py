"""Tests for the link check status-transition engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ook.domain.linkcheck import (
    CheckResult,
    LinkCheckOutcome,
    LinkState,
    LinkStatus,
    RetryLadderConfig,
    evaluate_outcome,
)

URL = "https://example.com/page"

T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
"""Base time for constructing check timelines."""

LADDER = RetryLadderConfig(
    broken_threshold=timedelta(hours=48),
    min_attempts=3,
    recheck_intervals=(
        timedelta(hours=1),
        timedelta(hours=4),
        timedelta(hours=24),
        timedelta(hours=48),
    ),
)
"""Ladder configuration matching the PRD defaults."""


def make_ok_state(checked_at: datetime) -> LinkState:
    """Create a prior state for a link last seen OK at ``checked_at``."""
    return LinkState(
        url=URL,
        status=LinkStatus.ok,
        checked_at=checked_at,
        last_ok_at=checked_at,
        status_code=200,
    )


def fail_at(checked_at: datetime) -> LinkCheckOutcome:
    """Create a failure outcome at ``checked_at``."""
    return LinkCheckOutcome(
        checked_at=checked_at,
        result=CheckResult.failure,
        status_code=503,
        error="503 Service Unavailable",
    )


def test_new_link_success_is_ok() -> None:
    """A successful check of a never-seen link yields ``ok``."""
    outcome = LinkCheckOutcome(
        checked_at=T0, result=CheckResult.success, status_code=200
    )
    state = evaluate_outcome(
        url=URL, prior=None, outcome=outcome, ladder=LADDER
    )
    assert state.url == URL
    assert state.status == LinkStatus.ok
    assert state.checked_at == T0
    assert state.last_ok_at == T0
    assert state.status_code == 200
    assert state.failure_count == 0
    assert state.failing_since is None
    assert state.redirect_url is None


def test_new_link_failure_is_broken_immediately() -> None:
    """A never-seen-OK link that fails is broken immediately.

    A brand-new broken link is most likely an authoring error, so the
    retry ladder does not apply.
    """
    outcome = LinkCheckOutcome(
        checked_at=T0,
        result=CheckResult.failure,
        status_code=404,
        error="404 Not Found",
    )
    state = evaluate_outcome(
        url=URL, prior=None, outcome=outcome, ladder=LADDER
    )
    assert state.status == LinkStatus.broken
    assert state.last_ok_at is None
    assert state.failing_since == T0
    assert state.failure_count == 1
    assert state.status_code == 404
    assert state.error == "404 Not Found"


def test_previously_ok_link_failure_is_failing() -> None:
    """A previously-OK link that fails enters the retry ladder as
    ``failing`` and is scheduled for the first recheck interval.
    """
    prior = make_ok_state(T0)
    checked_at = T0 + timedelta(hours=12)
    state = evaluate_outcome(
        url=URL, prior=prior, outcome=fail_at(checked_at), ladder=LADDER
    )
    assert state.status == LinkStatus.failing
    assert state.last_ok_at == T0
    assert state.failing_since == checked_at
    assert state.failure_count == 1
    assert state.next_check_at == checked_at + LADDER.recheck_intervals[0]


def make_failing_state(
    failing_since: datetime, failure_count: int
) -> LinkState:
    """Create a prior state for a previously-OK link on the ladder."""
    return LinkState(
        url=URL,
        status=LinkStatus.failing,
        checked_at=failing_since,
        last_ok_at=failing_since - timedelta(hours=1),
        failing_since=failing_since,
        failure_count=failure_count,
        status_code=503,
        error="503 Service Unavailable",
    )


@pytest.mark.parametrize(
    ("prior_failure_count", "hours_since_first_failure"),
    [
        # Three attempts, but the streak spans less than 48 hours.
        (2, 24),
        # The streak spans at least 48 hours, but only two attempts.
        (1, 50),
    ],
)
def test_failing_link_stays_failing_below_thresholds(
    prior_failure_count: int, hours_since_first_failure: int
) -> None:
    """A previously-OK link stays ``failing`` until the streak meets
    both the attempt-count and duration thresholds.
    """
    prior = make_failing_state(T0, prior_failure_count)
    checked_at = T0 + timedelta(hours=hours_since_first_failure)
    state = evaluate_outcome(
        url=URL, prior=prior, outcome=fail_at(checked_at), ladder=LADDER
    )
    assert state.status == LinkStatus.failing
    assert state.failing_since == T0
    assert state.failure_count == prior_failure_count + 1


def test_failing_link_becomes_broken_when_ladder_exhausted() -> None:
    """A previously-OK link flips to ``broken`` once its failure streak
    spans at least the duration threshold with enough attempts.
    """
    prior = make_failing_state(T0, 2)
    checked_at = T0 + timedelta(hours=48)
    state = evaluate_outcome(
        url=URL, prior=prior, outcome=fail_at(checked_at), ladder=LADDER
    )
    assert state.status == LinkStatus.broken
    assert state.failing_since == T0
    assert state.failure_count == 3
    assert state.next_check_at is None


@pytest.mark.parametrize(
    "prior_status", [LinkStatus.failing, LinkStatus.broken]
)
def test_successful_check_recovers_link_to_ok(
    prior_status: LinkStatus,
) -> None:
    """A successful check returns a failing or broken link to ``ok``
    and resets the failure streak.
    """
    prior = make_failing_state(T0, 2)
    prior = prior.model_copy(update={"status": prior_status})
    checked_at = T0 + timedelta(hours=24)
    outcome = LinkCheckOutcome(
        checked_at=checked_at, result=CheckResult.success, status_code=200
    )
    state = evaluate_outcome(
        url=URL, prior=prior, outcome=outcome, ladder=LADDER
    )
    assert state.status == LinkStatus.ok
    assert state.last_ok_at == checked_at
    assert state.failing_since is None
    assert state.failure_count == 0
    assert state.error is None
    assert state.next_check_at is None


@pytest.mark.parametrize("redirect_status_code", [301, 308])
def test_permanent_redirect_is_redirected(
    redirect_status_code: int,
) -> None:
    """A permanent redirect (301/308) maps to ``redirected`` with the
    final location recorded so the source can be updated.
    """
    outcome = LinkCheckOutcome(
        checked_at=T0,
        result=CheckResult.success,
        status_code=200,
        redirect_status_code=redirect_status_code,
        redirect_url="https://example.com/moved",
    )
    state = evaluate_outcome(
        url=URL, prior=None, outcome=outcome, ladder=LADDER
    )
    assert state.status == LinkStatus.redirected
    assert state.redirect_url == "https://example.com/moved"
    assert state.redirect_status_code == redirect_status_code
    assert state.last_ok_at == T0


@pytest.mark.parametrize("redirect_status_code", [302, 307])
def test_temporary_redirect_is_ok_with_metadata(
    redirect_status_code: int,
) -> None:
    """A temporary redirect (302/307) resolves ``ok`` while retaining
    the redirect metadata.
    """
    outcome = LinkCheckOutcome(
        checked_at=T0,
        result=CheckResult.success,
        status_code=200,
        redirect_status_code=redirect_status_code,
        redirect_url="https://example.com/temporary",
    )
    state = evaluate_outcome(
        url=URL, prior=None, outcome=outcome, ladder=LADDER
    )
    assert state.status == LinkStatus.ok
    assert state.redirect_url == "https://example.com/temporary"
    assert state.redirect_status_code == redirect_status_code


def test_successful_check_recovers_link_to_redirected() -> None:
    """A successful check through a permanent redirect returns a
    failing link to ``redirected``.
    """
    prior = make_failing_state(T0, 2)
    checked_at = T0 + timedelta(hours=24)
    outcome = LinkCheckOutcome(
        checked_at=checked_at,
        result=CheckResult.success,
        status_code=200,
        redirect_status_code=301,
        redirect_url="https://example.com/moved",
    )
    state = evaluate_outcome(
        url=URL, prior=prior, outcome=outcome, ladder=LADDER
    )
    assert state.status == LinkStatus.redirected
    assert state.last_ok_at == checked_at
    assert state.failing_since is None
    assert state.failure_count == 0


def test_ladder_thresholds_are_caller_supplied() -> None:
    """The engine honors ladder thresholds supplied by the caller
    instead of hardcoded defaults.
    """
    ladder = RetryLadderConfig(
        broken_threshold=timedelta(hours=2),
        min_attempts=2,
        recheck_intervals=(timedelta(minutes=30),),
    )
    prior = make_failing_state(T0, 1)
    checked_at = T0 + timedelta(hours=2)
    state = evaluate_outcome(
        url=URL, prior=prior, outcome=fail_at(checked_at), ladder=ladder
    )
    assert state.status == LinkStatus.broken

    # With the default thresholds the same timeline stays failing, and
    # the next recheck comes from the caller-supplied schedule.
    state = evaluate_outcome(
        url=URL, prior=prior, outcome=fail_at(checked_at), ladder=LADDER
    )
    assert state.status == LinkStatus.failing
    assert state.next_check_at == checked_at + LADDER.recheck_intervals[1]


def test_unsupported_outcome_is_unsupported() -> None:
    """An unsupported check outcome yields ``unsupported`` and never
    enters the retry ladder.
    """
    outcome = LinkCheckOutcome(
        checked_at=T0,
        result=CheckResult.unsupported,
        error="Unsupported URL scheme",
    )
    state = evaluate_outcome(
        url="mailto:someone@example.com",
        prior=None,
        outcome=outcome,
        ladder=LADDER,
    )
    assert state.status == LinkStatus.unsupported
    assert state.failure_count == 0
    assert state.failing_since is None
    assert state.next_check_at is None
    assert state.error == "Unsupported URL scheme"
