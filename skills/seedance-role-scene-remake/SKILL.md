---
name: seedance-role-scene-remake
description: 使用本项目的 seedance-role-scene-remake CLI 调用火山方舟能力，对本地视频先做原片剧本化、源角色/场景/道具/声音检查包，再建立角色表、妆造变体、场景表、声音表和语种策略，最后用 Seedance 2.0 做剧情保真的换角色、换场景和换角色声音。
metadata:
  short-description: Seedance 视频换角色换场景
---

# Seedance 视频换角色换场景

默认工作流是：**原片分析 -> 人工检查源素材 -> dialogue-aligned 准备目标设定 -> 审阅批准 -> 上传参考 -> Seedance 多参考重制 -> 音频处理 -> 拼接 -> 验证 -> 修复**。

不要一上来生成新视频。先用 `analyze` 把原视频转成剧本，并导出源角色、源场景、道具和角色声音样本，人工检查无误后再进入 `prepare`。源视频/源帧/源声音只用于理解原片和保持剧情、动作、镜头、对白时序，不作为目标人物或目标场景外观。

## 前置检查

```bash
which ffmpeg && which ffprobe
seedance-role-scene-remake version
test -n "$ARK_API_KEY"
test -n "$SEEDANCE_ROLE_SCENE_ANALYSIS_MODEL"
test -n "$SEEDANCE_ROLE_SCENE_ASR_MODEL"
test -n "$VOLC_ACCESSKEY" && test -n "$VOLC_SECRETKEY" && test -n "$TOS_BUCKET"
```

常用 `.env` 字段：

```bash
ARK_API_KEY="..."
SEEDANCE_ROLE_SCENE_ANALYSIS_MODEL="..."
SEEDANCE_ROLE_SCENE_ASR_PROVIDER="doubao_streaming"
SEEDANCE_ROLE_SCENE_ASR_MODEL="doubao_streaming_2.0"
SEEDANCE_ROLE_SCENE_DOUBAO_ASR_APP_ID="..."
SEEDANCE_ROLE_SCENE_DOUBAO_ASR_ACCESS_TOKEN="..."
SEEDANCE_ROLE_SCENE_DOUBAO_ASR_SECRET_KEY="..."
SEEDANCE_ROLE_SCENE_DOUBAO_ASR_RESOURCE_ID="volc.bigasr.sauc.duration"
SEEDANCE_ROLE_SCENE_MODEL="doubao-seedance-2-0-260128"
SEEDANCE_ROLE_SCENE_SEEDREAM_MODEL="doubao-seedream-5-0-lite-260128"
VOLC_ACCESSKEY="..."
VOLC_SECRETKEY="..."
TOS_BUCKET="..."
```

## 推荐流程

先分析原视频，生成剧本和源素材检查包：

```bash
seedance-role-scene-remake analyze ./input.mp4 -o ./job_remake \
  --sample-seconds 2 \
  --scene-threshold 0.35 \
  --script-detail detailed \
  --script-min-action-beats 2

seedance-role-scene-remake analyze-manifest ./job_remake/analysis/analysis.json
```

重点打开 `job_remake/analysis/index.html` 检查：

- `analysis/script/剧本.md`：按 `1-1 / 1-2...` 分场的原片剧本。
- `analysis/script/script_quality.json`：剧本细节质量检查；若动作节拍不足或情绪只写抽象词，先复核这一项。
- `analysis/roles/index.html`：源角色人工检查入口，每个角色一个独立文件夹，集中展示 `person_crops` 单人裁剪、`full_frames` 原始全帧、声音样本和对白转写。
- `analysis/characters/{character_id}/`：源角色 profile、证据帧和联系表。
- `analysis/scenes/{scene_id}/`：源场景 profile、关键帧和联系表。
- `analysis/props/{prop_id}/`：源道具 profile 和证据帧。
- `analysis/voices/{voice_id}/`：源角色声音样本和转写片段。

剧本检查时，不要接受只写“冷漠、强硬、平淡、坚定、暧昧”的抽象情绪；优先要求“女主移开视线，嘴角压平，回答前停顿半拍”这类可观察状态。

人工检查后，将分析 JSON 或后续 manifest 中的 `source_analysis.status` 标为 `reviewed`，并清空已处理的低置信度/待检查项。再生成目标设定草稿：

```bash
seedance-role-scene-remake prepare ./input.mp4 -o ./job_remake \
  --analysis ./job_remake/analysis/analysis.json \
  --dialogue-aligned \
  --dialogue-max-seconds 8 \
  --segment-seconds 12 \
  --source-language auto \
  --target-language preserve_source \
  --spoken-language preserve_source \
  --character-prompt "目标角色整体形象" \
  --scene-prompt "目标场景整体设定" \
  --auto-targets
```

