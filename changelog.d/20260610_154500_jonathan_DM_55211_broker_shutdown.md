### Bug fixes

- `Factory.create_standalone` now stops the Kafka broker when its context exits. Previously the broker (a module-level singleton shared with the FastAPI app's Kafka router) was left connected, leaking aiokafka producers bound to the event loop that created them. In the test suite this caused an "Event loop is closed" error during app shutdown whenever a test using the standalone `factory` fixture ran before a handler test in the same pytest invocation.
