---
name: project-mechanics
description: Project-specific build/test/lint/typing commands for this repo. Read this skill at the start of any phase that runs validation (`stoker-work`, `stoker-fixup`, `stoker-rebase`).
---

# Project mechanics

This file is the source of truth for how this repo runs tests, lint,
and type-checking. Profile-shipped phase skills read it at the start
of each phase and use the named commands verbatim.

## Test commands

- `focused_test`: `uv run --only-group=nox nox -s test -- tests/path/to/foo_test.py`
- `complete_test`: `uv run --only-group=nox nox -s test`

## Lint

- `lint_touched`: `uv run --only-group=lint pre-commit run --files {files}`
- `lint_all`: `uv run --only-group=nox nox -s lint`

## Typing

- `typing`: `uv run --only-group=nox nox -s typing`

## Final validation

End-of-task validation runs `uv run --only-group=nox nox -s test` +
`uv run --only-group=nox nox -s lint` +
`uv run --only-group=nox nox -s typing` in that order, in the
foreground. This is a single Python package (`src/ook`), not a
monorepo, so `complete_test` already runs the whole suite. Tests use
pytest under testcontainers (Postgres + Kafka), so a working Docker
runtime is required; the suite manages its own containers and env
vars. Coverage (`nox -s test-coverage`) and the multi-platform Docker
image build are CI's responsibility, not the in-iteration gate.

Docs extra: when a change touches `docs/` or docstrings, also run
`uv run --only-group=nox nox -s docs` (mirrors CI's docs build; needs
graphviz installed locally).

<!-- stoker-onboarded-from: github.com/lsst-sqre/rubin-stoker//profile@main
     prompt-hash: 348ec538f8f7f6fa42da3569d855eab629174668ef28ea225f8b37511daac9d4
     onboarded-at: 2026-07-05T01:17:47Z -->
