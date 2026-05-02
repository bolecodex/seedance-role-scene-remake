"""Source-video analysis, script rendering, and review asset export."""

from __future__ import annotations

import html
import json
import shutil
import gzip
import struct
import uuid
from difflib import SequenceMatcher
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import websocket

from seedance_role_scene_remake.config import AppConfig
from seedance_role_scene_remake.errors import ArkError, PipelineError
from seedance_role_scene_remake.ffmpeg import (
    detect_scene_timestamps,
    extract_audio_clip,
    extract_audio_for_asr,
    get_video_duration,
    image_to_data_url,
    run_cmd,
)
from seedance_role_scene_remake.manifest import Manifest, SourceAnalysisSpec


@dataclass
class AnalysisFrame:
    id: str
    timestamp: float
    path: str
    kind: str = "sample"


class ArkASRClient:
    """Small OpenAI-style audio transcription adapter.

    The adapter is intentionally isolated because Ark/ASR deployments may use
    different endpoints. Tests mock this class; production users can point the
    endpoint/model at their enabled ASR service.
    """

    def __init__(self, *, api_key: str, base_url: str, endpoint: str, timeout_s: int) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint
        self.timeout_s = timeout_s

    def transcribe(self, audio: Path, *, model: str, language: str = "auto") -> dict[str, Any]:
        url = f"{self.base_url}{self.endpoint}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = {"model": model, "response_format": "verbose_json"}
        if language and language != "auto":
            data["language"] = language
        try:
            with audio.open("rb") as fh:
                files = {"file": (audio.name, fh, "audio/wav")}
                with httpx.Client(timeout=self.timeout_s) as client:
                    response = client.post(url, headers=headers, data=data, files=files)
        except httpx.HTTPError as exc:
            raise ArkError(f"ASR 提交失败：{exc}") from exc
        return _json_or_error(response, label="ASR")


class DoubaoStreamingASRClient:
    """Doubao streaming ASR 2.0 WebSocket adapter."""

    PROTOCOL_VERSION = 0b0001
    DEFAULT_HEADER_SIZE = 0b0001
    FULL_CLIENT_REQUEST = 0b0001
    AUDIO_ONLY_REQUEST = 0b0010
    FULL_SERVER_RESPONSE = 0b1001
    SERVER_ACK = 0b1011
    SERVER_ERROR_RESPONSE = 0b1111
    NO_SEQUENCE = 0b0000
    POS_SEQUENCE = 0b0001
    NEG_SEQUENCE = 0b0010
    NEG_WITH_SEQUENCE = 0b0011
    JSON_SERIALIZATION = 0b0001
    GZIP_COMPRESSION = 0b0001

    def __init__(
        self,
        *,
        app_id: str,
        access_token: str,
        resource_id: str,
        ws_url: str,
        timeout_s: int,
    ) -> None:
        self.app_id = app_id
        self.access_token = access_token
        self.resource_id = resource_id
        self.ws_url = ws_url
        self.timeout_s = timeout_s

    def transcribe(self, audio: Path, *, language: str = "auto", chunk_size: int = 16000) -> dict[str, Any]:
        if not self.app_id or not self.access_token:
            raise PipelineError("豆包流式 ASR 需要 SEEDANCE_ROLE_SCENE_DOUBAO_ASR_APP_ID 和 SEEDANCE_ROLE_SCENE_DOUBAO_ASR_ACCESS_TOKEN。")
        headers = [
            f"X-Api-App-Key: {self.app_id}",
            f"X-Api-Access-Key: {self.access_token}",
            f"X-Api-Resource-Id: {self.resource_id}",
            f"X-Api-Connect-Id: {uuid.uuid4()}",
        ]
        try:
            ws = websocket.create_connection(self.ws_url, header=headers, timeout=self.timeout_s)
        except Exception as exc:
            raise ArkError(f"豆包流式 ASR 连接失败：{exc}") from exc
        responses: list[dict[str, Any]] = []
        try:
            init_payload = {
                "user": {"uid": "seedance-role-scene-remake"},
                "audio": {"format": "wav", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
                "request": {
                    "model_name": "bigmodel",
                    "enable_itn": True,
                    "enable_punc": True,
                    "show_utterances": True,
                    "result_type": "full",
                },
            }
            if language and language != "auto":
                init_payload["request"]["language"] = language
            ws.send_binary(self._client_packet(self.FULL_CLIENT_REQUEST, self.NO_SEQUENCE, json.dumps(init_payload, ensure_ascii=False).encode("utf-8")))
            self._collect_response(ws, responses)

            data = audio.read_bytes()
            if not data:
                raise PipelineError(f"ASR 音频为空：{audio}")
            chunks = [data[index : index + chunk_size] for index in range(0, len(data), chunk_size)]
            for index, chunk in enumerate(chunks, start=1):
                flags = self.NEG_WITH_SEQUENCE if index == len(chunks) else self.POS_SEQUENCE
                sequence = index + 1
                ws.send_binary(self._audio_packet(chunk, sequence=-sequence if flags == self.NEG_WITH_SEQUENCE else sequence, flags=flags))
                self._collect_response(ws, responses)
        finally:
            ws.close()
        return _normalize_doubao_asr_responses(responses)

    def _collect_response(self, ws: Any, responses: list[dict[str, Any]]) -> None:
        message = ws.recv()
        parsed = self._parse_response(message)
        if parsed:
            responses.append(parsed)

    def _client_packet(self, message_type: int, flags: int, payload: bytes) -> bytes:
        compressed = gzip.compress(payload)
        return self._header(message_type, flags) + struct.pack(">I", len(compressed)) + compressed

    def _audio_packet(self, payload: bytes, *, sequence: int, flags: int) -> bytes:
        compressed = gzip.compress(payload)
        return self._header(self.AUDIO_ONLY_REQUEST, flags) + struct.pack(">iI", sequence, len(compressed)) + compressed

    def _header(self, message_type: int, flags: int) -> bytes:
        return bytes(
            [
                (self.PROTOCOL_VERSION << 4) | self.DEFAULT_HEADER_SIZE,
                (message_type << 4) | flags,
                (self.JSON_SERIALIZATION << 4) | self.GZIP_COMPRESSION,
                0x00,
            ]
        )

    def _parse_response(self, message: bytes | str) -> dict[str, Any]:
        if isinstance(message, str):
            try:
                return json.loads(message)
            except json.JSONDecodeError:
                return {"raw": message}
        if len(message) < 4:
            return {}
        header_size = message[0] & 0x0F
        message_type = message[1] >> 4
        flags = message[1] & 0x0F
        compression = message[2] & 0x0F
        offset = header_size * 4
        if flags in {self.POS_SEQUENCE, self.NEG_SEQUENCE, self.NEG_WITH_SEQUENCE} and len(message) >= offset + 4:
            offset += 4
        if len(message) < offset + 4:
            return {"message_type": message_type}
        size = struct.unpack(">I", message[offset : offset + 4])[0]
        offset += 4
        payload = message[offset : offset + size]
        if message_type == self.SERVER_ERROR_RESPONSE:
            text = payload.decode("utf-8", errors="ignore")
            raise ArkError(f"豆包流式 ASR 返回错误：{text}")
        if not payload:
            return {"message_type": message_type}
        if compression == self.GZIP_COMPRESSION:
            payload = gzip.decompress(payload)
        try:
            result = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            result = {"payload": payload.decode("utf-8", errors="ignore")}
        result["message_type"] = message_type
        return result


class ArkVLMClient:
    """Ark OpenAI-compatible multimodal analysis client."""

    def __init__(self, *, api_key: str, base_url: str, endpoint: str, timeout_s: int) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.endpoint = endpoint
        self.timeout_s = timeout_s

    def analyze(self, *, model: str, frames: list[AnalysisFrame], transcript: dict[str, Any], video_duration: float, job_dir: Path) -> dict[str, Any]:
        url = f"{self.base_url}{self.endpoint}"
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": _analysis_prompt(frames=frames, transcript=transcript, duration=video_duration),
            }
        ]
        for frame in frames:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_to_data_url(job_dir / frame.path)},
                }
            )
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是短剧视频分析师，只输出严格 JSON，不输出 Markdown。",
                },
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                response = client.post(url, headers=headers, json=payload)
                if response.status_code == 400 and _response_format_unsupported(response):
                    payload.pop("response_format", None)
                    response = client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise ArkError(f"视频理解提交失败：{exc}") from exc
        data = _json_or_error(response, label="视频理解")
        text = _extract_message_text(data)
        try:
            return _parse_json_text(text)
        except json.JSONDecodeError as exc:
            raise ArkError(f"视频理解响应不是严格 JSON：{text[:500]}") from exc


