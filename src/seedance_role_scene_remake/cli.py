"""Typer CLI for Seedance character, scene, and voice remake."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from seedance_role_scene_remake import __version__
from seedance_role_scene_remake.config import load_config
from seedance_role_scene_remake.errors import RoleSceneError
from seedance_role_scene_remake.manifest import Manifest
from seedance_role_scene_remake.pipeline import (
    analyze_job,
    analyze_manifest_job,
    approve_job,
    asset_register_job,
    extract_audio_job,
    init_spec_job,
    merge_job,
    prepare_job,
    render_targets_job,
    review_job,
    refresh_status,
    remake_job,
    repair_job,
    run_job,
    split_job,
    summarize_status,
    upload_job,
    verify_job,
)
from seedance_role_scene_remake.prompts import inline_character, inline_scene

app = typer.Typer(
    add_completion=False,
    help=(
        "seedance-role-scene-remake — Seedance 2.0 视频换角色、换场景、换声音工具\n\n"
        "准备设定 -> 审阅批准 -> TOS 上传 -> 多模态参考重制 -> 提取生成音轨 -> 对齐拼接 -> 验证"
    ),
)


@app.callback()
def root(
    ctx: typer.Context,
    api_key: str = typer.Option("", "--api-key", help="临时指定 ARK_API_KEY。"),
    base_url: str = typer.Option("", "--base-url", help="临时指定火山方舟 Base URL。"),
    model: str = typer.Option("", "--model", help="临时指定 Seedance 模型或接入点。"),
    resolution: str = typer.Option("", "--resolution", help="临时指定分辨率，例如 720p。"),
    seedream_model: str = typer.Option("", "--seedream-model", help="临时指定 Seedream 目标参考图模型。"),
    seedream_size: str = typer.Option("", "--seedream-size", help="临时指定 Seedream 目标参考图尺寸，例如 2K。"),
    output_json: bool = typer.Option(False, "--json", help="错误信息使用 JSON 输出。"),
) -> None:
    ctx.obj = {
        "config": load_config(
            {
                "api_key": api_key,
                "base_url": base_url,
                "model": model,
                "resolution": resolution,
                "seedream_model": seedream_model,
                "seedream_size": seedream_size,
            }
        ),
        "json": output_json,
    }


@app.command()
def version() -> None:
    """显示版本号。"""
    typer.echo(f"seedance-role-scene-remake {__version__}")


@app.command("init-spec")
def init_spec(
    video: Path = typer.Argument(..., help="输入视频路径。"),
    output: Path = typer.Option(..., "-o", "--output", help="spec.yaml 输出路径。"),
) -> None:
    """生成角色/场景/音色配置模板。"""
    init_spec_job(video=video, output=output)


@app.command()
def split(
    ctx: typer.Context,
    video: Path = typer.Argument(..., help="输入视频路径。"),
    output: Path = typer.Option(..., "-o", "--output", help="任务输出目录。"),
    spec: Optional[Path] = typer.Option(None, "--spec", help="角色、场景、音色配置 YAML/JSON。"),
    character: list[str] = typer.Option([], "--character", help="内联角色：id=hero,image=...,voice=...,prompt=..."),
    scene: list[str] = typer.Option([], "--scene", help="内联场景：id=street,image=...,prompt=..."),
    character_prompt: str = typer.Option("", "--character-prompt", help="纯提示词角色设定。"),
    scene_prompt: str = typer.Option("", "--scene-prompt", help="纯提示词场景设定。"),
    voice_prompt: str = typer.Option("", "--voice-prompt", help="目标音色设定。"),
    prompt: str = typer.Option("", "--prompt", help="全局补充要求。"),
    ratio: str = typer.Option("auto", "--ratio", help="auto、9:16、16:9 等；默认保持源比例。"),
    segment_seconds: int = typer.Option(15, "--segment-seconds", "-s", help="每段最大秒数，范围 1-15。"),
    no_upload: bool = typer.Option(False, "--no-upload", help="只生成本地片段和 manifest，不上传 TOS。"),
) -> None:
    """分割视频、抽取帧和原音频，并生成 manifest.json。"""
    split_job(
        config=ctx.obj["config"],
        video=video,
        output=output,
        spec_path=spec,
        characters=[inline_character(item) for item in character],
        scenes=[inline_scene(item) for item in scene],
        character_prompt=character_prompt,
        scene_prompt=scene_prompt,
        voice_prompt=voice_prompt,
        prompt=prompt,
        ratio=ratio,
        segment_seconds=segment_seconds,
        no_upload=no_upload,
    )


@app.command()
def analyze(
    ctx: typer.Context,
    video: Path = typer.Argument(..., help="输入视频路径。"),
    output: Path = typer.Option(..., "-o", "--output", help="任务输出目录。"),
    analysis_model: str = typer.Option("", "--analysis-model", help="临时指定 Ark 视频/图片理解模型或接入点。"),
    asr_model: str = typer.Option("", "--asr-model", help="临时指定 Ark/豆包 ASR 模型或接入点。"),
    sample_seconds: float = typer.Option(2.0, "--sample-seconds", help="密集抽帧间隔秒数。"),
    scene_threshold: float = typer.Option(0.35, "--scene-threshold", help="FFmpeg 镜头变化检测阈值。"),
    script_detail: str = typer.Option("detailed", "--script-detail", help="剧本细节模式：standard 或 detailed。"),
    script_min_action_beats: int = typer.Option(2, "--script-min-action-beats", help="每个分场至少需要的动作节拍数。"),
    script_quality_json: Optional[Path] = typer.Option(None, "--script-quality-json", help="额外输出剧本质量检查 JSON。"),
    allow_skeleton: bool = typer.Option(False, "--allow-skeleton", help="缺少 Ark VLM/ASR 配置时仅输出本地骨架；默认禁止。"),
) -> None:
    """分析原视频，输出剧本和角色/场景/道具/声音源素材检查包。"""
    analyze_job(
        config=ctx.obj["config"],
        video=video,
        output=output,
        analysis_model=analysis_model,
        asr_model=asr_model,
        sample_seconds=sample_seconds,
        scene_threshold=scene_threshold,
        script_detail=script_detail,
        script_min_action_beats=script_min_action_beats,
        script_quality_json=script_quality_json,
        allow_skeleton=allow_skeleton,
    )


@app.command("analyze-manifest")
def analyze_manifest(
    analysis_path: Path = typer.Argument(..., help="analysis/analysis.json 路径。"),
) -> None:
    """打印原视频分析结果的人工检查清单。"""
    analyze_manifest_job(analysis_path=analysis_path)


@app.command()
def prepare(
    ctx: typer.Context,
    video: Path = typer.Argument(..., help="输入视频路径。"),
    output: Path = typer.Option(..., "-o", "--output", help="任务输出目录。"),
    spec: Optional[Path] = typer.Option(None, "--spec", help="可选初始配置 YAML/JSON。"),
    analysis: Optional[Path] = typer.Option(None, "--analysis", help="可选 analysis/analysis.json；将剧本和源素材索引写入 manifest。"),
    ratio: str = typer.Option("auto", "--ratio", help="auto、9:16、16:9 等；默认保持源比例。"),
    segment_seconds: int = typer.Option(15, "--segment-seconds", "-s", help="每段最大秒数，范围 1-15。"),
    source_language: str = typer.Option("auto", "--source-language", help="源视频语种，默认 auto。"),
    target_language: str = typer.Option("preserve_source", "--target-language", help="目标语种；默认 preserve_source。"),
    spoken_language: str = typer.Option("preserve_source", "--spoken-language", help="角色口语语种；默认 preserve_source，可设 en/zh-CN 等。"),
    character_prompt: str = typer.Option("", "--character-prompt", help="目标角色整体意图；缺少目标角色图时用于 Seedream 生成参考图。"),
    scene_prompt: str = typer.Option("", "--scene-prompt", help="目标场景整体意图；缺少目标场景图时用于 Seedream 生成参考图。"),
    voice_prompt: str = typer.Option("", "--voice-prompt", help="目标角色声音整体意图。"),
    prompt: str = typer.Option("", "--prompt", help="全局任务意图。"),
    auto_targets: bool = typer.Option(True, "--auto-targets/--no-auto-targets", help="缺少目标角色/场景参考图时自动调用 Seedream 5.0 Lite 生成。"),
    target_size: str = typer.Option("", "--target-size", help="目标参考图尺寸，默认使用配置中的 Seedream size。"),
) -> None:
    """生成可审阅的角色/妆造/场景/声音/语言准备草稿，不提交生成。"""
    prepare_job(
        config=ctx.obj["config"],
        video=video,
        output=output,
        spec_path=spec,
        analysis_path=analysis,
        ratio=ratio,
        segment_seconds=segment_seconds,
        source_language=source_language,
        target_language=target_language,
        spoken_language=spoken_language,
        character_prompt=character_prompt,
        scene_prompt=scene_prompt,
        voice_prompt=voice_prompt,
        prompt=prompt,
        auto_render_targets=auto_targets,
        target_size=target_size or None,
    )


@app.command()
def review(
    manifest_path: Path = typer.Argument(..., help="manifest.json 路径。"),
) -> None:
    """检查准备阶段设定是否足够完整。"""
    review_job(manifest_path=manifest_path)


@app.command()
def approve(
    manifest_path: Path = typer.Argument(..., help="manifest.json 路径。"),
) -> None:
    """在设定完整时批准准备阶段，允许后续 remake/run。"""
    approve_job(manifest_path=manifest_path)


@app.command("render-targets")
def render_targets(
    ctx: typer.Context,
    manifest_path: Path = typer.Argument(..., help="manifest.json 路径。"),
    characters: bool = typer.Option(True, "--characters/--no-characters", help="为缺少目标图的角色妆造生成参考素材。"),
    scenes: bool = typer.Option(True, "--scenes/--no-scenes", help="为缺少目标图的场景生成参考素材。"),
    size: str = typer.Option("", "--size", help="目标参考图尺寸，默认使用配置中的 Seedream size。"),
    overwrite: bool = typer.Option(False, "--overwrite", help="覆盖已存在的目标参考图。"),
) -> None:
    """显式调用 Seedream 5.0 Lite 生成目标角色/场景参考图；不会自动批准。"""
    render_targets_job(
        config=ctx.obj["config"],
        manifest_path=manifest_path,
        characters=characters,
        scenes=scenes,
        size=size or None,
        overwrite=overwrite,
    )


@app.command()
def upload(
    ctx: typer.Context,
    manifest_path: Path = typer.Argument(..., help="manifest.json 路径。"),
    force: bool = typer.Option(False, "--force", help="重新上传已有 URL 的素材。"),
) -> None:
    """上传 manifest 中尚未上传的视频、音频、角色图和场景图。"""
    upload_job(config=ctx.obj["config"], manifest_path=manifest_path, force=force)


@app.command("asset-register")
def asset_register(
    ctx: typer.Context,
    manifest_path: Path = typer.Argument(..., help="manifest.json 路径。"),
    group_name: str = typer.Option("seedance-role-scene-remake", "--group-name", "-g", help="资产组名称。"),
    no_wait: bool = typer.Option(False, "--no-wait", help="注册后不等待资产 Active。"),
) -> None:
    """将 TOS 视频参考注册为私域资产 asset:// URI。"""
    asset_register_job(config=ctx.obj["config"], manifest_path=manifest_path, group_name=group_name, wait=not no_wait)


