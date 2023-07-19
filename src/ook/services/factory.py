"""Factory for creating Ook services."""

from __future__ import annotations

from aiokafka import AIOKafkaProducer
from algoliasearch.search_client import SearchClient
from httpx import AsyncClient
from kafkit.fastapi.dependencies.aiokafkaproducer import (
    kafka_producer_dependency,
)
from kafkit.fastapi.dependencies.pydanticschemamanager import (
    pydantic_schema_manager_dependency,
)
from kafkit.registry.manager import PydanticSchemaManager
from safir.dependencies.http_client import http_client_dependency
from safir.github import GitHubAppClientFactory
from structlog.stdlib import BoundLogger

from ..config import config
from ..dependencies.algoliasearch import algolia_client_dependency
from .algoliadocindex import AlgoliaDocIndexService
from .classification import ClassificationService
from .githubmetadata import GitHubMetadataService
from .kafkaproducer import PydanticKafkaProducer
from .landerjsonldingest import LtdLanderJsonLdIngestService
from .ltdmetadataservice import LtdMetadataService
from .sphinxtechnoteingest import SphinxTechnoteIngestService


class Factory:
    """A factory for creating Ook services."""

    def __init__(
        self,
        *,
        logger: BoundLogger,
        http_client: AsyncClient,
        kafka_producer: AIOKafkaProducer,
        schema_manager: PydanticSchemaManager,
        algolia_client: SearchClient,
    ) -> None:
        self._http_client = http_client
        self._logger = logger
        self._kafka_producer = kafka_producer
        self._schema_manager = schema_manager
        self._algolia_client = algolia_client

    @classmethod
    async def create(cls, *, logger: BoundLogger) -> Factory:
        """Create a Factory (for use outside a request context)."""
        return cls(
            logger=logger,
            http_client=await http_client_dependency(),
            kafka_producer=await kafka_producer_dependency(),
            schema_manager=await pydantic_schema_manager_dependency(),
            algolia_client=await algolia_client_dependency(),
        )

    def set_logger(self, logger: BoundLogger) -> None:
        """Set the logger for the factory."""
        self._logger = logger

    @property
    def kafka_producer(self) -> PydanticKafkaProducer:
        """The PydanticKafkaProducer."""
        return PydanticKafkaProducer(
            producer=self._kafka_producer, schema_manager=self._schema_manager
        )

    @property
    def schema_manager(self) -> PydanticSchemaManager:
        """The PydanticSchemaManager."""
        return self._schema_manager

    @property
    def http_client(self) -> AsyncClient:
        """The shared HTTP client."""
        return self._http_client

    def create_algolia_doc_index_service(self) -> AlgoliaDocIndexService:
        """Create an Algolia document indexing service."""
        index = self._algolia_client.init_index(
            config.algolia_document_index_name
        )

        return AlgoliaDocIndexService(
            index=index,
            logger=self._logger,
        )

    def create_github_metadata_service(self) -> GitHubMetadataService:
        """Create a GitHubMetadataService."""
        if (
            config.github_app_id is None
            or config.github_app_private_key is None
        ):
            raise RuntimeError(
                "GitHub app ID and private key must be set use the "
                "GitHubMetadataService."
            )
        gh_factory = GitHubAppClientFactory(
            id=config.github_app_id,
            key=config.github_app_private_key.get_secret_value(),
            name="lsst-sqre/ook",
            http_client=self._http_client,
        )
        return GitHubMetadataService(
            gh_factory=gh_factory,
            logger=self._logger,
        )

    def create_ltd_metadata_service(self) -> LtdMetadataService:
        """Create an LtdMetadataService."""
        return LtdMetadataService(
            http_client=self._http_client,
            logger=self._logger,
        )

    def create_classification_service(self) -> ClassificationService:
        """Create a ClassificationService."""
        return ClassificationService(
            http_client=self._http_client,
            github_service=self.create_github_metadata_service(),
            ltd_service=self.create_ltd_metadata_service(),
            kafka_producer=self.kafka_producer,
            logger=self._logger,
        )

    def create_lander_ingest_service(self) -> LtdLanderJsonLdIngestService:
        """Create a LtdLanderJsonLdIngestService."""
        return LtdLanderJsonLdIngestService(
            http_client=self._http_client,
            algolia_service=self.create_algolia_doc_index_service(),
            github_service=self.create_github_metadata_service(),
            logger=self._logger,
        )

    def create_sphinx_technote_ingest_service(
        self,
    ) -> SphinxTechnoteIngestService:
        """Create a SphinxTechnoteIngestService."""
        return SphinxTechnoteIngestService(
            http_client=self._http_client,
            algolia_service=self.create_algolia_doc_index_service(),
            github_service=self.create_github_metadata_service(),
            logger=self._logger,
        )
