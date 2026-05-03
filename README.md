# seedance-role-scene-remake

面向 Seedance 2.0 的视频换角色、换场景、换声音 CLI 和 Codex Skill。

目标不是简单套风格，而是尽量保持输入视频的剧情事件、动作轨迹、人物站位、镜头运动、剪辑节奏、对白语义和对白时序不变，只替换已确认的角色形象、角色妆造、场景设定和角色声音。

## 核心流程

推荐工作流是：

```text
analyze -> 人工检查源素材 -> dialogue-aligned prepare -> review -> approve -> upload -> remake(多参考) -> 音频处理 -> merge -> verify -> repair
```

关键原则：

- 先分析原片，生成剧本、源角色、源场景、道具和声音样本检查包。
- 再准备目标角色、目标妆造、目标场景、声音和语言策略。
- 未批准的 manifest 默认不能提交 Seedance 生成。
- 源视频帧只用于理解原片，不作为新角色或新场景外观参考。
- 目标参考图统一放在 `target_refs/`，并在 prompt 中用 `图片1/图片2/...` 明确指代。
- 多参考生成默认使用 `--reference-strategy full`，每段最多编排 `9` 张图片、`3` 段视频、`3` 段音频，并在报告里记录实际槽位。

## 功能

- 原视频剧本化：按分场输出中文剧本，格式接近影视脚本。
- 源素材检查包：导出源角色、源场景、源道具、源声音样本和 HTML 联系表。
- 角色和妆造归类：自动生成候选角色、候选妆造变体和候选场景簇，供人工复核。
- 目标参考图生成：缺少角色/场景目标图时，可调用 Seedream 5.0 Lite 生成。
- Seedance 多参考重制：目标角色/场景图锁定外观，源片段或授权私域资产参考动作/运镜/时序，源音频片段参考英文对白节奏。
- 音频策略：支持 Seedance 生成音轨，也支持“只换画面、保留源音轨”。保留源音轨时，`merge` 直接挂载完整源视频音轨，避免分段重编码造成音画偏移。
- 可恢复任务：`manifest.json` 记录上传、提交、轮询、下载、修复历史，支持中断后继续。
- 质量报告：检查角色/场景/语言/声音绑定、目标参考图、音轨时长和片段连续性。

## 安装

需要本机已安装：

- Python 3.9+
- `ffmpeg`
- `ffprobe`

安装 CLI 和本地 Codex Skill：

```bash
git clone https://github.com/bolecodex/seedance-role-scene-remake.git
cd seedance-role-scene-remake
bash scripts/setup-local.sh
source .venv/bin/activate
```

也可以只安装 Python 包：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

检查：

```bash
seedance-role-scene-remake version
seedance-role-scene-remake --help
```

未安装 entrypoint 时，也可以用：

```bash
PYTHONPATH=src python3 -m seedance_role_scene_remake.cli --help
```

## 配置

复制示例环境变量：

```bash
cp .env.example .env
```

最少需要配置：

```bash
ARK_API_KEY=...
ARK_BASE_URL=https://ark.cn-beijing.volces.com

SEEDANCE_ROLE_SCENE_MODEL=doubao-seedance-2-0-260128
SEEDANCE_ROLE_SCENE_RESOLUTION=720p

VOLC_ACCESSKEY=...
VOLC_SECRETKEY=...
TOS_BUCKET=...
TOS_ENDPOINT=tos-cn-beijing.volces.com
TOS_REGION=cn-beijing
```

原视频分析推荐使用 Ark VLM + 豆包流式语音识别 2.0：

```bash
SEEDANCE_ROLE_SCENE_ANALYSIS_MODEL=...
SEEDANCE_ROLE_SCENE_ASR_PROVIDER=doubao_streaming
SEEDANCE_ROLE_SCENE_ASR_MODEL=doubao_streaming_2.0
SEEDANCE_ROLE_SCENE_DOUBAO_ASR_APP_ID=...
SEEDANCE_ROLE_SCENE_DOUBAO_ASR_ACCESS_TOKEN=...
SEEDANCE_ROLE_SCENE_DOUBAO_ASR_SECRET_KEY=...
SEEDANCE_ROLE_SCENE_DOUBAO_ASR_WS_URL=wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
SEEDANCE_ROLE_SCENE_DOUBAO_ASR_RESOURCE_ID=volc.bigasr.sauc.duration
```

目标参考图生成使用 Seedream：

```bash
SEEDANCE_ROLE_SCENE_SEEDREAM_MODEL=doubao-seedream-5-0-lite-260128
SEEDANCE_ROLE_SCENE_SEEDREAM_SIZE=2K
```

不要把真实 `.env`、视频素材、生成任务目录或最终视频提交到仓库。

## 快速开始

### 1. 分析原视频

