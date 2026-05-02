"""Verification report generation."""

from __future__ import annotations

import html
import json
from pathlib import Path

from seedance_role_scene_remake.ffmpeg import get_video_duration, has_audio
from seedance_role_scene_remake.manifest import Manifest
from seedance_role_scene_remake.preparation import preparation_issues


def build_quality_report(
    manifest: Manifest,
    *,
    job_dir: Path,
    audio_report: bool = False,
    continuity_report: bool = False,
    identity_report: bool = False,
    scene_report: bool = False,
    language_report: bool = False,
    voice_report: bool = False,
    target_report: bool = False,
) -> dict:
    segments: list[dict] = []
    issues: list[dict] = []
    variant_map = manifest.variant_map()
    variant_ids = set(variant_map)
    unique_variants = []
    seen_variants: set[str] = set()
    for _char, variant in variant_map.values():
        if variant.id in seen_variants:
            continue
        unique_variants.append(variant)
        seen_variants.add(variant.id)
    scene_ids = {scene.id for scene in manifest.scenes}
    scene_map = manifest.scene_map()
    voice_ids = set(manifest.voice_map())
    prep_issues = preparation_issues(manifest)
    for seg in manifest.segments:
        remade = job_dir / seg.remade_path if seg.remade_path else None
        audio = job_dir / seg.aligned_audio_path if seg.aligned_audio_path else None
        item = {
            "index": seg.index,
            "status": seg.status,
            "duration": seg.duration,
            "remade_path": seg.remade_path,
            "aligned_audio_path": seg.aligned_audio_path,
            "audio": {},
            "continuity": {},
            "identity": {},
            "scene": {},
            "language": {},
            "voice": {},
            "target": {},
        }
        if identity_report:
            missing = not seg.character_variant_ids
            invalid = [item for item in seg.character_variant_ids if item not in variant_ids]
            item["identity"] = {"issue": missing or bool(invalid), "character_variant_ids": seg.character_variant_ids, "invalid": invalid}
            if missing:
                issues.append({"segment": seg.index, "type": "identity", "reason": "missing character_variant_ids"})
            for variant_id in invalid:
                issues.append({"segment": seg.index, "type": "identity", "reason": f"unknown variant {variant_id}"})
        if scene_report:
            missing = not seg.scene_ids
            invalid = [item for item in seg.scene_ids if item not in scene_ids]
            item["scene"] = {"issue": missing or bool(invalid), "scene_ids": seg.scene_ids, "invalid": invalid}
            if missing:
                issues.append({"segment": seg.index, "type": "scene", "reason": "missing scene_ids"})
            for scene_id in invalid:
                issues.append({"segment": seg.index, "type": "scene", "reason": f"unknown scene {scene_id}"})
        if language_report:
            policy = manifest.language_policy
            item["language"] = {
                "issue": not policy.approved or not policy.spoken_language,
                "source_language": policy.source_language,
                "target_language": policy.target_language,
                "spoken_language": policy.spoken_language,
                "translate_dialogue": policy.translate_dialogue,
                "translate_visible_text": policy.translate_visible_text,
                "approved": policy.approved,
            }
            if not policy.approved:
                issues.append({"segment": seg.index, "type": "language", "reason": "language_policy not approved"})
            if not policy.spoken_language:
                issues.append({"segment": seg.index, "type": "language", "reason": "spoken_language missing"})
        if voice_report:
            missing = not seg.voice_ids
            invalid = [item for item in seg.voice_ids if item not in voice_ids]
            item["voice"] = {"issue": missing or bool(invalid), "voice_ids": seg.voice_ids, "invalid": invalid}
            if missing:
                issues.append({"segment": seg.index, "type": "voice", "reason": "missing voice_ids"})
            for voice_id in invalid:
                issues.append({"segment": seg.index, "type": "voice", "reason": f"unknown voice {voice_id}"})
        if target_report:
            missing_variants: list[str] = []
            prompt_only_variants: list[str] = []
            for variant_id in seg.character_variant_ids:
                found = variant_map.get(variant_id)
                if not found:
                    continue
                _char, variant = found
                if not (variant.image_path or variant.image_uri):
                    missing_variants.append(variant_id)
                elif not variant.image_uri:
                    prompt_only_variants.append(variant_id)
            missing_scenes: list[str] = []
            prompt_only_scenes: list[str] = []
            for scene_id in seg.scene_ids:
                scene = scene_map.get(scene_id)
                if not scene:
                    continue
                if not (scene.image_path or scene.image_uri):
                    missing_scenes.append(scene_id)
                elif not scene.image_uri:
                    prompt_only_scenes.append(scene_id)
            item["target"] = {
                "issue": bool(missing_variants or missing_scenes),
                "missing_variant_refs": missing_variants,
                "missing_scene_refs": missing_scenes,
                "local_variant_refs_need_upload": prompt_only_variants,
                "local_scene_refs_need_upload": prompt_only_scenes,
            }
            for variant_id in missing_variants:
                issues.append({"segment": seg.index, "type": "target", "reason": f"missing target variant reference {variant_id}"})
            for scene_id in missing_scenes:
                issues.append({"segment": seg.index, "type": "target", "reason": f"missing target scene reference {scene_id}"})
        if audio_report:
            if not audio or not audio.exists():
                item["audio"] = {"issue": True, "reason": "missing generated audio"}
                issues.append({"segment": seg.index, "type": "audio", "reason": "missing generated audio"})
            else:
                duration = get_video_duration(audio)
                delta = abs(duration - seg.duration)
                item["audio"] = {
                    "issue": delta > 0.25,
                    "duration": duration,
                    "expected_duration": seg.duration,
                    "delta": round(delta, 4),
                    "has_audio": has_audio(audio),
                }
                if delta > 0.25:
                    issues.append({"segment": seg.index, "type": "audio", "reason": "duration drift"})
        if continuity_report and remade and remade.exists():
            item["continuity"] = {"checked": True, "note": "visual continuity frame extraction is available in artifacts"}
        segments.append(item)
    return {
        "source": manifest.source,
        "preparation": {
            "status": manifest.preparation.status,
            "contact_sheet_path": manifest.preparation.contact_sheet_path,
            "issues": prep_issues,
        },
        "language_policy": {
            "source_language": manifest.language_policy.source_language,
            "target_language": manifest.language_policy.target_language,
            "spoken_language": manifest.language_policy.spoken_language,
            "translate_dialogue": manifest.language_policy.translate_dialogue,
            "translate_visible_text": manifest.language_policy.translate_visible_text,
            "approved": manifest.language_policy.approved,
        },
        "target_references": {
            "person_candidates": len(manifest.person_candidates),
            "unclassified_person_candidates": [
                item.id for item in manifest.person_candidates if not item.character_id or not item.appearance_variant_id
            ],
            "scene_candidates": len(manifest.scene_candidates),
            "unclassified_scene_candidates": [item.id for item in manifest.scene_candidates if not item.scene_id],
            "missing_variant_refs": [
                variant.id for variant in unique_variants if not (variant.image_path or variant.image_uri)
            ],
            "missing_scene_refs": [
                scene.id for scene in manifest.scenes if not (scene.image_path or scene.image_uri)
            ],
        },
        "dialogue_fidelity": manifest.dialogue_fidelity,
        "audio_mode": manifest.audio_mode,
        "generate_audio": manifest.generate_audio,
        "segments": segments,
        "issues": issues,
        "issue": bool(issues),
    }


