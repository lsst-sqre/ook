"""Storage to the lsst/lsst-texmf GitHub repository."""

from __future__ import annotations

import csv
from io import StringIO
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, BeforeValidator, Field, ValidationError
from safir.github import GitHubAppClientFactory
from structlog.stdlib import BoundLogger

from ook.domain.authors import Affiliation, Author, Collaboration
from ook.domain.latex import Latex
from ook.storage.github import GitHubRepoStore


class LsstTexmfGitHubRepo:
    """Storage interface to the lsst/lsst-texmf GitHub repository."""

    def __init__(
        self,
        *,
        logger: BoundLogger,
        repo_client: GitHubRepoStore,
        github_owner: str = "lsst",
        github_repo: str = "lsst-texmf",
        git_ref: str,
    ) -> None:
        self._logger = logger
        self._repo_client = repo_client
        self._gh_repo = {"owner": github_owner, "repo": github_repo}
        self._git_ref = git_ref

    @classmethod
    async def create_with_default_branch(
        cls,
        *,
        logger: BoundLogger,
        gh_factory: GitHubAppClientFactory,
        github_owner: str = "lsst",
        github_repo: str = "lsst-texmf",
    ) -> LsstTexmfGitHubRepo:
        """Create a new storage interface to the lsst/lsst-texmf GitHub
        repository using the default branch.
        """
        gh_client = await gh_factory.create_installation_client_for_repo(
            owner=github_owner, repo=github_repo
        )

        repo_client = GitHubRepoStore(github_client=gh_client, logger=logger)

        # Get the default branch from the repository
        repo_info = await repo_client.get_repo(
            owner=github_owner, repo=github_repo
        )
        default_branch = repo_info.default_branch

        return cls(
            logger=logger,
            repo_client=repo_client,
            github_owner=github_owner,
            github_repo=github_repo,
            git_ref=default_branch,
        )

    async def load_authordb(self) -> AuthorDbYaml:
        """Load the authordb.yaml file."""
        # Get the contents of the authordb.yaml file
        authordb_path = "etc/authordb.yaml"
        file_contents = await self._repo_client.get_file_contents(
            owner=self._gh_repo["owner"],
            repo=self._gh_repo["repo"],
            path=authordb_path,
            ref=self._git_ref,
        )
        authordb_yaml = yaml.safe_load(
            StringIO(file_contents.decode_content())
        )
        return AuthorDbYaml.model_validate(authordb_yaml)

    async def load_glossarydefs(self) -> list[GlossaryDef]:
        """Load the glossarydefs.csv file."""
        glossary_path = "etc/glossarydefs.csv"
        file_contents = await self._repo_client.get_file_contents(
            owner=self._gh_repo["owner"],
            repo=self._gh_repo["repo"],
            path=glossary_path,
            ref=self._git_ref,
        )
        csv_content = file_contents.decode_content()
        if csv_content is None:
            raise ValueError("No content in glossarydefs.csv file")
        return GlossaryDef.parse_csv(csv_content)

    async def load_glossarydefs_es(self) -> list[GlossaryDefEs]:
        """Load the glossarydefs_es.csv file."""
        glossary_path = "etc/glossarydefs_es.csv"
        file_contents = await self._repo_client.get_file_contents(
            owner=self._gh_repo["owner"],
            repo=self._gh_repo["repo"],
            path=glossary_path,
            ref=self._git_ref,
        )
        csv_content = file_contents.decode_content()
        if csv_content is None:
            raise ValueError("No content in glossarydefs_es.csv file")
        return GlossaryDefEs.parse_csv(csv_content)


class AuthorDbAuthor(BaseModel):
    """Model for an author entry in the authordb.yaml file."""

    name: str = Field(description="Author's surname.")

    initials: str = Field(description="Author's given name.")

    affil: list[str] = Field(
        default_factory=list, description="Affiliation IDs"
    )

    alt_affil: list[str] = Field(
        default_factory=list, description="Alternative affiliations / notes."
    )

    orcid: str | None = Field(
        default=None,
        description="Author's ORCiD identifier (optional)",
    )

    email: str | None = Field(
        default=None,
        description=(
            "Author's email username (if using a known email provider given "
            "their affiliation ID) or ``username@provider`` (to specify the "
            "provider) or their full email address."
        ),
    )

    @property
    def is_collaboration(self) -> bool:
        """Check if the author is a collaboration."""
        return self.initials == "" and self.affil == ["_"]


