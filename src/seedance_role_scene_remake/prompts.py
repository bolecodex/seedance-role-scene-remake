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
    slot_map = _slot_map(reference_assets or [])
    lines = [
        "你正在基于原视频片段做视频换角色、换场景和换角色声音。",
        "参考素材指代表：",
    ]
    if reference_assets:
        for asset in reference_assets:
            lines.append(f"- {asset.slot}：{asset.note or asset.role}（{asset.bound_type}:{asset.bound_id}）")
    else:
        lines.append("- 视频1：原视频片段，仅参考动作、站位、运镜、构图、剪辑节奏、对白语义和对白时序，不参考人物外观或场景陈设。")
    lines.extend(
        [
        "硬性要求：剧情事件、动作轨迹、人物站位、镜头运动、剪辑节奏、对白语义、对白顺序和对白时序保持不变。",
        "硬性要求：不要新增剧情，不要删减台词，不要重写分镜，不要改变角色之间的关系和互动顺序。",
        "角色映射必须稳定：不要把一个原角色的新形象串到另一个原角色身上。",
        "视频1只用于动作、镜头、构图、剪辑节奏、对白时序和人物关系参考；不要把源角色脸、发色、肤色、服装细节或源场景陈设当成目标外观。",
        "身份与场景必须按下面已批准的 Bible 替换；如果 Bible 与参考视频外观冲突，以 Bible 为准。",
        "分镜表达结构遵循：精准主体 + 动作细节 + 场景环境 + 光影色调 + 镜头运镜 + 约束条件。",
        "角色一致性：同一个角色 id 在所有片段中保持同一身份、脸型、体型和人物关系，不要串人。",
        "妆造一致性：只在 segment 绑定的 appearance_variant 要求下改变发型、妆容和服装；同一 variant 跨片段必须保持一致。",
        "场景一致性：只使用 segment 绑定的 scene id；同一 scene id 跨片段保持空间结构、材质、光照和陈设一致。",
        "声音一致性：同一 voice id 跨片段保持同一音色、年龄感、口音和情绪表达方式。",
        "多人禁忌：视频全程不要在同一画面中复制相同人物，不要多人同脸，不要出现双胞胎同款人物。",
        "画质约束：面部稳定不变形、五官清晰、人体结构正常、动作自然流畅、不僵硬、画面无卡顿、无闪烁。",
        ]
    )
    if manifest.prompt.strip():
        lines.append(f"全局要求：{manifest.prompt.strip()}")
    _append_story_bible(lines, manifest)
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
    if voices:
        lines.append("本片段声音 Bible：")
        for voice in voices:
            char_part = f"；角色={voice.character_id}" if voice.character_id else ""
            source = f"；原片对应：{voice.source_hint}" if voice.source_hint else ""
            lines.append(f"- {voice.id}{char_part}{source}：{voice.prompt or '生成稳定角色音色，台词内容和时序不变。'}")
    elif characters:
        lines.append("角色声音：")
        for char in characters:
            voice = char.voice_prompt or "生成符合角色设定的新音色，但台词逐字不改。"
            lines.append(f"- {char.id}：{voice}")
    if manifest.dialogue_fidelity == "strict":
        lines.append("对白保真：生成音频必须沿用原片对白内容、停顿位置、情绪强弱和说话节奏；台词逐字不改，只改变音色质感。")
    lines.append(_spoken_language_line(manifest))
    lines.append("输出需要包含新生成音轨；不要静音，不要只保留原声音轨。")
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
