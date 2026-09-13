# Testing

Everything below runs with no paid account. Real providers are replaced by simulations that speak
their protocols over real sockets, a scripted model, and recording push providers, and each real
adapter is held to the same contract suite as its simulation.

## The suites

| Suite | Where | Proves | Needs |
| --- | --- | --- | --- |
| Unit | `apps/backend/tests/unit/` | domain rules, policies, application services, adapters in isolation | nothing |
| Contract | `apps/backend/tests/contracts/` | every implementation of a port behaves as the port promises | nothing |
| Integration | `apps/backend/tests/integration/` | the API, storage, migrations, speech over websockets, transports over simulated providers | PostgreSQL |
| End to end | `apps/backend/tests/e2e/` | whole calls through the running application: routing, the assistant, escalation, screening, provider faults, restarts | PostgreSQL |
| Evaluation | `apps/backend/tests/evaluation/` | how often a real model judges and summarises scenarios correctly | a model endpoint; not in CI |
| Mobile | `apps/mobile/src/**/__tests__` | hooks, state, services and the API client | nothing |

## Running them

From the repository root:

```bash
make up          # PostgreSQL for the suites that need it, and the backend
make test        # backend and mobile suites
make e2e         # only the end-to-end scenarios
make coverage    # the suites again, failing below the floors
make verify      # everything CI runs
```

If something else already has port 5432, move the database, and the tests with it:

```bash
POSTGRES_PORT=5433 make up verify
```

A single backend test, from `apps/backend`:

```bash
uv run pytest tests/unit/test_settings.py -q
uv run pytest tests/e2e/test_escalation.py -q
```

The tests that need a database read `TEST_DATABASE_URL`, which the Makefile sets from
`POSTGRES_PORT`. Running `uv run pytest` directly, set it yourself:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://letmehandle:letmehandle@127.0.0.1:5432/letmehandle uv run pytest
```

Without a database those tests skip with a message saying so, except in CI, where they fail: a
suite that silently skips its database tests reports green while proving nothing. Each run works in
a schema, or for the end-to-end scenarios a database, named after its process, so two runs against
one server do not collide.

## Coverage gates

From D-020:

| Area | Floor | Enforced by |
| --- | --- | --- |
| Backend | 98% of lines and branches | `make coverage` and CI, `--cov-fail-under=98` |
| Backend domain and call orchestration | 100% of branches, by intent | review; the current figure is in each phase's verification report |
| Mobile hooks, state, services, API clients, business logic | 90% | `coverageThreshold` in `apps/mobile/jest.config.js` |

A floor, not a goal. A test that exists only to move the number is a defect.

## What the simulations are

| Real thing | Stands in for it in tests |
| --- | --- |
| Programmable telephony | `tests/support/simulated_twilio.py`: answers the REST API, signs callbacks as the provider does, opens real media websockets, and can duplicate, reorder and drop callbacks |
| Realtime speech service | `tests/support/simulated_realtime_service.py`, `simulated_elevenlabs_service.py`: in-process websocket servers speaking each protocol; `scripted_gpt_live_connection.py`: the GPT-Live protocol in memory |
| The model | `tests/support/scripted_model.py`: a script of what the model says, run through the real agent loop and tools |
| Push services | `tests/support/push_services.py`, and the recording providers in `tests/contracts/fakes.py` |
| Time | `FixedClock`, which moves only when told to |

Audio in tests is generated in memory. No audio file is ever committed (D-013).

## Checks that are not tests

Also run by `make verify`:

| Check | Command | Fails when |
| --- | --- | --- |
| Disclosure and secrets | `make audit` | anything private is in the tree or in any commit (D-021) |
| Lint and format | `make lint` | ruff, ESLint or Prettier disagree |
| Types and boundaries | `make typecheck` | mypy strict, TypeScript or an import-linter contract fails |
| Generated API types | `make api-types-check` | the mobile client's types no longer match the backend |
| Configuration reference | `make config-reference-check` | the reference, `.env.example` or the compose file no longer match the settings |
| Documentation links | `make docs-check` | a relative link in a Markdown file points at a missing file or heading |

Not in `make verify`, because a dependency update changes it without anything being wrong:
`make licences` rewrites [`licences.md`](licences.md) and exits non-zero when a shipped dependency's
licence is not known to be compatible with MIT. Run it when dependencies change.

Two documents are checked by tests, because a drawing that drifts is worse than none: the capability
matrix in `docs/architecture/call-transport.md` and the state diagram in
`docs/architecture/call-flow.md`.

## Evaluating a real model

Not part of any gate, because a real model's answers vary. With the `LLM_*` variables set:

```bash
cd apps/backend
uv run python ../../scripts/agent_evaluation.py --minimum 0.9
uv run python ../../scripts/summary_evaluation.py
```

Each prints a pass rate per class of call. Record it when a prompt or the model changes.

## By hand

What automation cannot reach — a real call on a real number, a real push on a real device, native
screening on a real Android phone — is scripted in
[`docs/testing/manual-verification.md`](../testing/manual-verification.md).
