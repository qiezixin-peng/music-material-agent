from __future__ import annotations

import os
from dataclasses import dataclass


SERVICE = "music-material-agent:gandalf"


@dataclass(frozen=True, slots=True)
class Credentials:
    username: str
    password: str


def get_gandalf_credentials() -> Credentials | None:
    username = os.environ.get("MUSIC_MATERIAL_GANDALF_USERNAME", "").strip()
    password = os.environ.get("MUSIC_MATERIAL_GANDALF_PASSWORD", "")
    if username and password:
        return Credentials(username, password)
    try:
        import keyring

        username = keyring.get_password(SERVICE, "__username__") or ""
        password = keyring.get_password(SERVICE, username) if username else None
    except Exception:
        return None
    return Credentials(username, password) if username and password else None


def store_gandalf_credentials(username: str, password: str) -> None:
    if not username.strip() or not password:
        raise ValueError("Username and password are required")
    import keyring

    keyring.set_password(SERVICE, "__username__", username.strip())
    keyring.set_password(SERVICE, username.strip(), password)
