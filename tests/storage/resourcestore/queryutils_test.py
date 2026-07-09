"""Tests for the resource-store SQL query builders."""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from ook.storage.resourcestore._queryutils import (
    create_resource_with_relations_stmt,
)


def test_contributor_subquery_excludes_author_email() -> None:
    """The resource contributor subquery must not select ``author.email``.

    Author email addresses are personal data that should not be surfaced
    through resource contributor listings, so the column is intentionally
    omitted from the query. The affiliation email domain (a non-personal
    organizational field) is unaffected.
    """
    stmt = create_resource_with_relations_stmt(1)
    compiled_sql = str(stmt.compile(dialect=postgresql.dialect()))

    assert "author.email" not in compiled_sql
    # The affiliation email domain is a distinct, non-personal field that
    # must remain selectable.
    assert "affiliation.email_domain" in compiled_sql
