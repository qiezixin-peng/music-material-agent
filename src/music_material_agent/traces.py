from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .util import json_default, utc_now


TRACE_SUBDIRECTORIES: tuple[str, ...] = (
    "input",
    "logs",
    "search-traces",
    "raw-responses/soda",
    "raw-responses/netease",
    "raw-responses/qq",
    "raw-responses/kugou",
    "raw-responses/kuwo",
    "raw-responses/gandalf",
    "screenshots",
    "checkpoints",
    "candidates",
    "downloads-partial",
    "validation",
    "reports",
    "collectors",
    "reviewers",
    "disputes",
)


def initialize_trace_directory(trace_dir: Path) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    for relative in TRACE_SUBDIRECTORIES:
        (trace_dir / relative).mkdir(parents=True, exist_ok=True)


def append_event(trace_dir: Path, event_type: str, payload: dict[str, Any]) -> None:
    record = {
        "at": utc_now(),
        "event_type": event_type,
        "payload": payload,
    }
    encoded = (
        json.dumps(record, ensure_ascii=False, default=json_default, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    path = trace_dir / "logs" / "events.jsonl"
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, encoded)
    finally:
        os.close(descriptor)
