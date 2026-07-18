"""Load and gate the new versioned Ootang rule-warning protocol.

The current file deliberately supports a draft protocol but rejects it for a
formal run.  It keeps the documented source gaps from being filled by legacy
V0, NGBoost, or fusion defaults before the protocol is explicitly frozen.
It is not wired into the historical pipeline stages yet; those stages remain
legacy/exploratory and cannot be relabeled as the new formal warning path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from warning.levels import WARNING_COLORS


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROTOCOL_PATH = ROOT / "config" / "ootang_warning_protocol.v1.draft.json"
VALID_STATUSES = {"draft", "frozen"}


class ProtocolValidationError(ValueError):
    """Raised when a protocol file is malformed."""


class ProtocolNotFrozenError(ProtocolValidationError):
    """Raised when a draft or incomplete protocol is requested for a formal run."""


def _validate_protocol(protocol: dict[str, Any], path: Path) -> None:
    required_keys = {
        "protocol_id",
        "protocol_version",
        "status",
        "case",
        "confirmed",
        "unresolved_items",
    }
    missing = sorted(required_keys.difference(protocol))
    if missing:
        raise ProtocolValidationError(
            f"Protocol {path} is missing required keys: {', '.join(missing)}"
        )

    if protocol["status"] not in VALID_STATUSES:
        raise ProtocolValidationError(
            f"Protocol {path} has invalid status: {protocol['status']!r}"
        )
    if not isinstance(protocol["confirmed"], dict):
        raise ProtocolValidationError(
            f"Protocol {path} must contain a confirmed object"
        )
    if protocol["confirmed"].get("warning_levels") != list(WARNING_COLORS):
        colors = ", ".join(WARNING_COLORS)
        raise ProtocolValidationError(
            f"Protocol {path} warning_levels must match the shared order: {colors}"
        )
    if not isinstance(protocol["unresolved_items"], list):
        raise ProtocolValidationError(
            f"Protocol {path} must contain an unresolved_items list"
        )

    unresolved_ids = unresolved_item_ids(protocol)
    if len(set(unresolved_ids)) != len(unresolved_ids):
        raise ProtocolValidationError(
            f"Protocol {path} contains duplicate unresolved item IDs"
        )


def unresolved_item_ids(protocol: dict[str, Any]) -> tuple[str, ...]:
    """Return auditable unresolved decision IDs in declaration order."""
    ids: list[str] = []
    for item in protocol.get("unresolved_items", []):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ProtocolValidationError(
                "Every unresolved protocol item must be an object with a string id"
            )
        ids.append(item["id"])
    return tuple(ids)


def load_protocol(path: str | Path = DEFAULT_PROTOCOL_PATH) -> dict[str, Any]:
    """Read and structurally validate a warning protocol without authorizing a run."""
    protocol_path = Path(path)
    try:
        payload = json.loads(protocol_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProtocolValidationError(
            f"Warning protocol does not exist: {protocol_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ProtocolValidationError(
            f"Warning protocol is not valid JSON: {protocol_path}"
        ) from exc

    if not isinstance(payload, dict):
        raise ProtocolValidationError(
            f"Warning protocol must be a JSON object: {protocol_path}"
        )
    _validate_protocol(payload, protocol_path)
    return payload


def protocol_content_sha256(protocol: dict[str, Any]) -> str:
    """Return a canonical content fingerprint for an already loaded protocol.

    The human-readable protocol version may remain unchanged while a draft
    decision or source reconciliation changes.  Diagnostic artifacts therefore
    use this semantic JSON fingerprint to record exactly which protocol content
    governed their generation; whitespace and JSON key order do not affect it.
    """

    if not isinstance(protocol, dict):
        raise ProtocolValidationError("Protocol fingerprint requires a JSON object")
    try:
        canonical = json.dumps(
            protocol,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolValidationError(
            "Protocol fingerprint requires JSON-serializable finite content"
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def require_frozen_protocol(
    path: str | Path = DEFAULT_PROTOCOL_PATH,
) -> dict[str, Any]:
    """Return a protocol only after all formal-run decisions are frozen."""
    protocol = load_protocol(path)
    unresolved = unresolved_item_ids(protocol)
    if protocol["status"] != "frozen" or unresolved:
        pending = ", ".join(unresolved) if unresolved else "status"
        raise ProtocolNotFrozenError(
            "Formal warning runs require a frozen protocol without unresolved items; "
            f"pending: {pending}"
        )
    return protocol
