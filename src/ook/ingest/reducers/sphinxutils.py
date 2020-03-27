"""Utilities for working with Sphinx-based content."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Generator, List, Optional

if TYPE_CHECKING:
    import lxml.html

__all__ = [
    "get_section_title",
    "clean_title_text",
    "ANCHOR_CHARACTERS",
    "SphinxSection",
    "iter_sphinx_sections",
]


ANCHOR_CHARACTERS = "¶#"
"""Characters that are used for anchor links in Sphinx documentation."""


_HEADER_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
"""Names of HTML header tags."""


def get_section_title(
    header_element: lxml.html.HtmlElement,
    anchor_characters: Optional[str] = None,
) -> str:
    """Extract the title from a header element and clean it from Sphinx
    permalink characters.

    Parameters
    ----------
    header_element : `lxml.htmlHtmlElement`
        An HTML header element (h1, h2, h3, and so on).
    anchor_characters : `str`, optional
        Characters that are used for anchor links in Sphinx headers. If not
        set, default characters are used (`ANCHOR_CHARACTERS`).

    Returns
    -------
    str
        The section title.
    """
    return clean_title_text(header_element.text_content())


def clean_title_text(
    title: str, anchor_characters: Optional[str] = None
) -> str:
    """Clean a title of Sphinx permalink characters and non-breaking space
    characters.

    Parameters
    ----------
    title : `str`
        The extracted title.
    anchor_characters : `str`, optional
        Characters that are used for anchor links in Sphinx headers. If not
        set, default characters are used (`ANCHOR_CHARACTERS`).

    Returns
    -------
    str
        The section title.
    """
    if anchor_characters is None:
        anchor_characters = ANCHOR_CHARACTERS
    return title.strip(anchor_characters).replace("\xa0", " ").strip()


@dataclass
class SphinxSection:
    """A section of content from a Sphinx document.
    """

    content: str
    """The plain-text content of the section.
    """

    headers: List[str]
    """The section headers, ordered by hierarchy.

    The header of the present section is the last element.
    """

    url: str
    """The URL of this section (typically an anchor link).
    """

    @property
    def header_level(self) -> int:
        """The header level of this section.

        For example, ``1`` corresponds to an "H1" section.
        """
        return len(self.headers)


def iter_sphinx_sections(
    *,
    root_section: "lxml.html.HtmlElement",
    base_url: str,
    headers: List[str],
    header_callback: Optional[Callable[[str], str]] = None,
    content_callback: Optional[Callable[[str], str]] = None,
) -> Generator[SphinxSection, None, None]:
    """Iterate through the hierarchical sections in a root HTML element,
    yielding the content between that section header and the next section
    header.

    This class is designed specifically for Sphinx-generated HTML, where
    ``div.section`` elements to contain each hierarchical section of content.

    Parameters
    ----------
    root_section : lxml.html.HtmlElement
        The root HTML element. It should begin with the highest level of
        heading hierarchy, which is usually the "h1" header.
    base_url : str
        The URL of the HTML page itself.
    headers : list of str
        The ordered list of heading titles at hierarchical levels above the
        present section. This parameter should be an empty list for the
        *first* (h1) section.
    header_callback : callable
        This callback function processes the section title. The callable takes
        a string and returns a string.
    content_callback : callable
        This callback function processes the section content. The callable
        takes a string and returns a string.

    Yields
    ------
    section : Section
        Yields `Section` objects for each section segment. Sections are yielded
        depth-first. The top-level section is yielded last.
    """
    id_ = root_section.attrib["id"]
    url = f"{base_url}#{id_}"
    text_elements: List[str] = []
    for element in root_section:
        if element.tag in _HEADER_TAGS:
            current_header = element.text_content()
            if header_callback:
                current_header = header_callback(current_header)
            current_headers = headers + [current_header]
        elif element.tag == "div" and "section" in element.classes:
            yield from iter_sphinx_sections(
                root_section=element,
                base_url=base_url,
                headers=current_headers,
                header_callback=header_callback,
                content_callback=content_callback,
            )
        else:
            if content_callback:
                text_elements.append(content_callback(element.text_content()))
            else:
                text_elements.append(element.text_content())

    yield SphinxSection(
        content="\n\n".join(text_elements), headers=current_headers, url=url
    )
