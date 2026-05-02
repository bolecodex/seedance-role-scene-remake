"""Manifest contract shared by split, upload, remake, merge, verify, and repair."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from seedance_role_scene_remake.errors import ManifestError

MANIFEST_VERSION = 5


@dataclass
class PreparationSpec:
    status: str = "unprepared"
    created_at: str | None = None
    approved_at: str | None = None
    notes: str = ""
    contact_sheet_path: str | None = None
    required_review_items: list[str] = field(default_factory=list)


@dataclass
class LanguagePolicy:
    source_language: str = "auto"
    target_language: str = "preserve_source"
    spoken_language: str = "preserve_source"
    translate_dialogue: bool = False
    translate_visible_text: bool = False
    subtitle_policy: str = "preserve_if_present"
    approved: bool = False


@dataclass
class StoryBible:
    synopsis: str = ""
    beats: list[dict[str, Any]] = field(default_factory=list)
    locked_constraints: list[str] = field(default_factory=list)


@dataclass
class SourceAnalysisSpec:
    status: str = "missing"
    analysis_json_path: str | None = None
    script_path: str | None = None
    script_json_path: str | None = None
    script_quality_path: str | None = None
    index_path: str | None = None
    character_index: list[dict[str, Any]] = field(default_factory=list)
    scene_index: list[dict[str, Any]] = field(default_factory=list)
    prop_index: list[dict[str, Any]] = field(default_factory=list)
    voice_index: list[dict[str, Any]] = field(default_factory=list)
    low_confidence_items: list[dict[str, Any]] = field(default_factory=list)
    review_items: list[str] = field(default_factory=list)
    backend: str = ""


@dataclass
class PersonCandidateSpec:
    id: str
    segment_index: int
    timestamp: float
    image_path: str
    bbox: list[int] = field(default_factory=list)
    crop_type: str = "unknown"
    quality: str = "unknown"
    needs_better_reference: bool = True
    character_id: str | None = None
    appearance_variant_id: str | None = None
    note: str = ""


@dataclass
class SceneCandidateSpec:
    id: str
    segment_index: int
    timestamp: float
    image_path: str
    scene_id: str | None = None
    note: str = ""


@dataclass
class ReferenceAsset:
    slot: str
    kind: str
    role: str
    uri: str | None = None
    path: str | None = None
    bound_type: str = ""
    bound_id: str = ""
    note: str = ""


@dataclass
class AppearanceVariantSpec:
    id: str
    source_hint: str = ""
    image_path: str | None = None
    image_uri: str | None = None
    prompt: str = ""
    reference_paths: list[str] = field(default_factory=list)
    reference_uris: list[str] = field(default_factory=list)
    segment_indices: list[int] = field(default_factory=list)
    approved: bool = False


@dataclass
class VoiceSpec:
    id: str
    character_id: str = ""
    source_hint: str = ""
    prompt: str = ""
    reference_path: str | None = None
    reference_uri: str | None = None
    mode: str = "generated_prompt"
    segment_indices: list[int] = field(default_factory=list)
    approved: bool = False


@dataclass
class CharacterSpec:
    id: str
    source_hint: str = ""
    image_path: str | None = None
    image_uri: str | None = None
    prompt: str = ""
    voice_prompt: str = ""
    voice_reference_path: str | None = None
    voice_reference_uri: str | None = None
    voice_id: str | None = None
    reference_paths: list[str] = field(default_factory=list)
    reference_uris: list[str] = field(default_factory=list)
    segment_indices: list[int] = field(default_factory=list)
    appearance_variants: list[AppearanceVariantSpec] = field(default_factory=list)
    approved: bool = False


@dataclass
class SceneSpec:
    id: str
    source_hint: str = ""
    image_path: str | None = None
    image_uri: str | None = None
    prompt: str = ""
    reference_paths: list[str] = field(default_factory=list)
    reference_uris: list[str] = field(default_factory=list)
    continuity_anchor_paths: list[str] = field(default_factory=list)
    segment_indices: list[int] = field(default_factory=list)
    approved: bool = False


@dataclass
class SegmentEntry:
    index: int
    start: float
    duration: float
    reference_duration: float
    generation_duration: int
    source_path: str
    frame_path: str
    reference_path: str
    source_audio_path: str | None = None
    reference_uri: str | None = None
    source_audio_uri: str | None = None
    character_ids: list[str] = field(default_factory=list)
    scene_ids: list[str] = field(default_factory=list)
    character_variant_ids: list[str] = field(default_factory=list)
    voice_ids: list[str] = field(default_factory=list)
    shot_id: str | None = None
    scene_cluster_id: str | None = None
    continuity_anchor_path: str | None = None
    generated_url: str | None = None
    remade_path: str | None = None
    generated_audio_path: str | None = None
    aligned_audio_path: str | None = None
    task_id: str | None = None
    status: str = "pending"
    attempts: int = 0
    error: str | None = None
    audio_report: dict[str, Any] = field(default_factory=dict)


@dataclass
class Manifest:
    version: int = MANIFEST_VERSION
    source: str = ""
    source_ratio: str = "auto"
    target_ratio: str = "auto"
    prompt: str = ""
    model: str = "doubao-seedance-2-0-260128"
    resolution: str = "720p"
    segment_seconds: int = 15
    dialogue_fidelity: str = "strict"
    audio_mode: str = "generated"
    generate_audio: bool = True
    keep_generated_audio: bool = True
    preparation: PreparationSpec = field(default_factory=PreparationSpec)
    story_bible: StoryBible = field(default_factory=StoryBible)
    source_analysis: SourceAnalysisSpec = field(default_factory=SourceAnalysisSpec)
    language_policy: LanguagePolicy = field(default_factory=LanguagePolicy)
    person_candidates: list[PersonCandidateSpec] = field(default_factory=list)
    scene_candidates: list[SceneCandidateSpec] = field(default_factory=list)
    reference_assets: list[ReferenceAsset] = field(default_factory=list)
    voice_registry: list[VoiceSpec] = field(default_factory=list)
    characters: list[CharacterSpec] = field(default_factory=list)
    scenes: list[SceneSpec] = field(default_factory=list)
    repair_round: int = 0
    repair_history: list[dict[str, Any]] = field(default_factory=list)
    segments: list[SegmentEntry] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not path.exists():
            raise ManifestError(f"Manifest 不存在：{path}")
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ManifestError(f"Manifest JSON 无效：{exc}") from exc
        version = int(raw.get("version", 0))
        if version not in (1, 2, 3, 4, MANIFEST_VERSION):
            raise ManifestError(f"不支持的 manifest 版本：{version}")

        raw["preparation"] = _load_dataclass(PreparationSpec, raw.get("preparation", {}))
        raw["story_bible"] = _load_dataclass(StoryBible, raw.get("story_bible", {}))
        raw["source_analysis"] = _load_dataclass(SourceAnalysisSpec, raw.get("source_analysis", {}))
        raw["language_policy"] = _load_dataclass(LanguagePolicy, raw.get("language_policy", {}))
        raw["person_candidates"] = _load_dataclasses(PersonCandidateSpec, raw.get("person_candidates", []))
        raw["scene_candidates"] = _load_dataclasses(SceneCandidateSpec, raw.get("scene_candidates", []))
        raw["reference_assets"] = _load_dataclasses(ReferenceAsset, raw.get("reference_assets", []))
        raw["voice_registry"] = _load_dataclasses(VoiceSpec, raw.get("voice_registry", []))
        raw["characters"] = _load_characters(raw.get("characters", []))
        raw["scenes"] = _load_dataclasses(SceneSpec, raw.get("scenes", []))
        raw["segments"] = _load_dataclasses(SegmentEntry, raw.get("segments", []))

        if version in (1, 2, 3, 4):
            raw["version"] = MANIFEST_VERSION
            raw.setdefault("dialogue_fidelity", "strict")
            raw.setdefault("audio_mode", "generated")
            raw.setdefault("generate_audio", True)
            raw.setdefault("keep_generated_audio", True)
            raw.setdefault("preparation", PreparationSpec())
            raw.setdefault("story_bible", StoryBible())
            raw.setdefault("source_analysis", SourceAnalysisSpec())
            raw.setdefault("language_policy", LanguagePolicy())
            raw.setdefault("person_candidates", [])
            raw.setdefault("scene_candidates", [])
            raw.setdefault("reference_assets", [])
            raw.setdefault("voice_registry", [])
            raw.setdefault("characters", [])
            raw.setdefault("scenes", [])
            raw.setdefault("repair_round", 0)
            raw.setdefault("repair_history", [])

        manifest_fields = {item.name for item in fields(cls)}
        raw = {key: value for key, value in raw.items() if key in manifest_fields}
        return cls(**raw)

    def succeeded_segments(self) -> list[SegmentEntry]:
        return [seg for seg in self.segments if seg.status == "succeeded"]

    def pending_segments(self) -> list[SegmentEntry]:
        return [seg for seg in self.segments if seg.status in ("pending", "failed", "running")]

    def character_map(self) -> dict[str, CharacterSpec]:
        return {item.id: item for item in self.characters}

    def scene_map(self) -> dict[str, SceneSpec]:
        return {item.id: item for item in self.scenes}

    def voice_map(self) -> dict[str, VoiceSpec]:
        return {item.id: item for item in self.voice_registry}

    def variant_map(self) -> dict[str, tuple[CharacterSpec, AppearanceVariantSpec]]:
        mapping: dict[str, tuple[CharacterSpec, AppearanceVariantSpec]] = {}
        for char in self.characters:
            for variant in char.appearance_variants:
                mapping[variant.id] = (char, variant)
                mapping[f"{char.id}:{variant.id}"] = (char, variant)
        return mapping

    def requires_preparation_approval(self) -> bool:
        return self.preparation.status != "approved"


def _load_dataclasses(cls: type, items: list[dict[str, Any]]) -> list[Any]:
    if not isinstance(items, list):
        return []
    valid = {item.name for item in fields(cls)}
    return [cls(**{key: value for key, value in item.items() if key in valid}) for item in items]


def _load_dataclass(cls: type, item: dict[str, Any] | Any) -> Any:
    if isinstance(item, cls):
        return item
    if not isinstance(item, dict):
        return cls()
    valid = {field_item.name for field_item in fields(cls)}
    return cls(**{key: value for key, value in item.items() if key in valid})


def _load_characters(items: list[dict[str, Any]]) -> list[CharacterSpec]:
    if not isinstance(items, list):
        return []
    chars: list[CharacterSpec] = []
    valid = {item.name for item in fields(CharacterSpec)}
    for item in items:
        raw = {key: value for key, value in item.items() if key in valid}
        raw["appearance_variants"] = _load_dataclasses(AppearanceVariantSpec, item.get("appearance_variants", []))
        chars.append(CharacterSpec(**raw))
    return chars


def spec_template(video: Path) -> str:
    return f"""# seedance-role-scene-remake spec
