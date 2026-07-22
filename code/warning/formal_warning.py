"""Single gated entry for a future formal Ootang warning executor.

The current four-indicator protocol is intentionally draft.  This module does
not create a timeline or reinterpret any historical output; it makes the
required protocol check the first operation of any future formal executor.
"""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

from warning.protocol import DEFAULT_PROTOCOL_PATH, require_frozen_protocol


class FormalWarningExecutorUnavailableError(RuntimeError):
    """Raised after a frozen protocol passes but no formal executor exists."""


def run_formal_warning(
    *,
    protocol_path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> NoReturn:
    """Reject all formal execution until a reviewed executor is implemented.

    Calling this entry with the repository's draft protocol raises
    :class:`warning.protocol.ProtocolNotFrozenError` before any formal
    executor could read inputs or write outputs.  A frozen protocol alone is insufficient:
    the current repository has no reviewed four-indicator formal executor, so
    the function then raises :class:`FormalWarningExecutorUnavailableError`.
    This deliberately prevents a caller from injecting a legacy V0/fusion
    callable through the formal path.
    """

    protocol = require_frozen_protocol(protocol_path)
    raise FormalWarningExecutorUnavailableError(
        "No reviewed formal four-indicator warning executor is registered for "
        f"{protocol['protocol_id']} {protocol['protocol_version']}."
    )
