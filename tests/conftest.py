from __future__ import annotations

from pathlib import Path

import pytest

from music_material_agent.paths import ProjectPaths
from music_material_agent.service import MusicMaterialService


@pytest.fixture
def service(tmp_path: Path) -> MusicMaterialService:
    paths = ProjectPaths(
        project_root=tmp_path / "project",
        data_root=tmp_path / "project" / "data",
        temp_root=tmp_path / "workspace" / "临时文件",
        output_root=tmp_path / "workspace" / "outputs",
    )
    return MusicMaterialService(paths)
