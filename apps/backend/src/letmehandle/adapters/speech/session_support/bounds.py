"""How much a session may hold, and how long a service may take to answer.

The same numbers for every protocol, because they are about callers and memory rather than about
any one service: a caller waits the same length of time whoever is on the other end of the socket.
"""

from __future__ import annotations

from typing import Final

# Speech held for a consumer before reading stops. Minutes, not seconds: a speaker plays in real
# time and a service sends faster than that, so a whole long reply is routinely waiting, and
# reading must go on behind it. Two minutes of wideband audio is a few megabytes.
DEFAULT_AUDIO_CEILING_SECONDS: Final = 120.0

# Enough to carry a conversation across a reconnect; a long call's opening minutes matter less
# to its next sentence than the cost of replaying them.
DEFAULT_HISTORY_TURNS: Final = 24

# How long a handshake may take before it counts as a failure worth retrying. Long enough for a
# service that is starting a model, short enough that a caller is not left listening to nothing.
DEFAULT_OPEN_TIMEOUT_SECONDS: Final = 10.0