@app.command()
def remake(
    ctx: typer.Context,
    manifest_path: Path = typer.Argument(..., help="manifest.json 路径。"),
    stop_on_error: bool = typer.Option(False, "--stop-on-error", help="任一片段失败后停止。"),
    allow_unprepared: bool = typer.Option(False, "--allow-unprepared", help="允许使用未批准设定生成；不推荐。"),
) -> None:
    """提交或恢复 Seedance 任务，并下载生成片段。"""
    remake_job(
        config=ctx.obj["config"],
        manifest_path=manifest_path,
        stop_on_error=stop_on_error,
        allow_unprepared=allow_unprepared,
    )


@app.command("extract-audio")
def extract_audio(
    manifest_path: Path = typer.Argument(..., help="manifest.json 路径。"),
    stop_on_error: bool = typer.Option(False, "--stop-on-error", help="任一片段音频失败后停止。"),
) -> None:
    """从生成视频中提取新音轨，并按原片段时长对齐。"""
    extract_audio_job(manifest_path=manifest_path, stop_on_error=stop_on_error)


@app.command()
def merge(
    manifest_path: Path = typer.Argument(..., help="manifest.json 路径。"),
    output: Optional[Path] = typer.Option(None, "-o", "--output", help="输出视频路径，默认 job/final.mp4。"),
) -> None:
    """拼接成功片段，并使用生成音轨合成最终视频。"""
    merge_job(manifest_path=manifest_path, output=output)


