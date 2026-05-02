from pathlib import Path
import shutil

import pytest

from seedance_role_scene_remake.analysis import (
    ArkASRClient,
    ArkVLMClient,
    AnalysisFrame,
    format_script_markdown,
    run_source_analysis,
    summarize_source_analysis,
)
from seedance_role_scene_remake.config import AppConfig
from seedance_role_scene_remake.errors import PipelineError
from seedance_role_scene_remake.ffmpeg import run_cmd


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


def test_format_script_markdown_matches_reference_shape():
    text = format_script_markdown(
        {
            "shots": [
                {
                    "id": "1-1",
                    "scene_description": "全景-现代客厅里，主角站在门口。",
                    "camera": "近景",
                    "action": "主角抬头看向对方。",
                    "dialogues": [{"speaker": "方洛", "emotion": "冷笑", "text": "你们怎么结账？"}],
                    "sounds": ["手机震动声"],
                }
            ]
        }
    )

    assert "1-1" in text
    assert "近景-主角抬头看向对方。" in text
    assert "方洛（冷笑）：“你们怎么结账？”" in text
    assert "音效：手机震动声" in text


def test_run_source_analysis_requires_models_by_default(tmp_path: Path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"not a real video")

    with pytest.raises(PipelineError, match="缺少原视频分析配置"):
        run_source_analysis(config=AppConfig(api_key=""), video=video, output=tmp_path / "job")


def test_run_source_analysis_allow_skeleton_exports_review_package(tmp_path: Path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe unavailable")
    video = tmp_path / "input.mp4"
    _make_test_video(video)

    analysis_path = run_source_analysis(
        config=AppConfig(api_key=""),
        video=video,
        output=tmp_path / "job",
        sample_seconds=0.6,
        allow_skeleton=True,
    )

    assert analysis_path.exists()
    assert (tmp_path / "job" / "analysis" / "script" / "剧本.md").exists()
    assert (tmp_path / "job" / "analysis" / "script" / "script.json").exists()
    assert (tmp_path / "job" / "analysis" / "index.html").exists()
    assert (tmp_path / "job" / "analysis" / "scenes" / "scene_01" / "profile.json").exists()
    assert any("分场" in line for line in summarize_source_analysis(analysis_path))


def test_vlm_client_payload_contains_frames_and_json_request(tmp_path: Path, monkeypatch):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"fake")
    captured = {}

    class Response:
        status_code = 200
        headers = {}

        def json(self):
            return {"choices": [{"message": {"content": '{"shots": []}'}}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return Response()

    monkeypatch.setattr("seedance_role_scene_remake.analysis.httpx.Client", FakeClient)

    result = ArkVLMClient(api_key="key", base_url="https://ark", endpoint="/chat", timeout_s=1).analyze(
        model="vlm",
        frames=[AnalysisFrame(id="frame_0000", timestamp=0, path="frame.jpg")],
        transcript={"segments": [{"start": 0, "end": 1, "text": "hello"}]},
        video_duration=1,
        job_dir=tmp_path,
    )

    assert result == {"shots": []}
    assert captured["url"] == "https://ark/chat"
    assert captured["json"]["model"] == "vlm"
    assert captured["json"]["response_format"] == {"type": "json_object"}
    content = captured["json"]["messages"][1]["content"]
    assert any(item.get("type") == "image_url" for item in content)


def test_asr_client_payload_contains_audio_file(tmp_path: Path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")
    captured = {}

    class Response:
        status_code = 200
        headers = {}

        def json(self):
            return {"segments": [{"start": 0, "end": 1, "text": "hello"}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, headers, data, files):
            captured["url"] = url
            captured["headers"] = headers
            captured["data"] = data
            captured["files"] = files
            return Response()

    monkeypatch.setattr("seedance_role_scene_remake.analysis.httpx.Client", FakeClient)

    result = ArkASRClient(api_key="key", base_url="https://ark", endpoint="/asr", timeout_s=1).transcribe(audio, model="asr")

    assert result["segments"][0]["text"] == "hello"
    assert captured["url"] == "https://ark/asr"
    assert captured["data"]["model"] == "asr"
    assert "file" in captured["files"]
