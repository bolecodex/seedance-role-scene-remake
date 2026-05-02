# Spec And Prompts

## Manifest 核心字段

- `preparation.status`: `draft` 或 `approved`。未 approved 时 `run/remake` 默认停止。
- `source_analysis`: `analyze` 生成的原片剧本和源素材索引。`status` 应在人工检查后设为 `reviewed`。
- `story_bible`: 全片剧情摘要、分段剧情节拍和锁定约束。只记录原片发生了什么，不写新剧情。
- `language_policy`: 默认 `target_language: preserve_source`、`spoken_language: preserve_source`。指定目标语种时，`translate_dialogue` 和 `translate_visible_text` 都应为 `true`；只想让人物继续说英文时设 `spoken_language: en`，不要改 `target_language`。
- `voice_registry`: 稳定声音表。每个角色至少绑定一个 `voice_id`，同一 `voice_id` 跨片段保持音色一致。
- `person_candidates`: 准备阶段的人物候选裁剪，由工具自动归类到 `character_id` 与 `appearance_variant_id`。启发式裁剪不等于目标人物参考图。
- `scene_candidates`: 准备阶段的全帧场景候选，由工具自动归类到真实 `scene_id`。

## 文件夹约定

- `preparation/keyframes/`: 原片关键帧，只用于分析镜头、剧情、场景和动作。
- `analysis/`: `analyze` 输出的剧本、源角色、源场景、源道具和源声音检查包；详细约定见 `source-analysis.md`。
- `preparation/person_candidates/`: 原片人物候选裁剪，只用于自动识别角色和妆造，不作为目标外观。
- `preparation/scene_candidates/`: 原片全帧场景候选，只用于自动识别源场景簇，不作为目标场景。
- `target_refs/characters/{appearance_variant_id}.jpg`: 目标角色/妆造参考图。Seedance 生成时会作为 `图片N` 绑定到对应角色。
- `target_refs/characters/{appearance_variant_id}.prompt.txt`: 生成该角色参考图的 Seedream prompt。
- `target_refs/scenes/{scene_id}.jpg`: 目标场景参考图。Seedance 生成时会作为 `图片N` 绑定到对应场景。
- `target_refs/scenes/{scene_id}.prompt.txt`: 生成该场景参考图的 Seedream prompt。

默认情况下，如果任务没有提供角色或场景参考图，`prepare --auto-targets` 会调用 Seedream 5.0 Lite 自动生成 `target_refs`。

## 角色与妆造

`characters` 记录真实角色，不要用“所有人”长期生成：

```json
{
  "id": "lead_male",
  "source_hint": "原片男主",
  "prompt": "目标人物身份、年龄感、脸型、体型、整体风格。",
  "voice_id": "lead_male_voice",
  "image_path": "target_refs/characters/lead_male_identity.jpg",
  "approved": true,
  "appearance_variants": [
    {
      "id": "lead_male_home_look",
      "source_hint": "原片男主居家服妆造",
      "image_path": "target_refs/characters/lead_male_home_look.jpg",
      "prompt": "目标居家服、发型、妆容，跨片段保持一致。",
      "approved": true
    }
  ]
}
```

若原片角色换装、换发型或妆容变化，工具会自动建立多个 `appearance_variants` 并在对应 segment 绑定 variant。复核时只修正明显错分。
每个 `appearance_variant` 默认都要有目标参考图，路径放在 `target_refs/characters/`。源视频帧只用于识别原片角色和妆造，不作为新角色外观。

人物候选归类示例：

```json
{
  "id": "person_candidate_000_middle_center",
  "segment_index": 0,
  "timestamp": 5.4,
  "image_path": "preparation/person_candidates/person_candidate_000_middle_center.jpg",
  "bbox": [210, 80, 300, 960],
  "crop_type": "center_half_body",
  "quality": "heuristic_crop",
  "needs_better_reference": false,
  "character_id": "lead_male",
  "appearance_variant_id": "lead_male_home_look"
}
```

## 场景

`scenes` 记录真实空间。跨多个片段属于同一空间时，复用同一个 `scene id`：

