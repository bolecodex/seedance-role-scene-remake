# 原视频分析约定

`analyze` 是所有重制任务的前置步骤。它只理解原片，不生成目标角色或目标场景。

## 输出目录

- `analysis/analysis.json`：总分析结果，供 `prepare --analysis` 读取。
- `analysis/script/剧本.md`：人工可读剧本，格式参考 `docs/剧本示例.md`。
- `analysis/script/script.json`：结构化分场和 ASR 转写。
- `analysis/script/script_review.html`：分场、摘要和关键帧复核页。
- `analysis/roles/index.html`：源角色人工检查总入口，每个角色一个独立文件夹。
- `analysis/roles/{character_id}/profile.json`：源角色、人物裁剪、原始全帧、关联声音样本和对白转写汇总。
- `analysis/roles/{character_id}/person_crops/`：按 `bbox` 裁出的单角色截图；只有模型给出可信框时才生成。
- `analysis/roles/{character_id}/full_frames/`：该角色出现过的原始全帧证据，可能包含其他人物，只用于定位来源镜头。
- `analysis/roles/{character_id}/voice_samples/`：该角色关联声音样本副本。
- `analysis/roles/{character_id}/contact_sheet.html`：该角色独立检查页。
- `analysis/characters/{character_id}/profile.json`：源角色描述、置信度、确认状态和证据帧。
- `analysis/characters/{character_id}/evidence/`：源角色证据帧。
- `analysis/scenes/{scene_id}/profile.json`：源场景描述、置信度、确认状态和关键帧。
- `analysis/scenes/{scene_id}/keyframes/`：源场景关键帧。
- `analysis/props/{prop_id}/profile.json`：源道具描述和证据帧。
- `analysis/voices/{voice_id}/profile.json`：源声音候选、对应角色候选、样本范围。
- `analysis/voices/{voice_id}/samples/`：源角色声音样本。
- `analysis/voices/{voice_id}/transcript.json`：该声音候选关联的对白转写。
- `analysis/index.html`：人工检查总入口。

## 剧本格式

剧本应按 `1-1 / 1-2...` 分场。每个分场包含：

- 场景或动作叙述：例如 `全景-客厅内，男主站在门口。`
- 景别/运镜 + 动作：例如 `近景-男主抬头看向女主。`
- 对白：`角色（情绪）：“台词”`
- OS/旁白：`角色（OS）：“内心台词”` 或 `旁白：“内容”`
- 音效：`音效：电话铃声`

剧本只描述原片，不写目标替换设定。

## JSON 关键字段

`analysis.json` 至少包含：

- `status`：`draft` 或 `reviewed`。
- `shots[]`：`id/start/end/summary/description/camera/action/dialogues/evidence_paths/confidence`。
- `characters[]`：机器可读的源角色候选，含 `id/name/description/evidence_paths/evidence_regions/confidence/confirmed`。其中 `evidence_paths` 是全帧证据，`evidence_regions` 是单人 bbox。
- `scenes[]`：源场景候选，含 `id/name/description/evidence_paths/confidence/confirmed`。
- `props[]`：源道具候选。
- `voices[]`：源声音候选，含 `id/character_id/sample_paths/transcript_segments/confidence/confirmed`。
- `low_confidence_items[]`：需要人工复核的低置信度项。
- `review_items[]`：待人工检查清单。

人工检查完成后，将 `status` 改为 `reviewed`，并清空已处理的 `review_items` 和 `low_confidence_items`。否则 `approve` 会阻止后续生成。

## 使用原则

- 源角色、源场景、源道具和源声音只用于理解原片和人工检查。
- 人工检查角色时优先打开 `analysis/roles/index.html`，它把每个角色的人物裁剪、全帧证据和声音样本集中到独立文件夹。
- `person_crops` 才能作为“人物截图”检查；`full_frames` 可能出现其他人，不用于判断角色是否截全。
- 目标角色外观仍放在 `target_refs/characters/`。
- 目标场景外观仍放在 `target_refs/scenes/`。
- 源声音样本可作为声音一致性检查材料；如果模型不支持音频参考，不能伪装为已使用音色参考。
- 如果 Ark VLM/ASR 未配置，默认应失败；只有调试命令显式加 `--allow-skeleton` 才可输出本地骨架。
