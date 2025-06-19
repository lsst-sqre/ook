# Bibliography API design document

Ook will provide a bibliography API that lists all types of cite-able resources at Rubin Observatory, and includes bibliographic information for each resource.

## Database design

### Entities

Based on the unified resource model approach, I've identified the following core entities for the bibliography API:

#### 1. Resource

The central entity representing any trackable item in the system. This unified approach handles both root resources (e.g., a GitHub repository) and their versioned snapshots (e.g., tagged releases) as the same entity type.

**Attributes:**

- `id` (Primary Key): Unique identifier
- `title`: Display name/title of the resource
- `description`: Optional description
- `url`: Primary URL for the resource (if applicable)
- `resource_type`: Enumeration (GitHub_repository, document, documentation_website, etc.)
- `date_created`: Timestamp when resource was added to Ook
- `date_updated`: Timestamp of last modification
- `is_citable`: Boolean indicating if resource can be cited
- `doi`: Digital Object Identifier (nullable, for citable resources)
- `version_identifier`: Version string/tag (nullable, e.g., "v1.2.0", "2024-01-15")
- `version_type`: Enumeration (nullable, semantic_version, date_version, git_tag, etc.)
- `is_default_version`: Boolean indicating if this is the current default version
- `date_released`: When this version was released (nullable, for versioned resources)
- `type_metadata`: JSONB field for type-specific metadata (nullable)

**Resource Hierarchy Examples:**

- **Root resource**: `is_default_version = TRUE`, `version_identifier = NULL`
- **Versioned resource**: `is_default_version = FALSE`, `version_identifier = "v1.2.0"`
- **Version relationships**: Established through ResourceRelationship using DataCite relationship types

#### 2. Type-specific resource tables

These tables are related to the Resource table through joined-table inheritance, allowing for specific metadata storage while maintaining a unified resource model.

#### GitHubRepository

Represents GitHub repositories with specific metadata.

**Attributes:**

- `resource_id` (Primary Key, Foreign Key): References Resource
- `github_owner`: GitHub owner (organization or user)
- `github_name`: GitHub repository name
- `default_branch`: Default branch name (e.g., "main")
- `language`: Primary programming language of the repository (nullable)
- `stars_count`: Number of stars (nullable)
- `forks_count`: Number of forks (nullable)
- `watchers_count`: Number of watchers (nullable)
- `date_created`: Timestamp when added to system
- `date_updated`: Timestamp of last update

#### Document

Represents documents with specific metadata, such as series and handle.

**Attributes:**

- `resource_id` (Primary Key, Foreign Key): References Resource
- `series`: Series name (e.g., "DMTN", "LDM")
- `handle`: Handle identifier (e.g., "031")
- `generator`: Document generator used (e.g., `Documenteer 2.0.0`, `Lander 2.0.0`)
- `abstract`: Optional abstract or summary of the document
- `date_created`: Timestamp when added to system
- `date_updated`: Timestamp of last update

#### DocumentationWebsite

Represents documentation websites with specific metadata.
The `url` of the Resource will point to the main page of the documentation.

**Attributes:**

- `resource_id` (Primary Key, Foreign Key): References Resource
- `sitemap_url`: URL to the sitemap (if available)
- `generator`: Documentation generator used (e.g. `Documenteer 2.0.0`)
- `date_created`: Timestamp when added to system
- `date_updated`: Timestamp of last update

#### 3. ResourceRelationship

Captures all types of relationships between resources, including citations, references, and other semantic connections. This unified approach aligns with DataCite's RelatedIdentifier model.

**Attributes:**

- `id` (Primary Key): Unique identifier
- `source_resource_id` (Foreign Key): References Resource (the "from" resource)
- `target_resource_id` (Foreign Key, nullable): References Resource (if target is in Ook)
- `external_reference_id` (Foreign Key, nullable): References ExternalReference (for external targets)
- `relationship_type`: Enumeration matching DataCite relationType values
- `citation_context`: Optional context where citation/reference appears (nullable)
- `date_created`: Timestamp when relationship was established

**DataCite-Compatible Relationship Types:**

