### Other changes

- Adopted [`faststream_fastapi`](https://faststream-community.github.io/faststream_fastapi/) in place of FastStream's now-deprecated built-in FastAPI plugin (`faststream.kafka.fastapi.KafkaRouter`). The Kafka subscribers now hang off a plain `KafkaBroker` (`ook.kafkabroker`), and `FastStreamAPI` wraps the FastAPI app in `ook.main` to start/stop the broker around its lifespan. This also lifts the `fastapi<0.140` cap that was needed to work around the deprecated plugin.
