from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ._class import ResourceClass
from ._resource import Resource

__all__ = ["Document"]


class Document(Resource):
    """A document resource with additional metadata.

    For example, a technote or change-controlled document.
    """

    resource_class: Annotated[
        Literal[ResourceClass.document],
        Field(
            description=(
                "Class of the resource, used for metadata specialization."
            ),
        ),
    ] = ResourceClass.document

    series: Annotated[str, Field(description="Series name of the document.")]

    handle: Annotated[
        str,
        Field(description="Project document identifier", examples=["SQR-000"]),
    ]

    generator: Annotated[
        str | None,
        Field(
            description="Document generator used to create the document",
            examples=["Documenteer 2.0.0", "Lander 2.0.0"],
        ),
    ] = None

    number: Annotated[
        int,
        Field(
            description=(
                "Numeric component of handle for sorting within series"
            ),
            examples=[50, 123, 1],
        ),
    ]


# Rebuild the model to ensure all fields are correctly set up
Document.model_rebuild()
