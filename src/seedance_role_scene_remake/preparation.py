"""Preparation-stage helpers for reviewable role, scene, voice, and language bibles."""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from seedance_role_scene_remake.ffmpeg import crop_image, extract_frame_at, get_video_duration, image_rgb_embedding
from seedance_role_scene_remake.manifest import (
    AppearanceVariantSpec,
    CharacterSpec,
    LanguagePolicy,
    Manifest,
    PersonCandidateSpec,
    PreparationSpec,
    SceneCandidateSpec,
    SceneSpec,
    StoryBible,
    VoiceSpec,
)


LOCKED_STORY_CONSTRAINTS = [
    "剧情事件、动作轨迹、人物站位、镜头运动、剪辑节奏保持不变。",
    "对白语义、对白顺序、停顿位置和情绪节奏保持不变。",
    "只替换已确认的角色形象、妆造、场景和角色声音，不新增人物或重写剧情。",
]


def enrich_preparation_draft(
    manifest: Manifest,
    *,
    job_dir: Path,
    source_language: str = "auto",
    target_language: str = "preserve_source",
    spoken_language: str = "preserve_source",
) -> Path:
    source_characters = list(manifest.characters)
    source_scenes = list(manifest.scenes)
    source_voice_registry = list(manifest.voice_registry)
    character_intent = _character_intent(manifest)
    scene_intent = _scene_intent(manifest)
    voice_intent = _voice_intent(manifest)
    prep_dir = job_dir / "preparation"
    keyframes_dir = prep_dir / "keyframes"
    people_dir = prep_dir / "person_candidates"
    scenes_dir = prep_dir / "scene_candidates"
    keyframes_dir.mkdir(parents=True, exist_ok=True)
    people_dir.mkdir(parents=True, exist_ok=True)
    scenes_dir.mkdir(parents=True, exist_ok=True)

    character_id = "character_candidate_1"
    voice_id = "voice_candidate_1"
    segment_indices: list[int] = []
    variants: list[AppearanceVariantSpec] = []
    scenes: list[SceneSpec] = []
    person_candidates: list[PersonCandidateSpec] = []
    scene_candidates: list[SceneCandidateSpec] = []
    beats: list[dict] = []

    for seg in manifest.segments:
        source = job_dir / seg.source_path
        segment_indices.append(seg.index)
        sampled = _sample_keyframes(source, keyframes_dir, seg.index)
        scene_refs: list[str] = []
        for sample in sampled:
            scene_image = scenes_dir / f"{seg.index:03d}_{sample['label']}.jpg"
            scene_image.write_bytes((job_dir / sample["path"]).read_bytes())
            rel_scene = _relative(scene_image, job_dir)
            scene_refs.append(rel_scene)
            scene_candidates.append(
                SceneCandidateSpec(
                    id=f"scene_candidate_{seg.index:03d}_{sample['label']}",
                    segment_index=seg.index,
                    timestamp=float(sample["timestamp"]),
                    image_path=rel_scene,
                    scene_id=f"scene_candidate_{seg.index:03d}",
                    note="全帧场景候选；仅用于归类源场景，不作为目标场景外观。",
                )
            )
            for crop in _person_candidate_crops(seg.index, sample["label"]):
                out = people_dir / f"{crop['id']}.jpg"
                crop_image(job_dir / sample["path"], out, crop["bbox"])
                rel_person = _relative(out, job_dir)
                person_candidates.append(
                    PersonCandidateSpec(
                        id=crop["id"],
                        segment_index=seg.index,
                        timestamp=float(sample["timestamp"]),
                        image_path=rel_person,
                        bbox=list(crop["bbox"]),
                        crop_type=str(crop["crop_type"]),
                        quality="heuristic_crop",
                        needs_better_reference=True,
                        note="启发式人物候选裁剪；自动分类后仅作为源角色/妆造证据，不作为目标人物参考图。",
                    )
                )

        scene_id = f"scene_candidate_{seg.index:03d}"
        scenes.append(
            SceneSpec(
                id=scene_id,
                source_hint=f"片段 {seg.index:03d} 的源场景候选；请 review 后合并跨片段同一场景。",
                reference_paths=scene_refs,
                continuity_anchor_paths=scene_refs[-1:],
                segment_indices=[seg.index],
            )
        )
        seg.scene_ids = [scene_id]
        seg.shot_id = f"shot_{seg.index:03d}"
        seg.scene_cluster_id = scene_id
        seg.continuity_anchor_path = scene_refs[-1] if scene_refs else None
        beats.append(
            {
                "segment": seg.index,
                "start": round(seg.start, 3),
                "duration": round(seg.duration, 3),
                "source_path": seg.source_path,
                "note": "请补充该片段剧情事件；生成时不得改写。",
            }
        )

    manifest.person_candidates = person_candidates
    manifest.scene_candidates = scene_candidates
    manifest.characters, manifest.voice_registry = _auto_classify_people(
        manifest,
        job_dir=job_dir,
        candidates=person_candidates,
        character_intent=character_intent,
        voice_intent=voice_intent,
        source_characters=source_characters,
        source_voice_registry=source_voice_registry,
    )
    manifest.scenes = _auto_classify_scenes(
        manifest,
        job_dir=job_dir,
        candidates=scene_candidates,
        scene_intent=scene_intent,
        source_scenes=source_scenes,
    )
    translate = target_language not in {"", "auto", "preserve_source"}
    manifest.language_policy = LanguagePolicy(
        source_language=source_language or "auto",
        target_language=target_language or "preserve_source",
        spoken_language=spoken_language or ("preserve_source" if not translate else target_language),
        translate_dialogue=translate,
        translate_visible_text=translate,
        subtitle_policy="preserve_if_present",
        approved=False,
    )
    manifest.story_bible = StoryBible(
        synopsis="请 review 后补充全片剧情摘要；生成时不得改写剧情。",
        beats=beats,
        locked_constraints=list(LOCKED_STORY_CONSTRAINTS),
    )
    contact_sheet = write_contact_sheet(manifest, job_dir=job_dir, output=prep_dir / "contact_sheet.html")
    manifest.preparation = PreparationSpec(
        status="draft",
        created_at=datetime.now().isoformat(timespec="seconds"),
        contact_sheet_path=_relative(contact_sheet, job_dir),
        required_review_items=preparation_issues(manifest),
    )
    return contact_sheet