@app.command()
def status(
    ctx: typer.Context,
    manifest_path: Path = typer.Argument(..., help="manifest.json 路径。"),
    refresh: bool = typer.Option(False, "--refresh", help="调用火山方舟刷新远端任务状态。"),
) -> None:
    """查看 manifest 状态和失败原因。"""
    if refresh:
        refresh_status(config=ctx.obj["config"], manifest_path=manifest_path)
    manifest = Manifest.load(manifest_path)
    for line in summarize_status(manifest):
        typer.echo(line)


@app.command()
def verify(
    manifest_path: Path = typer.Argument(..., help="manifest.json 路径。"),
    output: Optional[Path] = typer.Option(None, "-o", "--output", help="HTML 报告输出路径。"),
    quality_json: Optional[Path] = typer.Option(None, "--quality-json", help="输出结构化质量检测 JSON。"),
    audio_report: bool = typer.Option(False, "--audio-report", help="检查生成音轨是否存在并与片段时长对齐。"),
    continuity_report: bool = typer.Option(False, "--continuity-report", help="记录视觉连续性检查入口。"),
    identity_report: bool = typer.Option(False, "--identity-report", help="检查角色/妆造绑定完整性。"),
    scene_report: bool = typer.Option(False, "--scene-report", help="检查场景绑定完整性。"),
    language_report: bool = typer.Option(False, "--language-report", help="检查语种策略完整性。"),
    voice_report: bool = typer.Option(False, "--voice-report", help="检查角色声音绑定完整性。"),
    target_report: bool = typer.Option(False, "--target-report", help="检查目标角色/场景参考图是否完整。"),
) -> None:
    """生成 HTML/JSON 效果报告。"""
    verify_job(
        manifest_path=manifest_path,
        output=output,
        quality_json=quality_json,
        audio_report=audio_report,
        continuity_report=continuity_report,
        identity_report=identity_report,
        scene_report=scene_report,
        language_report=language_report,
        voice_report=voice_report,
        target_report=target_report,
    )


