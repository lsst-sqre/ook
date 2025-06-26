"""Resource relations."""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "RelationType",
]


class RelationType(StrEnum):
    """Enumeration of resource relation types.

    These relationship types correspond to the DataCite relationType:
    https://datacite-metadata-schema.readthedocs.io/en/4.6/appendices/appendix-1/relationType/
    """

    is_cited_by = "IsCitedBy"
    cites = "Cites"
    is_supplement_to = "IsSupplementTo"
    is_supplemented_by = "IsSupplementedBy"
    is_continued_by = "IsContinuedBy"
    continues = "Continues"
    describes = "Describes"
    is_described_by = "IsDescribedBy"
    has_metadata = "HasMetadata"
    is_metadata_for = "IsMetadataFor"
    has_version = "HasVersion"
    is_version_of = "IsVersionOf"
    is_new_version_of = "IsNewVersionOf"
    is_previous_version_of = "IsPreviousVersionOf"
    is_part_of = "IsPartOf"
    has_part = "HasPart"
    is_published_in = "IsPublishedIn"
    is_referenced_by = "IsReferencedBy"
    references = "References"
    is_documented_by = "IsDocumentedBy"
    documents = "Documents"
    is_compiled_by = "IsCompiledBy"
    compiles = "Compiles"
    is_variant_form_of = "IsVariantFormOf"
    is_original_form_of = "IsOriginalFormOf"
    is_identical_to = "IsIdenticalTo"
    is_reviewed_by = "IsReviewedBy"
    reviews = "Reviews"
    is_derived_from = "IsDerivedFrom"
    is_source_of = "IsSourceOf"
    is_required_by = "IsRequiredBy"
    requires = "Requires"
    obsoletes = "Obsoletes"
    is_obsoleted_by = "IsObsoletedBy"
    is_collected_by = "IsCollectedBy"
    collects = "Collects"
    is_translation_of = "IsTranslationOf"
    has_translation = "HasTranslation"