def preparation_issues(manifest: Manifest) -> list[str]:
    issues: list[str] = []
    if manifest.preparation.status != "approved":
        issues.append("preparation.status 不是 approved，需要 review 后运行 approve。")
    analysis = manifest.source_analysis
    if analysis.analysis_json_path:
        if analysis.status != "reviewed":
            issues.append("source_analysis.status 不是 reviewed；请先检查 analysis/index.html、剧本、源角色、源场景、道具和声音样本。")
        for item in analysis.review_items:
            issues.append(f"source_analysis 待检查：{item}")
        for item in analysis.low_confidence_items:
            item_id = item.get("id") if isinstance(item, dict) else item
            issues.append(f"source_analysis 低置信度项：{item_id}")
    if not manifest.characters:
        issues.append("characters 为空；需要建立明确角色表。")
    if not manifest.scenes:
        issues.append("scenes 为空；需要建立明确场景表。")

    voice_ids = {voice.id for voice in manifest.voice_registry}
    for candidate in manifest.person_candidates:
        label = f"person_candidate:{candidate.id}"
        if not candidate.character_id:
            issues.append(f"{label} 未归类到 character_id。")
        if not candidate.appearance_variant_id:
            issues.append(f"{label} 未归类到 appearance_variant_id。")
        if candidate.needs_better_reference:
            issues.append(f"{label} 标记 needs_better_reference，不能作为完整目标参考。")
    for candidate in manifest.scene_candidates:
        if not candidate.scene_id:
            issues.append(f"scene_candidate:{candidate.id} 未归类到 scene_id。")
    for char in manifest.characters:
        label = f"character:{char.id}"
        if not char.approved:
            issues.append(f"{label} 未 approved。")
        if not (char.prompt or char.image_path or char.image_uri):
            issues.append(f"{label} 缺少目标角色形象 prompt 或目标角色图。")
        if not char.appearance_variants:
            issues.append(f"{label} 缺少 appearance_variants，无法保持妆造一致。")
        if char.voice_id and char.voice_id not in voice_ids:
            issues.append(f"{label} voice_id={char.voice_id} 不存在于 voice_registry。")
        if not (char.voice_id or char.voice_prompt or char.voice_reference_path or char.voice_reference_uri):
            issues.append(f"{label} 缺少稳定音色绑定。")
        for variant in char.appearance_variants:
            variant_label = f"{label}/variant:{variant.id}"
            if not variant.approved:
                issues.append(f"{variant_label} 未 approved。")
            if not (variant.prompt or variant.image_path or variant.image_uri):
                issues.append(f"{variant_label} 缺少目标妆造 prompt 或目标妆造图。")
            if not (variant.image_path or variant.image_uri):
                issues.append(f"{variant_label} 缺少目标角色参考图；不能只靠源视频和文字 prompt。")

    for scene in manifest.scenes:
        label = f"scene:{scene.id}"
        if not scene.approved:
            issues.append(f"{label} 未 approved。")
        if not (scene.prompt or scene.image_path or scene.image_uri):
            issues.append(f"{label} 缺少目标场景 prompt 或目标场景图。")
        if not (scene.image_path or scene.image_uri):
            issues.append(f"{label} 缺少目标场景参考图；不能只靠源视频和文字 prompt。")

    for voice in manifest.voice_registry:
        label = f"voice:{voice.id}"
        if not voice.approved:
            issues.append(f"{label} 未 approved。")
        if not (voice.prompt or voice.reference_path or voice.reference_uri):
            issues.append(f"{label} 缺少目标音色 prompt 或音色参考。")

    lang = manifest.language_policy
    if not lang.approved:
        issues.append("language_policy 未 approved。")
    if not lang.source_language:
        issues.append("language_policy.source_language 为空。")
    if not lang.target_language:
        issues.append("language_policy.target_language 为空。")
    if not lang.spoken_language:
        issues.append("language_policy.spoken_language 为空。")
    target_translates = lang.target_language not in {"", "auto", "preserve_source"}
    if target_translates and not (lang.translate_dialogue and lang.translate_visible_text):
        issues.append("指定目标语种时 translate_dialogue 和 translate_visible_text 都必须为 true。")

    variant_ids = set(manifest.variant_map())
    scene_ids = {scene.id for scene in manifest.scenes}
    for seg in manifest.segments:
        prefix = f"segment:{seg.index:03d}"
        if not seg.character_variant_ids:
            issues.append(f"{prefix} 缺少 character_variant_ids。")
        for variant_id in seg.character_variant_ids:
            if variant_id not in variant_ids:
                issues.append(f"{prefix} 绑定了不存在的角色妆造变体：{variant_id}。")
        for candidate in manifest.person_candidates:
            if candidate.segment_index == seg.index and (not candidate.character_id or not candidate.appearance_variant_id):
                issues.append(f"{prefix} 有未归类人物候选：{candidate.id}。")
        if not seg.scene_ids:
            issues.append(f"{prefix} 缺少 scene_ids。")
        for scene_id in seg.scene_ids:
            if scene_id not in scene_ids:
                issues.append(f"{prefix} 绑定了不存在的场景：{scene_id}。")
        if not seg.voice_ids:
            issues.append(f"{prefix} 缺少 voice_ids。")
        for voice_id in seg.voice_ids:
            if voice_id not in voice_ids:
                issues.append(f"{prefix} 绑定了不存在的声音：{voice_id}。")
    return issues


