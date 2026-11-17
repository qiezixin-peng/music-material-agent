from __future__ import annotations

import base64
import html
import re
from typing import Any

import httpx


_TIME_TAG = re.compile(r"\[(?:\d{1,3}:\d{2}(?:[.:]\d{1,3})?|\d{1,2}:\d{2}:\d{2}(?:[.:]\d{1,3})?)\]")
_META_TAG = re.compile(r"^\[(?:ar|ti|al|by|offset|re|ve|length):.*?\]\s*$", re.I)


def plain_lyric(timed: str) -> str:
    """Create a readable lyric without destroying the original timed LRC."""
    lines: list[str] = []
    for raw in html.unescape(timed).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _META_TAG.match(raw.strip()):
            continue
        value = _TIME_TAG.sub("", raw).strip()
        if value and value not in lines[-1:]:
            lines.append(value)
    return "\n".join(lines)


def detect_lyric_language(text: str) -> dict[str, Any]:
    """Deterministic script analysis suitable for an auditable batch pipeline."""
    value = plain_lyric(text) or text
    counts = {
        "zh": len(re.findall(r"[\u3400-\u9fff]", value)),
        "ja": len(re.findall(r"[\u3040-\u30ff]", value)),
        "ko": len(re.findall(r"[\uac00-\ud7af]", value)),
        "ru": len(re.findall(r"[\u0400-\u04ff]", value)),
        "ar": len(re.findall(r"[\u0600-\u06ff]", value)),
        "th": len(re.findall(r"[\u0e00-\u0e7f]", value)),
        "latin": len(re.findall(r"[A-Za-z]", value)),
    }
    # Japanese routinely contains Han characters; Kana is the decisive script.
    if counts["ja"]:
        counts["ja"] += counts.pop("zh")
    total = sum(counts.values())
    if not total:
        return {"lyric_language": "und", "lyric_languages": [], "lyric_language_confidence": 0.0}
    labels = {"zh": "zh-Hans", "ja": "ja", "ko": "ko", "ru": "ru", "ar": "ar", "th": "th", "latin": "en"}
    ranked = sorted(((key, count) for key, count in counts.items() if count), key=lambda item: item[1], reverse=True)
    languages = [labels[key] for key, count in ranked if count / total >= 0.12]
    primary, primary_count = ranked[0]
    return {
        "lyric_language": labels[primary],
        "lyric_languages": languages,
        "lyric_language_confidence": round(primary_count / total, 4),
        "lyric_language_method": "unicode_script_v1",
    }


def _decode_base64(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        return base64.b64decode(value).decode("utf-8", errors="replace")
    except Exception:
        return value


def _netease(identifier: str) -> tuple[str, str]:
    response = httpx.get(
        "https://music.163.com/api/song/lyric",
        params={"id": identifier, "lv": -1, "kv": -1, "tv": -1},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com/"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    return str((payload.get("lrc") or {}).get("lyric") or ""), "netease.api.song.lyric"


def _qq(identifier: str) -> tuple[str, str]:
    request = {
        "comm": {"ct": 24, "cv": 0},
        "lyric": {
            "module": "music.musichallSong.PlayLyricInfo",
            "method": "GetPlayLyricInfo",
            "param": {"songMID": identifier, "crypt": 0, "ct": 24, "cv": 0},
        },
    }
    response = httpx.post(
        "https://u.y.qq.com/cgi-bin/musicu.fcg",
        json=request,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://y.qq.com/"},
        timeout=20,
    )
    response.raise_for_status()
    data = ((response.json().get("lyric") or {}).get("data") or {})
    lyric = data.get("lyric") or data.get("lyric_content") or ""
    return _decode_base64(lyric), "qq.PlayLyricInfo.GetPlayLyricInfo"


def _kugou(identifier: str) -> tuple[str, str]:
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.kugou.com/"}
    response = httpx.get(
        "https://lyrics.kugou.com/search",
        params={"ver": 1, "man": "yes", "client": "pc", "hash": identifier.upper()},
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    candidates = response.json().get("candidates") or []
    if not candidates:
        return "", "kugou.lyrics.search"
    item = candidates[0]
    response = httpx.get(
        "https://lyrics.kugou.com/download",
        params={"ver": 1, "client": "pc", "id": item.get("id"), "accesskey": item.get("accesskey"), "fmt": "lrc", "charset": "utf8"},
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    return _decode_base64(response.json().get("content")), "kugou.lyrics.download"


def _kuwo(identifier: str) -> tuple[str, str]:
    response = httpx.get(
        "https://kuwo.cn/openapi/v1/www/lyric/getlyric",
        params={"musicId": identifier},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.kuwo.cn/"},
        timeout=20,
    )
    response.raise_for_status()
    rows = ((response.json().get("data") or {}).get("lrclist") or [])
    lines = []
    for row in rows:
        if not isinstance(row, dict) or row.get("lineLyric") is None:
            continue
        try:
            seconds = float(row.get("time") or 0)
        except (TypeError, ValueError):
            seconds = 0
        minutes, remainder = divmod(seconds, 60)
        lines.append(f"[{int(minutes):02d}:{remainder:05.2f}]{row.get('lineLyric')}")
    return "\n".join(lines), "kuwo.openapi.lyric.getlyric"


def fetch_platform_lyric(platform: str, identifier: str) -> dict[str, Any]:
    """Fetch a platform's own lyric. Third-party lyric matching is deliberately excluded."""
    fetchers = {"netease": _netease, "qq": _qq, "kugou": _kugou, "kuwo": _kuwo}
    fetcher = fetchers.get(platform)
    if not fetcher:
        return {}
    timed, source = fetcher(str(identifier).strip())
    if not timed.strip():
        return {}
    plain = plain_lyric(timed)
    return {
        "lyric": timed,
        "lyric_timed": timed,
        "lyric_plain": plain,
        "lyric_source": source,
        **detect_lyric_language(plain or timed),
    }
