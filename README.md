[![CI](https://github.com/lsst-sqre/ook/actions/workflows/ci.yaml/badge.svg)](https://github.com/lsst-sqre/ook/actions/workflows/ci.yaml)

# Ook

Ook is the librarian indexing the Vera C. Rubin Observatory's technical documentation.
Ook's primary deployment is on the [Roundtable](https://phalanx.lsst.io/environments/roundtable-prod/index.html) platform.
It retrieves triggers from documentation builds, GitHub Actions, and other sources through both Kafka (SQuaRE Events) and an HTTP API.
Ook indexes that content and saves records to an Algolia database, which is used to power the [Rubin Observatory technical documentation website, www.lsst.io](https://www.lsst.io).

## Links

| Phalanx Env. Argo CD                                                                                    | API URL                               | API Docs                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [roundtable-prod](https://roundtable.lsst.cloud/argo-cd/applications/argocd/ook?view=tree&resource=)    | https://roundtable.lsst.cloud/ook     | [Swagger](https://roundtable.lsst.cloud/ook/docs) · [Redoc](https://roundtable.lsst.cloud/ook/redoc) · [AsyncAPI](https://roundtable.lsst.cloud/ook/asyncapi)             |
| [roundtable-dev](https://roundtable-dev.lsst.cloud/argo-cd/applications/argocd/ook?view=tree&resource=) | https://roundtable-dev.lsst.cloud/ook | [Swagger](https://roundtable-dev.lsst.cloud/ook/docs) · [Redoc](https://roundtable-dev.lsst.cloud/ook/redoc) · [AsyncAPI](https://roundtable-dev.lsst.cloud/ook/asyncapi) |

## Documentation and related reading

- [ook.lsst.io](https://ook.lsst.io) — Documentation site.
- [SQR-086](https://sqr-086.lsst.io) — Deep linking to data documentation with IVOA DataLink
- [SQR-090](https://sqr-090.lsst.io) — System for collecting user feedback in Rubin documentation

## Development

Ook is developed with SQuaRE's [Safir](https://safir.lsst.io) framework for FastAPI apps.

### Environment set up

```bash
nox -s init-dev
```

Or if in an existing Python virtual environment:

```bash
nox -s init
```

### Testing and linking nox sessions

```bash
nox -s test
nox -s typing
nox -s lint
```

### Running the app

```bash
nox -s run
```

See [square.env](./square.env) for using 1Password credentials for local development.

### Database migrations

See [alembic/README.md](alembic/README.md) for instructions on how to create and apply database migrations.

### Preparing release notes

Create a changelog fragment in the `changelog.d` directory.

```bash
nox -s scriv-create
```

To collect fragments into the `CHANGELOG.md` file for a release, run:

```bash
nox -s scriv-collect -- X.Y.Z
```