def _auto_classify_people(
    manifest: Manifest,
    *,
    job_dir: Path,
    candidates: list[PersonCandidateSpec],
    character_intent: str,
    voice_intent: str,
    source_characters: list[CharacterSpec],
    source_voice_registry: list[VoiceSpec],
) -> tuple[list[CharacterSpec], list[VoiceSpec]]:
    slot_order = ["left_half_body", "center_half_body", "right_half_body"]
    by_slot: dict[str, list[PersonCandidateSpec]] = {slot: [] for slot in slot_order}
    for candidate in candidates:
        by_slot.setdefault(candidate.crop_type, []).append(candidate)

    characters: list[CharacterSpec] = []
    voices: list[VoiceSpec] = []
    voice_by_id = {voice.id: voice for voice in source_voice_registry}
    used_source_chars = _use_source_characters_for_slots(source_characters)
    for slot_index, slot in enumerate([item for item in slot_order if by_slot.get(item)]):
        source_char = used_source_chars[slot_index] if slot_index < len(used_source_chars) else None
        char_id = _safe_id(source_char.id) if source_char else f"character_auto_{slot.split('_')[0]}"
        prompt = _joined(
            source_char.prompt if source_char else "",
            character_intent,
            "保留该源角色的年龄层、性别呈现、人物关系、表情强弱、站位和动作时序；目标外观按任务要求替换。",
        )
        segment_records = _segment_embedding_records(by_slot[slot], job_dir=job_dir)
        clusters = _cluster_records(segment_records, threshold=0.18)
        variants: list[AppearanceVariantSpec] = []
        for cluster_index, cluster in enumerate(clusters, start=1):
            segment_ids = sorted({int(record["segment_index"]) for record in cluster})
            variant_id = f"{char_id}_look_{cluster_index:02d}"
            cluster_candidates = [candidate for candidate in by_slot[slot] if candidate.segment_index in segment_ids]
            for candidate in cluster_candidates:
                candidate.character_id = char_id
                candidate.appearance_variant_id = variant_id
                candidate.needs_better_reference = False
                candidate.note = "已自动归类为源角色/妆造候选；仍需使用 target_refs 下的目标参考图进行生成。"
            source_variant = _source_variant_for_segments(source_char, segment_ids) if source_char else None
            variants.append(
                AppearanceVariantSpec(
                    id=variant_id,
                    source_hint=f"自动分类：{_slot_label(slot)}，覆盖片段 {', '.join(f'{idx:03d}' for idx in segment_ids)}。",
                    image_path=(source_variant.image_path if source_variant else None) or (source_char.image_path if source_char else None),
                    image_uri=(source_variant.image_uri if source_variant else None) or (source_char.image_uri if source_char else None),
                    prompt=_joined(
                        source_variant.prompt if source_variant else "",
                        source_char.prompt if source_char else "",
                        character_intent,
                        f"该妆造变体对应源视频片段 {', '.join(f'{idx:03d}' for idx in segment_ids)}；若源角色换发型、服装或妆容，目标角色也同步换为一致的新妆造。",
                    ),
                    reference_paths=[item.image_path for item in cluster_candidates],
                    segment_indices=segment_ids,
                    approved=False,
                )
            )
        voice_id = source_char.voice_id if source_char and source_char.voice_id else f"{char_id}_voice"
        source_voice = voice_by_id.get(voice_id)
        characters.append(
            CharacterSpec(
                id=char_id,
                source_hint=f"自动分类：{_slot_label(slot)} 的源人物候选。",
                image_path=source_char.image_path if source_char else None,
                image_uri=source_char.image_uri if source_char else None,
                prompt=prompt,
                voice_prompt=(source_char.voice_prompt if source_char else "") or voice_intent,
                voice_reference_path=source_char.voice_reference_path if source_char else None,
                voice_reference_uri=source_char.voice_reference_uri if source_char else None,
                voice_id=voice_id,
                reference_paths=[item.image_path for item in by_slot[slot]],
                segment_indices=sorted({item.segment_index for item in by_slot[slot]}),
                appearance_variants=variants,
                approved=False,
            )
        )
        voices.append(
            VoiceSpec(
                id=voice_id,
                character_id=char_id,
                source_hint=f"自动绑定：{char_id} 的稳定声音。",
                prompt=(source_voice.prompt if source_voice else "") or voice_intent or "生成与目标角色匹配的稳定音色；保留原片对白内容、停顿和情绪节奏。",
                reference_path=source_voice.reference_path if source_voice else None,
                reference_uri=source_voice.reference_uri if source_voice else None,
                mode=source_voice.mode if source_voice else "generated_prompt",
                segment_indices=sorted({item.segment_index for item in by_slot[slot]}),
                approved=bool(source_voice and source_voice.approved),
            )
        )

    voice_ids_by_char = {voice.character_id: voice.id for voice in voices}
    for seg in manifest.segments:
        seg_candidates = [candidate for candidate in candidates if candidate.segment_index == seg.index and candidate.character_id]
        seg.character_ids = _unique([str(candidate.character_id) for candidate in seg_candidates])
        seg.character_variant_ids = _unique([str(candidate.appearance_variant_id) for candidate in seg_candidates if candidate.appearance_variant_id])
        seg.voice_ids = _unique([voice_ids_by_char[item] for item in seg.character_ids if item in voice_ids_by_char])
    return characters, voices