class AuthorDbYaml(BaseModel):
    """Model for the authordb.yaml file in lsst/lsst-texmf."""

    affiliations: dict[str, str] = Field(
        description=(
            "Mapping of affiliation IDs to affiliation info. Affiliations "
            "are their name, a comma, and their address."
        )
    )

    emails: dict[str, str] = Field(
        description=("Mapping of affiliation IDs to email domains.")
    )

    authors: dict[str, AuthorDbAuthor] = Field(
        description="Mapping of author IDs to author information"
    )

    def affiliations_to_domain(self) -> dict[str, Affiliation]:
        """Convert the affiliations to a domain model."""
        return {
            internal_id: self._parse_affiliation_text(
                internal_id=internal_id,
                affiliation_text=affiliation_text,
            )
            for internal_id, affiliation_text in self.affiliations.items()
        }

    def _parse_affiliation_text(
        self, internal_id: str, affiliation_text: str
    ) -> Affiliation:
        parts = affiliation_text.split(",")

        name = Latex(parts[0]).to_text()

        if len(parts) > 1:
            address_parts = [p.strip() for p in parts[1:]]
            address = ", ".join(address_parts)
            address = Latex(address).to_text()
        else:
            address = None

        return Affiliation(
            name=name,
            internal_id=internal_id,
            address=address,
        )

    def authors_to_domain(self) -> dict[str, Author]:
        """Convert the authors to domain models."""
        affiliations = self.affiliations_to_domain()
        return {
            author_id: self._process_author(
                internal_id=author_id,
                author=author,
                all_affiliations=affiliations,
            )
            for author_id, author in self.authors.items()
        }

    def _process_author(
        self,
        *,
        internal_id: str,
        author: AuthorDbAuthor,
        all_affiliations: dict[str, Affiliation],
    ) -> Author:
        """Process an author entry."""
        if author.email is not None:
            email: str | None = self._resolve_email(
                email_entry=author.email,
                first_affiliation_id=author.affil[0] if author.affil else None,
            )
        else:
            email = None

        return Author(
            internal_id=internal_id,
            surname=Latex(author.name).to_text(),
            given_name=Latex(author.initials).to_text(),
            orcid=author.orcid,
            email=email,
            affiliations=[all_affiliations[affil] for affil in author.affil],
            notes=[Latex(note).to_text() for note in author.alt_affil],
        )

    def _resolve_email(
        self, email_entry: str | None, first_affiliation_id: str | None
    ) -> str | None:
        """Resolve the email address for the author."""
        if email_entry is None:
            return None

        if email_entry == "_":
            return None

        if email_entry == "":
            return None

        if "@" not in email_entry:
            # This is a username for an email provider
            if first_affiliation_id is None:
                return None
            return f"{email_entry}@{self.emails[first_affiliation_id]}"

        if "@" in email_entry:
            parts = email_entry.split("@", maxsplit=1)
            if parts[1] in self.emails:
                # This is a username for an email provider
                return f"{parts[0]}@{self.emails[parts[1]]}"
            # This is a full email address
            return email_entry

        return None

    def collaborations_to_domain(self) -> dict[str, Collaboration]:
        """Convert the collaborations to domain models."""
        return {
            author_id: Collaboration(
                name=Latex(author.name).to_text(),
                internal_id=author_id,
            )
            for author_id, author in self.authors.items()
            if author.is_collaboration
        }


def split_list(
    value: str | list[str] | None,
) -> list[str]:
    """Split a string or list of strings into a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split() if v.strip()]
    return [v.strip() for v in value if v.strip()]


def split_list_csv(
    value: str | list[str] | None,
) -> list[str]:
    """Split a string or list of strings based on comma-separated values
    into a list of strings.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [v.strip() for v in value if v.strip()]


class GlossaryDef(BaseModel):
    """Model for a row in the glossarydefs.csv file in lsst/lsst-texmf."""

    term: str = Field(
        description="The glossary term.",
        validation_alias="Term",
    )

    definition: str = Field(
        description="The glossary term definition.",
        validation_alias="Description",
    )

    contexts: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="The glossary term contexts.",
            validation_alias="Subsystem Tags",
        ),
        BeforeValidator(split_list),
    ]

    documentation_tags: Annotated[
        list[str],
        Field(
            default_factory=list,
            description=(
                "The glossary term documentation tags. These are used to "
                "determine the related documentation for the glossary term."
            ),
            validation_alias="Documentation Tags",
        ),
        BeforeValidator(split_list_csv),
    ]

    related_terms: Annotated[
        list[str],
        Field(
            default_factory=list,
            description=(
                "The glossary term related terms. These are used to determine "
                "the related terms for the glossary term."
            ),
            validation_alias="Associated Acronyms and Alternative Terms",
        ),
        BeforeValidator(split_list_csv),
    ]

    type: Literal["A", "G"] = Field(
        default="G",
        description=(
            "The glossary term type. ``A`` for abbreviation, ``G`` for "
            "glossary term."
        ),
        validation_alias="Type",
    )

    @classmethod
    def parse_csv(
        cls,
        csv_content: str,
    ) -> list[Self]:
        """Parse the glossarydefs.csv file."""
        glossary_defs = []
        reader = csv.DictReader(StringIO(csv_content))
        for i, row in enumerate(reader):
            try:
                glossary_def = cls.model_validate(row)
            except ValidationError as e:
                raise ValueError(
                    f"Error parsing glossarydefs.csv at line {i + 2}"
                    f"\n\n{row}\n\n{e}"
                ) from e
            glossary_defs.append(glossary_def)
        return glossary_defs


class GlossaryDefEs(BaseModel):
    """Model for a row in the glossarydefs_es.csv file in lsst/lsst-texmf."""

    term: str = Field(
        description="The glossary term (English).",
        validation_alias="English",
    )

    definition: str = Field(
        description="The glossary term definition (in Spanish).",
        validation_alias="Español",
    )

    contexts: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="The glossary term contexts.",
            validation_alias="Subsystem Tags",
        ),
        BeforeValidator(split_list),
    ]

    @classmethod
    def parse_csv(
        cls,
        csv_content: str,
    ) -> list[Self]:
        """Parse the glossarydefs_es.csv file."""
        glossary_defs = []
        reader = csv.DictReader(StringIO(csv_content))
        for i, row in enumerate(reader):
            try:
                glossary_def = cls.model_validate(row)
            except ValidationError as e:
                raise ValueError(
                    f"Error parsing glossarydefs_es.csv at line {i + 2}"
                    f"\n\n{row}\n\n{e}"
                ) from e
            glossary_defs.append(glossary_def)
        return glossary_defs