```bash
seedance-role-scene-remake analyze ./video/input.mp4 \
  -o ./job_input_analysis \
  --analysis-model "$SEEDANCE_ROLE_SCENE_ANALYSIS_MODEL" \
  --asr-model doubao_streaming_2.0 \
  --sample-seconds 2 \
  --scene-threshold 0.35 \
  --script-detail detailed \
  --script-min-action-beats 2
```

查看检查清单：

```bash
seedance-role-scene-remake analyze-manifest ./job_input_analysis/analysis/analysis.json
```

重点打开：

- `job_input_analysis/analysis/index.html`
- `job_input_analysis/analysis/roles/index.html`
- `job_input_analysis/analysis/script/剧本.md`
- `job_input_analysis/analysis/script/script_quality.json`
- `job_input_analysis/analysis/characters/`
- `job_input_analysis/analysis/scenes/`
- `job_input_analysis/analysis/props/`
- `job_input_analysis/analysis/voices/`

剧本检查重点：动作和情绪要可观察。不要只写“女主冷淡”或“男主强硬”，应尽量写成“女主移开视线，嘴角压平，回答前停顿半拍”或“男主下巴微收，压低声音，视线没有离开对方”。无法判断的细节会在 `script_quality.json` 中标记，供人工复核。

### 2. 准备目标设定

示例：把角色改成自然现代中国人，场景改成现代中式家居，人物继续说英文。

```bash
seedance-role-scene-remake prepare ./video/input.mp4 \
  -o ./job_input_remake \
  --analysis ./job_input_analysis/analysis/analysis.json \
  --dialogue-aligned \
  --dialogue-max-seconds 8 \
  --segment-seconds 12 \
  --source-language en \
  --target-language preserve_source \
  --spoken-language en \
  --character-prompt "所有角色替换为自然现代中国人，保留原年龄层、性别呈现、人物关系、动作、站位和表情强弱，不要多人同脸" \
  --scene-prompt "现代中式家居，木质家具，暖色灯光，屏风、书柜、茶几、中式软装，保持原镜头构图和空间动线" \
  --voice-prompt "自然英文对白，中国人声线，保持原片情绪、停顿和对白时序" \
  --auto-targets
```

`prepare` 会生成：

- `job_input_remake/manifest.json`
- `job_input_remake/preparation/keyframes/`
- `job_input_remake/preparation/person_candidates/`
- `job_input_remake/preparation/scene_candidates/`
- `job_input_remake/preparation/contact_sheet.html`

如果没有提供目标角色图或目标场景图，`--auto-targets` 会调用 Seedream 5.0 Lite 生成：

- `job_input_remake/target_refs/characters/{appearance_variant_id}.jpg`
- `job_input_remake/target_refs/characters/{appearance_variant_id}.prompt.txt`
- `job_input_remake/target_refs/scenes/{scene_id}.jpg`
- `job_input_remake/target_refs/scenes/{scene_id}.prompt.txt`

这些 `target_refs` 才是 Seedance 生成时的目标外观参考。`analysis/` 和 `preparation/` 下的源帧只用于理解原片和人工检查。

### 3. 审阅并批准

```bash
seedance-role-scene-remake review ./job_input_remake/manifest.json
seedance-role-scene-remake approve ./job_input_remake/manifest.json
```

若 review 提示仍有未确认源角色、源场景、道具、声音或低置信度项，请先打开 HTML 检查包修正 manifest，再 approve。

### 4. 上传、生成、合成

```bash
seedance-role-scene-remake upload ./job_input_remake/manifest.json
seedance-role-scene-remake asset-register ./job_input_remake/manifest.json --group-type AIGC
seedance-role-scene-remake remake ./job_input_remake/manifest.json --reference-strategy full --reference-privacy assetized --stop-on-error
seedance-role-scene-remake merge ./job_input_remake/manifest.json -o ./final_input_remake.mp4
```

`--reference-strategy` 可选：

- `full`：优先使用目标图、源视频、上一段尾部、源音频节奏参考，尽量吃满 Seedance 多参考能力。
- `safe`：不传任何视频或音频参考；只使用目标图、上一段生成尾帧图片和 ASR 时间表，适合源视频参考触发真人隐私审核时继续生成。
- `script-only`：只使用目标图和剧本/ASR 时间表，不传源视频或音频参考。

`--reference-privacy` 可选：

- `assetized`：默认值。视频/图片参考必须是 `asset://`，或在提交前自动注册到私域素材库；默认使用 `AIGC` 虚拟素材库，它同样可以承载授权真人素材。
- `raw`：显式允许直接提交 TOS URL/data URL；若触发隐私审核，CLI 只记录失败，不绕过审核。
- `script-only`：不传源视频、音频、尾帧或尾部视频，只使用目标图和 ASR 时间轴。

`asset-register --group-type AIGC` 会把已上传的视频、图片、音频参考注册为私域资产。`LivenessFace` 是可选的真人人像库路径，不是使用真人素材的唯一前提；遇到拒审时优先检查 `reference_report` 中是否仍混入 raw TOS/data URL 或尾帧 data URL。

