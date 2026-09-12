# letmehandle-backend

The LetMeHandle backend: the domain, the call orchestration, and the provider adapters.

Run it from the repository root — `make up` for the whole local stack, `make verify` for every
gate. See [`docs/development/setup.md`](../../docs/development/setup.md).

## Layout

```
src/letmehandle/
  domain/         the product. Imports no framework, no driver, no vendor SDK.
  application/    use cases, composed from domain objects and ports.
  adapters/       implementations of the ports. Every vendor SDK lives here.
  api/            HTTP and webhooks.
  config/         the only place the environment is read.
  observability/  logging, and later tracing and metrics.
```

The dependency arrow points one way, and import-linter fails the build if it stops doing so.
See [`docs/architecture/overview.md`](../../docs/architecture/overview.md).
