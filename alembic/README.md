# Alembic database schema migrations

## Preparing a new migration

### Step 1: Check schema_dump.sql for the existing schema

First, the existing database schema should be reflected in the [schema_dump.sql](schema_dump.sql) file. Specifically, the `COPY public.alembic_version` line should have the ID of the latest existing migration. If the schema dump is not up-to-date, check out a version of Ook that has the schema for the last migration and run the following command from the repo's root directory:

```bash
nox -s db-dump-schema
```

This updates [schema_dump.sql](schema_dump.sql).

### Step 2: Create a new migration

From the root of the repo, run the following command:

```bash
nox -s alembic -- revision --autogenerate -m "Brief description of the migration"
```

Review, modify, and commit the new migration file in the `alembic/versions` directory.

```bash
git add alembic/versions/<new_migration_file>
```

### Step 3: Update schema_dump.sql

After the migration has been committed, update the [schema_dump.sql](schema_dump.sql) file to reflect the new schema. Run the following command from the repo's root directory:

```bash
nox -s db-dump-schema
git add alembic/schema_dump.sql
```

### Step 4: Deployment

When deploying Ook with the new database schema, you will need to enable the alembic migration in the app's Phalanx deployment.
