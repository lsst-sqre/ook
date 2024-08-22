"""Factory for creating Ook services."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from typing import Self

from algoliasearch.search_client import SearchClient
from faststream.kafka import KafkaBroker
from faststream.kafka.asyncapi import Publisher
from httpx import AsyncClient
from safir.github import GitHubAppClientFactory
from structlog.stdlib import BoundLogger

from .config import config
from .dependencies.algoliasearch import algolia_client_dependency
from .kafkarouter import kafka_router
from .services.algoliaaudit import AlgoliaAuditService
from .services.algoliadocindex import AlgoliaDocIndexService
from .services.classification import ClassificationService
from .services.githubmetadata import GitHubMetadataService
from .services.landerjsonldingest import LtdLanderJsonLdIngestService
from .services.ltdmetadataservice import LtdMetadataService
from .services.sphinxtechnoteingest import SphinxTechnoteIngestService
from .services.technoteingest import TechnoteIngestService


@dataclass(kw_only=True, frozen=True, slots=True)
class ProcessContext:
    """Holds singletons in the context of a Ook process, which might be a
    API server or a CLI command.
    """

    http_client: AsyncClient
    """Shared HTTP client."""

    kafka_broker: KafkaBroker
    """The aiokafka broker provided through the FastStream Kafka router."""

    kafka_ingest_publisher: Publisher

    algolia_client: SearchClient
    """Algolia client."""

    @classmethod
    async def create(
        cls, kafka_broker: KafkaBroker | None = None
    ) -> ProcessContext:
        """Create a ProcessContext."""
        # Not using Safir's http_client_dependency because I found that in
        # standalone Factory setting the http_client wasn't opened, for some
        # reason. Ook doesn't use any http_client beyond this one from
        # ProcessContext.
        http_client = AsyncClient()

        # Use the provided broker (typically for CLI contexts)
        broker = kafka_broker if kafka_broker else kafka_router.broker

        algolia_client = await algolia_client_dependency()

        return cls(
            http_client=http_client,
            kafka_broker=broker,
            kafka_ingest_publisher=broker.publisher(
                config.ingest_kafka_topic, description="Ook ingest requests"
            ),
            algolia_client=algolia_client,
        )

    async def aclose(self) -> None:
        """Clean up a process context.

        Called during shutdown, or before recreating the process context using
        a different configuration.
        """
        await self.algolia_client.close_async()
        await self.http_client.aclose()


class Factory:
    """A factory for creating Ook services."""

    def __init__(
        self,
        *,
        logger: BoundLogger,
        process_context: ProcessContext,
    ) -> None:
        self._process_context = process_context
        self._logger = logger

    @classmethod
    async def create(
        cls, *, logger: BoundLogger, kafka_broker: KafkaBroker | None = None
    ) -> Self:
        """Create a Factory (for use outside a request context)."""
        context = await ProcessContext.create(kafka_broker=kafka_broker)
        return cls(
            logger=logger,
            process_context=context,
        )

    @classmethod
    @asynccontextmanager
    async def create_standalone(
        cls, *, logger: BoundLogger, kafka_broker: KafkaBroker | None = None
    ) -> AsyncIterator[Self]:
        """Create a standalone factory, outside the FastAPI process, as a
        context manager.

        Use this for creating a factory in CLI commands.
        """
        factory = await cls.create(logger=logger, kafka_broker=kafka_broker)
        async with aclosing(factory):
            # Manually connect the broker after the publishers are created
            # so that the producer can be added to each publisher.
            await factory._process_context.kafka_broker.connect()  # noqa: SLF001
            yield factory

    async def aclose(self) -> None:
        """Shut down the factory and the internal process context."""
        await self._process_context.aclose()

    def set_logger(self, logger: BoundLogger) -> None:
        """Set the logger for the factory."""
        self._logger = logger

    @property
    def http_client(self) -> AsyncClient:
        """The shared HTTP client."""
        return self._process_context.http_client

    def create_algolia_doc_index_service(self) -> AlgoliaDocIndexService:
        """Create an Algolia document indexing service."""
        index = self._process_context.algolia_client.init_index(
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
            http_client=self.http_client,
        )
        return GitHubMetadataService(
            gh_factory=gh_factory,
            logger=self._logger,
        )

    def create_ltd_metadata_service(self) -> LtdMetadataService:
        """Create an LtdMetadataService."""
        return LtdMetadataService(
            http_client=self.http_client,
            logger=self._logger,
        )

    def create_classification_service(self) -> ClassificationService:
        """Create a ClassificationService."""
        publisher = self._process_context.kafka_ingest_publisher
        return ClassificationService(
            http_client=self.http_client,
            github_service=self.create_github_metadata_service(),
            ltd_service=self.create_ltd_metadata_service(),
            kafka_ingest_publisher=publisher,
            logger=self._logger,
        )

    def create_lander_ingest_service(self) -> LtdLanderJsonLdIngestService:
        """Create a LtdLanderJsonLdIngestService."""
        return LtdLanderJsonLdIngestService(
            http_client=self.http_client,
            algolia_service=self.create_algolia_doc_index_service(),
            github_service=self.create_github_metadata_service(),
            logger=self._logger,
        )

    def create_sphinx_technote_ingest_service(
        self,
    ) -> SphinxTechnoteIngestService:
        """Create a SphinxTechnoteIngestService."""
        return SphinxTechnoteIngestService(
            http_client=self.http_client,
            algolia_service=self.create_algolia_doc_index_service(),
            github_service=self.create_github_metadata_service(),
            logger=self._logger,
        )

    def create_technote_ingest_service(self) -> TechnoteIngestService:
        """Create a TechnoteIngestService."""
        return TechnoteIngestService(
            http_client=self.http_client,
            algolia_service=self.create_algolia_doc_index_service(),
            logger=self._logger,
        )

    def create_algolia_audit_service(self) -> AlgoliaAuditService:
        """Create an AlgoliaAuditService."""
        return AlgoliaAuditService(
            http_client=self.http_client,
            algolia_search_client=self._process_context.algolia_client,
            logger=self._logger,
            classification_service=self.create_classification_service(),
        )
