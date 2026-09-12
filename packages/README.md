# packages

Empty on purpose.

A package is created when a second consumer exists, not in anticipation of one (D-002).
Shared code with one consumer is just code in the wrong place, and a premature package costs
a build step, a version, and a boundary to maintain.

The first expected member is a TypeScript client generated from the backend's OpenAPI schema,
which arrives when the mobile app first calls an authenticated endpoint. It will be
generated, never hand-written: a hand-maintained mirror of a schema drifts silently, and the
drift is found by a user rather than by the build.
