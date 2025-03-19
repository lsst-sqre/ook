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

from ook.storage.github import GitHubRepoStore, GitTreeItem
from ook.storage.sdmschemaslinkstore import (
    SdmSchemasColumnLink,
    SdmSchemasLinks,
    SdmSchemasLinkStore,
    SdmSchemasSchemaLink,
    SdmSchemasTableLink,
)


class SdmSchemasIngestService:
    """A service for ingesting documentation links to sdm-schemas.lsst.io.

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
        link_store: SdmSchemasLinkStore,
    ) -> None:
        self._logger = logger
        self._http_client = http_client
        self._gh_repo_store = github_repo_store
        self._link_store = link_store

        # Shortcut to the source repository for sdm_schemas
        self._sdm_schemas_repo = {"owner": "lsst", "repo": "sdm_schemas"}
        self._md_parser = MarkdownIt("gfm-like").use(front_matter_plugin)

    @classmethod
    async def create(
        cls,
        http_client: AsyncClient,
        logger: BoundLogger,
        link_store: SdmSchemasLinkStore,
        gh_factory: GitHubAppClientFactory,
    ) -> Self:
        """Create a new instance of the service with a GitHubRepoStore
        authenticated to the sdm_schemas repository.
        """
        gh_repo_store = GitHubRepoStore(
            github_client=await gh_factory.create_installation_client_for_repo(
                owner="lsst", repo="sdm_schemas"
            ),
            logger=logger,
        )
        return cls(
            logger=logger,
            http_client=http_client,
            github_repo_store=gh_repo_store,
            link_store=link_store,
        )

    async def ingest(self) -> None:
        """Ingest links to sdm-schemas.lsst.io."""
        latest_release = await self._gh_repo_store.get_latest_release(
            **self._sdm_schemas_repo
        )
        repo_tree = await self._gh_repo_store.get_recursive_git_tree(
            **self._sdm_schemas_repo, ref=latest_release.tag_name
        )
        browser_markdown_items = repo_tree.glob("browser/*.md")
        deployed_schemas_item = repo_tree.get_path(
            "python/lsst/sdm/schemas/deployed-schemas.txt"
        )
        deployed_schemas = self._list_deployed_schemas(
            await self._get_tree_item_blob(deployed_schemas_item)
        )
        schema_browser_urls = await self._map_schema_name_to_url(
            browser_markdown_items
        )
        self._logger.debug(
            "Mapped schema names to root URLs", urls=schema_browser_urls
        )
        for schema_filename in deployed_schemas:
            try:
                schema_item = repo_tree.get_path(
                    f"python/lsst/sdm/schemas/{schema_filename}"
                )
            except ValueError:
                self._logger.warning(
                    f"Schema {schema_filename} not found in sdm_schemas",
                    deployed_schemas=deployed_schemas,
                )
                continue
            try:
                doc_url = schema_browser_urls[
                    PurePosixPath(schema_filename).stem
                ]
            except KeyError:
                self._logger.warning(
                    f"Schema {schema_filename} not found in "
                    "sdm_schemas markdown",
                    deployed_schemas=deployed_schemas,
                )
                continue
            schema_blob = await self._get_tree_item_blob(schema_item)
            schema_content = schema_blob.decode()
            self._logger.debug(
                "Ingesting SDM schema",
                file_name=schema_filename,
                docs_url=doc_url,
                schema_content=schema_content[:50],
            )
            schema_links = self._load_schema(
                docs_url=doc_url,
                yaml_content=schema_content,
            )
            await self._link_store.update_schema(schema_links)

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

    def _load_schema(
        self, *, docs_url: str, yaml_content: str
    ) -> SdmSchemasLinks:
        """Load a schema page."""
        schema: Schema = Schema.model_validate(yaml.safe_load(yaml_content))
        self._logger.debug(
            "Ingesting schema",
            schema=schema.name,
            url=docs_url,
        )
        schema_link = SdmSchemasSchemaLink(
            name=schema.name,
            id=schema.id,
            url=docs_url,
            description=schema.description,
        )

        table_links: list[SdmSchemasTableLink] = []
        column_links: list[SdmSchemasColumnLink] = []

        for table in schema.tables:
            table_docs_url = self._format_table_docs_url(docs_url, table)
            self._logger.debug(
                "Ingesting table",
                schema=schema.name,
                table=table.name,
                url=table_docs_url,
            )
            table_link = SdmSchemasTableLink(
                schema=schema_link,
                name=table.name,
                id=table.id,
                url=table_docs_url,
                description=table.description,
            )
            table_links.append(table_link)

            for column in table.columns:
                column_docs_url = self._format_column_docs_url(
                    docs_url, column
                )
                column_link = SdmSchemasColumnLink(
                    table=table_link,
                    name=column.name,
                    id=column.id,
                    url=column_docs_url,
                    description=column.description,
                )
                column_links.append(column_link)
            self._logger.debug(
                "Ingested columns",
                schema=schema.name,
                table=table.name,
                column_count=len(table.columns),
            )

        return SdmSchemasLinks(
            schema=schema_link, tables=table_links, columns=column_links
        )

    def _format_table_docs_url(self, url_base: str, table: Table) -> str:
        """Format a table's documentation URL in sdm-schemas.lsst.io."""
        # The ID starts with a hash, so we need to remove it
        return f"{url_base}{table.id}"

    def _format_column_docs_url(self, url_base: str, column: Column) -> str:
        """Format a column's documentation URL in sdm-schemas.lsst.io."""
        # The ID starts with a hash, so we need to remove
        return f"{url_base}{column.id}"
