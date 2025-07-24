from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import with_polymorphic

from ook.dbschema.authors import SqlAuthor
from ook.dbschema.resources import (
    SqlContributor,
    SqlDocumentResource,
    SqlExternalReference,
    SqlResource,
    SqlResourceRelation,
)

from ..authorstore import create_author_affiliations_subquery


def create_resource_with_relations_stmt(resource_id: int) -> Select:
    """Create a statement to select a resource with all relations.

    Parameters
    ----------
    resource_id
        The ID of the resource to retrieve.

    Returns
    -------
    Select
        SQLAlchemy select statement.
    """
    poly_resource = with_polymorphic(SqlResource, [SqlDocumentResource])

    # Create subqueries for each aggregation to avoid Cartesian products
    contributors_subquery = (
        select(
            func.coalesce(
                func.json_agg(
                    aggregate_order_by(
                        func.json_build_object(
                            "resource_id",
                            SqlContributor.resource_id,
                            "author",
                            # Use author columns directly and construct JSON
                            func.json_build_object(
                                "internal_id",
                                SqlAuthor.internal_id,
                                "surname",
                                SqlAuthor.surname,
                                "given_name",
                                SqlAuthor.given_name,
                                "orcid",
                                SqlAuthor.orcid,
                                "email",
                                SqlAuthor.email,
                                "notes",
                                SqlAuthor.notes,
                                "affiliations",
                                create_author_affiliations_subquery(),
                            ),
                            "role",
                            SqlContributor.role,
                            "order",
                            SqlContributor.order,
                        ),
                        SqlContributor.order,
                    )
                ),
                func.json_build_array(),
            ).label("contributors")
        )
        .select_from(SqlContributor)
        .join(SqlAuthor, SqlContributor.author_id == SqlAuthor.id)
        .where(SqlContributor.resource_id == resource_id)
    ).scalar_subquery()

    # Create an alias for the related resource to avoid naming conflicts
    RelatedSqlResource = with_polymorphic(  # noqa: N806
        SqlResource, [SqlDocumentResource], aliased=True
    )

    resource_relations_subquery = (
        select(
            func.coalesce(
                func.json_agg(
                    func.json_build_object(
                        "relation_type",
                        SqlResourceRelation.relation_type,
                        "resource",
                        func.json_build_object(
                            "id",
                            RelatedSqlResource.id,
                            "title",
                            RelatedSqlResource.title,
                            "description",
                            RelatedSqlResource.description,
                            "url",
                            RelatedSqlResource.url,
                            "doi",
                            RelatedSqlResource.doi,
                        ),
                    )
                ),
                func.json_build_array(),
            ).label("resource_relations")
        )
        .select_from(SqlResourceRelation)
        .join(
            RelatedSqlResource,
            SqlResourceRelation.related_resource_id == RelatedSqlResource.id,
        )
        .where(
            (SqlResourceRelation.source_resource_id == resource_id)
            & (SqlResourceRelation.related_resource_id.is_not(None))
        )
    ).scalar_subquery()

    external_relations_subquery = (
        select(
            func.coalesce(
                func.json_agg(
                    func.json_build_object(
                        "relation_type",
                        SqlResourceRelation.relation_type,
                        "external_reference",
                        func.json_build_object(
                            "url",
                            SqlExternalReference.url,
                            "doi",
                            SqlExternalReference.doi,
                            "arxiv_id",
                            SqlExternalReference.arxiv_id,
                            "isbn",
                            SqlExternalReference.isbn,
                            "issn",
                            SqlExternalReference.issn,
                            "ads_bibcode",
                            SqlExternalReference.ads_bibcode,
                            "type",
                            SqlExternalReference.type,
                            "title",
                            SqlExternalReference.title,
                            "publication_year",
                            SqlExternalReference.publication_year,
                            "volume",
                            SqlExternalReference.volume,
                            "issue",
                            SqlExternalReference.issue,
                            "number",
                            SqlExternalReference.number,
                            "number_type",
                            SqlExternalReference.number_type,
                            "first_page",
                            SqlExternalReference.first_page,
                            "last_page",
                            SqlExternalReference.last_page,
                            "publisher",
                            SqlExternalReference.publisher,
                            "edition",
                            SqlExternalReference.edition,
                            "contributors",
                            SqlExternalReference.contributors,
                        ),
                    )
                ),
                func.json_build_array(),
            ).label("external_relations")
        )
        .select_from(SqlResourceRelation)
        .join(
            SqlExternalReference,
            SqlResourceRelation.related_external_ref_id
            == SqlExternalReference.id,
        )
        .where(
            (SqlResourceRelation.source_resource_id == resource_id)
            & (SqlResourceRelation.related_external_ref_id.is_not(None))
        )
    ).scalar_subquery()

    return select(
        poly_resource,
        contributors_subquery.label("contributors"),
        resource_relations_subquery.label("resource_relations"),
        external_relations_subquery.label("external_relations"),
    ).where(poly_resource.id == resource_id)
