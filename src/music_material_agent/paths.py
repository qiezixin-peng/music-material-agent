from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


WORKSPACE_ROOT = Path(r"D:\三喜")


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    project_root: Path
    data_root: Path
    temp_root: Path
    output_root: Path

    @classmethod
    def defaults(cls) -> "ProjectPaths":
        project_root = Path(
            os.environ.get(
                "MUSIC_MATERIAL_PROJECT_ROOT",
                str(WORKSPACE_ROOT / "Agent项目" / "音乐物料Agent"),
            )
        ).resolve()
        return cls(
            project_root=project_root,
            data_root=Path(
                os.environ.get("MUSIC_MATERIAL_DATA_ROOT", str(project_root / "data"))
            ).resolve(),
            temp_root=Path(
                os.environ.get("MUSIC_MATERIAL_TEMP_ROOT", str(WORKSPACE_ROOT / "临时文件"))
            ).resolve(),
            output_root=Path(
                os.environ.get(
                    "MUSIC_MATERIAL_OUTPUT_ROOT",
                    str(WORKSPACE_ROOT / "歌曲物料下载" / "Agent任务"),
                )
            ).resolve(),
        )

    @property
    def database_path(self) -> Path:
        return self.data_root / "jobs.sqlite"

    def ensure(self) -> None:
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.output_root.mkdir(parents=True, exist_ok=True)

    def trace_dir(self, job_id: str) -> Path:
        return self.temp_root / f"音乐物料任务-{job_id}"

    def output_dir(self, job_id: str) -> Path:
        return self.output_root / job_id
