### Bug fixes

- Adapt the SDM links domain endpoint to FastAPI 0.137's nested route tree by using `url_path_for` instead of iterating `app.routes`. FastAPI 0.137 nests routes added via `include_router` inside intermediate router objects, so the previous flat iteration over `app.routes` no longer found the SDM link routes and the `/ook/links/domains/sdm` endpoint raised a `KeyError`.
