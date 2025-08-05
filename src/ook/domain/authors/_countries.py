"""Country code normalization utilities for author affiliations."""

from __future__ import annotations

from functools import lru_cache

import pycountry

__all__ = ["get_country_name", "normalize_country_code"]

# Custom mapping for known non-standard country codes and names
# All keys are stored in uppercase for case-insensitive lookup
_CUSTOM_COUNTRY_MAPPING = {
    "USA": "US",
    "UK": "GB",
    "THE NETHERLANDS": "NL",
    "PEOPLE'S REPUBLIC OF CHINA": "CN",
    "CZECH REPUBLIC": "CZ",
    "SERBIA": "RS",
    "SWITZERLAND": "CH",
    "GERMANY": "DE",
    "ARGENTINA": "AR",
    "MEXICO": "MX",
    "FRANCE": "FR",
    "ITALY": "IT",
    "SPAIN": "ES",
    "CANADA": "CA",
    "BRAZIL": "BR",
    "JAPAN": "JP",
    "POLAND": "PL",
    "SLOVENIA": "SI",
    "SWEDEN": "SE",
    "BELGIUM": "BE",
    "INDIA": "IN",
}


@lru_cache(maxsize=256)
def _cached_normalize_country_code(code: str) -> str | None:
    """Normalize country codes to ISO format."""
    # Check custom mapping first (case-insensitive)
    code_upper = code.upper()
    if code_upper in _CUSTOM_COUNTRY_MAPPING:
        return _CUSTOM_COUNTRY_MAPPING[code_upper]

    # Try fuzzy search for other cases
    try:
        countries = pycountry.countries.search_fuzzy(code)
        if countries:
            return getattr(countries[0], "alpha_2", None)
    except (IndexError, AttributeError, LookupError):
        # Could not normalize country code
        pass

    return None


def normalize_country_code(raw_country_code: str | None) -> str | None:
    """Convert country names/codes to ISO 3166-1 alpha-2 format.

    This function handles various forms of country identification including:
    - Full country names (e.g., "United States" -> "US")
    - Common variations (e.g., "USA" -> "US", "UK" -> "GB")
    - Non-standard names (e.g., "The Netherlands" -> "NL")
    - Already valid ISO codes (passed through)

    Parameters
    ----------
    raw_country_code
        The raw country name or code to normalize.

    Returns
    -------
    str or None
        The normalized ISO 3166-1 alpha-2 country code,
        or None if normalization failed.
    """
    if not raw_country_code:
        return None

    # Strip whitespace from input
    cleaned_code = raw_country_code.strip()
    if not cleaned_code:
        return None

    return _cached_normalize_country_code(cleaned_code)


@lru_cache(maxsize=256)
def get_country_name(country_code: str | None) -> str | None:
    """Convert ISO 3166-1 alpha-2 code to country name.

    Parameters
    ----------
    country_code
        The ISO 3166-1 alpha-2 country code.

    Returns
    -------
    str or None
        The official country name, or None if not found.
    """
    if not country_code:
        return None

    try:
        country = pycountry.countries.get(alpha_2=country_code.upper())
        if country:
            return getattr(country, "name", None)
    except (AttributeError, KeyError):
        pass

    return None
