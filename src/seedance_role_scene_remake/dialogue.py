"""Dialogue-timed segmentation helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def read_analysis(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def transcript_segments(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    transcript = analysis.get("transcript") or {}
    items = transcript.get("segments") if isinstance(transcript, dict) else []
    if not isinstance(items, list):
        return []
    speaker_by_text = _speaker_map_from_shots(analysis)
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or start)
        speaker = _safe_character_id(item.get("character_id") or item.get("speaker") or "")
        if not speaker or speaker.startswith("voice_"):
            speaker = speaker_by_text.get(_normalize_text(text), speaker)
        normalized.append(
            {
                "id": str(item.get("id") or f"utt_{index:03d}"),
                "start": max(0.0, start),
                "end": max(start, end),
                "text": text,
                "speaker": speaker or "voice_unknown",
            }
        )
    return sorted(normalized, key=lambda item: (float(item["start"]), float(item["end"])))


def dialogue_aligned_ranges(
    *,
    duration: float,
    transcript: list[dict[str, Any]],
    max_segment_seconds: float = 8.0,
    min_segment_seconds: float = 1.0,
) -> list[tuple[float, float]]:
    max_segment_seconds = max(1.0, min(15.0, float(max_segment_seconds)))
    min_segment_seconds = max(0.2, float(min_segment_seconds))
    duration = max(0.0, float(duration))
    if duration <= 0:
        return []
    ranges: list[tuple[float, float]] = []
    start = 0.0
    items = [item for item in transcript if float(item.get("end") or 0) > 0]
    while start < duration - 0.05:
        target = min(duration, start + max_segment_seconds)
        crossing = next(
            (
                item
                for item in items
                if float(item.get("start") or 0) < target < float(item.get("end") or 0)
            ),
            None,
        )
        if crossing:
            utterance_start = float(crossing.get("start") or start)
            utterance_end = float(crossing.get("end") or target)
            if utterance_start - start >= min_segment_seconds:
                target = utterance_start
            else:
                target = min(duration, utterance_end)
        if target - start < min_segment_seconds and target < duration:
            target = min(duration, start + min_segment_seconds)
        ranges.append((round(start, 3), round(target - start, 3)))
        start = target
    return ranges


def timings_for_range(transcript: list[dict[str, Any]], *, start: float, end: float) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in transcript:
        item_start = float(item.get("start") or 0)
        item_end = float(item.get("end") or item_start)
        if item_start < end and item_end > start:
            result.append(
                {
                    "id": item.get("id"),
                    "start": round(max(0.0, item_start - start), 3),
                    "end": round(max(0.0, min(item_end, end) - start), 3),
                    "absolute_start": round(item_start, 3),
                    "absolute_end": round(item_end, 3),
                    "speaker": item.get("speaker") or "voice_unknown",
                    "text": item.get("text") or "",
                }
            )
    return result


def shots_for_range(analysis: dict[str, Any], *, start: float, end: float) -> list[dict[str, Any]]:
    shots = analysis.get("shots") if isinstance(analysis.get("shots"), list) else []
    return [
        item
        for item in shots
        if _overlaps(float(item.get("start") or 0), float(item.get("end") or 0), start, end)
    ]


def character_ids_for_range(analysis: dict[str, Any], *, start: float, end: float) -> list[str]:
    ids: list[str] = []
    for shot in shots_for_range(analysis, start=start, end=end):
        for raw in shot.get("character_ids") or []:
            char_id = _safe_character_id(raw)
            if char_id and char_id not in ids:
                ids.append(char_id)
    return ids


def scene_ids_for_range(analysis: dict[str, Any], *, start: float, end: float) -> list[str]:
    ids: list[str] = []
    for shot in shots_for_range(analysis, start=start, end=end):
        scene_id = str(shot.get("scene_id") or "").strip()
        if scene_id and scene_id not in ids:
            ids.append(scene_id)
    return ids


def story_beats_from_analysis(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    beats: list[dict[str, Any]] = []
    for shot in analysis.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        beats.append(
            {
                "id": shot.get("id"),
                "start": shot.get("start"),
                "end": shot.get("end"),
                "summary": shot.get("summary") or shot.get("description") or "",
                "environment_detail": shot.get("environment_detail") or shot.get("scene_description") or "",
                "camera_plan": shot.get("camera_plan") or ([shot.get("camera")] if shot.get("camera") else []),
                "action_beats": shot.get("action_beats") or [],
                "character_states": shot.get("character_states") or [],
                "dialogues": shot.get("dialogues") or [],
                "characters": shot.get("character_ids") or [],
                "scene": shot.get("scene_id") or "",
            }
        )
    return beats


def _speaker_map_from_shots(analysis: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for shot in analysis.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        for dialogue in shot.get("dialogues") or []:
            if not isinstance(dialogue, dict):
                continue
            text = _normalize_text(str(dialogue.get("text") or ""))
            speaker = _safe_character_id(dialogue.get("character_id") or dialogue.get("speaker") or "")
            if text and speaker:
                mapping.setdefault(text, speaker)
    return mapping


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _safe_character_id(value: Any) -> str:
    text = str(value or "").strip()
    match = re.match(r"^(c\d+)", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).lower()
    return text


def _overlaps(start: float, end: float, seg_start: float, seg_end: float) -> bool:
    return max(start, seg_start) < min(end, seg_end)
