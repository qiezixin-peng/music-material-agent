from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from music_material_agent import assets
from music_material_agent.assets import download_best_image, image_url_candidates, save_lyric_text


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


def test_qq_high_resolution_variant_is_tried_first() -> None:
    values = image_url_candidates("qq", "https://y.gtimg.cn/music/photo_new/T002R800x800M000abc.jpg")
    assert "R3000x3000" in values[0]


def test_best_image_can_emit_exact_dimensions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_download(url, staging_dir, output_dir, stem, kind):
        path = Path(output_dir) / f"{stem}-{kind}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (800, 400), "red").save(path)
        return {"status": "DOWNLOADED_VERIFIED", "valid": True, "path": str(path), "size_bytes": path.stat().st_size, "sha256": "old", "width": 800, "height": 400, "source_url": url, "error": None}

    monkeypatch.setattr(assets, "download_asset", fake_download)
    result = download_best_image(
        "https://example.invalid/a.png", "netease", tmp_path / "stage", tmp_path / "out",
        "row-1", "album_cover", exact_width=300, exact_height=300, resize_mode="cover",
    )
    assert (result["width"], result["height"]) == (300, 300)
    with Image.open(result["path"]) as image:
        assert image.size == (300, 300)
