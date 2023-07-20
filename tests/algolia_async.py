"""Demo of using the Algolia API asynchronously.

See https://github.com/algolia/algoliasearch-client-python/issues/559
"""

import asyncio
from os import getenv

from algoliasearch.search_client import SearchClient


async def main() -> None:
    """Run the demo."""
    await with_context_getting_iterator()
    await with_context()
    await manual_context()
    await sync()


async def with_context_getting_iterator() -> None:
    async with SearchClient.create(
        getenv("ALGOLIA_APP_ID"), getenv("ALGOLIA_API_KEY")
    ) as client:
        index = client.init_index("ook_documents_test")
        iterator = index.browse_objects_async({"attributesToRetrieve": []})
        async for hit in iterator:
            print(hit)


async def with_context() -> None:
    async with SearchClient.create(
        getenv("ALGOLIA_APP_ID"), getenv("ALGOLIA_API_KEY")
    ) as client:
        index = client.init_index("ook_documents_test")
        async for hit in index.browse_objects_async(
            {"attributesToRetrieve": []}
        ):
            print(hit)


async def manual_context() -> None:
    base_url = "https://sqr-000.lsst.io"
    filters = f"baseUrl:{escape_facet_value(base_url)}"
    settings = {
        "filters": filters,
        "attributesToRetrieve": ["baseUrl", "surrogateKey"],
        "attributesToHighlight": [],
    }

    client = await SearchClient.create(
        getenv("ALGOLIA_APP_ID"), getenv("ALGOLIA_API_KEY")
    ).__aenter__()
    index = client.init_index("ook_documents_test")
    hits_count = 0
    async for hit in index.browse_objects_async(settings):
        hits_count += 1
        print(hit)
    print(f"Total hits: {hits_count}")
    await client.close_async()


async def sync() -> None:
    client = await SearchClient.create(
        getenv("ALGOLIA_APP_ID"), getenv("ALGOLIA_API_KEY")
    ).__aenter__()
    index = client.init_index("ook_documents_test")
    hits_count = 0
    for hit in index.browse_objects({"attributesToRetrieve": []}):
        hits_count += 1
        print(hit)
    print(f"Total hits: {hits_count}")
    await client.close_async()


def escape_facet_value(value: str) -> str:
    """Escape a facet value for use in Algolia filters."""
    value = value.replace('"', r"\"").replace("'", r"\'")
    return f'"{value}"'


if __name__ == "__main__":
    asyncio.run(main())
