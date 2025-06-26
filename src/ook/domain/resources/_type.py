"""Resource type."""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ResourceType"]


class ResourceType(StrEnum):
    """Enumeration of resource types.

    These types correspond to DataCite resource types and are used for
    categorization of resources. They are not to be confused with
    `ResourceClass`, which is used for metadata specialization within Ook.

    See
    https://datacite-metadata-schema.readthedocs.io/en/4.6/properties/resourcetype/
    """

    audiovisual = "Audiovisual"
    award = "Award"
    book = "Book"
    book_chapter = "BookChapter"
    collection = "Collection"
    computational_notebook = "ComputationalNotebook"
    conference_paper = "ConferencePaper"
    conference_proceeding = "ConferenceProceeding"
    data_paper = "DataPaper"
    dataset = "Dataset"
    dissertation = "Dissertation"
    event = "Event"
    image = "Image"
    interactive_resource = "InteractiveResource"
    instrument = "Instrument"
    journal = "Journal"
    journal_article = "JournalArticle"
    model = "Model"
    output_management_plan = "OutputManagementPlan"
    peer_review = "PeerReview"
    physical_object = "PhysicalObject"
    preprint = "Preprint"
    project = "Project"
    report = "Report"
    service = "Service"
    software = "Software"
    sound = "Sound"
    standard = "Standard"
    study_registration = "StudyRegistration"
    text = "Text"
    workflow = "Workflow"
    other = "Other"