source: "{video}"
prompt: "整体电影感自然写实，剧情、动作、镜头、剪辑节奏和对白内容保持不变。"
dialogue_fidelity: "strict"
audio_mode: "generated"
generate_audio: true
preparation:
  status: "draft"
story_bible:
  synopsis: ""
  beats: []
source_analysis:
  status: "missing"
  analysis_json_path: ""
  script_path: ""
language_policy:
  source_language: "auto"
  target_language: "preserve_source"
  spoken_language: "preserve_source"
  translate_dialogue: false
  translate_visible_text: false
  subtitle_policy: "preserve_if_present"
  approved: false
characters:
  - id: "hero"
    source_hint: "原片主角"
    image_path: ""
    prompt: "替换为二十多岁短发青年，深色夹克，五官稳定，服装全片一致。"
    voice_prompt: "年轻男性，普通话自然口语，情绪和原片一致，台词逐字不改。"
    voice_reference_path: ""
    voice_id: "hero_voice"
    appearance_variants:
      - id: "hero_default"
        source_hint: "原片主角默认妆造"
        image_path: ""
        prompt: "目标主角默认发型、服装和妆容；跨片段保持一致。"
        approved: false
    approved: false
scenes:
  - id: "main_scene"
    source_hint: "原片主要场景"
    image_path: ""
    prompt: "替换为现代城市夜景，霓虹灯、湿润街面、真实电影光影。"
    approved: false
