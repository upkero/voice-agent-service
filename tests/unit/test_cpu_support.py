from pathlib import Path

from src.app.voice.session import native_turn_model_supported


def test_needs_avx2_and_fma(tmp_path: Path) -> None:
    modern = tmp_path / "modern"
    modern.write_text("flags\t\t: fpu sse4_2 avx avx2 fma f16c\n")
    old = tmp_path / "old"
    old.write_text("flags\t\t: fpu sse4_2 avx f16c\n")  # Xeon E5-2680 v2

    assert native_turn_model_supported(modern)
    assert not native_turn_model_supported(old)
    assert native_turn_model_supported(tmp_path / "missing")
