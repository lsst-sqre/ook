# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Ook is the librarian indexing the Vera C. Rubin Observatory's technical documentation. It's a FastAPI application that ingests documentation metadata from various sources (GitHub, Kafka events) and indexes content to Algolia for the [Rubin Observatory technical documentation website](https://www.lsst.io).

## Development Commands

### Environment Setup

```bash
uv venv
source .venv/bin/activate
make init
```

### Core Development Tasks

```bash
# Run tests
uv run --only-group=nox nox -s test

# Run linting
uv run --only-group=nox nox -s lint

# Run type checking
uv run --only-group=nox nox -s typing

# Run the application locally
uv run --only-group=nox nox -s run

# Run Ook CLI commands
uv run --only-group=nox nox -s cli -- <command_args>

# Build documentation
uv run --only-group=nox nox -s docs

# Create changelog fragment
uv run --only-group=nox nox -s scriv-create

# Collect changelog for release
uv run --only-group=nox nox -s scriv-collect -- X.Y.Z
```

### Updating dependencies

At the start of a new feature branch, update dependencies:

```bash
make update
```

Run nox lint, type checking, and tests to ensure everything is still working, then commit the changes with a comment "Update dependencies".

### Database Migrations

```bash
# Create new migration
uv run --only-group=nox nox -s alembic -- revision --autogenerate -m "Description"

# Update schema dump after migration
uv run --only-group=nox nox -s dump-db-schema
```

## Architecture Overview

Ook is built using SQuaRE's [Safir](https://safir.lsst.io) framework for FastAPI applications. The architecture follows a layered approach:

### Core Layers

- **Handlers** (`src/ook/handlers/`): FastAPI route handlers organized by domain (authors, glossary, ingest, links, resources)
- **Services** (`src/ook/services/`): Business logic and orchestration layer
- **Storage** (`src/ook/storage/`): Data access layer with stores for different data types
- **Domain** (`src/ook/domain/`): Domain models and business entities
- **Database Schema** (`src/ook/dbschema/`): SQLAlchemy table definitions

### Key Components

#### Document Ingestion Pipeline

- **Ingest Services** (`src/ook/services/ingest/`): Process different document types (SDM schemas, technical notes)
- **GitHub Metadata Service** (`src/ook/services/githubmetadata/`): Extract metadata from GitHub repositories
- **LTD Integration**: Processes LSST the Docs platform content

#### Search Integration

- **Algolia Services** (`src/ook/services/algolia*.py`): Index documents to Algolia for search
- **Classification Service** (`src/ook/services/classification.py`): Categorize and tag documents

#### Data Domains

- **Authors**: Author information and affiliations from lsst-texmf
- **Glossary**: Technical terminology definitions
- **Links**: Cross-references and related documents
- **Resources**: Bibliographic resources and citations

### External Integrations

- **Kafka**: Receives events from SQuaRE Events system
- **Algolia**: Search index for www.lsst.io
- **GitHub API**: Metadata extraction from repositories
- **PostgreSQL**: Primary data store with full-text search

## Testing

The test suite uses pytest with testcontainers for integration testing. Database and Kafka containers are automatically managed during test runs.

Pytest cannot be run directly because nox is required to set up environment variables and test containers. Use the `nox -s test` command instead.
Prefer to test single files by passing the file path to pytest:

```bash
uv run --only-group=nox nox -s test -- tests/handlers/authors/authors_endpoints_test.py
```

Test structure mirrors the source code organization under `tests/`.

## Configuration

Application configuration is handled through Pydantic settings in `src/ook/config.py`. Use environment variables or the `square.env` file for local development with 1Password integration.

## Code Style

- Code style is determined by ruff linting (see `lint` nox session).
- Type checking is enforced using mypy (see `typing` nox session) using the `from __future__ import annotations` feature for forward references.
- Docstrings use the Numpydoc style, though types are omitted in docstrings as per project convention. Return and yield types are still specified by their type.
- Pytest tests use the functional style with fixtures for setup and teardown rather than class-based tests.
