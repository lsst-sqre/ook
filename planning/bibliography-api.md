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
- `created_at`: Timestamp when resource was added to Ook
- `updated_at`: Timestamp of last modification
- `is_citable`: Boolean indicating if resource can be cited
- `doi`: Digital Object Identifier (nullable, for citable resources)
- `version_identifier`: Version string/tag (nullable, e.g., "v1.2.0", "2024-01-15")
- `version_type`: Enumeration (nullable, semantic_version, date_version, git_tag, etc.)
- `parent_resource_id` (Foreign Key, nullable): References Resource (null for root resources, set for versions)
- `is_default_version`: Boolean indicating if this is the current default version
- `release_date`: When this version was released (nullable, for versioned resources)

**Resource Hierarchy Examples:**

- **Root resource**: `parent_resource_id = NULL`, `is_default_version = TRUE`, `version_identifier = NULL`
- **Versioned resource**: `parent_resource_id = <root_id>`, `is_default_version = FALSE`, `version_identifier = "v1.2.0"`
- **Promoted version**: Any version can become default by setting `is_default_version = TRUE`

#### 2. ResourceRelationship

Captures relationships between resources (e.g., "documentation website is output of repository").

**Attributes:**

- `id` (Primary Key): Unique identifier
- `source_resource_id` (Foreign Key): References Resource (the "from" resource)
- `target_resource_id` (Foreign Key): References Resource (the "to" resource)
- `relationship_type`: Enumeration (generates, documents, implements, supersedes, etc.)
- `created_at`: Timestamp when relationship was established

#### 3. Citation

Represents bibliographic references made by citable resources to other resources. Since all resources (root and versioned) can make citations, this table works uniformly across the system.

**Attributes:**

- `id` (Primary Key): Unique identifier
- `citing_resource_id` (Foreign Key): References Resource (the resource making the citation)
- `cited_resource_id` (Foreign Key, nullable): References Resource (if cited resource is in Ook)
- `external_reference_id` (Foreign Key, nullable): References ExternalReference (for external citations)
- `citation_context`: Optional context where citation appears
- `created_at`: Timestamp when citation was recorded

#### 4. ExternalReference

Stores information about resources referenced by Ook resources but not tracked in the Ook database.

**Attributes:**

- `id` (Primary Key): Unique identifier
- `doi`: DOI if available
- `url`: URL if available
- `title`: Title of the external resource
- `authors`: Author information (JSON or separate table)
- `publication_date`: When external resource was published
- `resource_type`: Type of external resource (journal_article, book, website, etc.)
- `created_at`: Timestamp when added to system

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

- `resource_id` (Foreign Key): References Resource
- `author_id` (Foreign Key): References Author
- `author_order`: Order/position in author list
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

### Entity Relationships Summary

1. **Resource** ↔ **Resource**: Self-referencing one-to-many for versioning (parent_resource_id)
2. **Resource** ↔ **ResourceRelationship**: Many-to-many through self-referencing relationship
3. **Resource** ↔ **Citation** ↔ **Resource**: Many-to-many (resources can cite and be cited by many other resources)
4. **Citation** ↔ **ExternalReference**: Many-to-one (multiple citations can reference same external resource)
5. **Resource** ↔ **Author**: Many-to-many through ResourceAuthor
6. **Author** ↔ **Affiliation**: Many-to-many through AuthorAffiliation (with ordering)
7. **Author** ↔ **AuthorAffiliation**: One-to-many
8. **Affiliation** ↔ **AuthorAffiliation**: One-to-many
9. **Resource** ↔ **ResourceAuthor**: One-to-many

### Design Considerations

1. **Unified Model Benefits**:

   - **Single source of truth**: All bibliographic information (citations, authors, DOIs) handled consistently
   - **Simplified queries**: No need to join Resource and ResourceVersion tables for bibliographic data
   - **Flexible versioning**: Any resource can become versioned, and any version can become the default
   - **API consistency**: Single endpoint pattern for all resource operations

2. **Versioning Strategy**:

   - **Root resources**: `parent_resource_id = NULL`, `is_default_version = TRUE`
   - **Version snapshots**: `parent_resource_id` references root, `is_default_version = FALSE`
   - **Version promotion**: Update `is_default_version` flags to change which version is default
   - **Recursive queries**: Use CTEs to traverse version hierarchies

3. **Data Integrity Constraints**:

   - Ensure only one `is_default_version = TRUE` per resource family
   - Prevent circular references in parent_resource_id
   - Validate that versioned resources inherit compatible resource_type from parent

4. **Citation Model**:

   - **Internal citations**: Use `cited_resource_id` for resources within Ook
   - **External citations**: Use `external_reference_id` for resources outside Ook
   - **Version-specific citations**: Any resource (root or version) can cite any other resource
   - **Citation inheritance**: Consider whether citations should inherit from parent to versions

5. **Performance Optimizations**:

   - Index on `parent_resource_id` and `is_default_version` for version queries
   - Consider materialized views for complex bibliographic aggregations
   - Cache latest version information for frequently accessed resources

6. **Author System Integration**:

   - Leverages existing `SqlAuthor`, `SqlAffiliation`, and `SqlCollaboration` models
   - Supports complex author-affiliation relationships with ordering
   - Integrates with LSST TeX author database YAML format
   - Maintains backward compatibility with existing author data structures

7. **Extensibility**:
   - `resource_type` enumeration easily extended for new resource types
   - `relationship_type` enumeration supports various inter-resource relationships
   - JSON fields in `ExternalReference` allow flexible external metadata storage

### Example Usage Patterns

#### Resource Versioning

```sql
-- Root GitHub repository
INSERT INTO Resource (title, resource_type, parent_resource_id, is_default_version)
VALUES ('My Project', 'GitHub_repository', NULL, TRUE);

-- Version 1.0 release
INSERT INTO Resource (title, resource_type, parent_resource_id, is_default_version, version_identifier)
VALUES ('My Project v1.0', 'GitHub_repository', 1, FALSE, 'v1.0.0');

-- Promote version 1.0 to default
UPDATE Resource SET is_default_version = FALSE WHERE parent_resource_id = 1 OR id = 1;
UPDATE Resource SET is_default_version = TRUE WHERE id = 2;
```

#### Cross-Resource Relationships

```sql
-- Documentation website generated from repository
INSERT INTO ResourceRelationship (source_resource_id, target_resource_id, relationship_type)
VALUES (1, 3, 'generates');
```

#### Citations

```sql
-- Internal citation (resource cites another Ook resource)
INSERT INTO Citation (citing_resource_id, cited_resource_id) VALUES (1, 2);

-- External citation (resource cites external paper)
INSERT INTO Citation (citing_resource_id, external_reference_id) VALUES (1, 5);
```
