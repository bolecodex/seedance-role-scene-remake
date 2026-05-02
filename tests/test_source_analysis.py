from pathlib import Path
import shutil

import pytest

from seedance_role_scene_remake.analysis import (
    ArkASRClient,
    ArkVLMClient,
    AnalysisFrame,
    DoubaoStreamingASRClient,
    analyze_script_quality,
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


def test_format_script_markdown_renders_detailed_action_and_delivery():
    text = format_script_markdown(
        {
            "shots": [
                {
                    "id": "1-1",
                    "environment_detail": "礼服店试衣区里，深色丝绒帘垂在女主身后，暖光从侧面打亮她的裙摆。",
                    "camera_plan": ["中景固定女主从帘前站定，随后切男主近景。"],
                    "action_beats": [
                        {"actor": "女主", "description": "双手轻压裙摆边缘，肩膀略微收紧，先低头看裙子再抬眼看向男主。"},
                        {"actor": "男主", "description": "身体向前倾半寸，视线从裙摆移到女主脸上。"},
                    ],
                    "dialogues": [
                        {
                            "speaker": "男主",
                            "text": "Wow, look at you.",
                            "emotion": "赞赏",
                            "delivery": "声音放轻，尾音上扬",
                            "facial_expression": "嘴角抬起",
                            "gaze": "视线停在女主脸上",
                        }
                    ],
                }
            ]
        }
    )

    assert "动作：女主：双手轻压裙摆边缘" in text
    assert "镜头/运镜：中景固定女主" in text
    assert "男主（声音放轻，尾音上扬，嘴角抬起，视线停在女主脸上）：“Wow, look at you.”" in text


def test_script_quality_flags_abstract_emotion_and_missing_action_beats():
    result = analyze_script_quality(
        {
            "shots": [
                {
                    "id": "1-1",
                    "environment_detail": "店里",
                    "camera": "",
                    "action_beats": [],
                    "dialogues": [{"speaker": "女主", "text": "What future?", "emotion": "冷淡"}],
                }
            ]
        },
        min_action_beats=2,
    )

    issue_types = {item["type"] for item in result["issues"]}
    assert "abstract_emotion" in issue_types
    assert "insufficient_action_beats" in issue_types
    assert "thin_environment_detail" in issue_types


def test_script_quality_flags_abstract_words_inside_detailed_state():
    result = analyze_script_quality(
        {
            "shots": [
                {
                    "id": "1-1",
                    "environment_detail": "礼服店内有深色窗帘和木质衣架，暖光落在人物肩部。",
                    "camera_plan": ["近景固定拍摄女主脸部。"],
                    "action_beats": [{"description": "女主移开视线。"}, {"description": "女主嘴角压平。"}],
                    "dialogues": [{"speaker": "女主", "text": "No.", "delivery": "态度冷淡，回答前停顿半拍", "gaze": "视线转向地面"}],
                }
            ]
        },
        min_action_beats=2,
    )

    assert any(item["type"] == "abstract_state_detail" for item in result["issues"])


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
    assert (tmp_path / "job" / "analysis" / "script" / "script_quality.json").exists()
    assert (tmp_path / "job" / "analysis" / "index.html").exists()
    assert (tmp_path / "job" / "analysis" / "roles" / "index.html").exists()
    assert (tmp_path / "job" / "analysis" / "scenes" / "scene_01" / "profile.json").exists()
    assert any("分场" in line for line in summarize_source_analysis(analysis_path))


def test_role_review_package_groups_character_evidence_and_voice_samples(tmp_path: Path):
    from seedance_role_scene_remake.analysis import _write_role_review_package

    job = tmp_path / "job"
    image = job / "analysis" / "characters" / "char_001" / "evidence" / "frame.jpg"
    audio = job / "analysis" / "voices" / "voice_001" / "samples" / "sample.m4a"
    image.parent.mkdir(parents=True)
    audio.parent.mkdir(parents=True)
    image.write_bytes(b"jpg")
    audio.write_bytes(b"audio")
    payload = {
        "characters": [
            {
                "id": "char_001",
                "name": "男主",
                "description": "源视频男主。",
                "confidence": 0.95,
                "confirmed": False,
                "profile_path": "analysis/characters/char_001/profile.json",
                "evidence_paths": ["analysis/characters/char_001/evidence/frame.jpg"],
                "evidence_regions": [{"frame_path": "analysis/characters/char_001/evidence/frame.jpg", "bbox": [1, 2, 30, 40]}],
            }
        ],
        "voices": [
            {
                "id": "voice_001",
                "name": "男主声音",
                "character_id": "char_001",
                "confidence": 0.8,
                "profile_path": "analysis/voices/voice_001/profile.json",
                "sample_paths": ["analysis/voices/voice_001/samples/sample.m4a"],
                "transcript_segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
            }
        ],
    }

    def fake_crop(src: Path, dst: Path, bbox: list[int]) -> None:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"crop")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("seedance_role_scene_remake.analysis.crop_image", fake_crop)
    _write_role_review_package(payload, output=job)
    monkeypatch.undo()

    assert (job / "analysis" / "roles" / "index.html").exists()
    assert (job / "analysis" / "roles" / "char_001" / "profile.json").exists()
    assert (job / "analysis" / "roles" / "char_001" / "contact_sheet.html").exists()
    assert (job / "analysis" / "roles" / "char_001" / "full_frames" / "frame.jpg").exists()
    assert (job / "analysis" / "roles" / "char_001" / "person_crops" / "crop_00_frame.jpg").exists()
    assert (job / "analysis" / "roles" / "char_001" / "voice_samples" / "sample.m4a").exists()
    assert payload["characters"][0]["role_review_path"] == "analysis/roles/char_001/contact_sheet.html"


def test_normalize_entities_keeps_character_bbox_regions():
    from seedance_role_scene_remake.analysis import _normalize_entities

    frames = [AnalysisFrame(id="frame_0000", timestamp=0.0, path="analysis/source/keyframes/frame.jpg")]
    items = [
        {
            "id": "char_001",
            "name": "男主",
            "evidence_regions": [{"frame_id": "frame_0000", "bbox": [10.2, 20.8, 80, 160], "confidence": 0.8}],
        }
    ]

    result = _normalize_entities(items, prefix="character", default_name="未知角色", frames=frames)

    assert result[0]["evidence_regions"] == [
        {"frame_path": "analysis/source/keyframes/frame.jpg", "bbox": [10, 21, 80, 160], "note": "", "confidence": 0.8}
    ]


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
        script_detail="detailed",
        script_min_action_beats=2,
    )

    assert result == {"shots": []}
    assert captured["url"] == "https://ark/chat"
    assert captured["json"]["model"] == "vlm"
    assert captured["json"]["max_tokens"] == 16000
    assert captured["json"]["temperature"] == 0.1
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
