"""Utilities for handling LaTeX text content."""

from __future__ import annotations

import re
from typing import Self

from pylatexenc.latex2text import LatexNodes2Text
from pylatexenc.latexencode import unicode_to_latex

__all__ = ["Latex"]


class Latex:
    """A class for handling LaTeX text content."""

    def __init__(self, tex: str) -> None:
        self.tex = tex

    @classmethod
    def from_text(cls, text: str) -> Self:
        """Create a LaTeX object from text."""
        return cls(unicode_to_latex(cls.clean(text)))

    def __str__(self) -> str:
        """Return the LaTeX content as a string."""
        return self.tex

    def __repr__(self) -> str:
        """Return a string representation of the LaTeX object."""
        return f"Latex({self.tex!r})"

    def to_text(self) -> str:
        """Convert LaTeX to text."""
        text = LatexNodes2Text().latex_to_text(self.tex.strip())
        # Remove running spaces inside the content
        return self.clean(text)

    @staticmethod
    def clean(text: str) -> str:
        """Clean LaTeX text by removing extra spaces and newlines."""
        # Remove extra spaces and newlines
        return re.sub(r"\s+", " ", text).strip()