- **Primary citation types**: `Cites`, `IsCitedBy` (most commonly used)
- **Alternative citation types**: `References`, `IsReferencedBy`, `IsSupplementTo`, `IsSupplementedBy`
- **Version relationships**: `HasVersion`, `IsVersionOf`, `IsNewVersionOf`, `IsPreviousVersionOf`
- **Content relationships**: `IsPartOf`, `HasPart`
- **Derivation relationships**: `IsDerivedFrom`, `IsSourceOf`, `IsCompiledBy`, `Compiles`
- **Documentation relationships**: `Documents`, `IsDocumentedBy`, `Describes`, `IsDescribedBy`
- **Review relationships**: `Reviews`, `IsReviewedBy`
- **Publication relationships**: `IsPublishedIn`
- **Dependency relationships**: `Requires`, `IsRequiredBy`
- **Lifecycle relationships**: `Continues`, `IsContinuedBy`, `Obsoletes`, `IsObsoletedBy`
- **Collection relationships**: `Collects`, `IsCollectedBy`
- **Translation relationships**: `IsTranslationOf`, `HasTranslation`
- **Form relationships**: `IsVariantFormOf`, `IsOriginalFormOf`, `IsIdenticalTo`
- **Metadata relationships**: `HasMetadata`, `IsMetadataFor`
- **Ook-specific types**: `generates` (for repo→docs), `implements`, `supersedes`

#### 4. ExternalReference

Stores information about resources referenced by Ook resources but not tracked in the Ook database.

**Attributes:**

- `id` (Primary Key): Unique identifier
- `doi`: DOI if available
- `url`: URL if available
- `title`: Title of the external resource
- `authors`: Author information (JSON or separate table)
- `date_published`: When external resource was published
- `resource_type`: Type of external resource (journal_article, book, website, etc.)
- `date_created`: Timestamp when added to system

#### 5. Author

Represents authors/contributors to resources. This aligns with the existing `SqlAuthor` model in the codebase.

**Attributes:**

- `id` (Primary Key): Unique identifier (BigInteger, auto-increment)
- `internal_id`: Internal ID from author database YAML (unique, indexed)
- `surname`: Surname/family name of the author (indexed)
- `given_name`: Given/first name of the author (nullable, indexed)
- `email`: Email address (nullable)
- `orcid`: ORCID identifier (nullable, unique)
- `notes`: Array of notes/alt-affiliations for AASTeX
- `date_updated`: Timestamp of last update

**Related Entities:**

- Links to `Affiliation` through `AuthorAffiliation` junction table
- Links to `Collaboration` (future enhancement)

#### 6. ResourceAuthor

Many-to-many relationship between Resources and Authors. Works uniformly for both root resources and their versions.

**Attributes:**

- `resource_id` (Foreign Key): References Resource (primary key component)
- `author_id` (Foreign Key): References Author (primary key component)
- `author_order`: Order/position in author list (integer, 1-based indexing)
- `role`: Author role (author, editor, contributor, etc.)

#### 7. Affiliation

Represents institutional affiliations for authors. This aligns with the existing `SqlAffiliation` model.

**Attributes:**

- `id` (Primary Key): Unique identifier (BigInteger, auto-increment)
- `internal_id`: Internal ID from author database YAML (unique, indexed)
- `name`: Name of the institution/affiliation (indexed)
- `address`: Physical address of the institution (nullable, indexed)
- `date_updated`: Timestamp of last update

#### 8. AuthorAffiliation

Junction table for the many-to-many relationship between Authors and Affiliations, with ordering support.

**Attributes:**

- `author_id` (Foreign Key): References Author (primary key component)
- `affiliation_id` (Foreign Key): References Affiliation (primary key component)
- `position`: Order/position of this affiliation for the author

#### 9. Collaboration

Represents research collaborations. This aligns with the existing `SqlCollaboration` model.

**Attributes:**

- `id` (Primary Key): Unique identifier (BigInteger, auto-increment)
- `internal_id`: Internal ID from author database YAML (unique, indexed)
- `name`: Name of the collaboration (indexed)
- `date_updated`: Timestamp of last update

#### 10. LtdProject

Represents projects hosted on LSST the Docs (LTD) for specific resource types like Document and DocumentationWebsite.

**Attributes:**

- `resource_id` (Primary Key, Foreign Key): References Resource
- `project_name`: Name of the LTD project (unique, indexed)
- `api_resource_url`: URL to the API resource for this project
- `domain`: Domain of the LTD project (e.g., "lsst.io", "pipelines.lsst.io")
- `date_created`: Timestamp when added to system
- `date_updated`: Timestamp of last update

### Entity Relationships Summary

1. **Resource** ↔ **ResourceRelationship** ↔ **Resource**: Many-to-many relationships including citations and versioning
2. **ResourceRelationship** ↔ **ExternalReference**: Many-to-one (multiple relationships can reference same external resource)
3. **Resource** ↔ **Type-specific tables**: One-to-one inheritance (GitHubRepository, Document, DocumentationWebsite)
4. **Resource** ↔ **Author**: Many-to-many through ResourceAuthor
5. **Author** ↔ **Affiliation**: Many-to-many through AuthorAffiliation (with ordering)
6. **Author** ↔ **AuthorAffiliation**: One-to-many
7. **Affiliation** ↔ **AuthorAffiliation**: One-to-many
8. **Resource** ↔ **ResourceAuthor**: One-to-many
9. **Resource** ↔ **LtdProject**: One-to-one (optional, for LTD-hosted resources)

