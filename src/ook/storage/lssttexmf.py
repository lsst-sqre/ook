"""Storage to the lsst/lsst-texmf GitHub repository."""

from __future__ import annotations

import csv
from io import StringIO
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, BeforeValidator, Field, ValidationError
from safir.github import GitHubAppClientFactory
from structlog.stdlib import BoundLogger

from ook.domain.authors import (
    Address,
    Affiliation,
    Author,
    normalize_country_code,
)
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


def normalize_str(value: Any) -> str:
    """Normalize a required string in authordb.yaml.

    Strips leading/traiing whitespace and converts underscores to empty
    strings.
    """
    if value is None:
        raise ValueError(
            "Value cannot be None. Use normalize_nullable_str() for nullable "
            "strings."
        )
    value = str(value).strip()
    if value == "_":
        return ""
    return value


NormalizedStr = Annotated[str, BeforeValidator(normalize_str)]


def normalize_nullable_str(value: Any) -> str | None:
    """Normalize a nullable string in authordb.yaml.

    - Strips leading/trailing whitespace
    - Converts underscores to None.
    - Converts empty strings to None.
    """
    if value is None:
        return None
    value = str(value).strip()
    if value == "":
        return None
    if value == "_":
        return None
    return value


OptionalStr = Annotated[str | None, BeforeValidator(normalize_nullable_str)]


class AuthorDbAuthor(BaseModel):
    """Model for an author entry in the authordb.yaml file."""

    family_name: Annotated[
        NormalizedStr, Field(description="Author's surname/family name.")
    ]

    given_name: Annotated[
        OptionalStr, Field(description="Author's given name.")
    ] = None

    affil: Annotated[
        list[str], Field(default_factory=list, description="Affiliation IDs")
    ]

    altaffil: Annotated[
        list[str],
        Field(
            default_factory=list,
            description="Alternative affiliations / notes.",
        ),
    ]

    orcid: Annotated[
        OptionalStr,
        Field(
            description="Author's ORCiD identifier (optional)",
        ),
    ] = None

    email: Annotated[
        OptionalStr,
        Field(
            description=(
                "Author's email username (if using a known email provider "
                "given their affiliation ID) or ``username@provider`` (to "
                "specify the provider) or their full email address."
            ),
        ),
    ] = None


class AuthorDbAddress(BaseModel):
    """Model for an address in authordb.yaml."""

    street: Annotated[
        OptionalStr, Field(description="Street name and number")
    ] = None

    city: Annotated[OptionalStr, Field(description="City/town name.")] = None

    state: Annotated[
        OptionalStr, Field(description="State/province/region name")
    ] = None

    postcode: Annotated[OptionalStr, Field(description="Postal/ZIP code")] = (
        None
    )

    country_code: Annotated[
        OptionalStr, Field(description="ISO country code")
    ] = None


class AuthorDbAffiliation(BaseModel):
    """Representation of an affiliation."""

    institute: Annotated[
        NormalizedStr,
        Field(description="Name of the institution/organization"),
    ]

    department: Annotated[
        OptionalStr,
        Field(description="Department or division within the institution"),
    ] = None

    ror_id: Annotated[
        OptionalStr,
        Field(description="Research Organization Registry (ROR) identifier"),
    ] = None

    email: Annotated[
        OptionalStr, Field(description="Email domain for the institution")
    ] = None

    address: AuthorDbAddress | None = None


class AuthorDbYaml(BaseModel):
    """Model for the authordb.yaml file in lsst/lsst-texmf."""

    affiliations: dict[str, AuthorDbAffiliation] = Field(
        description=("Mapping of affiliation IDs to affiliation info.")
    )

    authors: dict[str, AuthorDbAuthor] = Field(
        description="Mapping of author IDs to author information"
    )

    def affiliations_to_domain(self) -> dict[str, Affiliation]:
        """Convert the affiliations to a domain model."""
        return {
            internal_id: Affiliation(
                name=Latex(affiliation.institute).to_text(),
                department=Latex(affiliation.department).to_text()
                if affiliation.department
                else None,
                internal_id=internal_id,
                email=affiliation.email,
                ror_id=affiliation.ror_id,
                address=Address(
                    street=Latex(affiliation.address.street).to_text()
                    if affiliation.address and affiliation.address.street
                    else None,
                    city=Latex(affiliation.address.city).to_text()
                    if affiliation.address and affiliation.address.city
                    else None,
                    state=Latex(affiliation.address.state).to_text()
                    if affiliation.address and affiliation.address.state
                    else None,
                    postal_code=Latex(affiliation.address.postcode).to_text()
                    if affiliation.address and affiliation.address.postcode
                    else None,
                    country_code=normalize_country_code(
                        Latex(affiliation.address.country_code).to_text()
                    )
                    if affiliation.address and affiliation.address.country_code
                    else None,
                    country_name=(
                        Latex(
                            affiliation.address.country_code
                        ).to_text()  # Store original LaTeX-processed
                        if affiliation.address
                        and affiliation.address.country_code
                        else None
                    ),
                ),
            )
            for internal_id, affiliation in self.affiliations.items()
        }

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
                first_affiliation=all_affiliations[author.affil[0]]
                if author.affil
                else None,
                all_affiliations=all_affiliations,
            )
        else:
            email = None

        return Author(
            internal_id=internal_id,
            surname=Latex(author.family_name).to_text(),
            given_name=Latex(author.given_name).to_text()
            if author.given_name
            else None,
            orcid=author.orcid,
            email=email,
            affiliations=[all_affiliations[affil] for affil in author.affil],
            notes=[Latex(note).to_text() for note in author.altaffil],
        )

    def _resolve_email(
        self,
        email_entry: str | None,
        first_affiliation: Affiliation | None,
        all_affiliations: dict[str, Affiliation] | None = None,
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
            if first_affiliation is None:
                return None
            if first_affiliation.email is None:
                # No email domain specified for the affiliation
                return None
            return f"{email_entry}@{first_affiliation.email}"

        if "@" in email_entry:
            parts = email_entry.split("@", maxsplit=1)
            if all_affiliations is None:
                # No affiliations provided, so we cannot resolve the email
                return None
            if parts[1] in all_affiliations:
                # This is a username for an email provider
                return f"{parts[0]}@{all_affiliations[parts[1]].email}"
            # This is a full email address
            return email_entry

        return None


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