@app.command()
def repair(
    manifest_path: Path = typer.Argument(..., help="manifest.json 路径。"),
    from_segment: int = typer.Option(..., "--from-segment", help="从该片段开始重置，例如 3。"),
    cascade: bool = typer.Option(True, "--cascade/--single", help="级联重置到结尾，或只重置单段。"),
    reason: str = typer.Option("quality repair", "--reason", help="写入 repair_history 的修复原因。"),
    archive: bool = typer.Option(True, "--archive/--no-archive", help="归档旧生成视频和音轨。"),
) -> None:
    """将问题片段重置为 pending，保留上传引用，便于重新 remake。"""
    repair_job(manifest_path=manifest_path, from_segment=from_segment, cascade=cascade, reason=reason, archive=archive)


@app.command()
def run(
    ctx: typer.Context,
    video: Path = typer.Argument(..., help="输入视频路径。"),
    output: Path = typer.Option(..., "-o", "--output", help="任务输出目录。"),
    spec: Optional[Path] = typer.Option(None, "--spec", help="角色、场景、音色配置 YAML/JSON。"),
    character: list[str] = typer.Option([], "--character", help="内联角色：id=hero,image=...,voice=...,prompt=..."),
    scene: list[str] = typer.Option([], "--scene", help="内联场景：id=street,image=...,prompt=..."),
    character_prompt: str = typer.Option("", "--character-prompt", help="纯提示词角色设定。"),
    scene_prompt: str = typer.Option("", "--scene-prompt", help="纯提示词场景设定。"),
    voice_prompt: str = typer.Option("", "--voice-prompt", help="目标音色设定。"),
    prompt: str = typer.Option("", "--prompt", help="全局补充要求。"),
    ratio: str = typer.Option("auto", "--ratio", help="auto、9:16、16:9 等；默认保持源比例。"),
    segment_seconds: int = typer.Option(15, "--segment-seconds", "-s", help="每段最大秒数，范围 1-15。"),
    generated_audio: bool = typer.Option(True, "--generated-audio/--no-generated-audio", help="必须使用 Seedance 生成音轨。"),
    final_output: Optional[Path] = typer.Option(None, "--final-output", help="最终成片路径，默认 job/final.mp4。"),
    no_upload: bool = typer.Option(False, "--no-upload", help="保留给 split 兼容；run 不允许跳过上传。"),
    stop_on_error: bool = typer.Option(False, "--stop-on-error", help="任一片段失败后停止。"),
    allow_unprepared: bool = typer.Option(False, "--allow-unprepared", help="允许使用未批准设定生成；不推荐。"),
) -> None:
    """一键执行：split -> upload -> remake -> extract-audio -> merge -> verify。"""
    if not generated_audio:
        raise typer.BadParameter("本工具 v1 不支持 --no-generated-audio；声音必须来自 Seedance 生成视频。")
    run_job(
        config=ctx.obj["config"],
        video=video,
        output=output,
        spec_path=spec,
        characters=[inline_character(item) for item in character],
        scenes=[inline_scene(item) for item in scene],
        character_prompt=character_prompt,
        scene_prompt=scene_prompt,
        voice_prompt=voice_prompt,
        prompt=prompt,
        ratio=ratio,
        segment_seconds=segment_seconds,
        final_output=final_output,
        no_upload=no_upload,
        stop_on_error=stop_on_error,
        allow_unprepared=allow_unprepared,
    )


def main() -> None:
    try:
        app()
    except RoleSceneError as exc:
        output_json = "--json" in sys.argv
        if output_json:
            typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False), err=True)
        else:
            typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    main()