def write_html_report(payload: dict, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in payload["segments"]:
        audio = item.get("audio") or {}
        identity = item.get("identity") or {}
        scene = item.get("scene") or {}
        language = item.get("language") or {}
        voice = item.get("voice") or {}
        target = item.get("target") or {}
        rows.append(
            "<tr>"
            f"<td>{item['index']:03d}</td>"
            f"<td>{html.escape(str(item['status']))}</td>"
            f"<td>{html.escape(str(item.get('remade_path') or '-'))}</td>"
            f"<td>{html.escape(str(item.get('aligned_audio_path') or '-'))}</td>"
            f"<td>{html.escape(json.dumps(identity, ensure_ascii=False))}</td>"
            f"<td>{html.escape(json.dumps(scene, ensure_ascii=False))}</td>"
            f"<td>{html.escape(json.dumps(language, ensure_ascii=False))}</td>"
            f"<td>{html.escape(json.dumps(voice, ensure_ascii=False))}</td>"
            f"<td>{html.escape(json.dumps(target, ensure_ascii=False))}</td>"
            f"<td>{html.escape(json.dumps(audio, ensure_ascii=False))}</td>"
            "</tr>"
        )
    body = "\n".join(rows)
    output.write_text(
        f"""<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>Seedance Role Scene Remake Report</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; }}
    th {{ background: #f5f5f5; text-align: left; }}
    code {{ white-space: pre-wrap; }}
  </style>
</head>
<body>
  <h1>视频换角色换场景报告</h1>
  <p>source: {html.escape(payload["source"])}</p>
  <p>preparation: {html.escape(str(payload.get("preparation", {}).get("status")))}</p>
  <p>audio_mode: {html.escape(str(payload["audio_mode"]))}, generate_audio: {payload["generate_audio"]}</p>
  <table>
    <thead><tr><th>片段</th><th>状态</th><th>生成视频</th><th>对齐音轨</th><th>角色</th><th>场景</th><th>语种</th><th>声音</th><th>目标参考</th><th>音频报告</th></tr></thead>
    <tbody>{body}</tbody>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )
    return output
