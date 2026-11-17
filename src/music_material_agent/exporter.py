from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook

from .constants import PLATFORMS
from .database import Database


FIELDS = (
    "status",
    "platform_id",
    "title_raw",
    "artists_raw",
    "album_raw",
    "song_url",
    "song_short_share_url",
    "album_url",
    "album_short_share_url",
    "artist_ids",
    "artist_homepage_url",
    "artist_homepage_urls",
    "artist_short_share_urls",
    "release_date",
    "release_company_raw",
    "release_company_kind",
    "release_label",
    "rights_holder_raw",
    "cover_url",
    "song_cover_url",
    "song_cover_path",
    "song_cover_status",
    "album_cover_url",
    "album_cover_path",
    "album_cover_status",
    "artist_avatar_url",
    "artist_avatar_path",
    "avatar_status",
    "lyric",
    "lyric_timed",
    "lyric_timed_path",
    "lyric_timed_status",
    "lyric_plain",
    "lyric_plain_path",
    "lyric_plain_status",
    "lyric_source",
    "lyric_language",
    "lyric_languages",
    "lyric_language_confidence",
    "lyric_language_method",
    "audio_path",
    "audio_status",
)


def _cell(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value)
    return text if len(text) <= 32767 else text[:32740] + "…[TRUNCATED]"


def export_job_workbook(database: Database, job_id: str, output_path: str | Path, *, allow_partial: bool = False) -> dict[str, Any]:
    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with database.connect() as connection:
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not job:
            raise KeyError(job_id)
        spec = json.loads(job["spec_json"] or "{}")
        partitions = connection.execute("SELECT platform,status,snapshot_hash FROM partitions WHERE job_id=? ORDER BY platform", (job_id,)).fetchall()
        incomplete = [row["platform"] for row in partitions if row["status"] != "SEALED"]
        if incomplete and not allow_partial:
            raise RuntimeError(f"Partitions are not sealed: {', '.join(incomplete)}")
        rows = connection.execute("SELECT row_id,row_number,data_json FROM job_rows WHERE job_id=? ORDER BY row_number", (job_id,)).fetchall()
        observations = connection.execute("SELECT row_id,platform,status,data_json,evidence_json FROM observations WHERE job_id=?", (job_id,)).fetchall()
        reviews = connection.execute(
            """SELECT rv.row_id,r.source_platform,r.reviewer_platform,rv.verdict,rv.patch_json
               FROM review_verdicts rv JOIN reviews r ON r.id=rv.review_id WHERE r.job_id=?""",
            (job_id,),
        ).fetchall()
        materials = connection.execute(
            "SELECT row_id,platform,kind,status,path FROM materials WHERE job_id=?",
            (job_id,),
        ).fetchall()

    obs_map = {(row["row_id"], row["platform"]): row for row in observations}
    material_map = {
        (row["row_id"], row["platform"], row["kind"]): row for row in materials
    }
    review_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in reviews:
        review_map.setdefault((row["row_id"], row["source_platform"]), []).append(
            {"reviewer_platform": row["reviewer_platform"], "verdict": row["verdict"], "patch": json.loads(row["patch_json"])}
        )
    source_headers: list[str] = []
    decoded_rows: list[tuple[Any, dict[str, Any]]] = []
    for row in rows:
        data = json.loads(row["data_json"])
        decoded_rows.append((row, data))
        for key in data:
            if key not in source_headers:
                source_headers.append(key)

    requested_fields = [
        str(field).strip() for field in (spec.get("requested_fields") or [])
        if isinstance(field, str) and str(field).strip() and str(field).strip() not in FIELDS
    ]
    platform_fields = (*FIELDS, *dict.fromkeys(requested_fields))
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("采集结果")
    headers = ["row_id", "source_row", *source_headers]
    for platform in PLATFORMS:
        headers.extend(f"{platform}.{field}" for field in platform_fields)
        headers.extend((f"{platform}.evidence", f"{platform}.reviews"))
    sheet.append(headers)
    for row, source_data in decoded_rows:
        values: list[Any] = [row["row_id"], row["row_number"], *(_cell(source_data.get(key)) for key in source_headers)]
        for platform in PLATFORMS:
            observation = obs_map.get((row["row_id"], platform))
            data = json.loads(observation["data_json"]) if observation else {}
            status = observation["status"] if observation else "MISSING"
            for kind, path_field, status_field in (
                ("song_cover", "song_cover_path", "song_cover_status"),
                ("album_cover", "album_cover_path", "album_cover_status"),
                ("avatar", "artist_avatar_path", "avatar_status"),
                ("lyric_timed", "lyric_timed_path", "lyric_timed_status"),
                ("lyric_plain", "lyric_plain_path", "lyric_plain_status"),
                ("audio", "audio_path", "audio_status"),
            ):
                material = material_map.get((row["row_id"], platform, kind))
                if material:
                    data[path_field] = material["path"]
                    data[status_field] = material["status"]
            values.extend(_cell(status if field == "status" else data.get(field)) for field in platform_fields)
            values.append(_cell(json.loads(observation["evidence_json"])) if observation else None)
            values.append(_cell(review_map.get((row["row_id"], platform), [])))
        sheet.append(values)

    audit = workbook.create_sheet("审计摘要")
    audit.append(["job_id", job_id])
    audit.append(["source_workbook", job["workbook_path"]])
    audit.append(["source_sha256", job["workbook_sha256"]])
    audit.append(["row_count", job["row_count"]])
    audit.append([])
    audit.append(["platform", "status", "snapshot_hash"])
    for row in partitions:
        audit.append([row["platform"], row["status"], row["snapshot_hash"]])

    temporary = target.with_suffix(target.suffix + ".tmp")
    workbook.save(temporary)
    temporary.replace(target)
    return {"job_id": job_id, "path": str(target), "rows": len(rows), "partial": bool(incomplete), "incomplete_platforms": incomplete}