### Design Considerations

#### Unified Model Benefits

- **Single source of truth**: All bibliographic information (citations, authors, DOIs) handled consistently
- **Simplified queries**: No need to join Resource and ResourceVersion tables for bibliographic data
- **Flexible versioning**: Any resource can become versioned, and any version can become the default
- **API consistency**: Single endpoint pattern for all resource operations

#### Versioning Strategy

- **Root resources**: `is_default_version = TRUE`, `version_identifier = NULL`
- **Version resources**: `is_default_version = FALSE`, `version_identifier = "v1.2.0"`
- **Version relationships**: Use ResourceRelationship with DataCite types (`HasVersion`, `IsVersionOf`, etc.)
- **Version promotion**: Update `is_default_version` flags to change which version is default
- **Version queries**: Use ResourceRelationship joins to traverse version hierarchies

#### Data Integrity Constraints

- Ensure only one `is_default_version = TRUE` per resource family
- Prevent circular references in version relationships through ResourceRelationship
- Validate that versioned resources inherit compatible resource_type
- Enforce proper use of version relationship types (`HasVersion`, `IsVersionOf`, etc.)

#### Unified Relationship Model

- **DataCite compatibility**: Uses the same relationship types as DataCite's RelatedIdentifier
- **Citations as relationships**: `Cites`, `IsCitedBy`, `References`, `IsReferencedBy` are relationship types
- **Internal relationships**: Use `target_resource_id` for resources within Ook
- **External relationships**: Use `external_reference_id` for resources outside Ook
- **Contextual information**: Optional `citation_context` field for additional details
- **Bidirectional tracking**: Automatic reciprocal relationship creation where appropriate

#### Citation Type Strategy

- **Default to primary types**: Use `Cites`/`IsCitedBy` for most citation relationships
- **Semantic choice**: Allow `References`/`IsReferencedBy` for general references
- **Supplementary materials**: Use `IsSupplementedBy`/`IsSupplementTo` for datasets, appendices, etc.
- **Functional equivalence**: All citation types count equally in DataCite metrics
- **User guidance**: Provide clear guidelines on when to use each type
- **Documentation linking**: Use `References`/`IsReferencedBy` for documentation cross-links and related material pointers

