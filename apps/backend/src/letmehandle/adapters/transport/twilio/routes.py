"""The HTTP and websocket routes the provider calls, owned by the adapter that understands them.

Here rather than under `api/`, so that no provider's name or callback shape enters the HTTP
layer the product's own clients use. Bootstrap mounts this router once for each line this
transport carries, and not at all when none is configured; otherwise these paths do not exist.

Every route does the same four things in the same order: prove the request came from the
provider, refuse it if it did not, recognise a redelivery, and hand what it says to the
transport — which changes state and returns at once. Nothing here waits on the network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi import APIRouter, Request, Response, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from letmehandle.adapters.http.body import BodyTooLargeError, read_limited_body
from letmehandle.adapters.transport.twilio import twiml
from letmehandle.adapters.transport.twilio.callbacks import (
    CALL_PARAMETER,
    LEG_PARAMETER,
    CallbackMalformedError,
    Parameters,
    read_conference_update,
    read_incoming_call,
    read_leg_progress,
)
from letmehandle.adapters.transport.twilio.signature import (
    SIGNATURE_HEADER,
    SignatureRejectedError,
)
from letmehandle.adapters.transport.twilio.stream import MediaSocketClosedError
from letmehandle.adapters.transport.twilio.transport import (
    ASSISTANT_PATH,
    CALLER_PATH,
    CONFERENCE_PATH,
    INCOMING_PATH,
    LEG_PATH,
    MEDIA_PATH,
)
from letmehandle.observability import catalogue
from letmehandle.observability.logging import get_logger
from letmehandle.observability.tracing import CALL_ID

if TYPE_CHECKING:
    from collections.abc import Callable

    from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.tracing import Tracer

logger = get_logger(__name__)

IDEMPOTENCY_HEADER: Final = "I-Twilio-Idempotency-Token"

# The largest callback body read. A callback is a few dozen short form fields; anything near this
# is not one, and its signature is only checked once it has been read.
TELEPHONY_BODY_LIMIT_BYTES: Final = 64 * 1024

# The close code for a handshake refused on policy, which Starlette turns into a 403 before the
# socket is ever accepted.
_POLICY_VIOLATION: Final = 1008

# A callback this provider delivered again, by the route it arrived on.
CALLBACK_REPEATED: Final = catalogue.count(
    "telephony.callback_repeated", stage={"incoming", "assistant", "caller", "conference", "leg"}
)

_FORBIDDEN: Final = 403
_TOO_LARGE: Final = 413
_UNPROCESSABLE: Final = 422
_NO_CONTENT: Final = 204


class _RefusedError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def build_router(
    transport: TwilioCallTransport, *, tracer: Tracer, metrics: MetricsRecorder
) -> APIRouter:
    """The provider's routes, bound to one transport instance.

    Each callback is a span of its own: the first of a call's life, and the way a provider's delay
    in calling back is told apart from this service's in answering.

    The routes sit under the transport's path prefix. A request is proved over the whole path it
    arrived on, prefix included, because that is the URL the provider was told and signed.
    """
    router = APIRouter(prefix=transport.path_prefix, include_in_schema=False)

    async def verified(request: Request) -> tuple[Parameters, dict[str, str]]:
        """The form parameters and our own query parameters, once the request is proved."""
        raw_query = request.scope.get("query_string", b"").decode("latin-1")
        try:
            body = await read_limited_body(request, TELEPHONY_BODY_LIMIT_BYTES)
        except BodyTooLargeError:
            logger.warning("telephony.webhook.rejected", path=request.url.path, reason="size")
            raise _RefusedError(_TOO_LARGE) from None
        try:
            pairs = transport.verifier.verify_form(
                path=request.url.path,
                raw_query=raw_query,
                body=body,
                signature=request.headers.get(SIGNATURE_HEADER),
            )
        except SignatureRejectedError as error:
            logger.warning("telephony.webhook.rejected", path=request.url.path, reason=str(error))
            raise _RefusedError(_FORBIDDEN) from None
        params = Parameters(pairs)
        if params.get("AccountSid") != transport.account_id:
            logger.warning("telephony.webhook.rejected", path=request.url.path, reason="account")
            raise _RefusedError(_FORBIDDEN)
        query = dict(request.query_params.items())
        return params, query

    async def handle(
        request: Request, stage: str, respond: _Respond, *, skip_repeats: bool
    ) -> Response:
        with tracer.span("telephony.callback", stage=stage) as span:
            try:
                params, query = await verified(request)
                # The call a callback is about: ours, named in the query or the form, and otherwise
                # the provider's call the callback arrived for, which on arrival is the same call.
                call = (
                    query.get(CALL_PARAMETER) or params.get(CALL_PARAMETER) or params.get("CallSid")
                )
                if call is not None:
                    span.set_attribute(CALL_ID, call)
                repeat = transport.is_repeat_delivery(request.headers.get(IDEMPOTENCY_HEADER))
                if repeat:
                    metrics.increment(CALLBACK_REPEATED, {"stage": stage})
                if repeat and skip_repeats:
                    span.set_attribute("outcome", "repeated")
                    return Response(status_code=_NO_CONTENT)
                return respond(params, query)
            except _RefusedError as refused:
                span.set_attribute("outcome", "refused")
                return Response(status_code=refused.status)
            except CallbackMalformedError as error:
                logger.warning(
                    "telephony.webhook.malformed", path=request.url.path, reason=str(error)
                )
                span.set_attribute("outcome", "malformed")
                return Response(status_code=_UNPROCESSABLE)

    @router.post(INCOMING_PATH)
    async def incoming(request: Request) -> Response:
        def respond(params: Parameters, _query: dict[str, str]) -> Response:
            return _twiml(transport.incoming_call(read_incoming_call(params)))

        # A redelivered call still needs its instructions: the first answer may never have reached
        # the provider. Answering is idempotent by the call's own identifier.
        return await handle(request, "incoming", respond, skip_repeats=False)

    @router.post(ASSISTANT_PATH)
    async def assistant(request: Request) -> Response:
        def respond(params: Parameters, query: dict[str, str]) -> Response:
            identifiers = {
                name: value
                for name in (CALL_PARAMETER, LEG_PARAMETER)
                if (value := params.get(name) or query.get(name))
            }
            return _twiml(transport.assistant_joining(identifiers, params.require("CallSid")))

        return await handle(request, "assistant", respond, skip_repeats=False)

    @router.post(CALLER_PATH)
    async def caller(request: Request) -> Response:
        def respond(params: Parameters, query: dict[str, str]) -> Response:
            return _twiml(
                transport.caller_left(query.get(CALL_PARAMETER), params.require("CallSid"))
            )

        # Instructions are owed on every delivery; ending a call twice is inert.
        return await handle(request, "caller", respond, skip_repeats=False)

    @router.post(CONFERENCE_PATH)
    async def conference(request: Request) -> Response:
        def respond(params: Parameters, query: dict[str, str]) -> Response:
            transport.conference_updated(query.get(CALL_PARAMETER), read_conference_update(params))
            return Response(status_code=_NO_CONTENT)

        return await handle(request, "conference", respond, skip_repeats=True)

    @router.post(LEG_PATH)
    async def leg(request: Request) -> Response:
        def respond(params: Parameters, query: dict[str, str]) -> Response:
            transport.leg_progressed(
                query.get(CALL_PARAMETER), query.get(LEG_PARAMETER), read_leg_progress(params)
            )
            return Response(status_code=_NO_CONTENT)

        return await handle(request, "leg", respond, skip_repeats=True)

    @router.websocket(MEDIA_PATH)
    async def media(websocket: WebSocket) -> None:
        try:
            transport.verifier.verify_handshake(
                path=websocket.url.path,
                raw_query=websocket.scope.get("query_string", b"").decode("latin-1"),
                signature=websocket.headers.get(SIGNATURE_HEADER),
            )
        except SignatureRejectedError as error:
            logger.warning("telephony.media.rejected", reason=str(error))
            await websocket.close(code=_POLICY_VIOLATION)
            return
        await websocket.accept()
        await transport.media_connected(StarletteMediaSocket(websocket))

    return router


# What a route does with a request once it is proved.
type _Respond = Callable[[Parameters, dict[str, str]], Response]


class StarletteMediaSocket:
    """An accepted Starlette websocket, as the transport's media socket."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def receive(self) -> str | None:
        if self._websocket.application_state is not WebSocketState.CONNECTED:
            return None
        try:
            message = await self._websocket.receive()
        except RuntimeError:
            # Starlette refuses to receive once a close has been sent from this side, which is
            # the socket having closed as far as a reader is concerned.
            return None
        if message["type"] == "websocket.disconnect":
            return None
        text = message.get("text")
        # A binary frame is not this protocol; the parser refuses it as it refuses any non-JSON.
        return text if isinstance(text, str) else ""

    async def send(self, text: str) -> None:
        if self._websocket.application_state is not WebSocketState.CONNECTED:
            raise MediaSocketClosedError
        try:
            await self._websocket.send_text(text)
        except (WebSocketDisconnect, RuntimeError, OSError):
            raise MediaSocketClosedError from None

    async def close(self) -> None:
        if (
            self._websocket.application_state is WebSocketState.DISCONNECTED
            or self._websocket.client_state is WebSocketState.DISCONNECTED
        ):
            return
        try:
            await self._websocket.close()
        except (RuntimeError, OSError):
            # The other end went away between the state check and the close frame. Closing a
            # socket that has already closed is the outcome that was asked for.
            return


def _twiml(document: str) -> Response:
    return Response(content=document, media_type=twiml.CONTENT_TYPE)
