"""JSON encoding for calibrated TEC rows."""

from __future__ import annotations

import json
from typing import Any


def rows_to_json_bytes(rows: list[dict[str, Any]]) -> bytes:
    """Serialize contract rows to UTF-8 JSON bytes.

    Produces a JSON array where each element is a TEC observation row.
    Epoch strings are preserved as-is (ISO 8601 with Z suffix).
    """
    return json.dumps(rows, default=str, ensure_ascii=False).encode("utf-8")
