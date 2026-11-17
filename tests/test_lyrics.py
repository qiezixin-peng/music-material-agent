from music_material_agent.lyrics import detect_lyric_language, plain_lyric


def test_plain_lyric_preserves_text_and_removes_lrc_metadata() -> None:
    value = "[ar:歌手]\n[00:01.20]你好 world\n[00:04.00]第二行"
    assert plain_lyric(value) == "你好 world\n第二行"


def test_language_analysis_reports_mixed_scripts() -> None:
    result = detect_lyric_language("你好世界 hello world this is music")
    assert result["lyric_language"] in {"zh-Hans", "en"}
    assert {"zh-Hans", "en"}.issubset(set(result["lyric_languages"]))
