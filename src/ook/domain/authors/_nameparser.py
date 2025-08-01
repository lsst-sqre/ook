"""Name parsing utilities for author search functionality."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar


class NameFormat(Enum):
    """Enum representing different name formats that can be parsed."""

    FIRST_LAST = "first_last"  # "Jonathan Sick"
    LAST_COMMA_FIRST = "last_comma_first"  # "Sick, Jonathan"
    LAST_COMMA_INITIAL = "last_comma_initial"  # "Sick, J" or "Sick, J."
    SINGLE_WORD = "single_word"  # "Sick"
    COMPLEX = "complex"  # Everything else that doesn't fit standard patterns


@dataclass
class ParsedName:
    """Parsed name components extracted from a search query."""

    original: str
    """The original search query string."""

    format: NameFormat
    """The detected format of the name."""

    surname: str | None = None
    """The surname/family name component."""

    given_name: str | None = None
    """The given name component (may include middle names/initials)."""

    initials: list[str] | None = None
    """List of extracted initials (without periods)."""

    suffix: str | None = None
    """Name suffix (Jr., Sr., III, etc.)."""


class NameParser:
    """Parses author names into structured components for flexible search."""

    # Common name suffixes to recognize and extract
    SUFFIXES: ClassVar[set[str]] = {
        "jr",
        "jr.",
        "sr",
        "sr.",
        "ii",
        "iii",
        "iv",
        "v",
    }

    def parse(self, query: str) -> ParsedName:
        """Parse a name query into structured components.

        Parameters
        ----------
        query
            The search query string to parse.

        Returns
        -------
        ParsedName
            Structured representation of the parsed name components.
        """
        query = query.strip()
        if not query:
            return ParsedName(original=query, format=NameFormat.COMPLEX)

        # Detect and handle suffix
        suffix = self._extract_suffix(query)
        if suffix:
            # Remove suffix and any trailing comma
            query = query[: -len(suffix)].rstrip(", ")

        # Detect format based on presence of comma
        if "," in query:
            return self._parse_comma_format(query, suffix)
        elif " " in query:
            return self._parse_space_format(query, suffix)
        else:
            return ParsedName(
                original=query + (f", {suffix}" if suffix else ""),
                format=NameFormat.SINGLE_WORD,
                surname=query,
                suffix=suffix,
            )

    def _extract_suffix(self, name: str) -> str | None:
        """Extract suffix from name if present.

        Parameters
        ----------
        name
            The name string to check for suffixes.

        Returns
        -------
        str or None
            The extracted suffix, or None if no suffix found.
        """
        parts = name.split()
        if len(parts) > 1:
            last_part = parts[-1].lower().rstrip(",.")
            if last_part in self.SUFFIXES:
                return parts[-1]
        return None

    def _parse_comma_format(
        self, query: str, suffix: str | None
    ) -> ParsedName:
        """Parse comma-separated format names.

        Handles formats like:
        - "Sick, Jonathan"
        - "Sick, J"
        - "Plazas Malagón, Andrés A."
        - "Neilsen, Jr., Eric H." (suffix in middle)

        Parameters
        ----------
        query
            The query string without suffix.
        suffix
            The extracted suffix, if any.

        Returns
        -------
        ParsedName
            Parsed name components.
        """
        # Handle the special case where suffix appears in the middle
        # e.g., "Neilsen, Jr., Eric H." should be parsed as
        # surname="Neilsen", suffix="Jr.", given="Eric H."
        parts = [p.strip() for p in query.split(",")]

        if len(parts) == 3:
            # Check if middle part is a suffix
            potential_suffix = parts[1].lower().rstrip(".")
            if potential_suffix in self.SUFFIXES:
                surname = parts[0]
                suffix = parts[1]
                given = parts[2]

                # Apply the same initial extraction logic
                given_parts = given.split()
                initials = []
                given_name_parts = []

                for part in given_parts:
                    # Check if this part is an initial (1-2 chars, optionally
                    # with period)
                    if len(part) <= 2 and (len(part) == 1 or part[1] == "."):
                        initials.append(part.rstrip("."))
                    else:
                        given_name_parts.append(part)

                # If only initials, treat as initial format
                if initials and not given_name_parts:
                    return ParsedName(
                        original=query
                        + (
                            f", {suffix}"
                            if suffix and suffix != parts[1]
                            else ""
                        ),
                        format=NameFormat.LAST_COMMA_INITIAL,
                        surname=surname,
                        initials=initials,
                        suffix=parts[1],  # Use the suffix from middle position
                    )

                # Otherwise, store full given name
                return ParsedName(
                    original=query
                    + (f", {suffix}" if suffix and suffix != parts[1] else ""),
                    format=NameFormat.LAST_COMMA_FIRST,
                    surname=surname,
                    given_name=given,
                    initials=initials if initials else None,
                    suffix=parts[1],  # Use the suffix from middle position
                )

        # Standard two-part comma format
        if len(parts) != 2:
            return ParsedName(
                original=query + (f", {suffix}" if suffix else ""),
                format=NameFormat.COMPLEX,
            )

        surname, given = parts

        # Extract middle initials from given name
        given_parts = given.split()
        initials = []
        given_name_parts = []

        for part in given_parts:
            # Check if this part is an initial
            # (1-2 chars, optionally with period)
            if len(part) <= 2 and (len(part) == 1 or part[1] == "."):
                initials.append(part.rstrip("."))
            else:
                given_name_parts.append(part)

        # If only initials, treat as initial format
        if initials and not given_name_parts:
            return ParsedName(
                original=query + (f", {suffix}" if suffix else ""),
                format=NameFormat.LAST_COMMA_INITIAL,
                surname=surname,
                initials=initials,
                suffix=suffix,
            )

        # Otherwise, store full given name (including middle initials)
        return ParsedName(
            original=query + (f", {suffix}" if suffix else ""),
            format=NameFormat.LAST_COMMA_FIRST,
            surname=surname,
            given_name=given,  # Keep full given name including middle initials
            initials=initials
            if initials
            else None,  # Store extracted initials separately
            suffix=suffix,
        )

    def _parse_space_format(
        self, query: str, suffix: str | None
    ) -> ParsedName:
        """Parse space-separated format names.

        Handles formats like:
        - "Jonathan Sick"
        - "Andrés A. Plazas Malagón"

        Parameters
        ----------
        query
            The query string without suffix.
        suffix
            The extracted suffix, if any.

        Returns
        -------
        ParsedName
            Parsed name components.
        """
        parts = query.split()
        if len(parts) < 2:
            return ParsedName(
                original=query + (f" {suffix}" if suffix else ""),
                format=NameFormat.COMPLEX,
            )

        # For space-separated format, assume first part(s) are given name,
        # last part(s) are surname. This is a simple heuristic that works
        # for most Western naming conventions.

        # Check if we have compound surnames (multiple capitalized words at
        # the end) This is a simple heuristic - could be enhanced with more
        # sophisticated logic
        if len(parts) >= 3:
            # Look for patterns like "Andrés A. Plazas Malagón"
            # where the last two parts might be a compound surname
            potential_surname_parts = []
            given_name_parts = []

            # Simple heuristic: if last two parts are both capitalized and
            # not single letters, they might be compound surname
            last_two = parts[-2:]
            if (
                len(last_two) == 2
                and all(len(p) > 1 and p[0].isupper() for p in last_two)
                and not any(p.endswith(".") for p in last_two)
            ):
                potential_surname_parts = last_two
                given_name_parts = parts[:-2]
            else:
                # Default: last part is surname
                potential_surname_parts = [parts[-1]]
                given_name_parts = parts[:-1]

            surname = " ".join(potential_surname_parts)
            given_name = " ".join(given_name_parts)
        else:
            # Simple case: first part given, last part surname
            given_name = parts[0]
            surname = " ".join(parts[1:])

        # Extract initials from given name
        given_parts = given_name.split()
        initials = [
            part.rstrip(".")
            for part in given_parts
            if len(part) <= 2 and (len(part) == 1 or part[1] == ".")
        ]

        return ParsedName(
            original=query + (f" {suffix}" if suffix else ""),
            format=NameFormat.FIRST_LAST,
            surname=surname,
            given_name=given_name,
            initials=initials if initials else None,
            suffix=suffix,
        )
