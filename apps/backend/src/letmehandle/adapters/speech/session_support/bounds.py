"""How much a session may hold, and how long a service may take to answer, for every protocol."""

from __future__ import annotations

from typing import Final

# Speech held for a consumer before reading stops: far beyond one reply.
DEFAULT_AUDIO_CEILING_SECONDS: Final = 120.0

# Settled turns a replacement connection is told.
DEFAULT_HISTORY_TURNS: Final = 24

# How long opening a connection may take before it is a retryable failure.
DEFAULT_OPEN_TIMEOUT_SECONDS: Final = 10.0
