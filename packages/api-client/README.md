# @letmehandle/api-client

TypeScript types for the backend's HTTP API, generated from its OpenAPI schema.

Generated, never hand-written. A hand-maintained mirror of a schema drifts silently, and the
drift is found by a user rather than by the build. `make verify` regenerates both the schema and
these types and fails on any difference, so a backend change that alters the wire format makes
the mobile app stop compiling — which is the moment to find out.

```bash
make api-types      # regenerate after changing the backend's request or response models
```

The package exports the generated types and the small amount of hand-written glue that names
them usefully. It contains no networking: how requests are made, retried and authenticated is
the mobile app's business, and putting it here would make this package depend on a runtime.