voice_registry:
  - id: "hero_voice"
    character_id: "hero"
    prompt: "年轻男性，普通话自然口语，情绪和原片一致。"
    approved: false
segments:
  default:
    characters: ["hero"]
    scenes: ["main_scene"]
    voices: ["hero_voice"]
"""


def load_spec(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ManifestError(f"Spec 不存在：{path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML subset emitted by init-spec without extra deps."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    root: dict[str, Any] = {}
    current_key: str | None = None
    current_item: dict[str, Any] | None = None
    nested_key: str | None = None
    nested_item: dict[str, Any] | None = None
    item_nested_key: str | None = None
    item_nested_item: dict[str, Any] | None = None

    for line in lines:
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            key, value = _split_yaml_pair(stripped)
            if value == "":
                root[key] = [] if key in {"characters", "scenes", "voice_registry"} else {}
                current_key = key
            else:
                root[key] = _yaml_value(value)
                current_key = None
            current_item = None
            nested_key = None
            nested_item = None
            item_nested_key = None
            item_nested_item = None
        elif indent == 2 and stripped.startswith("- "):
            if current_key not in {"characters", "scenes", "voice_registry"}:
                continue
            item_text = stripped[2:]
            key, value = _split_yaml_pair(item_text)
            current_item = {key: _yaml_value(value)}
            root[current_key].append(current_item)
            item_nested_key = None
            item_nested_item = None
        elif indent == 4 and current_item is not None:
            key, value = _split_yaml_pair(stripped)
            if value == "" and key in {"appearance_variants"}:
                current_item[key] = []
                item_nested_key = key
                item_nested_item = None
            else:
                current_item[key] = _yaml_value(value)
                item_nested_key = None
                item_nested_item = None
        elif indent == 6 and current_item is not None and item_nested_key and stripped.startswith("- "):
            item_text = stripped[2:]
            key, value = _split_yaml_pair(item_text)
            item_nested_item = {key: _yaml_value(value)}
            nested_list = current_item.get(item_nested_key)
            if isinstance(nested_list, list):
                nested_list.append(item_nested_item)
        elif indent == 8 and item_nested_item is not None:
            key, value = _split_yaml_pair(stripped)
            item_nested_item[key] = _yaml_value(value)
        elif indent == 2 and current_key in {"preparation", "language_policy", "story_bible", "source_analysis"}:
            key, value = _split_yaml_pair(stripped)
            if isinstance(root.get(current_key), dict):
                root[current_key][key] = _yaml_value(value)
        elif indent == 2 and current_key == "segments":
            key, value = _split_yaml_pair(stripped)
            root["segments"][key] = {} if value == "" else _yaml_value(value)
            nested_key = key
            nested_item = root["segments"][key] if isinstance(root["segments"].get(key), dict) else None
        elif indent == 4 and nested_item is not None:
            key, value = _split_yaml_pair(stripped)
            nested_item[key] = _yaml_value(value)
    return root


def _split_yaml_pair(text: str) -> tuple[str, str]:
    if ":" not in text:
        return text, ""
    key, value = text.split(":", 1)
    return key.strip(), value.strip()


def _yaml_value(value: str) -> Any:
    value = value.strip()
    if value == "":
        return ""
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value.replace("'", '"'))
        except json.JSONDecodeError:
            return [item.strip().strip('"').strip("'") for item in value[1:-1].split(",") if item.strip()]
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value
