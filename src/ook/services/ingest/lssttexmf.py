"""Ingest service for lsst-texmf data (authors and glossary)."""

from __future__ import annotations

from typing import Self

from httpx import AsyncClient
from safir.github import GitHubAppClientFactory
from structlog.stdlib import BoundLogger

from ook.storage.authorstore import AuthorStore
from ook.storage.github import GitHubRepoStore
from ook.storage.glossarystore import GlossaryStore
from ook.storage.lssttexmf import LsstTexmfGitHubRepo


class LsstTexmfIngestService:
    """Ingest service for lsst-texmf data (authors and glossary)."""

    def __init__(
        self,
        *,
        logger: BoundLogger,
        http_client: AsyncClient,
        github_repo_store: GitHubRepoStore,
        author_store: AuthorStore,
        glossary_store: GlossaryStore,
        github_owner: str = "lsst",
        github_repo: str = "lsst-texmf",
    ) -> None:
        self._logger = logger
        self._http_client = http_client
        self._author_store = author_store
        self._glossary_store = glossary_store
        self._gh_repo_store = github_repo_store
        self._gh_repo = {"owner": github_owner, "repo": github_repo}

    @classmethod
    async def create(
        cls,
        *,
        logger: BoundLogger,
        http_client: AsyncClient,
        gh_factory: GitHubAppClientFactory,
        author_store: AuthorStore,
        glossary_store: GlossaryStore,
        github_owner: str = "lsst",
        github_repo: str = "lsst-texmf",
    ) -> Self:
        """Create a new ingest service with a GitHubRepoStore authenticated to
        the lsst-texmf repository.
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
            author_store=author_store,
            glossary_store=glossary_store,
            github_owner=github_owner,
            github_repo=github_repo,
        )

    async def ingest(
        self,
        git_ref: str | None = None,
        *,
        ingest_authordb: bool = True,
        ingest_glossary: bool = True,
    ) -> None:
        """Ingest the lsst-texmf data.

        Parameters
        ----------
        git_ref
            The git reference to use for the lsst-texmf repository. If not
            provided, the default branch will be used.
        """
        if git_ref is None:
            repo_info = await self._gh_repo_store.get_repo(
                owner=self._gh_repo["owner"], repo=self._gh_repo["repo"]
            )
            resolved_git_ref = repo_info.default_branch
        else:
            resolved_git_ref = git_ref
        texmf_repo = LsstTexmfGitHubRepo(
            logger=self._logger,
            repo_client=self._gh_repo_store,
            github_owner=self._gh_repo["owner"],
            github_repo=self._gh_repo["repo"],
            git_ref=resolved_git_ref,
        )

        if ingest_authordb:
            await self._ingest_authordb(texmf_repo)

        if ingest_glossary:
            await self._ingest_glossary(texmf_repo)

    async def _ingest_authordb(self, repo: LsstTexmfGitHubRepo) -> None:
        """Ingest the authordb.yaml file."""
        author_db_data = await repo.load_authordb()
        await self._author_store.upsert_affiliations(
            affiliations=list(author_db_data.affiliations_to_domain().values())
        )
        authors = author_db_data.authors_to_domain()
        await self._author_store.upsert_authors(list(authors.values()))
        await self._author_store.upsert_collaborations(
            collaborations=list(
                author_db_data.collaborations_to_domain().values()
            )
        )

    async def _ingest_glossary(self, repo: LsstTexmfGitHubRepo) -> None:
        """Ingest the glossarydefs.csv file."""
        glossary_data = await repo.load_glossarydefs()
        es_glossary_data = await repo.load_glossarydefs_es()
        await self._glossary_store.store_glossarydefs(
            terms=glossary_data, es_translations=es_glossary_data
        )