`prepare` 会继续生成 `preparation/keyframes/`、`person_candidates/`、`scene_candidates/`，自动分类角色/妆造/场景，并在缺少目标图时调用 Seedream 5.0 Lite 输出：

- `target_refs/characters/{appearance_variant_id}.jpg`
- `target_refs/scenes/{scene_id}.jpg`

打开 `preparation/contact_sheet.html` 复核自动分类和目标参考图。确认角色、妆造、场景、声音、语种、剧本 Bible 后再批准：

```bash
seedance-role-scene-remake review ./job_remake/manifest.json
seedance-role-scene-remake approve ./job_remake/manifest.json
```

批准后再生成：

```bash
seedance-role-scene-remake upload ./job_remake/manifest.json
seedance-role-scene-remake asset-register ./job_remake/manifest.json --group-type AIGC
seedance-role-scene-remake remake ./job_remake/manifest.json --reference-strategy full --reference-privacy assetized
seedance-role-scene-remake merge ./job_remake/manifest.json -o ./final.mp4
```

`--reference-strategy full` 会按 Seedance 限额编排最多 `9` 张图片、`3` 段视频、`3` 段音频；`safe` 不传任何视频或音频参考，只使用目标图、上一段生成尾帧图片和 ASR 时间表；`script-only` 不传视频、音频或连续性尾帧参考。每段的 `reference_report` 会写入 manifest 和质量报告。

默认使用 `--reference-privacy assetized`：所有可能含真人/拟真人的视觉参考，包括源片段、目标角色图、上一段尾帧和上一段尾部视频，都应先进入私域素材库并以 `asset://` 参与生成。默认 `asset-register --group-type AIGC` 使用私域虚拟素材库，AIGC 素材库也可以上传授权真人素材；`LivenessFace` 是可选路径，不是唯一前提。

源音频参考应由 CLI 导出为 `source_audio/*.mp3` 后上传，避免 r2v 请求因 `.m4a` 音频参考格式被拒。若 `audio_mode=source`，这些 mp3 只用于生成时参考对白节奏，最终音轨仍直接使用完整源视频音轨。

音频策略：

- `audio_mode=generated`：运行 `extract-audio`，从 Seedance 生成片段中抽取音轨并分段对齐，再 `merge`。
- `audio_mode=source`：通常跳过 `extract-audio`；`merge` 会在拼接生成画面后直接挂载完整源视频音轨，避免把源音频切段重编码造成 AAC 编码延迟累计，引发音画不同步。

## 质量闭环

```bash
seedance-role-scene-remake verify ./job_remake/manifest.json \
  -o ./job_remake/report.html \
  --quality-json ./job_remake/quality.json \
  --identity-report --scene-report --language-report --voice-report \
  --target-report --audio-report --continuity-report
```

若 `quality.json` 标记问题，从最早问题片段修复：

```bash
seedance-role-scene-remake repair ./job_remake/manifest.json --from-segment 3 --cascade --reason "identity or scene drift"
seedance-role-scene-remake remake ./job_remake/manifest.json
seedance-role-scene-remake merge ./job_remake/manifest.json -o ./final_repair.mp4
```

## Prompt 规则

- 每段请求必须有“参考素材指代表”：`视频1=原片段，仅参考动作/运镜/对白时序；图片1=某角色目标外观；图片N=目标场景；音频1=节奏或音色参考`。
- 优先走 dialogue-aligned 分段；prompt 中必须写相对时间轴，例如 `0.44-1.00s：c001（图片1）说 "You need to leave."`，并要求无对白角色闭口或聆听。
- 正文始终用 `角色名（图片N）` 和 `场景名（图片N）` 指代，避免多人串脸或模型误用源视频人物。
- 多角色必须显式绑定角色和妆造变体；同一角色、同一妆造、同一场景跨片段沿用同一设定。
- 默认保持源语种；指定目标语种时，全片对白、字幕和屏幕文字都统一翻译。
- 若用户明确要求更换角色声音，生成音轨必须来自 Seedance 生成视频；若模型或账号拒绝 `generate_audio` 或音频参考，工具会报错，不静默伪装为已换音色。
- 若用户要求“其他保持不变”并保留原语种/原声音，可设置 `audio_mode=source`、`generate_audio=false`；此时只重制画面，最终直接挂载完整源音轨。

详细原片分析目录和 JSON 约定见 `references/source-analysis.md`；详细 manifest 和 prompt 约定见 `references/spec-and-prompts.md`。

## 合规

真人人像、虚拟真人形象、角色图、音色样本和视频素材必须有授权。遇到真人、人像或音色审核失败时，不要规避审核；优先检查 `reference_report` 是否仍混入 raw TOS URL、data URL、未资产化尾帧或尾部视频。