See [DataCite's Contributing Citations and References](https://support.datacite.org/docs/contributing-citations-and-references) for a discussion of citations versus references.

#### Performance Optimizations

- Index on `is_default_version` for version queries
- Index on `relationship_type` in ResourceRelationship for version traversal
- Consider materialized views for complex bibliographic aggregations
- Cache latest version information for frequently accessed resources

#### Author System Integration

- Leverages existing `SqlAuthor`, `SqlAffiliation`, and `SqlCollaboration` models
- Supports complex author-affiliation relationships with ordering
- Integrates with LSST TeX author database YAML format
- Maintains backward compatibility with existing author data structures

#### Extensibility

- `resource_type` enumeration easily extended for new resource types
- `relationship_type` enumeration supports various inter-resource relationships
- JSON fields in `ExternalReference` allow flexible external metadata storage

#### Type-Specific Metadata Strategy

- **Joined-table inheritance**: Well-defined types (GitHub repos, documents) get dedicated tables
- **JSONB fallback**: `type_metadata` field for flexible/experimental metadata
- **Performance balance**: Frequently queried fields in dedicated tables, occasional fields in JSON
- **Migration strategy**: Start with JSONB, promote to dedicated tables as patterns emerge
- **Query patterns**:
  - Type-specific queries use LEFT JOINs to subtype tables
  - Cross-type queries use base Resource table
  - Hybrid queries combine both approaches

#### Type-Specific Metadata Usage

```sql
-- Create a GitHub repository with type-specific metadata
INSERT INTO Resource (title, resource_type, is_default_version, type_metadata)
VALUES ('LSST Science Pipelines', 'GitHub_repository', TRUE, '{"topics": ["astronomy", "python"]}');

INSERT INTO GitHubRepository (resource_id, github_owner, github_name, default_branch, language)
VALUES (1, 'lsst', 'pipelines', 'main', 'Python');

-- Create a document with series and handle
INSERT INTO Resource (title, resource_type, is_default_version)
VALUES ('Data Management Test Plan', 'document', TRUE);

INSERT INTO Document (resource_id, series, handle, document_type, abstract)
VALUES (2, 'DMTN', '031', 'technical_note', 'This document describes...');

-- Query GitHub repositories with metadata
SELECT r.title, gr.github_owner, gr.github_name, gr.stars_count, r.type_metadata->'topics'
FROM Resource r
JOIN GitHubRepository gr ON r.id = gr.resource_id
WHERE r.resource_type = 'GitHub_repository';

-- Query documents by series
SELECT r.title, d.series, d.handle, d.document_type
FROM Resource r
JOIN Document d ON r.id = d.resource_id
WHERE d.series = 'DMTN'
ORDER BY d.handle;
```

#### LTD Project Association Strategy

For Document and DocumentationWebsite resources that are hosted on LSST the Docs (LTD), we need to capture specific LTD metadata including project name and API resource URL. Since only some resources of these types are LTD-hosted, we recommend a hybrid approach that aligns with the existing type-specific metadata strategy.

**Recommended Approach: Dedicated LTD Table**

Create a dedicated `LtdProject` table that can be optionally joined to Document and DocumentationWebsite resources:

```sql
CREATE TABLE LtdProject (
    resource_id INTEGER PRIMARY KEY REFERENCES Resource(id),
    project_name VARCHAR NOT NULL,
    api_resource_url VARCHAR NOT NULL,
    domain VARCHAR, -- e.g., 'lsst.io', 'pipelines.lsst.io'
    date_created TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    date_updated TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Ensure project names are unique
    UNIQUE(project_name)
);

-- Index for efficient querying by project name
CREATE INDEX idx_ltd_project_name ON LtdProject(project_name);
```

**Alternative Approach: JSONB Metadata Field**

For a more flexible approach, LTD metadata can be stored in the existing `type_metadata` JSONB field:

```sql
-- Example LTD metadata in type_metadata
{
    "ltd": {
        "project_name": "pipelines",
        "api_resource_url": "https://keeper.lsst.codes/projects/pipelines/",
        "domain": "pipelines.lsst.io"
    }
}
```

**Recommendation: Use Dedicated LTD Table**

The dedicated table approach is preferred because:

1. **Query Performance**: Direct SQL joins are faster than JSONB queries for frequent LTD lookups
2. **Data Integrity**: Foreign key constraints and unique constraints on project names
3. **Indexing**: Better index support for LTD-specific queries
4. **Clear Semantics**: Explicit relationship between resources and LTD projects
5. **API Efficiency**: Simpler joins for endpoints that need LTD information

**Usage Patterns:**

```sql
-- Find all LTD-hosted documents
SELECT r.title, d.series, d.handle, ltd.project_name, ltd.api_resource_url
FROM Resource r
JOIN Document d ON r.id = d.resource_id
JOIN LtdProject ltd ON r.id = ltd.resource_id
WHERE r.resource_type = 'document'
ORDER BY d.series, d.handle;

-- Find all resources for a specific LTD project
SELECT r.title, r.resource_type, r.url
FROM Resource r
JOIN LtdProject ltd ON r.id = ltd.resource_id
WHERE ltd.project_name = 'pipelines';

-- Find all LTD-hosted documentation websites
SELECT r.title, r.url, dw.sitemap_url, ltd.project_name
FROM Resource r
JOIN DocumentationWebsite dw ON r.id = dw.resource_id
JOIN LtdProject ltd ON r.id = ltd.resource_id
WHERE r.resource_type = 'documentation_website';

-- Check if a resource is LTD-hosted
SELECT r.title,
       CASE WHEN ltd.resource_id IS NOT NULL THEN 'LTD-hosted' ELSE 'External' END as hosting_type
FROM Resource r
LEFT JOIN LtdProject ltd ON r.id = ltd.resource_id
WHERE r.resource_type IN ('document', 'documentation_website');
```

**Migration Strategy:**

1. **Phase 1**: Create `LtdProject` table
2. **Phase 2**: Populate with existing LTD project data
3. **Phase 3**: Update API endpoints to include LTD information where available
4. **Phase 4**: Add validation to ensure LTD resources have proper project associations

**Benefits of This Approach:**

- **Selective Association**: Only LTD-hosted resources need entries in LtdProject table
- **Clean Queries**: Simple LEFT JOIN to determine LTD hosting status
- **Performance**: Indexed queries for LTD-specific operations
- **Consistency**: Aligns with existing joined-table inheritance pattern
- **Extensibility**: Easy to add more LTD-specific fields (build status, deployment info, etc.)
- **Data Integrity**: Unique constraints prevent duplicate project names
- **API Clarity**: Clear distinction between LTD-hosted and external resources

This approach maintains the flexibility of the hybrid metadata model while providing optimal performance and data integrity for the common use case of identifying and working with LTD-hosted resources.