def _auto_classify_scenes(
    manifest: Manifest,
    *,
    job_dir: Path,
    candidates: list[SceneCandidateSpec],
    scene_intent: str,
    source_scenes: list[SceneSpec],
) -> list[SceneSpec]:
    records = [
        {
            "candidate": candidate,
            "segment_index": candidate.segment_index,
            "embedding": _safe_embedding(job_dir / candidate.image_path),
        }
        for candidate in candidates
    ]
    clusters = _cluster_records(records, threshold=0.22)
    scenes: list[SceneSpec] = []
    use_source = _use_source_scenes_for_clusters(source_scenes)
    for cluster_index, cluster in enumerate(clusters, start=1):
        source_scene = use_source[cluster_index - 1] if cluster_index - 1 < len(use_source) else None
        scene_id = _safe_id(source_scene.id) if source_scene else f"scene_auto_{cluster_index:02d}"
        cluster_candidates = [record["candidate"] for record in cluster]
        for candidate in cluster_candidates:
            candidate.scene_id = scene_id
            candidate.note = "已自动归类为源场景簇；目标场景外观使用 target_refs 下的目标参考图。"
        segment_indices = sorted({candidate.segment_index for candidate in cluster_candidates})
        scenes.append(
            SceneSpec(
                id=scene_id,
                source_hint=f"自动场景簇：覆盖片段 {', '.join(f'{idx:03d}' for idx in segment_indices)}。",
                image_path=source_scene.image_path if source_scene else None,
                image_uri=source_scene.image_uri if source_scene else None,
                prompt=_joined(
                    source_scene.prompt if source_scene else "",
                    scene_intent,
                    "保持原镜头构图、空间动线、人物站位和剪辑节奏；目标空间结构与陈设跨片段一致。",
                ),
                reference_paths=[candidate.image_path for candidate in cluster_candidates],
                continuity_anchor_paths=[cluster_candidates[-1].image_path] if cluster_candidates else [],
                segment_indices=segment_indices,
                approved=False,
            )
        )
    for seg in manifest.segments:
        seg_scene_ids = _unique(
            [str(candidate.scene_id) for candidate in candidates if candidate.segment_index == seg.index and candidate.scene_id]
        )
        seg.scene_ids = seg_scene_ids[:1]
        seg.scene_cluster_id = seg.scene_ids[0] if seg.scene_ids else None
    return scenes


