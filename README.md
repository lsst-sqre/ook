[![CI](https://github.com/lsst-sqre/ook/actions/workflows/ci.yaml/badge.svg)](https://github.com/lsst-sqre/ook/actions/workflows/ci.yaml)

# Ook

Ook is the librarian indexing the Vera C. Rubin Observatory's documentation.
Ook's primary deployment is on the [Roundtable](https://phalanx.lsst.io/environments/roundtable-prod/index.html) platform.
It retrieves triggers from documentation builds, GitHub Actions, and other sources through both Kafka (SQuaRE Events) and an HTTP API.
Ook indexes that content and saves records to an Algolia database, which is used to power the [Rubin Observatory technical documentation website, www.lsst.io](https://www.lsst.io).

Ook is developed with SQuaRE's [Safir](https://safir.lsst.io) framework for FastAPI apps.
