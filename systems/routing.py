"""Small, deterministic parser for model-to-supervisor routing signals."""
from __future__ import annotations

import re

# Keep this list role-based rather than model-name based. The supervisor/config
# maps roles to whichever qualified model currently fills that role.
KNOWN_ROUTE_LABELS = {
    "SYSTEM_CONTROL",
    "HEAVY_CODING",
    "ENGINEERING",
    "FAST_VISION",
    "DEEP_TECHNICAL_VISION",
}

_ROUTE_RE = re.compile(r"^\s*ROUTE\s*:\s*([A-Z0-9 _-]+)\s*$", re.IGNORECASE)


def normalize_route_label(value: str) -> str:
    value = re.sub(r"[\s-]+", "_", str(value).strip().upper())
    value = re.sub(r"_+", "_", value)
    return value


def parse_route_signal(text: str) -> str | None:
    """Return a role label only when the whole response is a routing signal.

    Requiring the entire response to be a single ROUTE line prevents ordinary
    prose that happens to mention a role from accidentally triggering a model
    switch.
    """
    match = _ROUTE_RE.fullmatch(str(text or ""))
    if not match:
        return None
    label = normalize_route_label(match.group(1))
    return label if label in KNOWN_ROUTE_LABELS else None