def write_contact_sheet(manifest: Manifest, *, job_dir: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    scene_rows = []
    for seg in manifest.segments:
        thumbs = []
        for rel in _scene_refs(manifest, seg.index):
            src = html.escape(_relative_path_for_html(output.parent, job_dir / rel))
            thumbs.append(f'<img src="{src}" alt="{html.escape(rel)}">')
        scene_rows.append(
            "<tr>"
            f"<td>{seg.index:03d}</td>"
            f"<td>{seg.start:.2f}s / {seg.duration:.2f}s</td>"
            f"<td>{html.escape(', '.join(seg.scene_ids) or '-')}</td>"
            f"<td>{''.join(thumbs)}</td>"
            "</tr>"
        )
    person_rows = []
    for candidate in manifest.person_candidates:
        src = html.escape(_relative_path_for_html(output.parent, job_dir / candidate.image_path))
        person_rows.append(
            "<tr>"
            f"<td>{html.escape(candidate.id)}</td>"
            f"<td>{candidate.segment_index:03d} / {candidate.timestamp:.2f}s</td>"
            f"<td>{html.escape(str(candidate.bbox))}</td>"
            f"<td>{html.escape(candidate.crop_type)}</td>"
            f"<td>{html.escape(candidate.character_id or '-')}</td>"
            f"<td>{html.escape(candidate.appearance_variant_id or '-')}</td>"
            f"<td>{html.escape(str(candidate.needs_better_reference))}</td>"
            f'<td><img src="{src}" alt="{html.escape(candidate.id)}"></td>'
            "</tr>"
        )
    target_character_rows = []
    for char in manifest.characters:
        for variant in char.appearance_variants:
            target = variant.image_path or variant.image_uri or "-"
            target_character_rows.append(
                "<tr>"
                f"<td>{html.escape(char.id)}</td>"
                f"<td>{html.escape(variant.id)}</td>"
                f"<td>{html.escape(str(variant.segment_indices))}</td>"
                f"<td>{html.escape(target)}</td>"
                "</tr>"
            )
    target_scene_rows = []
    for scene in manifest.scenes:
        target = scene.image_path or scene.image_uri or "-"
        target_scene_rows.append(
            "<tr>"
            f"<td>{html.escape(scene.id)}</td>"
            f"<td>{html.escape(str(scene.segment_indices))}</td>"
            f"<td>{html.escape(target)}</td>"
            "</tr>"
        )
    output.write_text(
        f"""<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>Seedance Role Scene Preparation</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; }}
    th {{ background: #f5f5f5; text-align: left; }}
    img {{ width: 160px; margin: 2px; }}
  </style>
</head>
<body>
  <h1>准备阶段联系表</h1>
  <p>工具已自动归类角色、妆造变体和场景；请根据全帧场景表、人物候选表和 target_refs 复核，必要时校正后再运行 approve。</p>
  <h2>全帧场景表</h2>
  <table>
    <thead><tr><th>片段</th><th>时间</th><th>场景</th><th>全帧截图</th></tr></thead>
    <tbody>{''.join(scene_rows)}</tbody>
  </table>
  <h2>人物候选表</h2>
  <table>
    <thead><tr><th>候选</th><th>片段/时间</th><th>bbox</th><th>裁剪</th><th>角色</th><th>妆造</th><th>需更好参考</th><th>截图</th></tr></thead>
    <tbody>{''.join(person_rows)}</tbody>
  </table>
  <h2>目标角色参考图</h2>
  <table>
    <thead><tr><th>角色</th><th>妆造</th><th>片段</th><th>目标参考图</th></tr></thead>
    <tbody>{''.join(target_character_rows)}</tbody>
  </table>
  <h2>目标场景参考图</h2>
  <table>
    <thead><tr><th>场景</th><th>片段</th><th>目标参考图</th></tr></thead>
    <tbody>{''.join(target_scene_rows)}</tbody>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )
    return output


def _sample_keyframes(segment: Path, frames_dir: Path, index: int) -> list[dict[str, object]]:
    duration = get_video_duration(segment)
    samples = [
        ("first", 0.0),
        ("early", max(0.0, duration * 0.25)),
        ("middle", max(0.0, duration * 0.5)),
        ("late", max(0.0, duration * 0.75)),
        ("last", max(0.0, duration - 0.25)),
    ]
    result: list[dict[str, object]] = []
    for label, timestamp in samples:
        output = frames_dir / f"{index:03d}_{label}.jpg"
        extract_frame_at(segment, output, timestamp)
        result.append({"label": label, "timestamp": timestamp, "path": _relative(output, frames_dir.parent.parent)})
    return result


def _person_candidate_crops(index: int, label: str) -> list[dict[str, object]]:
    return [
        {"id": f"person_candidate_{index:03d}_{label}_left", "bbox": [0, 128, 300, 900], "crop_type": "left_half_body"},
        {"id": f"person_candidate_{index:03d}_{label}_center", "bbox": [210, 80, 300, 960], "crop_type": "center_half_body"},
        {"id": f"person_candidate_{index:03d}_{label}_right", "bbox": [420, 128, 300, 900], "crop_type": "right_half_body"},
    ]


def _scene_refs(manifest: Manifest, segment_index: int) -> list[str]:
    refs: list[str] = []
    for scene in manifest.scenes:
        if segment_index in scene.segment_indices:
            refs.extend(scene.reference_paths)
    seen: set[str] = set()
    unique: list[str] = []
    for ref in refs:
        if ref not in seen:
            unique.append(ref)
            seen.add(ref)
    return unique


def _segment_embedding_records(candidates: list[PersonCandidateSpec], *, job_dir: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for segment_index in sorted({candidate.segment_index for candidate in candidates}):
        segment_candidates = [candidate for candidate in candidates if candidate.segment_index == segment_index]
        embeddings = [_safe_embedding(job_dir / candidate.image_path) for candidate in segment_candidates]
        records.append(
            {
                "segment_index": segment_index,
                "embedding": _mean_embedding(embeddings),
            }
        )
    return records


def _cluster_records(records: list[dict[str, object]], *, threshold: float) -> list[list[dict[str, object]]]:
    clusters: list[list[dict[str, object]]] = []
    centroids: list[list[float]] = []
    for record in records:
        embedding = record.get("embedding")
        if not isinstance(embedding, list):
            continue
        best_index = -1
        best_distance = 999.0
        for idx, centroid in enumerate(centroids):
            distance = _embedding_distance(embedding, centroid)
            if distance < best_distance:
                best_index = idx
                best_distance = distance
        if best_index >= 0 and best_distance <= threshold:
            clusters[best_index].append(record)
            centroids[best_index] = _mean_embedding([item["embedding"] for item in clusters[best_index] if isinstance(item.get("embedding"), list)])
        else:
            clusters.append([record])
            centroids.append(embedding)
    return clusters


def _safe_embedding(path: Path) -> list[float]:
    try:
        return image_rgb_embedding(path, size=8)
    except Exception:
        return [0.0] * 192


def _mean_embedding(embeddings: list[list[float]]) -> list[float]:
    if not embeddings:
        return [0.0] * 192
    length = len(embeddings[0])
    return [sum(item[idx] for item in embeddings) / len(embeddings) for idx in range(length)]


def _embedding_distance(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 999.0
    length = min(len(left), len(right))
    return (sum((left[idx] - right[idx]) ** 2 for idx in range(length)) / length) ** 0.5


def _use_source_characters_for_slots(items: list[CharacterSpec]) -> list[CharacterSpec]:
    usable = [item for item in items if item.id and not _looks_global_id(item.id)]
    return usable


def _use_source_scenes_for_clusters(items: list[SceneSpec]) -> list[SceneSpec]:
    return [item for item in items if item.id and not _looks_global_id(item.id)]


def _looks_global_id(value: str) -> bool:
    lowered = value.lower()
    return any(token in lowered for token in ["all", "global", "candidate"])


def _source_variant_for_segments(char: CharacterSpec | None, segment_ids: list[int]) -> AppearanceVariantSpec | None:
    if not char:
        return None
    if not char.appearance_variants:
        return None
    for variant in char.appearance_variants:
        if variant.segment_indices and set(variant.segment_indices).intersection(segment_ids):
            return variant
    return char.appearance_variants[0]


def _character_intent(manifest: Manifest) -> str:
    parts = [manifest.prompt]
    for char in manifest.characters:
        parts.extend([char.prompt, char.voice_prompt])
        parts.extend(variant.prompt for variant in char.appearance_variants)
    return _joined(*parts) or "按用户任务意图生成目标角色；若未指定，使用自然写实的目标人物形象。"


def _scene_intent(manifest: Manifest) -> str:
    parts = [manifest.prompt]
    parts.extend(scene.prompt for scene in manifest.scenes)
    return _joined(*parts) or "按用户任务意图生成目标场景；若未指定，使用自然写实的目标空间。"


def _voice_intent(manifest: Manifest) -> str:
    parts = [manifest.prompt]
    parts.extend(voice.prompt for voice in manifest.voice_registry)
    parts.extend(char.voice_prompt for char in manifest.characters)
    return _joined(*parts) or "生成稳定角色音色，保留原片对白内容、停顿和情绪节奏。"


def _joined(*parts: str | None) -> str:
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        value = (part or "").strip()
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return "；".join(result)


def _slot_label(slot: str) -> str:
    return {
        "left_half_body": "画面左侧人物候选",
        "center_half_body": "画面中部人物候选",
        "right_half_body": "画面右侧人物候选",
    }.get(slot, slot)


def _safe_id(value: str) -> str:
    allowed = []
    for char in value.strip().lower().replace("-", "_").replace(" ", "_"):
        if char.isalnum() or char == "_":
            allowed.append(char)
    return "".join(allowed) or "auto_id"


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def _relative_path_for_html(from_dir: Path, path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(from_dir.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