```json
{
  "id": "living_room",
  "source_hint": "原片客厅",
  "image_path": "target_refs/scenes/living_room.jpg",
  "prompt": "现代中式家居客厅，木质家具，暖色灯光，茶几，书柜，空间动线保持原镜头。",
  "approved": true
}
```

每个 `scene_candidate` 会自动归类到真实 `scene_id`。目标场景参考图描述新场景外观，路径放在 `target_refs/scenes/`；源全帧只用于判断原片空间、镜头和动线。

## 自动目标参考图

Seedream 5.0 Lite 用于补目标参考图，不用于最终视频生成：

```bash
seedance-role-scene-remake prepare ./input.mp4 -o ./job \
  --character-prompt "所有角色替换为自然现代中国人，保留原年龄层、性别呈现、关系和动作" \
  --scene-prompt "现代中式家居，木质家具，暖色灯光，屏风、书柜、茶几、中式软装" \
  --spoken-language en \
  --auto-targets
```

若后续手动新增角色或场景，再运行：

```bash
seedance-role-scene-remake render-targets ./job/manifest.json --size 2K
```

生成后仍需 `review` 和 `approve`，但分类由工具先完成。

## Segment 绑定

每个片段必须显式绑定角色、妆造、场景和声音：

```json
{
  "index": 0,
  "character_ids": ["lead_male", "lead_female"],
  "character_variant_ids": ["lead_male_home_look", "lead_female_home_look"],
  "scene_ids": ["living_room"],
  "voice_ids": ["lead_male_voice", "lead_female_voice"]
}
```

## Prompt 原则

推荐语气：

```text
参考素材指代表：
- 视频1：原片段，仅参考动作、站位、运镜、构图、剪辑节奏、对白语义和对白时序，不参考人物外观或场景陈设。
- 图片1：lead_male / lead_male_home_look 的目标现代中国男性外观参考。
- 图片2：lead_female / lead_female_home_look 的目标现代中国女性外观参考。
- 图片3：living_room 的现代中式家居目标场景参考。
- 音频1：原片段音频，仅参考对白节奏、停顿和情绪。

硬性要求：剧情事件、动作轨迹、人物站位、镜头运动、剪辑节奏、对白语义、对白顺序和对白时序保持不变。
lead_male（图片1）使用 lead_male_home_look，保持同一身份和妆造一致。
lead_female（图片2）使用 lead_female_home_look，保持同一身份和妆造一致。
living_room（图片3）保持现代中式家居空间一致，构图和动线不变。
声音 lead_male_voice 跨片段保持同一音色，台词逐字不改。
口语策略：所有角色继续说英文，不翻译成中文，不生成中文字幕，不新增任何字幕。
多人禁忌：不要多人同脸，不要复制同一人物，不要把源视频欧美脸、金发、原服装或原场景陈设当作目标外观。
```

避免写“接下来发生什么”、新增冲突、改关系、改分镜、改台词顺序。

Seedance 多参考规则：

- 素材顺序必须和 prompt 中的 `视频1/图片1/图片2/音频1` 一致。
- 资产 URI 只是访问地址，不能假设模型知道某个 URI 对应哪个角色；prompt 必须写清楚。
- 多人场景全程使用 `角色名（图片N）` 指代，不要只写“男主/女主/这个人”。
- 视频参考只承担动作、运镜、剧情和时序职责；目标人物和目标场景必须来自目标参考图或已批准 prompt。
- 不要把九宫格或拼图当作单一人物参考图；为每个角色/妆造提供清晰单人图。

## 本任务常用目标

海外剧改自然现代中国人 + 现代中式家居 + 英文对白时，manifest 应体现：

```json
{
  "prompt": "目标为自然现代中国人角色和现代中式家居场景；剧情、动作、镜头、对白语义和时序保持原片。",
  "language_policy": {
    "source_language": "en",
    "target_language": "preserve_source",
    "spoken_language": "en",
    "translate_dialogue": false,
    "translate_visible_text": false,
    "subtitle_policy": "preserve_if_present",
    "approved": true
  }
}
```
