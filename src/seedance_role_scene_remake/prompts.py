"""Prompt and inline option builders."""

from __future__ import annotations

from seedance_role_scene_remake.manifest import (
    AppearanceVariantSpec,
    CharacterSpec,
    Manifest,
    ReferenceAsset,
    SceneSpec,
    SegmentEntry,
    VoiceSpec,
)


def parse_inline_mapping(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for part in _split_escaped(value, ","):
        if not part.strip():
            continue
        key, sep, raw = part.partition("=")
        if not sep:
            raise ValueError(f"内联参数缺少 '='：{part}")
        result[key.strip()] = raw.strip()
    return result


def inline_character(value: str) -> CharacterSpec:
    item = parse_inline_mapping(value)
    char_id = item.get("id")
    if not char_id:
        raise ValueError("--character 必须包含 id=...")
    return CharacterSpec(
        id=char_id,
        source_hint=item.get("source") or item.get("source_hint", ""),
        image_path=item.get("image") or item.get("image_path") or None,
        image_uri=item.get("image_uri") or None,
        prompt=item.get("prompt", ""),
        voice_prompt=item.get("voice", "") or item.get("voice_prompt", ""),
        voice_reference_path=item.get("voice_reference") or item.get("voice_reference_path") or None,
        voice_reference_uri=item.get("voice_reference_uri") or None,
    )


def inline_scene(value: str) -> SceneSpec:
    item = parse_inline_mapping(value)
    scene_id = item.get("id")
    if not scene_id:
        raise ValueError("--scene 必须包含 id=...")
    return SceneSpec(
        id=scene_id,
        source_hint=item.get("source") or item.get("source_hint", ""),
        image_path=item.get("image") or item.get("image_path") or None,
        image_uri=item.get("image_uri") or None,
        prompt=item.get("prompt", ""),
    )


def build_generation_prompt(
    manifest: Manifest,
    segment: SegmentEntry,
    *,
    reference_assets: list[ReferenceAsset] | None = None,
) -> str:
    characters = _select_characters(manifest, segment)
    variants = _select_variants(manifest, segment)
    scenes = _select_scenes(manifest, segment)
    voices = _select_voices(manifest, segment)
    assets = [asset for asset in reference_assets or [] if asset.uri or asset.path]
    has_video_reference = any(asset.kind == "video" for asset in assets)
    uses_generated_audio = manifest.generate_audio and manifest.audio_mode == "generated"
    slot_map = _slot_map(assets)
    lines = [
        "你正在基于已确认剧本和参考素材生成视频重拍片段。",
        "参考素材指代表：",
    ]
    if assets:
        for asset in assets:
            lines.append(f"- {asset.slot}：{asset.note or asset.role}（{asset.bound_type}:{asset.bound_id}）")
    else:
        lines.append("- 无外部参考素材：仅依据下方剧本 Bible 和设定生成。")
    if not has_video_reference:
        lines.append("本次不使用原视频片段作为输入参考；必须依据剧本中的动作、站位、对白节奏、环境和运镜描述复刻剧情。")
    lines.extend(
        [
        "硬性要求：剧情事件、动作轨迹、人物站位、镜头运动、剪辑节奏、对白语义、对白顺序和对白时序保持不变。",
        "硬性要求：不要新增剧情，不要删减台词，不要重写分镜，不要改变角色之间的关系和互动顺序。",
        "角色映射必须稳定：不要把一个原角色的新形象串到另一个原角色身上。",
        "身份与场景必须按下面已批准的 Bible 替换；如果 Bible 与参考视频外观冲突，以 Bible 为准。",
        "分镜表达结构遵循：精准主体 + 动作细节 + 场景环境 + 光影色调 + 镜头运镜 + 约束条件。",
        "角色一致性：同一个角色 id 在所有片段中保持同一身份、脸型、体型和人物关系，不要串人。",
        "妆造一致性：只在 segment 绑定的 appearance_variant 要求下改变发型、妆容和服装；同一 variant 跨片段必须保持一致。",
        "场景一致性：只使用 segment 绑定的 scene id；同一 scene id 跨片段保持空间结构、材质、光照和陈设一致。",
        "声音一致性：同一 voice id 跨片段保持同一音色、年龄感、口音和情绪表达方式。",
        "多人禁忌：视频全程不要在同一画面中复制相同人物，不要多人同脸，不要出现双胞胎同款人物。",
        "文字禁忌：画面中绝对不要出现任何字幕、台词文字、英文字幕、中文字幕、水印、标识或无关屏幕文字；对白只能通过口型和表演体现。",
        "画质约束：面部稳定不变形、五官清晰、人体结构正常、动作自然流畅、不僵硬、画面无卡顿、无闪烁。",
        ]
    )
    if has_video_reference:
        lines.append("视频1只用于动作、镜头、构图、剪辑节奏、对白时序和人物关系参考；不要把源角色脸、发色、肤色、服装细节或源场景陈设当成目标外观。")
    if manifest.prompt.strip():
        lines.append(f"全局要求：{manifest.prompt.strip()}")
    _append_story_bible(lines, manifest)
    _append_segment_story(lines, manifest, segment)
    _append_dialogue_timing(lines, segment, variants, slot_map)
    _append_language_policy(lines, manifest)
    if characters:
        lines.append("角色 Bible：")
        for char in characters:
            desc = char.prompt or "保持该角色替换设定稳定。"
            source = f"；原片对应：{char.source_hint}" if char.source_hint else ""
            lines.append(f"- {char.id}{source}：目标形象={desc}")
    if variants:
        lines.append("本片段角色妆造变体：")
        for char, variant in variants:
            source = f"；原片妆造对应：{variant.source_hint}" if variant.source_hint else ""
            desc = variant.prompt or char.prompt or "按已确认目标角色设定保持该妆造稳定。"
            slot = slot_map.get(("appearance_variant", variant.id), "缺少图片")
            lines.append(f"- {char.id}（{slot}）/{variant.id}{source}：{desc}")
    if scenes:
        lines.append("本片段场景 Bible：")
        for scene in scenes:
            source = f"；原片对应：{scene.source_hint}" if scene.source_hint else ""
            slot = slot_map.get(("scene", scene.id), "缺少图片")
            lines.append(f"- {scene.id}（{slot}）{source}：{scene.prompt or '按该场景参考保持一致。'}")
    if uses_generated_audio and voices:
        lines.append("本片段声音 Bible：")
        for voice in voices:
            char_part = f"；角色={voice.character_id}" if voice.character_id else ""
            source = f"；原片对应：{voice.source_hint}" if voice.source_hint else ""
            lines.append(f"- {voice.id}{char_part}{source}：{voice.prompt or '生成稳定角色音色，台词内容和时序不变。'}")
    elif uses_generated_audio and characters:
        lines.append("角色声音：")
        for char in characters:
            voice = char.voice_prompt or "生成符合角色设定的新音色，但台词逐字不改。"
            lines.append(f"- {char.id}：{voice}")
    if uses_generated_audio and manifest.dialogue_fidelity == "strict":
        lines.append("对白保真：生成音频必须沿用原片对白内容、停顿位置、情绪强弱和说话节奏；台词逐字不改，只改变音色质感。")
    elif not uses_generated_audio:
        lines.append("音频策略：本次只生成画面，最终合成会保留源片段英语音轨；画面中的口型、说话节奏和停顿尽量匹配剧本台词，但不要把台词渲染成画面文字或字幕。")
    lines.append(_spoken_language_line(manifest))
    if uses_generated_audio:
        lines.append("输出需要包含新生成音轨；不要静音，不要只保留原声音轨。")
    else:
        lines.append("输出视频可以不生成音轨；重点保证画面人物、动作、环境、运镜和口型节奏。")
    return "\n".join(lines)


def _select_characters(manifest: Manifest, segment: SegmentEntry) -> list[CharacterSpec]:
    mapping = manifest.character_map()
    ids = segment.character_ids or [item.id for item in manifest.characters]
    return [mapping[item] for item in ids if item in mapping]


def _select_scenes(manifest: Manifest, segment: SegmentEntry) -> list[SceneSpec]:
    mapping = manifest.scene_map()
    ids = segment.scene_ids or [item.id for item in manifest.scenes]
    return [mapping[item] for item in ids if item in mapping]


def _select_variants(manifest: Manifest, segment: SegmentEntry) -> list[tuple[CharacterSpec, AppearanceVariantSpec]]:
    mapping = manifest.variant_map()
    selected: list[tuple[CharacterSpec, AppearanceVariantSpec]] = []
    for item in segment.character_variant_ids:
        if item in mapping:
            selected.append(mapping[item])
    return selected


def _select_voices(manifest: Manifest, segment: SegmentEntry) -> list[VoiceSpec]:
    mapping = manifest.voice_map()
    ids = segment.voice_ids or [item.voice_id for item in _select_characters(manifest, segment) if item.voice_id]
    return [mapping[item] for item in ids if item in mapping]


def _append_story_bible(lines: list[str], manifest: Manifest) -> None:
    bible = manifest.story_bible
    if bible.synopsis.strip():
        lines.append(f"剧情 Bible：{bible.synopsis.strip()}")
    for constraint in bible.locked_constraints:
        if constraint.strip():
            lines.append(f"剧情锁定：{constraint.strip()}")


def _append_segment_story(lines: list[str], manifest: Manifest, segment: SegmentEntry) -> None:
    beats = [
        beat
        for beat in manifest.story_bible.beats
        if _overlaps(float(beat.get("start", segment.start)), float(beat.get("end", segment.start + segment.duration)), segment.start, segment.start + segment.duration)
    ]
    if not beats:
        return
    lines.append(f"当前片段剧本范围：{segment.start:.2f}s-{segment.start + segment.duration:.2f}s。")
    for beat in beats:
        shot_id = beat.get("id") or "未命名分场"
        lines.append(f"分场 {shot_id}：{beat.get('summary', '')}")
        if beat.get("environment_detail"):
            lines.append(f"- 环境细节：{beat['environment_detail']}")
        camera_plan = beat.get("camera_plan") or beat.get("camera")
        if isinstance(camera_plan, list):
            lines.append("- 镜头/运镜：" + "；".join(str(item) for item in camera_plan if item))
        elif camera_plan:
            lines.append(f"- 镜头/运镜：{camera_plan}")
        action_beats = beat.get("action_beats") or []
        if action_beats:
            lines.append("- 动作节拍：")
            for action in action_beats:
                actor = action.get("actor", "")
                timing = f"（{action.get('timing')}）" if action.get("timing") else ""
                camera = f"，{action.get('camera')}" if action.get("camera") else ""
                lines.append(f"  - {actor}{timing}{camera}：{action.get('description', '')}")
        dialogues = beat.get("dialogues") or []
        if dialogues:
            lines.append("- 英文对白和口型节奏：")
            for dialogue in dialogues:
                speaker = dialogue.get("speaker", "")
                text = dialogue.get("text", "")
                state = "，".join(
                    str(dialogue.get(key, "")).strip()
                    for key in ("delivery", "facial_expression", "body_language", "gaze", "pause")
                    if str(dialogue.get(key, "")).strip()
                )
                prefix = f"{speaker}（{state}）" if state else str(speaker)
                lines.append(f"  - {prefix}: \"{text}\"")


def _append_dialogue_timing(
    lines: list[str],
    segment: SegmentEntry,
    variants: list[tuple[CharacterSpec, AppearanceVariantSpec]],
    slot_map: dict[tuple[str, str], str],
) -> None:
    if not segment.dialogue_timings:
        lines.append("本片段无明确对白：所有角色保持闭口或自然聆听，只做剧本要求的动作和反应。")
        return
    lines.append("口型/对白时间表（必须按这些相对时间表演）：")
    for item in segment.dialogue_timings:
        speaker = str(item.get("speaker") or "未知角色")
        slot = _slot_for_speaker(speaker, variants, slot_map)
        text = str(item.get("text") or "")
        start = float(item.get("start") or 0)
        end = float(item.get("end") or start)
        speaker_text = f"{speaker}（{slot}）" if slot else speaker
        lines.append(f"- {start:.2f}-{end:.2f}s：{speaker_text} 开口说英文台词 \"{text}\"，口型和说话节奏对齐该时间段。")
    lines.append("停顿约束：不在上述时间表内说话的角色必须闭口、聆听或做无声反应；不要提前张嘴、不要延后张嘴。")


def _slot_for_speaker(
    speaker: str,
    variants: list[tuple[CharacterSpec, AppearanceVariantSpec]],
    slot_map: dict[tuple[str, str], str],
) -> str:
    direct = slot_map.get(("character_identity", speaker))
    if direct:
        return direct
    for char, variant in variants:
        if char.id == speaker:
            slot = slot_map.get(("appearance_variant", variant.id))
            if slot:
                return slot
    return ""


def _overlaps(start: float, end: float, seg_start: float, seg_end: float) -> bool:
    return max(start, seg_start) < min(end, seg_end)


def _append_language_policy(lines: list[str], manifest: Manifest) -> None:
    policy = manifest.language_policy
    if policy.target_language in {"", "auto", "preserve_source"}:
        lines.append(f"语种策略：保持源视频语种（source_language={policy.source_language or 'auto'}），不要擅自翻译对白、字幕或屏幕文字。")
    else:
        lines.append(
            "语种策略：全视频统一改为 "
            f"{policy.target_language}；对白、字幕和屏幕文字都必须翻译为该语种，"
            "语义、顺序、停顿和情绪节奏保持原片。"
        )


def _spoken_language_line(manifest: Manifest) -> str:
    spoken = manifest.language_policy.spoken_language
    if spoken in {"", "auto", "preserve_source"}:
        return "口语策略：人物继续使用源视频口语，不要擅自改变语种，不要新增字幕。"
    if spoken.lower() in {"en", "english"}:
        return "口语策略：所有角色继续说英文，不翻译成中文，不生成中文字幕，不新增任何字幕。"
    return f"口语策略：所有角色口语统一为 {spoken}，不要混入其他语种，不新增字幕。"


def _slot_map(reference_assets: list[ReferenceAsset]) -> dict[tuple[str, str], str]:
    return {(asset.bound_type, asset.bound_id): asset.slot for asset in reference_assets if asset.bound_type and asset.bound_id}


def _split_escaped(value: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            buf.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == delimiter:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    parts.append("".join(buf))
    return parts
