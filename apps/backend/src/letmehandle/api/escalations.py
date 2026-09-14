"""The signed-in user's devices and escalation contexts, over HTTP (D-012, D-016)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from letmehandle.api.body_limit import JSON_BODY_LIMIT_BYTES, limited_body_route
from letmehandle.api.dependencies import CurrentUser, Devices, EscalationContexts, Preferences
from letmehandle.api.errors import UNPROCESSABLE, ApiError
from letmehandle.api.escalation_schemas import EscalationContextResponse, RegisterDeviceRequest
from letmehandle.api.schemas import DevicePayload
from letmehandle.application.escalation.notification import notification_for
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.escalation_context import MAX_CALL_ID_LENGTH, EscalationContext
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.ports.notification import DeviceToken

router = APIRouter(
    prefix="/v1", tags=["escalation"], route_class=limited_body_route(JSON_BODY_LIMIT_BYTES)
)


@router.put(
    "/devices",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Register this device for escalation notifications",
)
async def register_device(
    body: RegisterDeviceRequest, user: CurrentUser, devices: Devices
) -> Response:
    """Record this device's push token for the signed-in user, idempotently."""
    token = DeviceToken(body.platform, body.token)
    replacing = (
        None if body.previous_token is None else DeviceToken(body.platform, body.previous_token)
    )
    await devices.register(user.id, token, replacing=replacing)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/devices/unregister",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop sending escalation notifications to this device",
)
async def unregister_device(body: DevicePayload, user: CurrentUser, devices: Devices) -> Response:
    """Forget this device for the signed-in user; a POST so the token stays out of access logs."""
    await devices.remove(user.id, DeviceToken(body.platform, body.token))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/escalations/{call_id}",
    response_model=EscalationContextResponse,
    summary="The context of an escalation",
)
async def read_escalation(
    call_id: Annotated[str, Path(min_length=1, max_length=MAX_CALL_ID_LENGTH)],
    user: CurrentUser,
    contexts: EscalationContexts,
    preferences: Preferences,
) -> EscalationContextResponse:
    """What the user was, or would have been, told about this escalation."""
    try:
        identifier = CallId(call_id)
    except InvariantError as error:
        raise ApiError(UNPROCESSABLE, "invalid_call_id", "That is not a call id.") from error

    context = await contexts.get(user.id, identifier)
    if context is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND, "escalation_not_found", "There is no such escalation."
        )
    return _context_response(context, locale=(await preferences.get(user.id)).locale)


def _context_response(context: EscalationContext, *, locale: str) -> EscalationContextResponse:
    """The response, built from the same notification a push would have carried."""
    shown = notification_for(context, locale=locale)
    return EscalationContextResponse(
        call_id=context.call_id.value,
        status=context.status,
        reason=context.reason,
        title=shown.title,
        caller_label=shown.caller_label,
        body=shown.body,
        caller=context.caller_label,
        established=context.established,
        needed=context.needed,
        raised_at=context.raised_at,
        ended_at=context.ended_at,
        delivery=context.delivery,
    )
