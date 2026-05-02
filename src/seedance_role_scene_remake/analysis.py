"""Source-video analysis, script rendering, and review asset export."""

from __future__ import annotations

import html
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from seedance_role_scene_remake.config import AppConfig
from seedance_role_scene_remake.errors import ArkError, PipelineError
from seedance_role_scene_remake.ffmpeg import (
    detect_scene_timestamps,
    extract_audio_clip,
    extract_audio_for_asr,
    extract_frame_at,
    get_video_duration,
    image_to_data_url,
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
        except httpx.HTTPError as exc:
            raise ArkError(f"视频理解提交失败：{exc}") from exc
        data = _json_or_error(response, label="视频理解")
        text = _extract_message_text(data)
        try:
            return json.loads(text)
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
    if not allow_skeleton:
        missing: list[str] = []
        if not config.api_key:
            missing.append("ARK_API_KEY")
        if not analysis_model:
            missing.append("SEEDANCE_ROLE_SCENE_ANALYSIS_MODEL 或 --analysis-model")
        if not asr_model:
            missing.append("SEEDANCE_ROLE_SCENE_ASR_MODEL 或 --asr-model")
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
    if has_audio and asr_model and config.api_key:
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
        extract_frame_at(video, output, timestamp)
        frames.append(AnalysisFrame(id=f"frame_{index:04d}", timestamp=timestamp, path=_relative(output, job_dir), kind="scene" if timestamp in timestamps else "sample"))
    return frames


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
                "character_ids": list(item.get("character_ids") or []),
                "scene_id": str(item.get("scene_id") or ""),
                "prop_ids": list(item.get("prop_ids") or []),
                "dialogues": list(item.get("dialogues") or _dialogues_in_range(transcript, start, end)),
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
            segments = by_speaker.get(str(item.get("speaker") or voice_id), [])
            result.append(
                {
                    "id": voice_id,
                    "name": str(item.get("name") or item.get("speaker") or voice_id),
                    "description": str(item.get("description") or "").strip(),
                    "character_id": str(item.get("character_id") or ""),
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
