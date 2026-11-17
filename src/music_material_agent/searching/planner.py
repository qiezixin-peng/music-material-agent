from __future__ import annotations

import re
import unicodedata
from typing import Iterable


BRACKET_PATTERN = re.compile(r"[（(【\[]([^）)】\]]+)[）)】\]]")
PUNCTUATION_PATTERN = re.compile(r"[\s·•—–_\-:：,，;；、/\\|]+")
ARTIST_SPLIT_PATTERN = re.compile(r"\s*(?:&|＆|,|，|;|；|、|/|\\)\s*")
VERSION_PATTERN = re.compile(
    r"(?:"
    r"DJ(?:版|弹鼓版|抖音版|沈念版|R7版)?|"
    r"R\s*&?\s*B版|架子鼓版|和声伴奏|弹鼓版|鼓版|"
    r"加速版|降速版|加快版|慢速版|氛围版|伴奏版?|纯音乐|纯享FM|"
    r"Live|Remix|Mix|Sped\s*Up|Slowed(?:\s*&\s*Reverb)?|Speed\s*Up|"
    r"Fast|Slow|Instrumental|Acoustic|Demo|Radio\s*Edit|"
    r"\d+(?:\.\d+)?\s*[xX]"
    r")",
    re.IGNORECASE,
)


def normalize_text(value: str | None, *, compact: bool = False) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    text = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    text = PUNCTUATION_PATTERN.sub(" " if not compact else "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace(" ", "") if compact else text


def split_artists(artists: str | Iterable[str] | None) -> list[str]:
    if artists is None:
        return []
    values = [artists] if isinstance(artists, str) else list(artists)
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in ARTIST_SPLIT_PATTERN.split(str(value)):
            cleaned = part.strip()
            key = normalize_text(cleaned, compact=True)
            if cleaned and key and key not in seen:
                seen.add(key)
                result.append(cleaned)
    return result


def version_tokens(title: str | None) -> list[str]:
    return list(dict.fromkeys(match.group(0).strip() for match in VERSION_PATTERN.finditer(title or "")))


def base_title(title: str) -> str:
    without_brackets = BRACKET_PATTERN.sub(" ", title)
    without_versions = VERSION_PATTERN.sub(" ", without_brackets)
    return re.sub(r"\s+", " ", without_versions).strip(" -_—–·,，;；:：")


def expand_queries(
    title: str,
    artists: str | Iterable[str] | None = None,
    *,
    album: str | None = None,
    max_queries: int = 16,
) -> list[dict[str, str]]:
    """Generate recall-oriented queries while preserving why each query exists."""
    if not title or not str(title).strip():
        raise ValueError("title is required")
    raw_title = unicodedata.normalize("NFKC", str(title)).strip()
    artist_values = split_artists(artists)
    bracket_parts = [part.strip() for part in BRACKET_PATTERN.findall(raw_title) if part.strip()]
    normalized = normalize_text(raw_title)
    base = base_title(raw_title)
    versions = version_tokens(raw_title)
    candidates: list[tuple[str, str]] = [
        ("raw_title", raw_title),
        ("normalized_title", normalized),
    ]
    if base and normalize_text(base, compact=True) != normalize_text(raw_title, compact=True):
        candidates.append(("base_title", base))
    for part in bracket_parts:
        if not VERSION_PATTERN.fullmatch(part):
            candidates.append(("bracket_alias", part))
    for artist in artist_values:
        candidates.append(("title_artist", f"{raw_title} {artist}"))
        if base:
            candidates.append(("base_title_artist", f"{base} {artist}"))
        candidates.append(("artist_title", f"{artist} {base or raw_title}"))
    if versions and base:
        for version in versions:
            candidates.append(("base_title_version", f"{base} {version}"))
    if album and normalize_text(album, compact=True) not in {
        normalize_text(raw_title, compact=True),
        normalize_text(base, compact=True),
    }:
        candidates.append(("title_album", f"{raw_title} {album}"))

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for reason, query in candidates:
        query = re.sub(r"\s+", " ", query).strip()
        key = normalize_text(query, compact=True)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append({"query": query, "reason": reason})
        if len(result) >= max_queries:
            break
    return result