def run_source_analysis(
    *,
    config: AppConfig,
    video: Path,
    output: Path,
    analysis_model: str = "",
    asr_model: str = "",
    sample_seconds: float = 2.0,
    scene_threshold: float = 0.35,
    allow_skeleton: bool = False,
) -> Path:
    if not video.exists():
        raise PipelineError(f"输入视频不存在：{video}")
    analysis_model = analysis_model or config.analysis_model
    asr_model = asr_model or config.asr_model
    doubao_asr_available = _doubao_asr_available(config)
    if not allow_skeleton:
        missing: list[str] = []
        if not config.api_key:
            missing.append("ARK_API_KEY")
        if not analysis_model:
            missing.append("SEEDANCE_ROLE_SCENE_ANALYSIS_MODEL 或 --analysis-model")
        if not asr_model and not doubao_asr_available:
            missing.append("SEEDANCE_ROLE_SCENE_ASR_MODEL 或豆包流式 ASR 凭据")
        if missing:
            raise PipelineError("缺少原视频分析配置：" + "、".join(missing) + "。默认不输出低质量骨架；调试可加 --allow-skeleton。")
    if sample_seconds <= 0:
        raise PipelineError("--sample-seconds 必须大于 0。")

    output.mkdir(parents=True, exist_ok=True)
    analysis_dir = output / "analysis"
    source_dir = analysis_dir / "source"
    frames_dir = source_dir / "keyframes"
    audio_dir = source_dir / "audio"
    frames_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    duration = get_video_duration(video)
    frames = _extract_analysis_frames(video, frames_dir=frames_dir, job_dir=output, sample_seconds=sample_seconds, scene_threshold=scene_threshold)
    asr_audio = audio_dir / "source_16k.wav"
    has_audio = extract_audio_for_asr(video, asr_audio)
    transcript = _skeleton_transcript()
    if has_audio and _use_doubao_asr(config=config, asr_model=asr_model):
        transcript = DoubaoStreamingASRClient(
            app_id=config.doubao_asr_app_id,
            access_token=config.doubao_asr_access_token,
            resource_id=config.doubao_asr_resource_id,
            ws_url=config.doubao_asr_ws_url,
            timeout_s=config.request_timeout_s,
        ).transcribe(asr_audio)
    elif has_audio and asr_model and config.api_key:
        transcript = ArkASRClient(
            api_key=config.api_key,
            base_url=config.base_url,
            endpoint=config.asr_endpoint,
            timeout_s=config.request_timeout_s,
        ).transcribe(asr_audio, model=asr_model)
    elif has_audio and not allow_skeleton:
        raise PipelineError("视频有音轨，但缺少 ASR 模型配置，无法生成对白剧本。")

    if analysis_model and config.api_key:
        raw = ArkVLMClient(
            api_key=config.api_key,
            base_url=config.base_url,
            endpoint=config.analysis_endpoint,
            timeout_s=config.request_timeout_s,
        ).analyze(model=analysis_model, frames=frames, transcript=transcript, video_duration=duration, job_dir=output)
    elif allow_skeleton:
        raw = _skeleton_visual_analysis(frames=frames, transcript=transcript, duration=duration)
    else:
        raise PipelineError("缺少视频理解模型配置，无法生成原视频剧本。")

    payload = _normalize_analysis(
        raw,
        source=video,
        output=output,
        frames=frames,
        transcript=transcript,
        duration=duration,
        backend="ark_vlm_asr" if analysis_model and asr_model else "local_skeleton",
    )
    _export_analysis_assets(payload, video=video, output=output)
    analysis_path = analysis_dir / "analysis.json"
    analysis_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return analysis_path


