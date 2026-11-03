import pytest

from controlled_chaos.services.base import AppError

WAV = b"RIFF" + b"\x00" * 200


def test_bundled_tracks_exist_as_files():
    from controlled_chaos.services.music_service import BUNDLED
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "assets" / "music"
    for key, _, _ in BUNDLED:
        assert (root / f"{key}.wav").stat().st_size > 100_000


def test_resolve_bundled_and_unknown(file_app):
    assert file_app.music.resolve("bundled:rain") == ("asset", "music/rain.wav")
    assert file_app.music.resolve("bundled:../../etc/passwd") is None
    assert file_app.music.resolve("file:missing.mp3") is None
    assert file_app.music.resolve("") is None


def test_add_user_file_is_copied_privately_and_resolves(file_app, tmp_path):
    name = file_app.music.add("My Song.MP3", WAV)
    assert name == "My Song.mp3"
    assert (tmp_path / "music" / name).read_bytes() == WAV
    assert file_app.music.resolve(f"file:{name}") == ("bytes", WAV)
    assert ("file:My Song.mp3", "My Song") in file_app.music.options()


def test_duplicate_names_get_a_suffix_not_overwritten(file_app):
    a = file_app.music.add("a.wav", WAV)
    b = file_app.music.add("a.wav", WAV + b"x")
    assert a == "a.wav" and b == "a (2).wav"
    assert len(file_app.music.user_tracks()) == 2


@pytest.mark.parametrize("bad", ["notes.txt", "x.exe", "noext", "evil.mp3.sh"])
def test_rejects_unsupported_types(file_app, bad):
    with pytest.raises(AppError):
        file_app.music.add(bad, WAV)


def test_path_traversal_in_name_is_flattened(file_app, tmp_path):
    name = file_app.music.add("../../outside/escape.wav", WAV)
    assert name == "escape.wav"
    assert (tmp_path / "music" / "escape.wav").exists() and not (tmp_path.parent / "outside").exists()


def test_size_and_empty_limits(file_app, monkeypatch):
    from controlled_chaos.services import music_service
    monkeypatch.setattr(music_service, "MAX_BYTES", 1000)
    with pytest.raises(AppError):
        file_app.music.add("big.wav", b"\x00" * 1001)
    with pytest.raises(AppError):
        file_app.music.add("tiny.wav", b"\x00")


def test_remove_resets_selection(file_app):
    n = file_app.music.add("gone.wav", WAV)
    file_app.settings.set("music_track", f"file:{n}")
    file_app.music.remove(n)
    assert file_app.music.user_tracks() == []
    assert file_app.settings.get("music_track") == "bundled:rain"


def test_memory_only_app_cannot_store_files(app):
    with pytest.raises(AppError):
        app.music.add("a.wav", WAV)
    assert app.music.user_tracks() == []


def test_music_is_off_by_default(app):
    assert app.settings.get_bool("music_auto") is False
