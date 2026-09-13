"""What a deployment carrying calls does when nothing is there to orchestrate them.

Each test here reproduces a defect and is expected to fail until it is fixed.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from letmehandle.config.settings import ConfigurationError
from letmehandle.main import create_app
from tests.unit.adapters.transport.test_twilio_routes import ARRIVAL, signed_post
from tests.unit.test_call_transport_bootstrap import telephony_settings


@pytest.mark.xfail(
    strict=True,
    reason="a streaming deployment without a database starts, mounts the provider's routes and "
    "answers callers into a conference that no orchestrator will ever hold",
)
async def test_a_streaming_deployment_with_no_storage_never_answers_a_caller_into_silence() -> None:
    app = create_app(telephony_settings())
    try:
        async with app.router.lifespan_context(app):
            assert app.state.orchestrator is None
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as client:
                response = await signed_post(client, "/telephony/voice/incoming", ARRIVAL)
    except ConfigurationError:
        # Refusing to start is one right answer: the misconfiguration never reaches a caller.
        return
    # The other is not putting the caller anywhere nothing will ever act on the call.
    assert "<Conference" not in response.text
