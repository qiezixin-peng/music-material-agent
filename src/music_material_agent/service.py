from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .constants import (
    OBSERVATION_TERMINAL_STATUSES,
    PLATFORMS,
    REVIEW_POLICIES,
    REVIEW_VERDICTS,
)
from .database import Database
from .paths import ProjectPaths
from .traces import append_event, initialize_trace_directory
from .util import canonical_json, sha256_file, sha256_text, utc_now
from .workbooks import inspect_workbook_file, iter_workbook_rows
from .exporter import export_job_workbook
from .media import inspect_media_file


class ConflictError(RuntimeError):
    """Raised when another agent owns an active lease or a snapshot changed."""


class IncompleteError(RuntimeError):
    """Raised when a partition/review cannot be sealed yet."""


def _lease_deadline(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="microseconds")


def _is_future(value: str | None) -> bool:
    return bool(value and datetime.fromisoformat(value) > datetime.now(timezone.utc))


class MusicMaterialService:
    def __init__(self, paths: ProjectPaths | None = None):
        self.paths = paths or ProjectPaths.defaults()
        self.paths.ensure()
        self.database = Database(self.paths.database_path)

    def inspect_workbook(self, path: str, sample_rows: int = 5) -> dict[str, Any]:
        return inspect_workbook_file(path, sample_rows=sample_rows)

    def inspect_media(self, path: str, expected_duration_s: float | None = None) -> dict[str, Any]:
        return inspect_media_file(path, expected_duration_s=expected_duration_s)

    def export_job(self, job_id: str, *, filename: str | None = None, allow_partial: bool = False) -> dict[str, Any]:
        safe_name = Path(filename or f"{job_id}-采集结果.xlsx").name
        if not safe_name.casefold().endswith(".xlsx"):
            safe_name += ".xlsx"
        return export_job_workbook(
            self.database,
            job_id,
            self.paths.output_dir(job_id) / safe_name,
            allow_partial=allow_partial,
        )

    def finalize_job(
        self,
        job_id: str,
        *,
        filename: str | None = None,
        require_reviews: bool = True,
        require_materials: bool = True,
        actor: str = "agent",
    ) -> dict[str, Any]:
        """Strictly close a job and emit a hashed manifest only after required coverage."""
        with self.database.connect() as connection:
            job = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise KeyError(job_id)
            partitions = connection.execute(
                "SELECT platform,status,snapshot_hash FROM partitions WHERE job_id=? ORDER BY platform",
                (job_id,),
            ).fetchall()
            reviews = connection.execute(
                "SELECT id,source_platform,reviewer_platform,status FROM reviews WHERE job_id=? ORDER BY id",
                (job_id,),
            ).fetchall()
            observation_count = connection.execute(
                "SELECT COUNT(*) AS count FROM observations WHERE job_id=?", (job_id,)
            ).fetchone()["count"]
            material_rows = connection.execute(
                """SELECT platform,kind,status,COUNT(*) AS count FROM materials
                   WHERE job_id=? GROUP BY platform,kind,status""",
                (job_id,),
            ).fetchall()
        unsealed = [row["platform"] for row in partitions if row["status"] != "SEALED"]
        if unsealed:
            raise IncompleteError(f"Unsealed collection partitions: {unsealed}")
        if require_reviews:
            if not reviews:
                raise IncompleteError("No full-review plan exists")
            open_reviews = [row["id"] for row in reviews if row["status"] != "SEALED"]
            if open_reviews:
                raise IncompleteError(f"Unsealed reviews: {open_reviews}")
        material_summary: dict[str, dict[str, dict[str, int]]] = {}
        for row in material_rows:
            material_summary.setdefault(row["platform"], {}).setdefault(row["kind"], {})[
                row["status"]
            ] = row["count"]
        if require_materials:
            expected = observation_count
            actual = sum(
                row["count"]
                for row in material_rows
                if row["kind"] in {"cover", "avatar", "lyric"}
            )
            if actual != expected * 3:
                raise IncompleteError(
                    f"Material manifest coverage is {actual}/{expected * 3}"
                )
        exported = self.export_job(job_id, filename=filename, allow_partial=False)
        workbook_path = Path(exported["path"])
        completed_at = utc_now()
        manifest = {
            "job_id": job_id,
            "completed_at": completed_at,
            "source_workbook": job["workbook_path"],
            "source_sha256": job["workbook_sha256"],
            "result_workbook": str(workbook_path),
            "result_sha256": sha256_file(workbook_path),
            "partitions": [dict(row) for row in partitions],
            "reviews": [dict(row) for row in reviews],
            "materials": material_summary,
            "requirements": {
                "reviews": require_reviews,
                "materials": require_materials,
            },
        }
        manifest_path = Path(job["output_dir"]) / "final-manifest.json"
        temporary = manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(manifest_path)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE jobs SET status='COMPLETE',updated_at=? WHERE id=?",
                (completed_at, job_id),
            )
            self._event(
                connection,
                job_id,
                "JOB_FINALIZED",
                {
                    "result_workbook": str(workbook_path),
                    "result_sha256": manifest["result_sha256"],
                    "manifest": str(manifest_path),
                },
                actor=actor,
            )
        return {
            "job_id": job_id,
            "status": "COMPLETE",
            "workbook": str(workbook_path),
            "workbook_sha256": manifest["result_sha256"],
            "manifest": str(manifest_path),
        }

    def download_candidate(self, job_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        with self.database.connect() as connection:
            job = connection.execute("SELECT trace_dir,output_dir FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise KeyError(job_id)
        platform = str(candidate.get("platform") or "")
        if platform == "soda":
            from .adapters.go_music_dl_adapter import GoMusicDlAdapter

            adapter = GoMusicDlAdapter(Path(job["trace_dir"]) / "runtime" / "soda-helper")
            try:
                result = adapter.download(
                    candidate,
                    Path(job["trace_dir"]) / "downloads" / "staging",
                    Path(job["output_dir"]) / "audio" / platform,
                )
            finally:
                if adapter._service:
                    adapter._service.close()
        else:
            from .musicdl_download import download_musicdl_candidate

            result = download_musicdl_candidate(
                candidate,
                Path(job["trace_dir"]) / "downloads" / "staging",
                Path(job["output_dir"]) / "audio" / (platform or "unknown"),
                Path(job["trace_dir"]) / "runtime" / "musicdl-download" / platform,
            )
        with self.database.transaction(immediate=True) as connection:
            self._event(connection, job_id, "CANDIDATE_DOWNLOAD", {"platform": candidate.get("platform"), "platform_id": candidate.get("platform_id"), "result": result}, actor="agent")
        return result

    def download_asset(self, job_id: str, row_id: str, platform: str, kind: str, url: str) -> dict[str, Any]:
        if platform not in PLATFORMS:
            raise ValueError(f"Unsupported platform: {platform}")
        with self.database.connect() as connection:
            job = connection.execute("SELECT trace_dir,output_dir FROM jobs WHERE id=?", (job_id,)).fetchone()
            row = connection.execute("SELECT 1 FROM job_rows WHERE job_id=? AND row_id=?", (job_id, row_id)).fetchone()
        if not job or not row:
            raise KeyError((job_id, row_id))
        from .assets import download_asset

        result = download_asset(
            url,
            Path(job["trace_dir"]) / "downloads" / "assets",
            Path(job["output_dir"]) / "assets" / platform / kind,
            row_id,
            kind,
        )
        with self.database.transaction(immediate=True) as connection:
            self._event(connection, job_id, "ASSET_DOWNLOAD", {"row_id": row_id, "platform": platform, "kind": kind, "result": result}, actor="agent")
        return result

    def save_lyric(self, job_id: str, row_id: str, platform: str, lyric: str) -> dict[str, Any]:
        """Save inline platform lyrics with the same durable audit trail as URL assets."""
        if platform not in PLATFORMS:
            raise ValueError(f"Unsupported platform: {platform}")
        with self.database.connect() as connection:
            job = connection.execute("SELECT trace_dir,output_dir FROM jobs WHERE id=?", (job_id,)).fetchone()
            row = connection.execute("SELECT 1 FROM job_rows WHERE job_id=? AND row_id=?", (job_id, row_id)).fetchone()
        if not job or not row:
            raise KeyError((job_id, row_id))
        from .assets import save_lyric_text

        result = save_lyric_text(
            lyric,
            Path(job["trace_dir"]) / "downloads" / "assets",
            Path(job["output_dir"]) / "assets" / platform / "lyric",
            row_id,
        )
        with self.database.transaction(immediate=True) as connection:
            self._event(
                connection,
                job_id,
                "LYRIC_TEXT_SAVED",
                {
                    "row_id": row_id,
                    "platform": platform,
                    # The trace records integrity metadata, not a duplicate of the full lyric.
                    "result": result,
                },
                actor="agent",
            )
        return result

    def record_material(
        self,
        job_id: str,
        row_id: str,
        platform: str,
        kind: str,
        result: dict[str, Any],
        *,
        actor: str = "agent",
    ) -> dict[str, Any]:
        if kind not in {"cover", "avatar", "lyric", "audio"}:
            raise ValueError(f"Unsupported material kind: {kind}")
        now = utc_now()
        metadata = {
            key: value
            for key, value in result.items()
            if key not in {"path", "size_bytes", "sha256", "error"}
        }
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """INSERT INTO materials(
                       job_id,row_id,platform,kind,status,path,size_bytes,sha256,error,
                       metadata_json,created_at,updated_at,version
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)
                   ON CONFLICT(job_id,row_id,platform,kind) DO UPDATE SET
                       status=excluded.status,path=excluded.path,size_bytes=excluded.size_bytes,
                       sha256=excluded.sha256,error=excluded.error,
                       metadata_json=excluded.metadata_json,updated_at=excluded.updated_at,
                       version=materials.version+1""",
                (
                    job_id,
                    row_id,
                    platform,
                    kind,
                    str(result.get("status") or "UNKNOWN"),
                    result.get("path"),
                    result.get("size_bytes"),
                    result.get("sha256"),
                    result.get("error"),
                    canonical_json(metadata),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                job_id,
                "MATERIAL_RECORDED",
                {
                    "row_id": row_id,
                    "platform": platform,
                    "kind": kind,
                    "status": result.get("status"),
                    "path": result.get("path"),
                    "sha256": result.get("sha256"),
                },
                actor=actor,
            )
        return {"row_id": row_id, "platform": platform, "kind": kind, "status": result.get("status")}

    def get_material_rows(
        self,
        job_id: str,
        platform: str,
        *,
        kinds: list[str],
        limit: int = 10,
        retry_failed: bool = False,
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            observations = connection.execute(
                """SELECT o.row_id,o.status,o.data_json
                   FROM observations o WHERE o.job_id=? AND o.platform=?
                   ORDER BY o.row_id""",
                (job_id, platform),
            ).fetchall()
            material_rows = connection.execute(
                "SELECT row_id,kind,status FROM materials WHERE job_id=? AND platform=?",
                (job_id, platform),
            ).fetchall()
        existing = {(row["row_id"], row["kind"]): row["status"] for row in material_rows}
        retryable = {"DOWNLOAD_FAILED", "VALIDATION_FAILED"}
        result: list[dict[str, Any]] = []
        for row in observations:
            pending = [
                kind
                for kind in kinds
                if (row["row_id"], kind) not in existing
                or (retry_failed and existing[(row["row_id"], kind)] in retryable)
            ]
            if not pending:
                continue
            result.append(
                {
                    "row_id": row["row_id"],
                    "observation_status": row["status"],
                    "observation": json.loads(row["data_json"]),
                    "pending_kinds": pending,
                }
            )
            if len(result) >= max(1, min(int(limit), 100)):
                break
        return result

    def material_progress(
        self, job_id: str, platform: str, *, kinds: list[str] | None = None
    ) -> dict[str, Any]:
        selected = kinds or ["cover", "avatar", "lyric"]
        with self.database.connect() as connection:
            total = connection.execute(
                "SELECT COUNT(*) AS count FROM observations WHERE job_id=? AND platform=?",
                (job_id, platform),
            ).fetchone()["count"]
            rows = connection.execute(
                """SELECT kind,status,COUNT(*) AS count FROM materials
                   WHERE job_id=? AND platform=? GROUP BY kind,status""",
                (job_id, platform),
            ).fetchall()
        counts = {
            kind: {
                row["status"]: row["count"]
                for row in rows
                if row["kind"] == kind
            }
            for kind in selected
        }
        return {
            "total_rows": total,
            "kinds": counts,
            "complete": all(sum(counts[kind].values()) == total for kind in selected),
        }

    def _event(
        self,
        connection: Any,
        job_id: str,
        event_type: str,
        payload: dict[str, Any],
        actor: str | None = None,
    ) -> None:
        at = utc_now()
        connection.execute(
            "INSERT INTO events(job_id, at, event_type, actor, payload_json) VALUES(?,?,?,?,?)",
            (job_id, at, event_type, actor, canonical_json(payload)),
        )
        trace = connection.execute("SELECT trace_dir FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if trace:
            append_event(Path(trace["trace_dir"]), event_type, {"actor": actor, **payload})

    def create_job(
        self,
        workbook_path: str,
        *,
        sheet_name: str | None = None,
        header_row: int = 1,
        spec: dict[str, Any] | None = None,
        created_by: str = "agent",
    ) -> dict[str, Any]:
        source = Path(workbook_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        selected_sheet, headers, rows = iter_workbook_rows(source, sheet_name, header_row)
        job_id = f"job_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
        trace_dir = self.paths.trace_dir(job_id).resolve()
        output_dir = self.paths.output_dir(job_id).resolve()
        initialize_trace_directory(trace_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        workbook_hash = sha256_file(source)
        spec_value = {
            "platforms": list(PLATFORMS),
            "search_depth": "thorough",
            "gandalf_policy": "uncertain_only",
            "review_policy": "ring_full",
            **(spec or {}),
        }
        unknown = set(spec_value["platforms"]) - set(PLATFORMS)
        if unknown:
            raise ValueError(f"Unsupported platforms: {sorted(unknown)}")
        now = utc_now()
        imported_rows: list[tuple[str, int, str]] = []
        for row_number, data in rows:
            row_hash = sha256_text(canonical_json(data))[:10]
            row_id = f"r{row_number:07d}-{row_hash}"
            imported_rows.append((row_id, row_number, canonical_json(data)))
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO jobs(
                    id, created_at, updated_at, status, workbook_path, workbook_sha256,
                    sheet_name, header_row, spec_json, trace_dir, output_dir, row_count
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    now,
                    now,
                    "PLANNED",
                    str(source),
                    workbook_hash,
                    selected_sheet,
                    header_row,
                    canonical_json(spec_value),
                    str(trace_dir),
                    str(output_dir),
                    len(imported_rows),
                ),
            )
            connection.executemany(
                "INSERT INTO job_rows(job_id, row_id, row_number, data_json) VALUES(?,?,?,?)",
                ((job_id, *row) for row in imported_rows),
            )
            for platform in spec_value["platforms"]:
                connection.execute(
                    "INSERT INTO partitions(id, job_id, platform, status) VALUES(?,?,?,?)",
                    (f"{job_id}:collect:{platform}", job_id, platform, "PENDING"),
                )
            self._event(
                connection,
                job_id,
                "JOB_CREATED",
                {
                    "workbook_path": str(source),
                    "workbook_sha256": workbook_hash,
                    "sheet_name": selected_sheet,
                    "header_row": header_row,
                    "headers": headers,
                    "row_count": len(imported_rows),
                    "spec": spec_value,
                },
                actor=created_by,
            )
        shutil.copy2(source, trace_dir / "input" / source.name)
        manifest = {
            "job_id": job_id,
            "created_at": now,
            "created_by": created_by,
            "workbook_path": str(source),
            "workbook_sha256": workbook_hash,
            "sheet_name": selected_sheet,
            "header_row": header_row,
            "headers": headers,
            "row_count": len(imported_rows),
            "spec": spec_value,
            "trace_dir": str(trace_dir),
            "output_dir": str(output_dir),
        }
        (trace_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id, created_at, updated_at, status, sheet_name, row_count FROM jobs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_rows(self, job_id: str, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        with self.database.connect() as connection:
            total = connection.execute(
                "SELECT row_count FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not total:
                raise KeyError(job_id)
            rows = connection.execute(
                "SELECT row_id, row_number, data_json FROM job_rows WHERE job_id = ? ORDER BY row_number LIMIT ? OFFSET ?",
                (job_id, max(1, min(limit, 1000)), max(0, offset)),
            ).fetchall()
        return {
            "job_id": job_id,
            "total": total["row_count"],
            "offset": offset,
            "rows": [
                {"row_id": row["row_id"], "row_number": row["row_number"], "data": json.loads(row["data_json"])}
                for row in rows
            ],
        }

    def partition_context(self, job_id: str, platform: str) -> dict[str, Any]:
        if platform not in PLATFORMS:
            raise ValueError(f"Unsupported platform: {platform}")
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT j.spec_json,j.trace_dir,j.output_dir,p.status AS partition_status,
                          p.claimed_by,p.lease_until
                   FROM jobs j JOIN partitions p ON p.job_id=j.id
                   WHERE j.id=? AND p.platform=?""",
                (job_id, platform),
            ).fetchone()
        if not row:
            raise KeyError((job_id, platform))
        result = dict(row)
        result["spec"] = json.loads(result.pop("spec_json"))
        return result

    def get_partition_rows(
        self,
        job_id: str,
        platform: str,
        *,
        limit: int = 10,
        retry_technical: bool = False,
    ) -> list[dict[str, Any]]:
        retry = ("SEARCH_UNAVAILABLE", "AUTH_REQUIRED", "RATE_LIMITED", "SEARCH_PARTIAL")
        retry_sql = " OR o.status IN (?,?,?,?)" if retry_technical else ""
        parameters: list[Any] = [platform, job_id]
        if retry_technical:
            parameters.extend(retry)
        parameters.append(max(1, min(int(limit), 100)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT r.row_id,r.row_number,r.data_json,o.status AS observation_status
                    FROM job_rows r
                    LEFT JOIN observations o
                      ON o.job_id=r.job_id AND o.row_id=r.row_id AND o.platform=?
                    WHERE r.job_id=? AND (o.id IS NULL{retry_sql})
                    ORDER BY r.row_number LIMIT ?""",
                parameters,
            ).fetchall()
        return [
            {
                "row_id": row["row_id"],
                "row_number": row["row_number"],
                "data": json.loads(row["data_json"]),
                "observation_status": row["observation_status"],
            }
            for row in rows
        ]

    def partition_progress(self, job_id: str, platform: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            partition = connection.execute(
                "SELECT status FROM partitions WHERE job_id=? AND platform=?",
                (job_id, platform),
            ).fetchone()
            if not partition:
                raise KeyError((job_id, platform))
            total = connection.execute(
                "SELECT row_count FROM jobs WHERE id=?", (job_id,)
            ).fetchone()["row_count"]
            counts = connection.execute(
                """SELECT status,COUNT(*) AS count FROM observations
                   WHERE job_id=? AND platform=? GROUP BY status""",
                (job_id, platform),
            ).fetchall()
        by_status = {row["status"]: row["count"] for row in counts}
        observed = sum(by_status.values())
        return {
            "job_id": job_id,
            "platform": platform,
            "partition_status": partition["status"],
            "total": total,
            "observed": observed,
            "remaining": total - observed,
            "statuses": by_status,
        }

    def claim_partition(
        self,
        job_id: str,
        platform: str,
        agent_id: str,
        lease_minutes: int = 30,
    ) -> dict[str, Any]:
        if platform not in PLATFORMS:
            raise ValueError(f"Unsupported platform: {platform}")
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM partitions WHERE job_id = ? AND platform = ?",
                (job_id, platform),
            ).fetchone()
            if not row:
                raise KeyError(f"Partition not found: {job_id}/{platform}")
            if row["status"] == "SEALED":
                raise ConflictError("Partition is sealed")
            if row["claimed_by"] not in (None, agent_id) and _is_future(row["lease_until"]):
                raise ConflictError(
                    f"Partition is leased by {row['claimed_by']} until {row['lease_until']}"
                )
            deadline = _lease_deadline(lease_minutes)
            connection.execute(
                "UPDATE partitions SET status='COLLECTING', claimed_by=?, lease_until=? WHERE id=?",
                (agent_id, deadline, row["id"]),
            )
            connection.execute(
                "UPDATE jobs SET status='COLLECTING', updated_at=? WHERE id=?",
                (utc_now(), job_id),
            )
            self._event(
                connection,
                job_id,
                "PARTITION_CLAIMED",
                {"platform": platform, "lease_until": deadline},
                actor=agent_id,
            )
            return {"partition_id": row["id"], "platform": platform, "lease_until": deadline}

    def heartbeat_partition(
        self, job_id: str, platform: str, agent_id: str, lease_minutes: int = 30
    ) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT id, claimed_by, status FROM partitions WHERE job_id=? AND platform=?",
                (job_id, platform),
            ).fetchone()
            if not row or row["claimed_by"] != agent_id or row["status"] != "COLLECTING":
                raise ConflictError("Agent does not own an active collecting partition")
            deadline = _lease_deadline(lease_minutes)
            connection.execute("UPDATE partitions SET lease_until=? WHERE id=?", (deadline, row["id"]))
            return {"partition_id": row["id"], "lease_until": deadline}

    def record_observation(
        self,
        job_id: str,
        row_id: str,
        platform: str,
        agent_id: str,
        status: str,
        data: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in OBSERVATION_TERMINAL_STATUSES:
            raise ValueError(f"Invalid observation status: {status}")
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            partition = connection.execute(
                "SELECT claimed_by, lease_until, status FROM partitions WHERE job_id=? AND platform=?",
                (job_id, platform),
            ).fetchone()
            if not partition:
                raise KeyError(f"Partition not found: {platform}")
            if (
                partition["claimed_by"] != agent_id
                or partition["status"] != "COLLECTING"
                or not _is_future(partition["lease_until"])
            ):
                raise ConflictError("Agent does not own an active partition lease")
            exists = connection.execute(
                "SELECT 1 FROM job_rows WHERE job_id=? AND row_id=?", (job_id, row_id)
            ).fetchone()
            if not exists:
                raise KeyError(f"Row not found: {row_id}")
            connection.execute(
                """
                INSERT INTO observations(
                    job_id,row_id,platform,collector,status,data_json,evidence_json,created_at,updated_at,version
                ) VALUES(?,?,?,?,?,?,?,?,?,1)
                ON CONFLICT(job_id,row_id,platform) DO UPDATE SET
                    collector=excluded.collector,
                    status=excluded.status,
                    data_json=excluded.data_json,
                    evidence_json=excluded.evidence_json,
                    updated_at=excluded.updated_at,
                    version=observations.version+1
                """,
                (
                    job_id,
                    row_id,
                    platform,
                    agent_id,
                    status,
                    canonical_json(data or {}),
                    canonical_json(evidence or {}),
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                job_id,
                "OBSERVATION_RECORDED",
                {"row_id": row_id, "platform": platform, "status": status},
                actor=agent_id,
            )
        return {"job_id": job_id, "row_id": row_id, "platform": platform, "status": status}

    def seal_partition(self, job_id: str, platform: str, agent_id: str) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            partition = connection.execute(
                "SELECT * FROM partitions WHERE job_id=? AND platform=?", (job_id, platform)
            ).fetchone()
            if not partition or partition["claimed_by"] != agent_id:
                raise ConflictError("Agent does not own this partition")
            row_count = connection.execute(
                "SELECT row_count FROM jobs WHERE id=?", (job_id,)
            ).fetchone()["row_count"]
            observations = connection.execute(
                "SELECT row_id,status,data_json,evidence_json,version FROM observations WHERE job_id=? AND platform=? ORDER BY row_id",
                (job_id, platform),
            ).fetchall()
            if len(observations) != row_count:
                raise IncompleteError(f"Partition coverage is {len(observations)}/{row_count}")
            snapshot = sha256_text(canonical_json([dict(row) for row in observations]))
            sealed_at = utc_now()
            connection.execute(
                """
                UPDATE partitions SET status='SEALED', lease_until=NULL, snapshot_hash=?,
                    sealed_at=?, version=version+1 WHERE id=?
                """,
                (snapshot, sealed_at, partition["id"]),
            )
            self._event(
                connection,
                job_id,
                "PARTITION_SEALED",
                {"platform": platform, "coverage": row_count, "snapshot_hash": snapshot},
                actor=agent_id,
            )
        return {"platform": platform, "coverage": row_count, "snapshot_hash": snapshot}

    def create_review_plan(
        self, job_id: str, policy: str = "ring_full", actor: str = "agent"
    ) -> dict[str, Any]:
        if policy not in REVIEW_POLICIES:
            raise ValueError(f"Invalid review policy: {policy}")
        with self.database.transaction(immediate=True) as connection:
            partitions = connection.execute(
                "SELECT platform,status,snapshot_hash FROM partitions WHERE job_id=? ORDER BY platform",
                (job_id,),
            ).fetchall()
            if not partitions:
                raise KeyError(job_id)
            not_sealed = [row["platform"] for row in partitions if row["status"] != "SEALED"]
            if not_sealed:
                raise IncompleteError(f"Unsealed partitions: {not_sealed}")
            snapshot_by_platform = {row["platform"]: row["snapshot_hash"] for row in partitions}
            enabled = [platform for platform in PLATFORMS if platform in snapshot_by_platform]
            pairs: list[tuple[str, str]] = []
            if policy == "ring_full":
                for index, source in enumerate(enabled):
                    reviewer = enabled[(index + 1) % len(enabled)]
                    if reviewer != source:
                        pairs.append((source, reviewer))
            else:
                pairs.extend((source, reviewer) for source in enabled for reviewer in enabled if source != reviewer)
            created: list[dict[str, str]] = []
            for source, reviewer in pairs:
                review_id = f"{job_id}:review:{source}:by:{reviewer}"
                connection.execute(
                    """
                    INSERT OR IGNORE INTO reviews(
                        id,job_id,source_platform,reviewer_platform,status,snapshot_hash
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (review_id, job_id, source, reviewer, "PENDING", snapshot_by_platform[source]),
                )
                created.append({"review_id": review_id, "source_platform": source, "reviewer_platform": reviewer})
            connection.execute(
                "UPDATE jobs SET status='REVIEW_PENDING', updated_at=? WHERE id=?", (utc_now(), job_id)
            )
            self._event(
                connection,
                job_id,
                "REVIEW_PLAN_CREATED",
                {"policy": policy, "review_count": len(created)},
                actor=actor,
            )
        return {"job_id": job_id, "policy": policy, "reviews": created}

    def claim_review(
        self, review_id: str, agent_id: str, lease_minutes: int = 30
    ) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            review = connection.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
            if not review:
                raise KeyError(review_id)
            if review["status"] == "SEALED":
                raise ConflictError("Review is sealed")
            if review["claimed_by"] not in (None, agent_id) and _is_future(review["lease_until"]):
                raise ConflictError(f"Review is leased by {review['claimed_by']}")
            if agent_id == review["source_platform"]:
                raise ConflictError("Collector platform cannot review itself")
            deadline = _lease_deadline(lease_minutes)
            connection.execute(
                "UPDATE reviews SET status='REVIEWING', claimed_by=?, lease_until=? WHERE id=?",
                (agent_id, deadline, review_id),
            )
            self._event(
                connection,
                review["job_id"],
                "REVIEW_CLAIMED",
                {"review_id": review_id, "lease_until": deadline},
                actor=agent_id,
            )
            return {"review_id": review_id, "lease_until": deadline}

    def heartbeat_review(
        self, review_id: str, agent_id: str, lease_minutes: int = 30
    ) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            review = connection.execute(
                "SELECT claimed_by,status FROM reviews WHERE id=?", (review_id,)
            ).fetchone()
            if not review or review["claimed_by"] != agent_id or review["status"] != "REVIEWING":
                raise ConflictError("Agent does not own an active review")
            deadline = _lease_deadline(lease_minutes)
            connection.execute(
                "UPDATE reviews SET lease_until=? WHERE id=?", (deadline, review_id)
            )
        return {"review_id": review_id, "lease_until": deadline}

    def review_context(self, review_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT r.*,j.spec_json,j.trace_dir,j.output_dir
                   FROM reviews r JOIN jobs j ON j.id=r.job_id WHERE r.id=?""",
                (review_id,),
            ).fetchone()
        if not row:
            raise KeyError(review_id)
        result = dict(row)
        result["spec"] = json.loads(result.pop("spec_json"))
        return result

    def get_review_rows(self, review_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            review = connection.execute(
                "SELECT job_id,source_platform FROM reviews WHERE id=?", (review_id,)
            ).fetchone()
            if not review:
                raise KeyError(review_id)
            rows = connection.execute(
                """SELECT jr.row_id,jr.row_number,jr.data_json,o.status AS source_status,
                          o.data_json AS source_data_json
                   FROM job_rows jr
                   JOIN observations o
                     ON o.job_id=jr.job_id AND o.row_id=jr.row_id AND o.platform=?
                   LEFT JOIN review_verdicts rv
                     ON rv.review_id=? AND rv.row_id=jr.row_id
                   WHERE jr.job_id=? AND rv.id IS NULL
                   ORDER BY jr.row_number LIMIT ?""",
                (
                    review["source_platform"],
                    review_id,
                    review["job_id"],
                    max(1, min(int(limit), 100)),
                ),
            ).fetchall()
        return [
            {
                "row_id": row["row_id"],
                "row_number": row["row_number"],
                "data": json.loads(row["data_json"]),
                "source_status": row["source_status"],
                "source_observation": json.loads(row["source_data_json"]),
            }
            for row in rows
        ]

    def submit_review_verdict(
        self,
        review_id: str,
        row_id: str,
        agent_id: str,
        verdict: str,
        patch: dict[str, Any] | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if verdict not in REVIEW_VERDICTS:
            raise ValueError(f"Invalid review verdict: {verdict}")
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            review = connection.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
            if not review:
                raise KeyError(review_id)
            if (
                review["claimed_by"] != agent_id
                or review["status"] != "REVIEWING"
                or not _is_future(review["lease_until"])
            ):
                raise ConflictError("Agent does not own an active review lease")
            current_snapshot = connection.execute(
                "SELECT snapshot_hash FROM partitions WHERE job_id=? AND platform=?",
                (review["job_id"], review["source_platform"]),
            ).fetchone()["snapshot_hash"]
            if current_snapshot != review["snapshot_hash"]:
                raise ConflictError("Source partition changed after review plan creation")
            exists = connection.execute(
                "SELECT 1 FROM job_rows WHERE job_id=? AND row_id=?",
                (review["job_id"], row_id),
            ).fetchone()
            if not exists:
                raise KeyError(row_id)
            connection.execute(
                """
                INSERT INTO review_verdicts(
                    review_id,row_id,verdict,patch_json,evidence_json,reviewer,created_at,updated_at,version
                ) VALUES(?,?,?,?,?,?,?,?,1)
                ON CONFLICT(review_id,row_id) DO UPDATE SET
                    verdict=excluded.verdict,
                    patch_json=excluded.patch_json,
                    evidence_json=excluded.evidence_json,
                    reviewer=excluded.reviewer,
                    updated_at=excluded.updated_at,
                    version=review_verdicts.version+1
                """,
                (
                    review_id,
                    row_id,
                    verdict,
                    canonical_json(patch or {}),
                    canonical_json(evidence or {}),
                    agent_id,
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                review["job_id"],
                "REVIEW_VERDICT_RECORDED",
                {"review_id": review_id, "row_id": row_id, "verdict": verdict},
                actor=agent_id,
            )
        return {"review_id": review_id, "row_id": row_id, "verdict": verdict}

    def review_coverage(self, review_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            review = connection.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
            if not review:
                raise KeyError(review_id)
            total = connection.execute(
                "SELECT row_count FROM jobs WHERE id=?", (review["job_id"],)
            ).fetchone()["row_count"]
            reviewed = connection.execute(
                "SELECT COUNT(*) AS count FROM review_verdicts WHERE review_id=?", (review_id,)
            ).fetchone()["count"]
            counts = connection.execute(
                "SELECT verdict,COUNT(*) AS count FROM review_verdicts WHERE review_id=? GROUP BY verdict",
                (review_id,),
            ).fetchall()
        return {
            "review_id": review_id,
            "total": total,
            "reviewed": reviewed,
            "remaining": total - reviewed,
            "coverage_percent": round((reviewed / total * 100) if total else 100.0, 4),
            "verdicts": {row["verdict"]: row["count"] for row in counts},
        }

    def seal_review(self, review_id: str, agent_id: str) -> dict[str, Any]:
        coverage = self.review_coverage(review_id)
        if coverage["remaining"]:
            raise IncompleteError(
                f"Review coverage is {coverage['reviewed']}/{coverage['total']}"
            )
        with self.database.transaction(immediate=True) as connection:
            review = connection.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
            if not review or review["claimed_by"] != agent_id:
                raise ConflictError("Agent does not own this review")
            sealed_at = utc_now()
            connection.execute(
                "UPDATE reviews SET status='SEALED', lease_until=NULL, sealed_at=? WHERE id=?",
                (sealed_at, review_id),
            )
            self._event(
                connection,
                review["job_id"],
                "REVIEW_SEALED",
                {"review_id": review_id, "coverage": coverage},
                actor=agent_id,
            )
        return {**coverage, "status": "SEALED", "sealed_at": sealed_at}

    def job_status(self, job_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            job = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise KeyError(job_id)
            partitions = connection.execute(
                """
                SELECT p.platform,p.status,p.claimed_by,p.lease_until,p.version,p.snapshot_hash,
                    COUNT(o.id) AS observed
                FROM partitions p
                LEFT JOIN observations o ON o.job_id=p.job_id AND o.platform=p.platform
                WHERE p.job_id=? GROUP BY p.id ORDER BY p.platform
                """,
                (job_id,),
            ).fetchall()
            reviews = connection.execute(
                """
                SELECT r.id,r.source_platform,r.reviewer_platform,r.status,r.claimed_by,
                    COUNT(v.id) AS reviewed
                FROM reviews r LEFT JOIN review_verdicts v ON v.review_id=r.id
                WHERE r.job_id=? GROUP BY r.id ORDER BY r.id
                """,
                (job_id,),
            ).fetchall()
        result = dict(job)
        result["spec"] = json.loads(result.pop("spec_json"))
        result["partitions"] = [dict(row) for row in partitions]
        result["reviews"] = [dict(row) for row in reviews]
        return result
