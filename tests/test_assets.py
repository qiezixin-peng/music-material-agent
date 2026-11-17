from __future__ import annotations

from pathlib import Path

import pytest

from music_material_agent.assets import save_lyric_text


def test_save_lrc_text_atomically_with_hash(tmp_path: Path) -> None:
    result = save_lyric_text(
        "[00:00.00]海阔天空\n[00:01.00]今天我",
        tmp_path / "stage",
        tmp_path / "output",
        "row-1",
    )
    target = Path(result["path"])
    assert result["status"] == "SAVED_VERIFIED"
    assert result["format"] == "lrc"
    assert target.read_text(encoding="utf-8").startswith("[00:00.00]")
    assert not (tmp_path / "stage" / "row-1-lyric.part").exists()


@pytest.mark.parametrize("lyric", ["", "<html>not a lyric</html>", "<!doctype html>"])
def test_save_lyric_rejects_empty_or_html(lyric: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        save_lyric_text(lyric, tmp_path / "stage", tmp_path / "output", "row-1")
