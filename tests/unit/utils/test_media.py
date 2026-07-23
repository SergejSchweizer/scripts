from __future__ import annotations

from pathlib import Path

import pytest

from scripts.utils.media import (
    MediaCommandAdapter,
    MediaStream,
    SubprocessMediaCommandAdapter,
    build_stream_map_args,
    filter_to_english_audio_and_subtitles,
    probe_streams,
    remove_empty_directories,
)
from ..fakes import DummyResult


def test_probe_streams_parses_ffprobe_output(tmp_path: Path) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("x", encoding="utf-8")

    class FakeAdapter(MediaCommandAdapter):
        def run_ffprobe(self, _file_path: Path):
            return DummyResult(stdout="0,video\n1,audio,eng\n2,subtitle,spa\n")

        def run_ffmpeg_copy(
            self,
            *,
            source_path: Path,
            map_args: list[str],
            target_path: Path,
            ffmpeg_threads: int,
        ):
            del source_path, map_args, target_path, ffmpeg_threads
            return DummyResult(returncode=0)

    streams = probe_streams(file_path, adapter=FakeAdapter())
    assert streams == [
        MediaStream(index=0, codec_type="video", language=None),
        MediaStream(index=1, codec_type="audio", language="eng"),
        MediaStream(index=2, codec_type="subtitle", language="spa"),
    ]


def test_subprocess_media_adapter_sets_safe_text_decoding(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_calls: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        del args
        captured_calls.append(kwargs)
        return DummyResult(returncode=0)

    monkeypatch.setattr("scripts.utils.media.subprocess.run", fake_run)
    adapter = SubprocessMediaCommandAdapter()
    source = Path("/tmp/source.mkv")
    target = Path("/tmp/target.mkv")

    adapter.run_ffprobe(source)
    adapter.run_ffmpeg_copy(
        source_path=source,
        map_args=["-map", "0:0"],
        target_path=target,
        ffmpeg_threads=1,
    )

    assert len(captured_calls) == 2
    for call in captured_calls:
        assert call["text"] is True
        assert call["encoding"] == "utf-8"
        assert call["errors"] == "replace"


def test_filter_to_english_returns_true_when_already_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.utils.media.probe_streams",
        lambda _path: [
            MediaStream(index=0, codec_type="video", language=None),
            MediaStream(index=1, codec_type="audio", language="eng"),
        ],
    )
    assert filter_to_english_audio_and_subtitles(file_path, ffmpeg_threads=1)


def test_filter_to_english_returns_false_when_ffmpeg_fails(tmp_path: Path) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("x", encoding="utf-8")

    class FakeAdapter(MediaCommandAdapter):
        def run_ffprobe(self, _file_path: Path):
            return DummyResult(stdout="0,video\n1,audio,rus\n")

        def run_ffmpeg_copy(
            self,
            *,
            source_path: Path,
            map_args: list[str],
            target_path: Path,
            ffmpeg_threads: int,
        ):
            del source_path, map_args, target_path, ffmpeg_threads
            return DummyResult(returncode=1)

    assert not filter_to_english_audio_and_subtitles(
        file_path,
        ffmpeg_threads=1,
        adapter=FakeAdapter(),
    )


def test_filter_to_english_returns_false_when_map_args_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "scripts.utils.media.probe_streams",
        lambda _path: [MediaStream(index=1, codec_type="audio", language="rus")],
    )
    assert not filter_to_english_audio_and_subtitles(file_path, ffmpeg_threads=1)


def test_filter_to_english_returns_false_when_verify_probe_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    file_path = tmp_path / "movie.mkv"
    file_path.write_text("x", encoding="utf-8")
    temp_name = f".scripts_tmp.{file_path.suffix.lstrip('.')}"

    def fake_probe(path: Path) -> list[MediaStream]:
        if path == file_path:
            return [
                MediaStream(index=0, codec_type="video", language=None),
                MediaStream(index=1, codec_type="audio", language="rus"),
            ]
        if path.name == temp_name:
            raise RuntimeError("verify failed")
        return []

    class FakeAdapter(MediaCommandAdapter):
        def run_ffprobe(self, _file_path: Path):
            raise AssertionError("unused in this test")

        def run_ffmpeg_copy(
            self,
            *,
            source_path: Path,
            map_args: list[str],
            target_path: Path,
            ffmpeg_threads: int,
        ):
            del source_path, map_args, ffmpeg_threads
            target_path.write_text("tmp", encoding="utf-8")
            return DummyResult(returncode=0)

    monkeypatch.setattr("scripts.utils.media.probe_streams", fake_probe)
    monkeypatch.setattr("scripts.utils.media._build_media_command_adapter", lambda: FakeAdapter())
    assert not filter_to_english_audio_and_subtitles(file_path, ffmpeg_threads=1)


def test_build_stream_map_args_can_become_empty() -> None:
    streams = [MediaStream(index=1, codec_type="audio", language="rus")]
    assert build_stream_map_args(streams, excluded_indexes={1}) == []


def test_remove_empty_directories_skips_non_empty_and_removes_empty(tmp_path: Path) -> None:
    root = tmp_path / "root"
    empty_dir = root / "empty"
    non_empty_dir = root / "not_empty"
    empty_dir.mkdir(parents=True)
    non_empty_dir.mkdir(parents=True)
    (non_empty_dir / "file.txt").write_text("x", encoding="utf-8")

    removed = remove_empty_directories(root)

    assert empty_dir in removed
    assert non_empty_dir not in removed
    assert non_empty_dir.exists()
