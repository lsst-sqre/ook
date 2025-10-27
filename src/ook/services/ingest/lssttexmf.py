"""Ingest service for lsst-texmf data (authors and glossary)."""

from __future__ import annotations

from typing import Self

from httpx import AsyncClient
from safir.github import GitHubAppClientFactory
from structlog.stdlib import BoundLogger

from ook.domain.authors import Author
from ook.exceptions import DuplicateOrcidError
from ook.services.slack import SlackNotificationService
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
        slack_service: SlackNotificationService,
        github_owner: str = "lsst",
        github_repo: str = "lsst-texmf",
    ) -> None:
        self._logger = logger
        self._http_client = http_client
        self._author_store = author_store
        self._glossary_store = glossary_store
        self._slack_service = slack_service
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
        slack_service: SlackNotificationService,
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
            slack_service=slack_service,
            github_owner=github_owner,
            github_repo=github_repo,
        )

    async def ingest(
        self,
        git_ref: str | None = None,
        *,
        ingest_authordb: bool = True,
        ingest_glossary: bool = True,
        delete_stale_records: bool = False,
    ) -> None:
        """Ingest the lsst-texmf data.

        Parameters
        ----------
        git_ref
            The git reference to use for the lsst-texmf repository. If not
            provided, the default branch will be used.
        ingest_authordb
            Whether to ingest the authordb.yaml file. Defaults to True.
        ingest_glossary
            Whether to ingest the glossarydefs.csv file. Defaults to True.
        delete_stale_records
            Whether to delete stale records in the author store. Defaults to
            False.
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
            await self._ingest_authordb(
                texmf_repo, delete_stale_records=delete_stale_records
            )

        if ingest_glossary:
            await self._ingest_glossary(texmf_repo)

    async def _ingest_authordb(
        self, repo: LsstTexmfGitHubRepo, *, delete_stale_records: bool
    ) -> None:
        """Ingest the authordb.yaml file with stale entry detection."""
        author_db_data = await repo.load_authordb()
        await self._author_store.upsert_affiliations(
            affiliations=list(
                author_db_data.affiliations_to_domain().values()
            ),
            delete_stale_records=delete_stale_records,
        )

        # Parse authors from authordb.yaml
        authors = author_db_data.authors_to_domain()

        # Detect stale entries before upserting
        stale_authors = await self._detect_stale_authors(authors)

        # Send Slack notification if stale entries found
        if stale_authors:
            self._logger.warning(
                "Found %d stale author entries",
                len(stale_authors),
                extra={
                    "stale_author_ids": [a.internal_id for a in stale_authors]
                },
            )
            await self._slack_service.notify_stale_authors(
                stale_authors,
                git_ref=repo._git_ref,  # noqa: SLF001
            )

        # Attempt to upsert authors with duplicate ORCID error handling
        try:
            await self._author_store.upsert_authors(
                list(authors.values()),
                git_ref=repo._git_ref,  # noqa: SLF001
                delete_stale_records=delete_stale_records,
            )
        except DuplicateOrcidError as e:
            self._logger.exception(
                "Duplicate ORCID constraint violation",
                extra={
                    "orcid": e.orcid,
                    "existing_author_id": e.existing_author.internal_id,
                    "new_author_ids": [a.internal_id for a in e.new_authors],
                },
            )
            # SlackException is automatically formatted and sent to Slack
            await self._slack_service.post_exception(e)
            raise

    async def _ingest_glossary(self, repo: LsstTexmfGitHubRepo) -> None:
        """Ingest the glossarydefs.csv file."""
        glossary_data = await repo.load_glossarydefs()
        es_glossary_data = await repo.load_glossarydefs_es()
        await self._glossary_store.store_glossarydefs(
            terms=glossary_data, es_translations=es_glossary_data
        )

    async def _detect_stale_authors(
        self, current_authors: dict[str, Author]
    ) -> list[Author]:
        """Detect author entries that no longer exist in authordb.yaml.

        Parameters
        ----------
        current_authors
            Dictionary of current authors from authordb.yaml, keyed by
            internal_id.

        Returns
        -------
        list[Author]
            List of stale author entries that exist in the database but
            not in the current authordb.yaml.
        """
        # Get all authors from database (using unlimited pagination)
        all_authors_page = await self._author_store.get_authors(limit=None)
        db_authors = all_authors_page.entries

        # Find authors in DB but not in current authordb.yaml
        return [
            author
            for author in db_authors
            if author.internal_id not in current_authors
        ]
