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

    def download_candidate(self, job_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
        with self.database.connect() as connection:
            job = connection.execute("SELECT trace_dir,output_dir FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise KeyError(job_id)
        from .adapters.go_music_dl_adapter import GoMusicDlAdapter

        adapter = GoMusicDlAdapter(Path(job["trace_dir"]) / "runtime" / "go-music-dl")
        try:
            result = adapter.download(
                candidate,
                Path(job["trace_dir"]) / "downloads" / "staging",
                Path(job["output_dir"]) / "audio" / str(candidate.get("platform") or "unknown"),
            )
        finally:
            if adapter._service:
                adapter._service.close()
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
