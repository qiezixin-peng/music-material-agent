from __future__ import annotations

from music_material_agent import workers


def test_collection_worker_repeats_durable_batches_until_sealed(monkeypatch) -> None:
    calls = []
    events = []

    def fake_batch(*args, **kwargs):
        calls.append((args, kwargs))
        observed = len(calls)
        return {
            "job_id": "job-1",
            "platform": "qq",
            "partition_status": "SEALED" if observed == 2 else "COLLECTING",
            "total": 2,
            "observed": observed,
            "remaining": 2 - observed,
            "statuses": {"FOUND_EXACT": observed},
            "processed": [{"row_id": str(observed)}],
            "sealed": observed == 2,
            "seal_result": None,
        }

    monkeypatch.setattr(workers, "run_platform_collection", fake_batch)
    result = workers.run_collection_worker(
        object(), "job-1", "qq", "qq-agent", batch_size=1, event_sink=events.append
    )

    assert len(calls) == 2
    assert result["sealed"] is True
    assert result["batches"] == 2
    assert events[-1]["remaining"] == 0


def test_material_worker_repeats_until_manifest_is_complete(monkeypatch) -> None:
    calls = []

    def fake_batch(*args, **kwargs):
        calls.append((args, kwargs))
        complete = len(calls) == 2
        return {
            "job_id": "job-1",
            "platform": "qq",
            "processed": [{"row_id": str(len(calls))}],
            "progress": {"complete": complete, "kinds": {}},
            "include_audio": False,
        }

    monkeypatch.setattr(workers, "run_material_batch", fake_batch)
    result = workers.run_material_worker(object(), "job-1", "qq", "agent", batch_size=1)

    assert len(calls) == 2
    assert result["progress"]["complete"] is True
    assert result["batches"] == 2
