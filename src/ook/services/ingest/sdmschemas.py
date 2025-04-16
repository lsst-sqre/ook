"""Ingest for sdm-schemas.lsst.io."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Self

import yaml
from felis.datamodel import Column, Schema, Table
from httpx import AsyncClient
from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.front_matter import front_matter_plugin
from safir.github import GitHubAppClientFactory
from safir.github.models import GitHubBlobModel
from structlog.stdlib import BoundLogger

from ook.domain.links import SdmColumnLink, SdmSchemaLink, SdmTableLink
from ook.domain.sdmschemas import (
    SdmColumn,
    SdmSchemaRecursive,
    SdmTableRecursive,
)
from ook.storage.github import (
    GitHubReleaseModel,
    GitHubRepoStore,
    GitTreeItem,
    RecursiveGitTreeModel,
)
from ook.storage.linkstore import LinkStore, SdmSchemaBulkLinks
from ook.storage.sdmschemastore import SdmSchemasStore


class SdmSchemasIngestService:
    """A service for ingesting SDM Schemas and schema browser links.

    Parameters
    ----------
    logger
        A logger for the service.
    http_client
        An HTTP client for fetching resources.
    github_repo_store
        A store for fetching resources from GitHub.
    """

    def __init__(
        self,
        *,
        logger: BoundLogger,
        http_client: AsyncClient,
        github_repo_store: GitHubRepoStore,
        sdm_schemas_store: SdmSchemasStore,
        link_store: LinkStore,
        github_owner: str,
        github_repo: str,
    ) -> None:
        self._logger = logger
        self._http_client = http_client
        self._gh_repo_store = github_repo_store
        self._link_store = link_store
        self._sdm_schemas_store = sdm_schemas_store

        # Shortcut to the source repository for sdm_schemas
        self._sdm_schemas_repo = {"owner": github_owner, "repo": github_repo}
        self._md_parser = MarkdownIt("gfm-like").use(front_matter_plugin)

    @classmethod
    async def create(
        cls,
        http_client: AsyncClient,
        logger: BoundLogger,
        link_store: LinkStore,
        sdm_schemas_store: SdmSchemasStore,
        gh_factory: GitHubAppClientFactory,
        github_owner: str,
        github_repo: str,
    ) -> Self:
        """Create a new instance of the service with a GitHubRepoStore
        authenticated to the sdm_schemas repository.
        """
        gh_repo_store = GitHubRepoStore(
            github_client=await gh_factory.create_installation_client_for_repo(
                owner=github_owner, repo=github_repo
            ),
            logger=logger,
        )
        return cls(
            logger=logger,
            http_client=http_client,
            github_repo_store=gh_repo_store,
            link_store=link_store,
            sdm_schemas_store=sdm_schemas_store,
            github_owner=github_owner,
            github_repo=github_repo,
        )

    async def ingest(self, release_tag: str | None = None) -> None:
        """Ingest schemas and links to sdm-schemas.lsst.io."""
        if release_tag is not None:
            github_release = await self._gh_repo_store.get_release_by_tag(
                **self._sdm_schemas_repo, tag=release_tag
            )
        else:
            github_release = await self._gh_repo_store.get_latest_release(
                **self._sdm_schemas_repo
            )
        self._logger.info(
            "Ingesting SDM schemas",
            release_tag=github_release.tag_name,
            github_owner=self._sdm_schemas_repo["owner"],
            github_repo=self._sdm_schemas_repo["repo"],
        )
        repo_tree = await self._gh_repo_store.get_recursive_git_tree(
            **self._sdm_schemas_repo, ref=github_release.tag_name
        )
        self._logger.info(
            "Got SDM schemas tree",
            is_truncated=repo_tree.truncated,
            url=repo_tree.url,
        )

        # Ingest the SDM schemas into the SdmSchemasStore
        await self._ingest_all_schemas(github_release, repo_tree)

        # Ingest the schema browser links into the LinkStore
        await self._ingest_schema_browser_links(repo_tree)

    async def _ingest_all_schemas(
        self, release: GitHubReleaseModel, repo_tree: RecursiveGitTreeModel
    ) -> None:
        """Ingest all schemas from a GitHub release."""
        schema_items = repo_tree.glob("python/lsst/sdm/schemas/*.yaml")
        self._logger.info(
            "Found SDM schemas in release",
            schema_count=len(schema_items),
            schema_paths=[item.path for item in schema_items],
        )
        for schema_tree_item in schema_items:
            schema_blob = await self._get_tree_item_blob(schema_tree_item)
            schema_content = schema_blob.decode()
            self._logger.debug(
                "Ingesting SDM schema",
                path=schema_tree_item.path,
                schema_content=schema_content[:50],
            )
            sdm_schema = self._load_schema(
                yaml_content=schema_content,
                github_owner=self._sdm_schemas_repo["owner"],
                github_repo=self._sdm_schemas_repo["repo"],
                github_ref=release.tag_name,
                github_path=schema_tree_item.path,
            )
            await self._sdm_schemas_store.update_schema(sdm_schema)

    async def _ingest_schema_browser_links(
        self, repo_tree: RecursiveGitTreeModel
    ) -> None:
        """Ingest schema browser links to sdm-schemas.lsst.io for all schemas
        in the SDM Schemas release.
        """
        browser_markdown_items = repo_tree.glob("browser/*.md")
        for browser_markdown_item in browser_markdown_items:
            if browser_markdown_item.path == "browser/index.md":
                continue

            md_blob = await self._get_tree_item_blob(browser_markdown_item)

            # The schema field in the Markdown frontmatter has the stem of the
            # schema's YAML filename. For example, "dp02_dc2" for
            # "python/lsst/sdm/schemas/dp02_dc2.yaml".
            md_content = md_blob.decode()
            frontmatter_data = self._get_md_frontmatter(
                self._md_parser.parse(md_content, {})
            )
            if frontmatter_data is None:
                continue
            schema_name_stem = frontmatter_data.get("schema")
            if schema_name_stem is None:
                self._logger.warning(
                    "schema unknown in sdm_schemas markdown",
                    path=browser_markdown_item.path,
                )
                continue
            schema_repo_path = (
                f"python/lsst/sdm/schemas/{schema_name_stem}.yaml"
            )

            schema_browser_url_path = str(
                PurePosixPath(browser_markdown_item.path)
                .with_suffix(".html")
                .relative_to("browser")
            )
            schema_url = (
                f"https://sdm-schemas.lsst.io/{schema_browser_url_path}"
            )

            # Make the SdmSchemaLink to the schema, along with the SdmTableLink
            # and SdmColumnLink for each table and column in the schema.
            sdm_schema = await self._sdm_schemas_store.get_schema_by_repo_path(
                github_owner=self._sdm_schemas_repo["owner"],
                github_repo=self._sdm_schemas_repo["repo"],
                github_path=schema_repo_path,
            )
            if sdm_schema is None:
                self._logger.warning(
                    "No SDM schema for schema browser markdown",
                    schema_browser_url=schema_url,
                    github_owner=self._sdm_schemas_repo["owner"],
                    github_repo=self._sdm_schemas_repo["repo"],
                    github_path=schema_repo_path,
                )
                continue
            schema_links = SdmSchemaBulkLinks(
                schema=SdmSchemaLink(
                    schema_name=sdm_schema.name,
                    html_url=schema_url,
                    title=f"{sdm_schema.name} schema",
                    type="schema_browser",
                    collection_title="Science Data Model Schemas",
                ),
                tables=[
                    SdmTableLink(
                        table_name=table.name,
                        schema_name=sdm_schema.name,
                        html_url=f"{schema_url}{table.felis_id}",
                        title=f"{table.name} table",
                        type="schema_browser",
                        collection_title="Science Data Model Schemas",
                    )
                    for table in sdm_schema.tables
                ],
                columns=[
                    SdmColumnLink(
                        column_name=column.name,
                        schema_name=sdm_schema.name,
                        table_name=table.name,
                        html_url=f"{schema_url}{column.felis_id}",
                        title=f"{column.name} column",
                        type="schema_browser",
                        collection_title="Science Data Model Schemas",
                    )
                    for table in sdm_schema.tables
                    for column in table.columns
                ],
            )
            await self._link_store.update_sdm_schema_links(schema_links)

    async def _get_tree_item_blob(self, item: GitTreeItem) -> GitHubBlobModel:
        """Get the blob for a tree item."""
        return await self._gh_repo_store.get_blob(str(item.url))

    def _list_deployed_schemas(self, git_item: GitHubBlobModel) -> list[str]:
        """List the deployed schemas from a GitHub blob.

        These are the filenames of the schemas in the deployed-schemas.txt file
        and include the .yaml extension.
        """
        return [
            n.strip()
            for n in git_item.decode().splitlines()
            if len(n.strip()) > 0
        ]

    def _load_schema(
        self,
        *,
        yaml_content: str,
        github_owner: str,
        github_repo: str,
        github_ref: str,
        github_path: str,
    ) -> SdmSchemaRecursive:
        """Load a schema into domain models."""
        schema: Schema = Schema.model_validate(yaml.safe_load(yaml_content))
        self._logger.debug(
            "Loaded SDM schema",
            schema=schema.name,
        )
        domain_schema = SdmSchemaRecursive(
            name=schema.name,
            felis_id=schema.id,
            description=schema.description,
            github_owner=github_owner,
            github_repo=github_repo,
            github_ref=github_ref,
            github_path=github_path,
            tables=[],
        )

        for table in schema.tables:
            domain_table = SdmTableRecursive(
                name=table.name,
                felis_id=table.id,
                schema_name=schema.name,
                description=table.description,
                tap_table_index=table.tap_table_index,
                columns=[],
            )
            domain_schema.tables.append(domain_table)

            for column in table.columns:
                domain_column = SdmColumn(
                    name=column.name,
                    felis_id=column.id,
                    table_name=table.name,
                    schema_name=schema.name,
                    description=column.description,
                    datatype=column.datatype,
                    ivoa_ucd=column.ivoa_ucd,
                    ivoa_unit=column.ivoa_unit,
                    tap_column_index=column.tap_column_index,
                )
                domain_table.columns.append(domain_column)
            self._logger.debug(
                "Loaded SDM table",
                schema=schema.name,
                table=table.name,
                column_count=len(domain_table.columns or []),
            )

        return domain_schema

    def _format_table_docs_url(self, url_base: str, table: Table) -> str:
        """Format a table's documentation URL in sdm-schemas.lsst.io."""
        # The ID starts with a hash, so we need to remove it
        return f"{url_base}{table.id}"

    def _format_column_docs_url(self, url_base: str, column: Column) -> str:
        """Format a column's documentation URL in sdm-schemas.lsst.io."""
        # The ID starts with a hash, so we need to remove
        return f"{url_base}{column.id}"

    async def _map_schema_name_to_url(
        self, browser_markdown_items: list[GitTreeItem]
    ) -> dict[str, str]:
        """Map schema names to URLs on sdm-schemas.lsst.io.

        Example::

          {"dp02_dc2": "https://sdm-schemas.lsst.io/dp02.html"}
        """
        urls: dict[str, str] = {}
        for item in browser_markdown_items:
            if item.path == "browser/index.md":
                continue
            md_blob = await self._get_tree_item_blob(item)
            md_content = md_blob.decode()
            yaml_data = self._get_md_frontmatter(
                self._md_parser.parse(md_content, {})
            )
            if yaml_data is None:
                continue
            schema_name = yaml_data.get("schema")
            if schema_name is None:
                self._logger.warning(
                    "schema unknown in sdm_schemas markdown",
                    path=item.path,
                )
                continue
            path = (
                PurePosixPath(item.path)
                .with_suffix(".html")
                .relative_to("browser")
            )
            url = f"https://sdm-schemas.lsst.io/{path}"
            urls[schema_name] = url
        return urls

    def _get_md_frontmatter(self, md_tokens: list[Token]) -> dict | None:
        """Get the frontmatter from a list of Markdown tokens."""
        for token in md_tokens:
            if token.type == "front_matter":
                return yaml.safe_load(token.content)
        return None