def summarize_source_analysis(analysis_path: Path) -> list[str]:
    payload = read_source_analysis(analysis_path)
    lines = [
        f"分析文件：{analysis_path}",
        f"剧本：{payload.get('script_path') or '-'}",
        f"角色：{len(payload.get('characters') or [])}",
        f"场景：{len(payload.get('scenes') or [])}",
        f"道具：{len(payload.get('props') or [])}",
        f"声音：{len(payload.get('voices') or [])}",
        f"分场：{len(payload.get('shots') or [])}",
    ]
    low = payload.get("low_confidence_items") or []
    review = payload.get("review_items") or []
    if low:
        lines.append(f"低置信度项：{len(low)}")
    if review:
        lines.append("待人工检查：")
        lines.extend(f"- {item}" for item in review)
    else:
        lines.append("待人工检查：无")
    return lines


def read_source_analysis(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PipelineError(f"分析文件不存在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"分析 JSON 无效：{path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"分析 JSON 顶层必须是对象：{path}")
    return payload


def apply_source_analysis_to_manifest(manifest: Manifest, *, analysis_path: Path, job_dir: Path) -> None:
    payload = read_source_analysis(analysis_path)
    manifest.source_analysis = source_analysis_spec(payload, analysis_path=analysis_path, job_dir=job_dir)
    synopsis = str(payload.get("synopsis") or "").strip()
    shots = payload.get("shots") if isinstance(payload.get("shots"), list) else []
    if synopsis:
        manifest.story_bible.synopsis = synopsis
    elif shots:
        manifest.story_bible.synopsis = "；".join(str(item.get("summary") or item.get("description") or "").strip() for item in shots[:3] if isinstance(item, dict) and (item.get("summary") or item.get("description")))[:300]
    if shots:
        manifest.story_bible.beats = [
            {
                "id": shot.get("id") or f"1-{index + 1}",
                "start": shot.get("start", 0),
                "end": shot.get("end", 0),
                "summary": shot.get("summary") or shot.get("description") or "",
                "characters": shot.get("character_ids") or [],
                "scene": shot.get("scene_id") or "",
                "props": shot.get("prop_ids") or [],
                "dialogue_count": len(shot.get("dialogues") or []),
            }
            for index, shot in enumerate(shots)
            if isinstance(shot, dict)
        ]
    if manifest.preparation.required_review_items is None:
        manifest.preparation.required_review_items = []
    for item in manifest.source_analysis.review_items:
        label = f"source_analysis: {item}"
        if label not in manifest.preparation.required_review_items:
            manifest.preparation.required_review_items.append(label)


def source_analysis_spec(payload: dict[str, Any], *, analysis_path: Path, job_dir: Path) -> SourceAnalysisSpec:
    return SourceAnalysisSpec(
        status=str(payload.get("status") or "draft"),
        analysis_json_path=_relative(analysis_path, job_dir),
        script_path=payload.get("script_path"),
        script_json_path=payload.get("script_json_path"),
        index_path=payload.get("index_path"),
        character_index=_index_entries(payload.get("characters") or []),
        scene_index=_index_entries(payload.get("scenes") or []),
        prop_index=_index_entries(payload.get("props") or []),
        voice_index=_index_entries(payload.get("voices") or []),
        low_confidence_items=list(payload.get("low_confidence_items") or []),
        review_items=list(payload.get("review_items") or []),
        backend=str(payload.get("backend") or ""),
    )


def format_script_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    for index, shot in enumerate(payload.get("shots") or [], start=1):
        if not isinstance(shot, dict):
            continue
        shot_id = str(shot.get("id") or f"1-{index}")
        lines.append(shot_id)
        scene_text = str(shot.get("scene_description") or shot.get("description") or shot.get("summary") or "请人工补充画面内容。").strip()
        if scene_text:
            lines.append(scene_text)
        camera = str(shot.get("camera") or "").strip()
        action = str(shot.get("action") or "").strip()
        if camera and action:
            lines.append(f"{camera}-{action}")
        elif action:
            lines.append(action)
        for dialogue in shot.get("dialogues") or []:
            if not isinstance(dialogue, dict):
                continue
            speaker = str(dialogue.get("speaker") or dialogue.get("character_id") or "未知角色").strip()
            text = str(dialogue.get("text") or "").strip()
            if not text:
                continue
            emotion = str(dialogue.get("emotion") or dialogue.get("tone") or "").strip()
            kind = str(dialogue.get("type") or "dialogue").lower()
            prefix = "旁白" if kind in {"narration", "voiceover"} else speaker
            if kind == "os":
                prefix = f"{speaker}（OS）"
            elif emotion and prefix != "旁白":
                prefix = f"{speaker}（{emotion}）"
            lines.append(f"{prefix}：“{text}”")
        for sound in shot.get("sounds") or []:
            value = str(sound).strip()
            if value:
                lines.append(f"音效：{value}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _extract_analysis_frames(video: Path, *, frames_dir: Path, job_dir: Path, sample_seconds: float, scene_threshold: float) -> list[AnalysisFrame]:
    duration = get_video_duration(video)
    timestamps = {0.0, max(0.0, duration - 0.25)}
    current = 0.0
    while current < duration:
        timestamps.add(round(current, 3))
        current += sample_seconds
    for scene_time in detect_scene_timestamps(video, threshold=scene_threshold):
        timestamps.add(round(max(0.0, min(scene_time, duration - 0.05)), 3))
    frames: list[AnalysisFrame] = []
    for index, timestamp in enumerate(sorted(timestamps)):
        output = frames_dir / f"frame_{index:04d}_{timestamp:08.3f}.jpg"
        _extract_analysis_frame_at(video, output, timestamp)
        frames.append(AnalysisFrame(id=f"frame_{index:04d}", timestamp=timestamp, path=_relative(output, job_dir), kind="scene" if timestamp in timestamps else "sample"))
    return frames


def _extract_analysis_frame_at(video: Path, output: Path, timestamp: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = get_video_duration(video)
    at = max(0.0, min(timestamp, max(0.0, duration - 0.05)))
    run_cmd(["ffmpeg", "-y", "-ss", f"{at:.3f}", "-i", str(video), "-vf", "scale=360:-1", "-frames:v", "1", "-q:v", "4", str(output)])


def _normalize_analysis(
    raw: dict[str, Any],
    *,
    source: Path,
    output: Path,
    frames: list[AnalysisFrame],
    transcript: dict[str, Any],
    duration: float,
    backend: str,
) -> dict[str, Any]:
    transcript_items = _normalize_transcript(transcript)
    shots = _normalize_shots(raw.get("shots") or raw.get("scenes") or raw.get("segments"), frames=frames, transcript=transcript_items, duration=duration)
    characters = _normalize_entities(raw.get("characters") or raw.get("roles"), prefix="character", default_name="未知角色", frames=frames)
    scenes = _normalize_entities(raw.get("scene_clusters") or raw.get("locations") or raw.get("source_scenes"), prefix="scene", default_name="未知场景", frames=frames)
    if not scenes:
        scenes = [{"id": "scene_01", "name": "源视频主要场景", "description": "请人工检查源场景。", "confidence": 0.5, "confirmed": False, "evidence_paths": [frame.path for frame in frames[:3]]}]
    props = _normalize_entities(raw.get("props") or raw.get("objects"), prefix="prop", default_name="未知道具", frames=frames)
    voices = _normalize_voices(raw.get("voices") or raw.get("speakers"), transcript_items=transcript_items)
    _attach_dialogue_voice_samples(voices=voices, shots=shots, transcript_items=transcript_items, characters=characters)
    review_items = _review_items(characters=characters, scenes=scenes, props=props, voices=voices, shots=shots)
    low_confidence = _low_confidence_items(characters + scenes + props + voices + shots)
    payload: dict[str, Any] = {
        "version": 1,
        "status": "draft" if review_items or low_confidence else "reviewed",
        "backend": backend,
        "source": str(source.resolve()),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "duration": duration,
        "synopsis": str(raw.get("synopsis") or raw.get("summary") or "").strip(),
        "frames": [frame.__dict__ for frame in frames],
        "transcript": {"segments": transcript_items, "raw": transcript},
        "shots": shots,
        "characters": characters,
        "scenes": scenes,
        "props": props,
        "voices": voices,
        "low_confidence_items": low_confidence,
        "review_items": review_items,
    }
    script_dir = output / "analysis" / "script"
    payload["script_path"] = _relative(script_dir / "剧本.md", output)
    payload["script_json_path"] = _relative(script_dir / "script.json", output)
    payload["script_review_path"] = _relative(script_dir / "script_review.html", output)
    payload["index_path"] = _relative(output / "analysis" / "index.html", output)
    return payload


def _export_analysis_assets(payload: dict[str, Any], *, video: Path, output: Path) -> None:
    analysis_dir = output / "analysis"
    script_dir = analysis_dir / "script"
    script_dir.mkdir(parents=True, exist_ok=True)
    (output / payload["script_path"]).write_text(format_script_markdown(payload), encoding="utf-8")
    (output / payload["script_json_path"]).write_text(json.dumps({"shots": payload["shots"], "transcript": payload["transcript"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_script_review(payload, output / payload["script_review_path"], job_dir=output)
    _write_entity_profiles(payload, kind="characters", output=output)
    _write_entity_profiles(payload, kind="scenes", output=output, evidence_subdir="keyframes")
    _write_entity_profiles(payload, kind="props", output=output)
    _write_voice_profiles(payload, video=video, output=output)
    _write_index(payload, output / payload["index_path"], job_dir=output)


def _write_entity_profiles(payload: dict[str, Any], *, kind: str, output: Path, evidence_subdir: str = "evidence") -> None:
    for item in payload.get(kind) or []:
        if not isinstance(item, dict):
            continue
        entity_id = _safe_id(str(item.get("id") or kind))
        base = output / "analysis" / kind / entity_id
        evidence_dir = base / evidence_subdir
        evidence_dir.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for rel in item.get("evidence_paths") or []:
            source = output / rel
            if not source.exists():
                continue
            target = evidence_dir / source.name
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            copied.append(_relative(target, output))
        item["evidence_paths"] = copied
        item["profile_path"] = _relative(base / "profile.json", output)
        (base / "profile.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_entity_contact_sheet(item, base / "contact_sheet.html", job_dir=output)


def _write_voice_profiles(payload: dict[str, Any], *, video: Path, output: Path) -> None:
    for item in payload.get("voices") or []:
        if not isinstance(item, dict):
            continue
        voice_id = _safe_id(str(item.get("id") or "voice"))
        base = output / "analysis" / "voices" / voice_id
        samples_dir = base / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)
        sample_paths: list[str] = []
        for idx, sample in enumerate(item.get("sample_ranges") or []):
            if not isinstance(sample, dict):
                continue
            start = float(sample.get("start") or 0)
            end = float(sample.get("end") or start + 2)
            target = samples_dir / f"sample_{idx:02d}_{start:.2f}_{end:.2f}.m4a"
            if extract_audio_clip(video, target, start=start, duration=max(0.2, end - start)):
                sample_paths.append(_relative(target, output))
        item["sample_paths"] = sample_paths
        item["profile_path"] = _relative(base / "profile.json", output)
        (base / "profile.json").write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        (base / "transcript.json").write_text(json.dumps({"segments": item.get("transcript_segments") or []}, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_entity_contact_sheet(item: dict[str, Any], output: Path, *, job_dir: Path) -> None:
    images = []
    for rel in item.get("evidence_paths") or []:
        src = html.escape(_html_rel(output.parent, job_dir / rel))
        images.append(f'<img src="{src}" alt="{html.escape(rel)}">')
    output.write_text(
        f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:24px}}img{{width:180px;margin:6px}}pre{{white-space:pre-wrap}}</style></head><body>
<h1>{html.escape(str(item.get('name') or item.get('id') or 'profile'))}</h1>
<p>{html.escape(str(item.get('description') or ''))}</p>
<p>confidence={html.escape(str(item.get('confidence', '-')))} confirmed={html.escape(str(item.get('confirmed', False)))}</p>
<div>{''.join(images)}</div>
<pre>{html.escape(json.dumps(item, ensure_ascii=False, indent=2))}</pre>
</body></html>""",
        encoding="utf-8",
    )


def _write_script_review(payload: dict[str, Any], output: Path, *, job_dir: Path) -> None:
    rows = []
    for shot in payload.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        frames = []
        for rel in shot.get("evidence_paths") or []:
            frames.append(f'<img src="{html.escape(_html_rel(output.parent, job_dir / rel))}" alt="{html.escape(rel)}">')
        start = float(shot.get("start") or 0)
        end = float(shot.get("end") or 0)
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(shot.get('id') or ''))}</td>"
            f"<td>{start:.2f}-{end:.2f}s</td>"
            f"<td>{html.escape(str(shot.get('summary') or shot.get('description') or ''))}</td>"
            f"<td>{''.join(frames)}</td>"
            "</tr>"
        )
    output.write_text(
        f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:8px;vertical-align:top}}img{{width:160px;margin:3px}}</style></head><body>
<h1>剧本复核</h1><table><thead><tr><th>分场</th><th>时间</th><th>摘要</th><th>关键帧</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>""",
        encoding="utf-8",
    )


def _write_index(payload: dict[str, Any], output: Path, *, job_dir: Path) -> None:
    links = [
        ("剧本", payload.get("script_path")),
        ("剧本复核", payload.get("script_review_path")),
    ]
    cards = []
    for title, rel in links:
        if rel:
            cards.append(f'<li><a href="{html.escape(_html_rel(output.parent, job_dir / rel))}">{html.escape(title)}</a></li>')
    summary = "".join(
        f"<li>{label}：{len(payload.get(key) or [])}</li>"
        for label, key in [("角色", "characters"), ("场景", "scenes"), ("道具", "props"), ("声音", "voices"), ("分场", "shots")]
    )
    review = "".join(f"<li>{html.escape(str(item))}</li>" for item in payload.get("review_items") or [])
    output.write_text(
        f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><style>body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:24px;line-height:1.5}}</style></head><body>
<h1>原视频分析索引</h1>
<ul>{summary}</ul>
<h2>入口</h2><ul>{''.join(cards)}</ul>
<h2>待人工检查</h2><ul>{review or '<li>无</li>'}</ul>
<p>源角色、源场景、道具和声音只用于理解原片和人工检查，不作为目标外观。</p>
</body></html>""",
        encoding="utf-8",
    )


def _normalize_transcript(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        text = str(transcript.get("text") or "").strip()
        return [{"id": "utt_000", "start": 0.0, "end": 0.0, "text": text, "speaker": "voice_unknown"}] if text else []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(segments):
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": str(item.get("id") or f"utt_{index:03d}"),
                "start": float(item.get("start") or 0),
                "end": float(item.get("end") or item.get("start") or 0),
                "text": str(item.get("text") or "").strip(),
                "speaker": str(item.get("speaker") or item.get("speaker_id") or "voice_unknown"),
            }
        )
    return result


def _normalize_shots(items: Any, *, frames: list[AnalysisFrame], transcript: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        return _skeleton_shots(frames=frames, transcript=transcript, duration=duration)
    shots: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        start = float(item.get("start") or item.get("start_time") or 0)
        end = float(item.get("end") or item.get("end_time") or min(duration, start + 2))
        evidence = item.get("evidence_paths") if isinstance(item.get("evidence_paths"), list) else _nearest_frame_paths(frames, start, end)
        shots.append(
            {
                "id": str(item.get("id") or f"1-{index + 1}"),
                "start": start,
                "end": end,
                "summary": str(item.get("summary") or item.get("description") or "").strip(),
                "description": str(item.get("description") or item.get("summary") or "").strip(),
                "scene_description": str(item.get("scene_description") or item.get("environment") or "").strip(),
                "camera": str(item.get("camera") or item.get("shot_size") or "").strip(),
                "action": str(item.get("action") or item.get("actions") or "").strip(),
                "character_ids": _safe_id_list(item.get("character_ids") or []),
                "scene_id": _safe_optional_id(item.get("scene_id")),
                "prop_ids": _safe_id_list(item.get("prop_ids") or []),
                "dialogues": _normalize_dialogues(item.get("dialogues") or _dialogues_in_range(transcript, start, end)),
                "sounds": list(item.get("sounds") or []),
                "confidence": float(item.get("confidence") or 0.5),
                "confirmed": bool(item.get("confirmed", False)),
                "evidence_paths": evidence,
            }
        )
    return shots


def _normalize_entities(items: Any, *, prefix: str, default_name: str, frames: list[AnalysisFrame]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        entity_id = _safe_id(str(item.get("id") or f"{prefix}_{index:02d}"))
        evidence = list(item.get("evidence_paths") or [])
        if not evidence:
            evidence = _evidence_from_timestamps(item.get("evidence_timestamps"), frames) or [frame.path for frame in frames[:3]]
        result.append(
            {
                "id": entity_id,
                "name": str(item.get("name") or default_name),
                "description": str(item.get("description") or item.get("source_hint") or "").strip(),
                "segment_indices": list(item.get("segment_indices") or []),
                "confidence": float(item.get("confidence") or 0.5),
                "confirmed": bool(item.get("confirmed", False)),
                "evidence_paths": evidence,
            }
        )
    return result


def _normalize_dialogues(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        dialogue = dict(item)
        if dialogue.get("speaker"):
            dialogue["speaker"] = _safe_optional_id(dialogue.get("speaker"))
        if dialogue.get("character_id"):
            dialogue["character_id"] = _safe_optional_id(dialogue.get("character_id"))
        result.append(dialogue)
    return result


def _normalize_voices(items: Any, *, transcript_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_speaker: dict[str, list[dict[str, Any]]] = {}
    for item in transcript_items:
        speaker = str(item.get("speaker") or "voice_unknown")
        by_speaker.setdefault(speaker, []).append(item)
    result: list[dict[str, Any]] = []
    if isinstance(items, list):
        for index, item in enumerate(items, start=1):
            if not isinstance(item, dict):
                continue
            voice_id = _safe_id(str(item.get("id") or item.get("speaker") or f"voice_{index:02d}"))
            speaker_key = str(item.get("speaker") or "").strip()
            segments = by_speaker.get(speaker_key, []) if speaker_key and speaker_key != "voice_unknown" else []
            result.append(
                {
                    "id": voice_id,
                    "name": str(item.get("name") or item.get("speaker") or voice_id),
                    "description": str(item.get("description") or "").strip(),
                    "character_id": _safe_optional_id(item.get("character_id")),
                    "confidence": float(item.get("confidence") or 0.5),
                    "confirmed": bool(item.get("confirmed", False)),
                    "sample_ranges": _sample_ranges(segments),
                    "transcript_segments": segments,
                }
            )
    for speaker, segments in by_speaker.items():
        voice_id = _safe_id(speaker)
        if any(item["id"] == voice_id for item in result):
            continue
        result.append(
            {
                "id": voice_id,
                "name": speaker,
                "description": "ASR 说话人候选；请人工确认对应角色。",
                "character_id": "",
                "confidence": 0.5,
                "confirmed": False,
                "sample_ranges": _sample_ranges(segments),
                "transcript_segments": segments,
            }
        )
    return result


def _attach_dialogue_voice_samples(
    *,
    voices: list[dict[str, Any]],
    shots: list[dict[str, Any]],
    transcript_items: list[dict[str, Any]],
    characters: list[dict[str, Any]] | None = None,
) -> None:
    voice_by_char = {_safe_optional_id(item.get("character_id")): item for item in voices if item.get("character_id")}
    char_ref_map = _character_reference_map(characters or [])
    used: set[int] = set()
    existing_by_voice: dict[int, set[tuple[float, float, str]]] = {}
    for voice in voices:
        key = id(voice)
        existing_by_voice[key] = {_segment_key(segment) for segment in voice.get("transcript_segments") or [] if isinstance(segment, dict)}
    for shot in shots:
        for dialogue in shot.get("dialogues") or []:
            if not isinstance(dialogue, dict):
                continue
            speaker_ref = _safe_optional_id(dialogue.get("speaker") or dialogue.get("character_id") or "")
            voice = voice_by_char.get(char_ref_map.get(speaker_ref, speaker_ref))
            if not voice:
                continue
            match_index = _best_transcript_match(str(dialogue.get("text") or ""), transcript_items, used)
            if match_index is None:
                continue
            used.add(match_index)
            segment = dict(transcript_items[match_index])
            voice_key = id(voice)
            segment_key = _segment_key(segment)
            if segment_key in existing_by_voice.setdefault(voice_key, set()):
                continue
            existing_by_voice[voice_key].add(segment_key)
            voice.setdefault("transcript_segments", []).append(segment)
    for voice in voices:
        segments = voice.get("transcript_segments") or []
        if segments:
            voice["sample_ranges"] = _sample_ranges(segments)


def _segment_key(segment: dict[str, Any]) -> tuple[float, float, str]:
    return (
        round(float(segment.get("start") or 0), 2),
        round(float(segment.get("end") or 0), 2),
        _clean_text(str(segment.get("text") or "")),
    )


def _character_reference_map(characters: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for character in characters:
        if not isinstance(character, dict):
            continue
        character_id = _safe_optional_id(character.get("id"))
        if not character_id:
            continue
        result[character_id] = character_id
        name = _safe_optional_id(character.get("name"))
        if name:
            result[name] = character_id
        for alias in character.get("aliases") or []:
            alias_id = _safe_optional_id(alias)
            if alias_id:
                result[alias_id] = character_id
    return result


def _best_transcript_match(text: str, transcript_items: list[dict[str, Any]], used: set[int]) -> int | None:
    target = _clean_text(text)
    if not target:
        return None
    best_index: int | None = None
    best_score = 0.0
    for index, item in enumerate(transcript_items):
        if index in used:
            continue
        candidate = _clean_text(str(item.get("text") or ""))
        if not candidate:
            continue
        if target == candidate:
            score = 1.0
        elif target in candidate or candidate in target:
            score = min(len(target), len(candidate)) / max(len(target), len(candidate))
        else:
            score = SequenceMatcher(None, target, candidate).ratio()
        if score > best_score:
            best_index = index
            best_score = score
    return best_index if best_score >= 0.72 else None


def _clean_text(value: str) -> str:
    return "".join(char.lower() for char in value if char.isalnum())


def _normalize_doubao_asr_responses(responses: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for item in responses:
        candidates.extend(_walk_dicts(item))
    utterances: list[dict[str, Any]] = []
    text = ""
    for item in candidates:
        result = item.get("result") if isinstance(item.get("result"), dict) else item
        if not isinstance(result, dict):
            continue
        if isinstance(result.get("text"), str) and result["text"].strip():
            text = result["text"].strip()
        raw_utterances = result.get("utterances") or result.get("utterance") or result.get("segments")
        if isinstance(raw_utterances, list):
            for index, utt in enumerate(raw_utterances):
                if not isinstance(utt, dict):
                    continue
                if utt.get("definite") is False:
                    continue
                utt_text = str(utt.get("text") or utt.get("utterance") or "").strip()
                if not utt_text:
                    continue
                start = _asr_time_to_seconds(utt.get("start_time", utt.get("start", 0)))
                end = _asr_time_to_seconds(utt.get("end_time", utt.get("end", start)))
                utterances.append(
                    {
                        "id": str(utt.get("id") or f"utt_{len(utterances):03d}"),
                        "start": start,
                        "end": max(end, start),
                        "text": utt_text,
                        "speaker": str(utt.get("speaker") or utt.get("speaker_id") or "voice_unknown"),
                    }
                )
    utterances = _dedupe_asr_utterances(utterances)
    if not utterances and text:
        utterances = [{"id": "utt_000", "start": 0.0, "end": 0.0, "text": text, "speaker": "voice_unknown"}]
    if not text:
        text = " ".join(item["text"] for item in utterances)
    return {"text": text, "segments": utterances, "raw": responses}


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        result.append(value)
        for child in value.values():
            result.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(_walk_dicts(child))
    return result


def _asr_time_to_seconds(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number >= 100:
        return number / 1000.0
    return number


def _dedupe_asr_utterances(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_time: dict[tuple[str, float, float], dict[str, Any]] = {}
    for item in items:
        key = (str(item.get("speaker") or ""), round(float(item.get("start") or 0), 2), round(float(item.get("end") or 0), 2))
        current = by_time.get(key)
        if current is None or len(str(item.get("text") or "")) > len(str(current.get("text") or "")):
            by_time[key] = item
    ordered = sorted(by_time.values(), key=lambda item: (float(item.get("start") or 0), float(item.get("end") or 0), -len(str(item.get("text") or ""))))
    result: list[dict[str, Any]] = []
    for item in ordered:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = float(item.get("start") or 0)
        skip = False
        for kept in result[-8:]:
            kept_text = str(kept.get("text") or "").strip()
            kept_start = float(kept.get("start") or 0)
            if abs(start - kept_start) <= 0.08 and (text == kept_text or text.startswith(kept_text) or kept_text.startswith(text)):
                if len(text) > len(kept_text):
                    kept.update(item)
                skip = True
                break
        if not skip:
            item["id"] = f"utt_{len(result):03d}"
            result.append(item)
    return result


def _doubao_asr_available(config: AppConfig) -> bool:
    return bool(config.doubao_asr_app_id and config.doubao_asr_access_token)


def _use_doubao_asr(*, config: AppConfig, asr_model: str) -> bool:
    provider = (config.asr_provider or "").lower().replace("-", "_")
    model = (asr_model or config.asr_model or "").lower().replace("-", "_")
    return _doubao_asr_available(config) and (provider in {"doubao", "doubao_streaming", "seedasr"} or model in {"doubao_streaming_2.0", "doubao_streaming_2_0", "seedasr_2.0", "seedasr_2_0"})


def _skeleton_visual_analysis(*, frames: list[AnalysisFrame], transcript: dict[str, Any], duration: float) -> dict[str, Any]:
    items = _normalize_transcript(transcript)
    return {
        "synopsis": "本地骨架分析：未调用 Ark VLM/ASR，请人工补充剧情、角色、场景和道具。",
        "shots": _skeleton_shots(frames=frames, transcript=items, duration=duration),
        "characters": [],
        "source_scenes": [{"id": "scene_01", "name": "源视频主要场景", "description": "本地骨架场景候选。", "confidence": 0.5}],
        "props": [],
        "voices": [],
    }


def _skeleton_shots(*, frames: list[AnalysisFrame], transcript: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    shots: list[dict[str, Any]] = []
    if not frames:
        return shots
    for index, frame in enumerate(frames):
        start = frame.timestamp
        end = frames[index + 1].timestamp if index + 1 < len(frames) else duration
        shots.append(
            {
                "id": f"1-{index + 1}",
                "start": start,
                "end": end,
                "summary": "请人工补充该分场剧情。",
                "description": "全景-请人工检查画面人物、动作、场景和道具。",
                "scene_description": "请人工补充场景环境。",
                "camera": "全景",
                "action": "请人工补充动作。",
                "dialogues": _dialogues_in_range(transcript, start, end),
                "sounds": [],
                "confidence": 0.3,
                "confirmed": False,
                "evidence_paths": [frame.path],
            }
        )
    return shots


def _analysis_prompt(*, frames: list[AnalysisFrame], transcript: dict[str, Any], duration: float) -> str:
    frame_lines = "\n".join(f"- {frame.id}: {frame.timestamp:.2f}s, path={frame.path}" for frame in frames)
    transcript_text = json.dumps(_normalize_transcript(transcript), ensure_ascii=False)
    return f"""请分析一个短剧原视频，把它转为可供视频换角色/换场景前置检查使用的结构化 JSON。
视频总时长：{duration:.2f}s。
关键帧列表：
{frame_lines}
ASR 转写候选：
{transcript_text}

只输出 JSON，字段如下：
{{
  "synopsis": "全片剧情摘要",
  "shots": [{{"id":"1-1","start":0,"end":2.0,"summary":"","description":"","scene_description":"","camera":"近景/中景/全景/特写","action":"","character_ids":[],"scene_id":"","prop_ids":[],"dialogues":[{{"speaker":"","text":"","emotion":"","type":"dialogue/os/narration"}}],"sounds":[],"confidence":0.0,"evidence_paths":[]}}],
  "characters": [{{"id":"","name":"","description":"","evidence_timestamps":[],"confidence":0.0,"confirmed":false}}],
  "source_scenes": [{{"id":"","name":"","description":"","evidence_timestamps":[],"confidence":0.0,"confirmed":false}}],
  "props": [{{"id":"","name":"","description":"","evidence_timestamps":[],"confidence":0.0,"confirmed":false}}],
  "voices": [{{"id":"","name":"","description":"","character_id":"","speaker":"","confidence":0.0,"confirmed":false}}]
}}
要求：剧本分场接近“1-1/1-2”格式；角色、场景、道具必须便于人工复核；不要把目标替换设定写入这里，只描述原视频。"""


def _dialogues_in_range(transcript: list[dict[str, Any]], start: float, end: float) -> list[dict[str, Any]]:
    dialogues = []
    for item in transcript:
        if float(item.get("start") or 0) < end and float(item.get("end") or 0) >= start:
            dialogues.append({"speaker": item.get("speaker") or "未知角色", "text": item.get("text") or "", "type": "dialogue"})
    return dialogues


def _nearest_frame_paths(frames: list[AnalysisFrame], start: float, end: float) -> list[str]:
    selected = [frame.path for frame in frames if start <= frame.timestamp <= end]
    if selected:
        return selected[:3]
    if not frames:
        return []
    center = (start + end) / 2
    return [min(frames, key=lambda frame: abs(frame.timestamp - center)).path]


def _evidence_from_timestamps(value: Any, frames: list[AnalysisFrame]) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        try:
            timestamp = float(item)
        except (TypeError, ValueError):
            continue
        if frames:
            result.append(min(frames, key=lambda frame: abs(frame.timestamp - timestamp)).path)
    return result


def _review_items(*, characters: list[dict[str, Any]], scenes: list[dict[str, Any]], props: list[dict[str, Any]], voices: list[dict[str, Any]], shots: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for label, collection in [("源角色", characters), ("源场景", scenes), ("源道具", props), ("源声音", voices)]:
        for item in collection:
            if not item.get("confirmed"):
                items.append(f"{label} {item.get('id')} 未人工确认。")
    for shot in shots:
        if float(shot.get("confidence") or 0) < 0.55:
            items.append(f"分场 {shot.get('id')} 置信度较低，需要人工检查。")
    return items


def _low_confidence_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in items:
        confidence = float(item.get("confidence") or 0)
        if confidence < 0.6:
            result.append({"id": item.get("id"), "confidence": confidence, "reason": "confidence_below_0.6"})
    return result


def _sample_ranges(segments: list[dict[str, Any]]) -> list[dict[str, float]]:
    ranges: list[dict[str, float]] = []
    for item in segments[:3]:
        start = float(item.get("start") or 0)
        end = float(item.get("end") or start + 2)
        ranges.append({"start": start, "end": max(end, start + 0.2)})
    return ranges


def _index_entries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "confidence": item.get("confidence"),
                "confirmed": item.get("confirmed", False),
                "profile_path": item.get("profile_path"),
            }
        )
    return result


def _skeleton_transcript() -> dict[str, Any]:
    return {"segments": [], "text": ""}


def _json_or_error(response: httpx.Response, *, label: str) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise ArkError(f"{label} 返回非 JSON：HTTP {response.status_code}") from exc
    if response.status_code >= 400:
        message = data.get("message") or data.get("error") or response.text
        raise ArkError(f"{label} HTTP {response.status_code}：{message}", request_id=response.headers.get("x-request-id"))
    return data


def _response_format_unsupported(response: httpx.Response) -> bool:
    try:
        data = response.json()
    except ValueError:
        return False
    message = str(data.get("message") or data.get("error") or data)
    return "response_format" in message and ("not valid" in message or "not supported" in message)


def _parse_json_text(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            return json.loads(value[start : end + 1])
        raise


def _extract_message_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [str(item.get("text") or "") for item in content if isinstance(item, dict)]
            return "\n".join(part for part in parts if part)
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    if isinstance(data.get("content"), str):
        return data["content"]
    return json.dumps(data, ensure_ascii=False)


def _safe_id(value: str) -> str:
    allowed = []
    for char in value.strip().lower().replace("-", "_").replace(" ", "_"):
        if char.isalnum() or char == "_":
            allowed.append(char)
    return "".join(allowed) or "auto_id"


def _safe_optional_id(value: Any) -> str:
    text = str(value or "").strip()
    return _safe_id(text) if text else ""


def _safe_id_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [_safe_id(str(value)) for value in values if str(value or "").strip()]


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def _html_rel(from_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(from_dir.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
