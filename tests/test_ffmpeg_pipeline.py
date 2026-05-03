from pathlib import Path
import json
import shutil

import pytest

from seedance_role_scene_remake.config import AppConfig
from seedance_role_scene_remake.dialogue import dialogue_aligned_ranges, timings_for_range
from seedance_role_scene_remake.ffmpeg import has_audio, run_cmd
from seedance_role_scene_remake.manifest import Manifest
from seedance_role_scene_remake.pipeline import extract_audio_job, merge_job, prepare_job, split_job


def _make_test_video(path: Path) -> None:
    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=160x90:rate=24:duration=1.2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(path),
        ]
    )


def test_split_and_extract_audio_integration(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe unavailable")
    video = tmp_path / "input.mp4"
    _make_test_video(video)

    manifest_path = split_job(
        config=AppConfig(api_key=""),
        video=video,
        output=tmp_path / "job",
        no_upload=True,
        segment_seconds=1,
    )
    manifest = Manifest.load(manifest_path)
    assert manifest.segments
    assert manifest.segments[0].source_audio_path

    for seg in manifest.segments:
        seg.remade_path = seg.source_path
        seg.status = "succeeded"
    manifest.save(manifest_path)

    extract_audio_job(manifest_path=manifest_path, stop_on_error=True)

    loaded = Manifest.load(manifest_path)
    assert loaded.segments[0].aligned_audio_path
    assert has_audio(tmp_path / "job" / loaded.segments[0].aligned_audio_path)


def test_merge_source_audio_uses_full_source_track(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe unavailable")
    video = tmp_path / "input.mp4"
    _make_test_video(video)

    manifest_path = split_job(
        config=AppConfig(api_key=""),
        video=video,
        output=tmp_path / "job_source_audio",
        no_upload=True,
        segment_seconds=1,
    )
    manifest = Manifest.load(manifest_path)
    manifest.audio_mode = "source"
    manifest.generate_audio = False
    for seg in manifest.segments:
        seg.remade_path = seg.source_path
        seg.status = "succeeded"
        seg.aligned_audio_path = None
    manifest.save(manifest_path)

    final = merge_job(manifest_path=manifest_path, output=tmp_path / "final_source_audio.mp4")

    assert final.exists()
    assert has_audio(final)
    loaded = Manifest.load(manifest_path)
    assert all(not seg.aligned_audio_path for seg in loaded.segments)


def test_dialogue_aligned_ranges_do_not_cut_utterances():
    transcript = [
        {"start": 0.4, "end": 1.0, "text": "a"},
        {"start": 7.5, "end": 9.0, "text": "b"},
        {"start": 15.0, "end": 16.0, "text": "c"},
    ]

    ranges = dialogue_aligned_ranges(duration=20, transcript=transcript, max_segment_seconds=8)

    assert ranges == [(0.0, 7.5), (7.5, 7.5), (15.0, 5.0)]
    assert timings_for_range(transcript, start=7.5, end=15.0)[0]["start"] == 0.0


def test_prepare_creates_reviewable_draft(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe unavailable")
    video = tmp_path / "input.mp4"
    _make_test_video(video)

    manifest_path = prepare_job(
        config=AppConfig(api_key=""),
        video=video,
        output=tmp_path / "job_prepare",
        segment_seconds=1,
        source_language="en",
        target_language="zh-CN",
        spoken_language="en",
    )

    manifest = Manifest.load(manifest_path)
    assert manifest.preparation.status == "draft"
    assert manifest.preparation.contact_sheet_path
    assert (tmp_path / "job_prepare" / manifest.preparation.contact_sheet_path).exists()
    assert manifest.characters[0].appearance_variants
    assert manifest.scenes
    assert manifest.language_policy.target_language == "zh-CN"
    assert manifest.language_policy.spoken_language == "en"
    assert manifest.language_policy.translate_dialogue is True
    assert manifest.segments[0].character_variant_ids
    assert manifest.person_candidates
    assert manifest.scene_candidates
    assert all(candidate.character_id for candidate in manifest.person_candidates)
    assert all(candidate.appearance_variant_id for candidate in manifest.person_candidates)
    assert all(not candidate.needs_better_reference for candidate in manifest.person_candidates)
    assert all(candidate.scene_id for candidate in manifest.scene_candidates)
    assert (tmp_path / "job_prepare" / "preparation" / "keyframes").exists()
    assert (tmp_path / "job_prepare" / "preparation" / "person_candidates").exists()
    assert (tmp_path / "job_prepare" / "preparation" / "scene_candidates").exists()


def test_prepare_can_attach_source_analysis(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe unavailable")
    video = tmp_path / "input.mp4"
    _make_test_video(video)
    analysis_path = tmp_path / "analysis.json"
    analysis_path.write_text(
        json.dumps(
            {
                "status": "reviewed",
                "backend": "mock",
                "script_path": "analysis/script/剧本.md",
                "script_json_path": "analysis/script/script.json",
                "index_path": "analysis/index.html",
                "synopsis": "主角在测试画面中行动。",
                "shots": [{"id": "1-1", "start": 0, "end": 1, "summary": "测试分场", "dialogues": []}],
                "characters": [{"id": "source_hero", "name": "源主角", "confidence": 0.9, "confirmed": True}],
                "scenes": [{"id": "source_room", "name": "源房间", "confidence": 0.9, "confirmed": True}],
                "props": [],
                "voices": [],
                "low_confidence_items": [],
                "review_items": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manifest_path = prepare_job(
        config=AppConfig(api_key=""),
        video=video,
        output=tmp_path / "job_with_analysis",
        analysis_path=analysis_path,
        segment_seconds=1,
    )

    manifest = Manifest.load(manifest_path)
    assert manifest.source_analysis.status == "reviewed"
    assert manifest.source_analysis.script_path == "analysis/script/剧本.md"
    assert manifest.story_bible.synopsis == "主角在测试画面中行动。"
    assert manifest.story_bible.beats[0]["id"] == "1-1"
