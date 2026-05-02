from pathlib import Path
import shutil

import pytest

from seedance_role_scene_remake.analysis import (
    ArkASRClient,
    ArkVLMClient,
    AnalysisFrame,
    DoubaoStreamingASRClient,
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


def test_doubao_streaming_asr_normalizes_ws_utterances(tmp_path: Path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake wav")
    sent = []

    class FakeWS:
        def __init__(self):
            self.count = 0

        def send_binary(self, payload):
            sent.append(payload)

        def recv(self):
            self.count += 1
            if self.count == 1:
                return _doubao_packet({"result": {"text": ""}})
            return _doubao_packet(
                {
                    "result": {
                        "text": "hello world",
                        "utterances": [{"start_time": 0, "end_time": 1200, "text": "hello world", "speaker": "speaker_1"}],
                    }
                }
            )

        def close(self):
            pass

    captured = {}

    def fake_create_connection(url, header, timeout):
        captured["url"] = url
        captured["header"] = header
        captured["timeout"] = timeout
        return FakeWS()

    monkeypatch.setattr("seedance_role_scene_remake.analysis.websocket.create_connection", fake_create_connection)

    result = DoubaoStreamingASRClient(
        app_id="app",
        access_token="token",
        resource_id="volc.bigasr.sauc.duration",
        ws_url="wss://example.com/ws",
        timeout_s=3,
    ).transcribe(audio)

    assert captured["url"] == "wss://example.com/ws"
    assert any(item.startswith("X-Api-App-Key: ") for item in captured["header"])
    assert any(item.startswith("X-Api-Resource-Id: volc.bigasr.sauc.duration") for item in captured["header"])
    assert len(sent) == 2
    assert result["segments"][0]["text"] == "hello world"
    assert result["segments"][0]["end"] == 1.2


def test_doubao_streaming_asr_dedupes_incremental_segments():
    from seedance_role_scene_remake.analysis import _normalize_doubao_asr_responses

    result = _normalize_doubao_asr_responses(
        [
            {"result": {"utterances": [{"start_time": 440, "end_time": 1000, "text": "You", "speaker": "s"}]}},
            {"result": {"utterances": [{"start_time": 440, "end_time": 1000, "text": "You need to leave.", "speaker": "s"}]}},
            {"result": {"utterances": [{"start_time": 1720, "end_time": 2200, "text": "How dare you?", "speaker": "s"}]}},
        ]
    )

    assert len(result["segments"]) == 2
    assert result["segments"][0]["start"] == 0.44
    assert result["segments"][0]["text"] == "You need to leave."
    assert result["segments"][1]["start"] == 1.72


def test_voice_samples_attach_from_dialogue_text():
    from seedance_role_scene_remake.analysis import _attach_dialogue_voice_samples

    voices = [{"id": "v1", "character_id": "char001", "sample_ranges": [], "transcript_segments": []}]
    shots = [{"dialogues": [{"speaker": "char001", "text": "What a beautiful princess."}]}]
    transcript_items = [{"start": 25.6, "end": 26.7, "text": "What a beautiful princess.", "speaker": "voice_unknown"}]

    _attach_dialogue_voice_samples(voices=voices, shots=shots, transcript_items=transcript_items)

    assert voices[0]["sample_ranges"] == [{"start": 25.6, "end": 26.7}]
    assert voices[0]["transcript_segments"][0]["text"] == "What a beautiful princess."


def test_voice_samples_attach_normalizes_character_ids():
    from seedance_role_scene_remake.analysis import _attach_dialogue_voice_samples

    voices = [{"id": "v1", "character_id": "c001", "sample_ranges": [], "transcript_segments": []}]
    shots = [{"dialogues": [{"speaker": "C001", "text": "Fine."}]}]
    transcript_items = [{"start": 52.2, "end": 52.7, "text": "Fine.", "speaker": "voice_unknown"}]

    _attach_dialogue_voice_samples(voices=voices, shots=shots, transcript_items=transcript_items)

    assert voices[0]["sample_ranges"] == [{"start": 52.2, "end": 52.7}]


def test_voice_samples_attach_resolves_character_names():
    from seedance_role_scene_remake.analysis import _attach_dialogue_voice_samples

    voices = [{"id": "v1", "character_id": "char_001", "sample_ranges": [], "transcript_segments": []}]
    characters = [{"id": "char_001", "name": "男主"}]
    shots = [{"dialogues": [{"speaker": "男主", "text": "You need to leave."}]}]
    transcript_items = [{"start": 0.44, "end": 1.0, "text": "You need to leave.", "speaker": "voice_unknown"}]

    _attach_dialogue_voice_samples(voices=voices, shots=shots, transcript_items=transcript_items, characters=characters)

    assert voices[0]["sample_ranges"] == [{"start": 0.44, "end": 1.0}]


def test_vlm_voice_without_speaker_does_not_inherit_unknown_asr_segments():
    from seedance_role_scene_remake.analysis import _normalize_voices

    voices = _normalize_voices(
        [{"id": "v1", "character_id": "c001", "speaker": "voice_unknown"}],
        transcript_items=[{"start": 0.0, "end": 1.0, "text": "hello", "speaker": "voice_unknown"}],
    )

    assert voices[0]["id"] == "v1"
    assert voices[0]["transcript_segments"] == []
    assert voices[1]["id"] == "voice_unknown"
    assert len(voices[1]["transcript_segments"]) == 1


def _doubao_packet(payload: dict) -> bytes:
    import gzip
    import json
    import struct

    body = gzip.compress(json.dumps(payload).encode("utf-8"))
    header = bytes([(1 << 4) | 1, (9 << 4), (1 << 4) | 1, 0])
    return header + struct.pack(">I", len(body)) + body