CLI 在 `split/prepare` 时会把每段源音频参考导出为 `source_audio/*.mp3`，用于 Seedance 的音频参考槽；最终成片如果使用 `audio_mode=source`，仍会直接挂载完整原视频音轨。

如果源视频、尾帧、尾部视频、图片或音频参考触发模型/账号能力限制，CLI 会在片段错误中保留具体失败原因和已使用的槽位指代表，不会静默伪装为已使用参考。

如果 manifest 使用 `audio_mode=generated`，在 `merge` 前先运行：

```bash
seedance-role-scene-remake extract-audio ./job_input_remake/manifest.json --stop-on-error
```

如果 manifest 使用 `audio_mode=source`，通常不要运行 `extract-audio`；`merge` 会直接使用 `manifest.source` 指向的完整源视频音轨。这样比切段抽取、逐段 AAC 重编码再拼接更不容易产生累计延迟。

也可以使用 `run` 做旧式一键流程，但它仍要求准备阶段已批准，除非显式加 `--allow-unprepared`：

```bash
seedance-role-scene-remake run ./video/input.mp4 \
  -o ./job_input_remake \
  --spec ./spec.yaml \
  --segment-seconds 12 \
  --final-output ./final_input_remake.mp4 \
  --stop-on-error
```

## 验证和修复

```bash
seedance-role-scene-remake verify ./job_input_remake/manifest.json \
  -o ./job_input_remake/report.html \
  --quality-json ./job_input_remake/quality.json \
  --identity-report \
  --scene-report \
  --language-report \
  --voice-report \
  --target-report \
  --audio-report \
  --continuity-report
```

从最早的问题片段级联修复：

```bash
seedance-role-scene-remake repair ./job_input_remake/manifest.json \
  --from-segment 3 \
  --cascade \
  --reason "identity or scene drift"

seedance-role-scene-remake remake ./job_input_remake/manifest.json --stop-on-error
seedance-role-scene-remake merge ./job_input_remake/manifest.json -o ./final_input_remake_repair.mp4
```

## Prompt 规则

生成视频时，工具会为每段 prompt 写清楚参考素材指代表：

```text
视频1=原片段，仅参考动作、站位、运镜、构图、剪辑节奏、对白语义和对白时序
图片1=某角色某妆造的目标外观
图片2=目标场景外观
音频1=原片段音频，仅参考对白节奏、停顿和情绪
```

多人场景应始终使用 `角色名（图片N）` 指代。不要把源视频中的欧美脸、金发、原服装或原场景陈设当作目标外观。

语言策略：

- 默认 `target_language=preserve_source`、`spoken_language=preserve_source`。
- 只想让角色继续说英文时，设置 `--spoken-language en`，不要把 `target_language` 改成中文。
- 如果明确要改成某个语种，应全片对白、字幕和屏幕文字统一处理。

## 目录约定

```text
analysis/                         原视频分析和人工检查包
analysis/script/剧本.md            中文剧本
analysis/script/script_quality.json 剧本细节质量检查
analysis/roles/                   源角色人工检查包，每个角色一个独立文件夹
analysis/roles/*/person_crops/    bbox 裁出的单角色截图
analysis/roles/*/full_frames/     原始全帧证据，可能包含其他人物
analysis/characters/              机器可读源角色 profile 和全帧证据
analysis/scenes/                  源场景证据
analysis/props/                   源道具证据
analysis/voices/                  源声音样本和转写

preparation/keyframes/            原片关键帧
preparation/person_candidates/    原片人物候选裁剪
preparation/scene_candidates/     原片场景候选
preparation/contact_sheet.html    准备阶段总览

target_refs/characters/           目标角色/妆造参考图
target_refs/scenes/               目标场景参考图

segments/                         原视频分段
remade/                           Seedance 生成片段
audio/                            原音频、生成音轨、对齐音轨
manifest.json                     可恢复任务状态
report.html / quality.json        质量报告
```

## 测试

```bash
PYTHONPATH=src python3 -m pytest -q
```

## Skill

本仓库自带 Codex Skill：

```text
skills/seedance-role-scene-remake/SKILL.md
```

运行 `scripts/setup-local.sh` 会复制到：

```text
${CODEX_HOME:-$HOME/.codex}/skills/seedance-role-scene-remake
```

## 合规

真人人像、虚拟真人形象、角色图、音色样本和视频素材必须有授权。遇到真人、人像或音色审核失败时，不要规避审核；优先把所有视觉参考注册为 AIGC/LivenessFace 私域资产并确认 Active 后再重试。

## 参考

- 火山方舟 Seedance / Seedream / 多模态能力
- 豆包流式语音识别 2.0
- `docs/` 下的 Seedance 2.0 提示词指南
- `skills/seedance-role-scene-remake/references/` 下的分析、manifest 和 prompt 约定
