from __future__ import annotations

import re


PLATFORM_ID_PATTERNS = {
    "soda": re.compile(r"^[0-9]+$"),
    "netease": re.compile(r"^[0-9]+$"),
    "qq": re.compile(r"^[A-Za-z0-9]{14}$"),
    "kugou": re.compile(r"^[0-9A-Fa-f]{32}$"),
    "kuwo": re.compile(r"^[0-9]+$"),
}


def valid_platform_id(platform: str, value: object) -> bool:
    """Reject cross-platform IDs before ranking or official recheck."""
    pattern = PLATFORM_ID_PATTERNS.get(platform)
    return bool(pattern and pattern.fullmatch(str(value or "").strip()))
