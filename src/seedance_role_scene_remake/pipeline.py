"""Composable pipeline operations used by the Typer CLI."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import typer

from seedance_role_scene_remake.analysis import (
    apply_source_analysis_to_manifest,
    run_source_analysis,
    summarize_source_analysis,
)
from seedance_role_scene_remake.assets import AssetsClient
from seedance_role_scene_remake.config import AppConfig
from seedance_role_scene_remake.errors import ManifestError, PipelineError, UploadError
from seedance_role_scene_remake.ffmpeg import (
    concat_audios,
    concat_videos,
    duration_for_generation,
    extract_audio,
    extract_first_frame,
    get_video_duration,
    get_video_fps,
    get_video_ratio,
    image_to_data_url,
    mux_audio,
    normalize_audio_duration,
    normalize_video_duration,
    parse_ratio,
    split_video,
)
from seedance_role_scene_remake.manifest import (
    AppearanceVariantSpec,
    CharacterSpec,
    LanguagePolicy,
    Manifest,
    PreparationSpec,
    ReferenceAsset,
    SceneSpec,
    SegmentEntry,
    StoryBible,
    VoiceSpec,
    load_spec,
    spec_template,
)
from seedance_role_scene_remake.preparation import enrich_preparation_draft, preparation_issues
from seedance_role_scene_remake.preparation import write_contact_sheet
from seedance_role_scene_remake.prompts import build_generation_prompt
from seedance_role_scene_remake.seedance import (
    SeedanceClient,
    VideoGenerateRequest,
    download_file,
    normalize_status,
    poll_task,
)
from seedance_role_scene_remake.seedream import ImageGenerateRequest, SeedreamClient, save_generated_image
from seedance_role_scene_remake.tos_upload import TOSConfig, upload_file
from seedance_role_scene_remake.verify import build_quality_report, write_html_report


def _relative(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def _path_from_manifest(value: str | None, job_dir: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else job_dir / path


def _client(config: AppConfig) -> SeedanceClient:
    if not config.api_key:
        raise PipelineError("缺少 ARK_API_KEY。提交 Seedance 任务前请先配置环境变量或 .env。")
    return SeedanceClient(
        api_key=config.api_key,
        base_url=config.base_url,
        submit_endpoint=config.submit_endpoint,
        status_endpoint_template=config.status_endpoint_template,
        timeout_s=config.request_timeout_s,
    )


def _seedream_client(config: AppConfig) -> SeedreamClient:
    if not config.api_key:
        raise PipelineError("缺少 ARK_API_KEY。生成目标参考图需要调用 Seedream 5.0 Lite。")
    return SeedreamClient(
        api_key=config.api_key,
        base_url=config.base_url,
        image_endpoint=config.seedream_image_endpoint,
        timeout_s=config.request_timeout_s,
    )


def _tos_config(config: AppConfig) -> TOSConfig:
    if not config.tos_available:
        raise UploadError("视频、音频和图片参考模式需要 TOS：请配置 VOLC_ACCESSKEY、VOLC_SECRETKEY、TOS_BUCKET。")
    return TOSConfig(
        access_key=config.tos_access_key,
        secret_key=config.tos_secret_key,
        bucket=config.tos_bucket,
        endpoint=config.tos_endpoint,
        region=config.tos_region,
        presign_expires_s=config.tos_presign_expires_s,
    )


def init_spec_job(video: Path, output: Path) -> Path:
    if not video.exists():
        raise PipelineError(f"输入视频不存在：{video}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(spec_template(video), encoding="utf-8")
    typer.echo(f"Spec 模板已保存：{output}")
    return output


def split_job(
    *,
    config: AppConfig,
    video: Path,
    output: Path,
    spec_path: Path | None = None,
    characters: list[CharacterSpec] | None = None,
    scenes: list[SceneSpec] | None = None,
    character_prompt: str = "",
    scene_prompt: str = "",
    voice_prompt: str = "",
    prompt: str = "",
    ratio: str = "auto",
    segment_seconds: int = 15,
    no_upload: bool = False,
) -> Path:
    if not video.exists():
        raise PipelineError(f"输入视频不存在：{video}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "segments").mkdir(exist_ok=True)
    (output / "frames").mkdir(exist_ok=True)
    (output / "source_audio").mkdir(exist_ok=True)
    (output / "remade").mkdir(exist_ok=True)
    (output / "generated_audio").mkdir(exist_ok=True)
    (output / "aligned").mkdir(exist_ok=True)

    spec = _load_spec_or_defaults(spec_path, characters or [], scenes or [], character_prompt, scene_prompt, voice_prompt, prompt)
    source_ratio = get_video_ratio(video)
    target_ratio = parse_ratio(ratio or str(spec.get("ratio", "auto")), source_ratio)
    segment_paths = split_video(video, output / "segments", segment_seconds)
    typer.echo(f"已分割 {len(segment_paths)} 个参考片段")

    default_segment = (spec.get("segments") or {}).get("default", {}) if isinstance(spec.get("segments"), dict) else {}
    entries: list[SegmentEntry] = []
    start = 0.0
    for idx, segment in enumerate(segment_paths):
        duration = get_video_duration(segment)
        frame = output / "frames" / f"{idx:03d}.jpg"
        source_audio = output / "source_audio" / f"{idx:03d}.m4a"
        extract_first_frame(segment, frame)
        audio_path = _relative(source_audio, output) if extract_audio(segment, source_audio) else None
        per_segment = _segment_spec(spec, idx, default_segment)
        char_ids = list(per_segment.get("characters", []))
        scene_ids = list(per_segment.get("scenes", []))
        variant_ids = list(per_segment.get("character_variants", per_segment.get("variants", [])))
        if not variant_ids:
            variant_ids = [f"{char_id}_default" for char_id in char_ids]
        voice_ids = list(per_segment.get("voices", []))
        if not voice_ids:
            voice_ids = _voice_ids_for_characters(spec, char_ids)
        entries.append(
            SegmentEntry(
                index=idx,
                start=start,
                duration=duration,
                reference_duration=duration,
                generation_duration=duration_for_generation(segment),
                source_path=_relative(segment, output),
                frame_path=_relative(frame, output),
                reference_path=_relative(segment, output),
                source_audio_path=audio_path,
                character_ids=char_ids,
                scene_ids=scene_ids,
                character_variant_ids=variant_ids,
                voice_ids=voice_ids,
            )
        )
        start += duration

    manifest = Manifest(
        source=str(video.resolve()),
        source_ratio=source_ratio,
        target_ratio=target_ratio,
        prompt=str(spec.get("prompt") or ""),
        model=config.model,
        resolution=config.resolution,
        segment_seconds=segment_seconds,
        dialogue_fidelity=str(spec.get("dialogue_fidelity") or "strict"),
        audio_mode=str(spec.get("audio_mode") or "generated"),
        generate_audio=bool(spec.get("generate_audio", True)),
        preparation=_normalize_preparation(spec.get("preparation", {})),
        story_bible=_normalize_dataclass(StoryBible, spec.get("story_bible", {})),
        language_policy=_normalize_dataclass(LanguagePolicy, spec.get("language_policy", {})),
        voice_registry=_normalize_voices(spec.get("voice_registry", []), base=spec_path.parent if spec_path else Path.cwd()),
        characters=_normalize_characters(spec.get("characters", []), base=spec_path.parent if spec_path else Path.cwd()),
        scenes=_normalize_scenes(spec.get("scenes", []), base=spec_path.parent if spec_path else Path.cwd()),
        segments=entries,
    )
    manifest_path = output / "manifest.json"
    manifest.save(manifest_path)
    typer.echo(f"Manifest 已保存：{manifest_path}")
    if not no_upload:
        upload_job(config=config, manifest_path=manifest_path)
    return manifest_path


def prepare_job(
    *,
    config: AppConfig,
    video: Path,
    output: Path,
    spec_path: Path | None = None,
    analysis_path: Path | None = None,
    ratio: str = "auto",
    segment_seconds: int = 15,
    source_language: str = "auto",
    target_language: str = "preserve_source",
    spoken_language: str = "preserve_source",
    character_prompt: str = "",
    scene_prompt: str = "",
    voice_prompt: str = "",
    prompt: str = "",
    auto_render_targets: bool = False,
    target_size: str | None = None,
) -> Path:
    manifest_path = split_job(
        config=config,
        video=video,
        output=output,
        spec_path=spec_path,
        character_prompt=character_prompt,
        scene_prompt=scene_prompt,
        voice_prompt=voice_prompt,
        prompt=prompt,
        ratio=ratio,
        segment_seconds=segment_seconds,
        no_upload=True,
    )
    manifest = Manifest.load(manifest_path)
    contact_sheet = enrich_preparation_draft(
        manifest,
        job_dir=manifest_path.parent,
        source_language=source_language,
        target_language=target_language,
        spoken_language=spoken_language,
    )
    if analysis_path:
        apply_source_analysis_to_manifest(manifest, analysis_path=analysis_path, job_dir=manifest_path.parent)
        manifest.preparation.required_review_items = preparation_issues(manifest)
    manifest.save(manifest_path)
    if auto_render_targets and _has_missing_target_refs(manifest):
        typer.echo("检测到缺少目标角色/场景参考图，开始调用 Seedream 5.0 Lite 生成 target_refs 素材。")
        render_targets_job(
            config=config,
            manifest_path=manifest_path,
            characters=True,
            scenes=True,
            size=target_size or config.seedream_size,
            overwrite=False,
        )
        manifest = Manifest.load(manifest_path)
        contact_sheet = write_contact_sheet(manifest, job_dir=manifest_path.parent, output=manifest_path.parent / "preparation" / "contact_sheet.html")
        manifest.preparation.contact_sheet_path = _relative(contact_sheet, manifest_path.parent)
        manifest.save(manifest_path)
    typer.echo(f"准备阶段草稿已保存：{manifest_path}")
    typer.echo(f"联系表已保存：{contact_sheet}")
    typer.echo("请根据联系表复核自动分类和目标参考图，必要时校正角色/妆造/场景/声音/语言策略，然后运行 review 与 approve。")
    return manifest_path


def analyze_job(
    *,
    config: AppConfig,
    video: Path,
    output: Path,
    analysis_model: str = "",
    asr_model: str = "",
    sample_seconds: float = 2.0,
    scene_threshold: float = 0.35,
    allow_skeleton: bool = False,
) -> Path:
    analysis_path = run_source_analysis(
        config=config,
        video=video,
        output=output,
        analysis_model=analysis_model,
        asr_model=asr_model,
        sample_seconds=sample_seconds,
        scene_threshold=scene_threshold,
        allow_skeleton=allow_skeleton,
    )
    typer.echo(f"原视频分析已保存：{analysis_path}")
    for line in summarize_source_analysis(analysis_path):
        typer.echo(line)
    return analysis_path


def analyze_manifest_job(*, analysis_path: Path) -> list[str]:
    lines = summarize_source_analysis(analysis_path)
    for line in lines:
        typer.echo(line)
    return lines


def review_job(*, manifest_path: Path) -> list[str]:
    manifest = Manifest.load(manifest_path)
    issues = preparation_issues(manifest)
    blocking = [issue for issue in issues if not issue.startswith("preparation.status ")]
    if blocking:
        typer.echo("准备设定尚未完整：")
        for issue in blocking:
            typer.echo(f"- {issue}")
    else:
        typer.echo("准备设定完整，可以运行 approve。")
    if manifest.preparation.contact_sheet_path:
        typer.echo(f"联系表：{manifest_path.parent / manifest.preparation.contact_sheet_path}")
    return blocking


def approve_job(*, manifest_path: Path) -> None:
    manifest = Manifest.load(manifest_path)
    issues = [issue for issue in preparation_issues(manifest) if not issue.startswith("preparation.status ")]
    if issues:
        detail = "\n".join(f"- {issue}" for issue in issues)
        raise ManifestError(f"准备设定还不能批准：\n{detail}")
    manifest.preparation.status = "approved"
    manifest.preparation.approved_at = datetime.now().isoformat(timespec="seconds")
    manifest.preparation.required_review_items = []
    manifest.save(manifest_path)
    typer.echo(f"准备设定已批准：{manifest_path}")


def render_targets_job(
    *,
    config: AppConfig,
    manifest_path: Path,
    characters: bool = True,
    scenes: bool = True,
    size: str | None = None,
    overwrite: bool = False,
) -> None:
    """Generate reviewable target reference images for missing character variants and scenes."""
    manifest = Manifest.load(manifest_path)
    job_dir = manifest_path.parent
    size = size or config.seedream_size
    client: SeedreamClient | None = None
    changed = False

    if characters:
        for char in manifest.characters:
            for variant in char.appearance_variants:
                if _has_image_reference(variant) and not overwrite:
                    continue
                if client is None:
                    client = _seedream_client(config)
                prompt = _target_character_prompt(char, variant)
                image_path = job_dir / "target_refs" / "characters" / f"{variant.id}.jpg"
                _render_target_image(
                    client=client,
                    config=config,
                    manifest=manifest,
                    prompt=prompt,
                    size=size,
                    output_image=image_path,
                )
                variant.image_path = _relative(image_path, job_dir)
                variant.image_uri = None
                changed = True
                typer.echo(f"[target:{variant.id}] 已生成目标角色参考图：{variant.image_path}")

    if scenes:
        for scene in manifest.scenes:
            if _has_image_reference(scene) and not overwrite:
                continue
            if client is None:
                client = _seedream_client(config)
            prompt = _target_scene_prompt(scene)
            image_path = job_dir / "target_refs" / "scenes" / f"{scene.id}.jpg"
            _render_target_image(
                client=client,
                config=config,
                manifest=manifest,
                prompt=prompt,
                size=size,
                output_image=image_path,
            )
            scene.image_path = _relative(image_path, job_dir)
            scene.image_uri = None
            changed = True
            typer.echo(f"[target:{scene.id}] 已生成目标场景参考图：{scene.image_path}")

    if changed:
        manifest.preparation.status = "draft"
        manifest.preparation.required_review_items = preparation_issues(manifest)
        manifest.save(manifest_path)
        typer.echo(f"Manifest 已回填目标参考素材：{manifest_path}")
        typer.echo("请人工检查目标参考图，确认角色/妆造/场景/声音/语种后再运行 approve。")
    else:
        typer.echo("没有需要生成的目标参考素材。")


def upload_job(*, config: AppConfig, manifest_path: Path, force: bool = False) -> None:
    manifest = Manifest.load(manifest_path)
    job_dir = manifest_path.parent
    tos_cfg = _tos_config(config)
    changed = False

    for char in manifest.characters:
        if char.image_path and (force or not char.image_uri):
            path = _path_from_manifest(char.image_path, job_dir) or Path(char.image_path)
            char.image_uri = upload_file(path, prefix="seedance-role-scene/characters", config=tos_cfg)
            typer.echo(f"[character:{char.id}] 已上传形象参考：{char.image_uri}")
            changed = True
        for variant in char.appearance_variants:
            if variant.image_path and (force or not variant.image_uri):
                path = _path_from_manifest(variant.image_path, job_dir) or Path(variant.image_path)
                variant.image_uri = upload_file(path, prefix="seedance-role-scene/character-variants", config=tos_cfg)
                typer.echo(f"[variant:{variant.id}] 已上传妆造参考：{variant.image_uri}")
                changed = True
        if char.voice_reference_path and (force or not char.voice_reference_uri):
            path = _path_from_manifest(char.voice_reference_path, job_dir) or Path(char.voice_reference_path)
            char.voice_reference_uri = upload_file(path, prefix="seedance-role-scene/voices", config=tos_cfg)
            typer.echo(f"[character:{char.id}] 已上传音色参考：{char.voice_reference_uri}")
            changed = True
    for voice in manifest.voice_registry:
        if voice.reference_path and (force or not voice.reference_uri):
            path = _path_from_manifest(voice.reference_path, job_dir) or Path(voice.reference_path)
            voice.reference_uri = upload_file(path, prefix="seedance-role-scene/voices", config=tos_cfg)
            typer.echo(f"[voice:{voice.id}] 已上传音色参考：{voice.reference_uri}")
            changed = True
    for scene in manifest.scenes:
        if scene.image_path and (force or not scene.image_uri):
            path = _path_from_manifest(scene.image_path, job_dir) or Path(scene.image_path)
            scene.image_uri = upload_file(path, prefix="seedance-role-scene/scenes", config=tos_cfg)
            typer.echo(f"[scene:{scene.id}] 已上传场景参考：{scene.image_uri}")
            changed = True
    for seg in manifest.segments:
        if force or not seg.reference_uri:
            reference = _path_from_manifest(seg.reference_path, job_dir)
            if reference:
                seg.reference_uri = upload_file(reference, prefix="seedance-role-scene/segments", config=tos_cfg)
                typer.echo(f"[{seg.index:03d}] 已上传视频参考：{seg.reference_uri}")
                changed = True
        if seg.source_audio_path and (force or not seg.source_audio_uri):
            audio = _path_from_manifest(seg.source_audio_path, job_dir)
            if audio and audio.exists():
                seg.source_audio_uri = upload_file(audio, prefix="seedance-role-scene/source-audio", config=tos_cfg)
                typer.echo(f"[{seg.index:03d}] 已上传原音频参考：{seg.source_audio_uri}")
                changed = True
        manifest.save(manifest_path)
    if changed:
        manifest.save(manifest_path)
        typer.echo(f"Manifest 已更新：{manifest_path}")
    else:
        typer.echo("没有需要上传的素材。")


def asset_register_job(*, config: AppConfig, manifest_path: Path, group_name: str, wait: bool = True) -> None:
    manifest = Manifest.load(manifest_path)
    client = AssetsClient(config.tos_access_key, config.tos_secret_key, config.tos_region)
    groups = client.list_asset_groups()
    group_id = ""
    for group in groups:
        if group.get("Name") == group_name:
            group_id = str(group["Id"])
            typer.echo(f"已有 AssetGroup：{group_id}")
            break
    if not group_id:
        group_id = client.create_asset_group(group_name, description="seedance-role-scene-remake trusted references")
        typer.echo(f"已创建 AssetGroup：{group_id}")

    changed = False
    for seg in manifest.segments:
        if not seg.reference_uri:
            typer.echo(f"[{seg.index:03d}] 跳过：没有视频参考 URL。")
            continue
        if seg.reference_uri.startswith("asset://"):
            typer.echo(f"[{seg.index:03d}] 已是资产 URI：{seg.reference_uri}")
            continue
        asset_id = client.create_asset(group_id, seg.reference_uri, asset_type="Video", name=f"{Path(manifest.source).stem}-{seg.index:03d}")
        if wait:
            client.wait_asset_active(asset_id)
        seg.reference_uri = f"asset://{asset_id}"
        if seg.status == "failed":
            seg.status = "pending"
            seg.error = None
        changed = True
        manifest.save(manifest_path)
        typer.echo(f"[{seg.index:03d}] OK：{seg.reference_uri}")
    manifest.repair_history.append(
        {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "action": "asset-register",
            "group_name": group_name,
            "changed": changed,
        }
    )
    manifest.save(manifest_path)


def remake_job(
    *,
    config: AppConfig,
    manifest_path: Path,
    stop_on_error: bool = False,
    allow_unprepared: bool = False,
) -> None:
    manifest = Manifest.load(manifest_path)
    job_dir = manifest_path.parent
    if manifest.requires_preparation_approval() and not allow_unprepared:
        issues = preparation_issues(manifest)
        detail = "\n".join(f"- {issue}" for issue in issues[:12])
        if len(issues) > 12:
            detail += f"\n- 另有 {len(issues) - 12} 项问题，运行 review 查看完整列表。"
        raise PipelineError(f"准备设定尚未 approved，已停止生成。\n{detail}")
    if allow_unprepared and manifest.requires_preparation_approval():
        typer.echo("警告：正在使用未批准的准备设定生成，角色/场景/语言/声音一致性风险较高。", err=True)
    if not manifest.generate_audio or manifest.audio_mode != "generated":
        raise PipelineError("本工具 v1 要求 audio_mode=generated 且 generate_audio=true，不会静默回退原音轨。")
    pending = manifest.pending_segments()
    if not pending:
        typer.echo("没有待处理片段。")
        return
    client = _client(config)
    typer.echo(f"开始重制 {len(pending)} 个片段：ratio={manifest.target_ratio}, generate_audio={manifest.generate_audio}")

    for seg in pending:
        typer.echo(f"\n── 片段 {seg.index:03d} ──")
        try:
            _remake_segment(client=client, config=config, manifest=manifest, manifest_path=manifest_path, job_dir=job_dir, seg=seg)
        except Exception as exc:
            seg.status = "failed"
            seg.error = str(exc)
            manifest.save(manifest_path)
            typer.echo(f"[FAIL] {exc}", err=True)
            if stop_on_error:
                raise
    typer.echo(f"\n重制完成：{len(manifest.succeeded_segments())}/{len(manifest.segments)} 成功")


def extract_audio_job(*, manifest_path: Path, stop_on_error: bool = False) -> None:
    manifest = Manifest.load(manifest_path)
    job_dir = manifest_path.parent
    for seg in manifest.succeeded_segments():
        if not seg.remade_path:
            continue
        try:
            remade = job_dir / seg.remade_path
            generated = job_dir / "generated_audio" / f"{seg.index:03d}.m4a"
            aligned = job_dir / "aligned" / f"{seg.index:03d}.m4a"
            if not extract_audio(remade, generated):
                raise PipelineError(f"[{seg.index:03d}] 生成视频没有可提取音轨。")
            normalize_audio_duration(generated, aligned, seg.duration)
            seg.generated_audio_path = _relative(generated, job_dir)
            seg.aligned_audio_path = _relative(aligned, job_dir)
            seg.audio_report = {"status": "ok", "target_duration": seg.duration}
            typer.echo(f"[{seg.index:03d}] 已提取并对齐生成音轨：{seg.aligned_audio_path}")
        except Exception as exc:
            seg.error = str(exc)
            manifest.save(manifest_path)
            typer.echo(f"[FAIL] {exc}", err=True)
            if stop_on_error:
                raise
    manifest.save(manifest_path)


def merge_job(*, manifest_path: Path, output: Path | None = None) -> Path:
    manifest = Manifest.load(manifest_path)
    job_dir = manifest_path.parent
    succeeded = sorted(manifest.succeeded_segments(), key=lambda item: item.index)
    if len(succeeded) != len(manifest.segments):
        raise ManifestError("仍有片段未成功，不能拼接。请先运行 status 或 remake。")

    aligned_video_dir = job_dir / "aligned" / "video"
    video_paths: list[Path] = []
    audio_paths: list[Path] = []
    source_fps = get_video_fps(Path(manifest.source)) if Path(manifest.source).exists() else None
    for seg in succeeded:
        if not seg.remade_path:
            raise ManifestError(f"[{seg.index:03d}] 缺少生成视频路径。")
        remade = job_dir / seg.remade_path
        aligned_video = aligned_video_dir / f"{seg.index:03d}.mp4"
        normalize_video_duration(remade, aligned_video, seg.duration, fps=source_fps)
        video_paths.append(aligned_video)
        if not seg.aligned_audio_path:
            raise ManifestError(f"[{seg.index:03d}] 缺少生成音轨，请先运行 extract-audio。")
        audio_paths.append(job_dir / seg.aligned_audio_path)
    final = output or (job_dir / "final.mp4")
    silent_video = final.with_name(final.stem + "_video" + final.suffix)
    concat_videos(video_paths, silent_video)
    generated_audio = final.with_name(final.stem + "_generated_audio.m4a")
    concat_audios(audio_paths, generated_audio)
    mux_audio(silent_video, generated_audio, final)
    if silent_video.exists():
        silent_video.unlink()
    typer.echo(f"成片已保存：{final}")
    return final


def refresh_status(*, config: AppConfig, manifest_path: Path) -> None:
    manifest = Manifest.load(manifest_path)
    client = _client(config)
    for seg in manifest.segments:
        if not seg.task_id or seg.status == "succeeded":
            continue
        try:
            status = client.status(seg.task_id)
        except Exception as exc:
            seg.error = str(exc)
            continue
        normalized = normalize_status(status.status)
        if normalized == "succeeded" and status.file_url:
            _save_generated_segment(status.file_url, manifest_path.parent, seg, timeout_s=config.request_timeout_s)
        elif normalized == "failed":
            seg.status = "failed"
            seg.error = status.fail_reason or status.status
        else:
            seg.status = "running"
            seg.error = None
    manifest.save(manifest_path)


def summarize_status(manifest: Manifest) -> list[str]:
    total = len(manifest.segments)
    succeeded = len(manifest.succeeded_segments())
    failed = sum(1 for seg in manifest.segments if seg.status == "failed")
    running = sum(1 for seg in manifest.segments if seg.status == "running")
    pending = total - succeeded - failed - running
    lines = [
        f"源视频：{manifest.source}",
        f"准备状态：{manifest.preparation.status}",
        f"角色数：{len(manifest.characters)}，场景数：{len(manifest.scenes)}",
        f"语种：{manifest.language_policy.source_language} -> {manifest.language_policy.target_language}，口语={manifest.language_policy.spoken_language}",
        f"比例：{manifest.source_ratio} -> {manifest.target_ratio}",
        f"音频：{manifest.audio_mode}, generate_audio={manifest.generate_audio}",
        f"进度：成功 {succeeded}/{total}，运行中 {running}，待处理 {pending}，失败 {failed}",
    ]
    for seg in manifest.segments:
        error = f" | {seg.error}" if seg.error else ""
        lines.append(f"[{seg.index:03d}] {seg.status} task={seg.task_id or '-'}{error}")
    return lines


def verify_job(
    *,
    manifest_path: Path,
    output: Path | None = None,
    quality_json: Path | None = None,
    audio_report: bool = False,
    continuity_report: bool = False,
    identity_report: bool = False,
    scene_report: bool = False,
    language_report: bool = False,
    voice_report: bool = False,
    target_report: bool = False,
) -> Path:
    manifest = Manifest.load(manifest_path)
    payload = build_quality_report(
        manifest,
        job_dir=manifest_path.parent,
        audio_report=audio_report,
        continuity_report=continuity_report,
        identity_report=identity_report,
        scene_report=scene_report,
        language_report=language_report,
        voice_report=voice_report,
        target_report=target_report,
    )
    if quality_json:
        quality_json.parent.mkdir(parents=True, exist_ok=True)
        quality_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        typer.echo(f"质量 JSON 已保存：{quality_json}")
    report = output or (manifest_path.parent / "report.html")
    write_html_report(payload, report)
    typer.echo(f"验证报告已保存：{report}")
    return report


def repair_job(
    *,
    manifest_path: Path,
    from_segment: int,
    cascade: bool = True,
    reason: str = "quality repair",
    archive: bool = True,
) -> None:
    manifest = Manifest.load(manifest_path)
    job_dir = manifest_path.parent
    if from_segment < 0 or from_segment >= len(manifest.segments):
        raise ManifestError(f"from_segment 超出范围：{from_segment}")
    affected = [seg for seg in manifest.segments if seg.index >= from_segment] if cascade else [
        seg for seg in manifest.segments if seg.index == from_segment
    ]
    round_no = manifest.repair_round + 1
    archive_dir = job_dir / "repairs" / f"round_{round_no:02d}"
    archived: dict[str, list[str]] = {}

    def archive_relative(rel_path: str | None, *, key: str) -> None:
        if not archive or not rel_path:
            return
        source = job_dir / rel_path
        if not source.exists():
            return
        target = archive_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        archived.setdefault(key, []).append(_relative(target, job_dir))

    for seg in affected:
        key = f"{seg.index:03d}"
        archive_relative(seg.remade_path, key=key)
        archive_relative(seg.generated_audio_path, key=key)
        archive_relative(seg.aligned_audio_path, key=key)
        archive_relative(f"aligned/video/{seg.index:03d}.mp4", key=key)
        seg.task_id = None
        seg.generated_url = None
        seg.remade_path = None
        seg.generated_audio_path = None
        seg.aligned_audio_path = None
        seg.audio_report = {}
        seg.status = "pending"
        seg.error = None

    manifest.repair_round = round_no
    manifest.repair_history.append(
        {
            "round": round_no,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "from_segment": from_segment,
            "cascade": cascade,
            "reason": reason,
            "segments": [seg.index for seg in affected],
            "archived": archived,
        }
    )
    manifest.save(manifest_path)
    typer.echo(f"已创建修复轮次 {round_no}：从片段 {from_segment:03d} 重置。")


def run_job(
    *,
    config: AppConfig,
    video: Path,
    output: Path,
    spec_path: Path | None,
    characters: list[CharacterSpec],
    scenes: list[SceneSpec],
    character_prompt: str,
    scene_prompt: str,
    voice_prompt: str,
    prompt: str,
    ratio: str,
    segment_seconds: int,
    final_output: Path | None,
    no_upload: bool,
    stop_on_error: bool,
    allow_unprepared: bool = False,
) -> Path:
    manifest_path = split_job(
        config=config,
        video=video,
        output=output,
        spec_path=spec_path,
        characters=characters,
        scenes=scenes,
        character_prompt=character_prompt,
        scene_prompt=scene_prompt,
        voice_prompt=voice_prompt,
        prompt=prompt,
        ratio=ratio,
        segment_seconds=segment_seconds,
        no_upload=True,
    )
    if no_upload:
        raise PipelineError("run 需要上传视频/音频/图片参考；如只想生成 manifest，请使用 split --no-upload。")
    manifest = Manifest.load(manifest_path)
    if manifest.requires_preparation_approval() and not allow_unprepared:
        issues = preparation_issues(manifest)
        detail = "\n".join(f"- {issue}" for issue in issues[:12])
        if len(issues) > 12:
            detail += f"\n- 另有 {len(issues) - 12} 项问题，运行 review 查看完整列表。"
        raise PipelineError(f"准备设定尚未 approved，已停止上传和生成。\n{detail}")
    upload_job(config=config, manifest_path=manifest_path)
    remake_job(config=config, manifest_path=manifest_path, stop_on_error=stop_on_error, allow_unprepared=allow_unprepared)
    extract_audio_job(manifest_path=manifest_path, stop_on_error=stop_on_error)
    final = merge_job(manifest_path=manifest_path, output=final_output)
    verify_job(
        manifest_path=manifest_path,
        output=output / "report.html",
        quality_json=output / "quality.json",
        audio_report=True,
        identity_report=True,
        scene_report=True,
        language_report=True,
        voice_report=True,
        target_report=True,
    )
    return final


def _remake_segment(
    *,
    client: SeedanceClient,
    config: AppConfig,
    manifest: Manifest,
    manifest_path: Path,
    job_dir: Path,
    seg: SegmentEntry,
) -> None:
    if seg.task_id:
        status = client.status(seg.task_id)
        normalized = normalize_status(status.status)
        if normalized == "succeeded" and status.file_url:
            _save_generated_segment(status.file_url, job_dir, seg, timeout_s=config.request_timeout_s)
            manifest.save(manifest_path)
            typer.echo(f"已恢复完成任务：{seg.task_id}")
            return
        if normalized == "running":
            typer.echo(f"恢复轮询已有任务：{seg.task_id}")
            result = poll_task(
                client.status,
                seg.task_id,
                interval_s=config.poll_interval_s,
                max_wait_s=config.poll_max_wait_s,
                on_update=lambda resp, state: typer.echo(f"轮询：{resp.status} -> {state}"),
            )
            _handle_polled_result(result.status, result.file_url, result.fail_reason, job_dir, seg, config.request_timeout_s)
            manifest.save(manifest_path)
            return
        seg.task_id = None

    if not seg.reference_uri:
        raise PipelineError(f"[{seg.index:03d}] 缺少视频参考 URL，请先运行 upload。")
    reference_assets = _reference_assets(manifest, job_dir, seg)
    req = VideoGenerateRequest(
        model=manifest.model,
        prompt=build_generation_prompt(manifest, seg, reference_assets=reference_assets),
        ratio=manifest.target_ratio,
        duration=seg.generation_duration,
        resolution=manifest.resolution,
        reference_assets=reference_assets,
        generate_audio=True,
    )
    try:
        submitted = client.submit(req)
    except Exception as exc:
        message = str(exc)
        if "generate_audio" in message or "audio" in message or "音频" in message:
            raise PipelineError(f"Seedance 当前模型或账号不支持生成音频/音频参考：{message}") from exc
        raise
    seg.task_id = submitted.task_id
    seg.status = "running"
    seg.attempts += 1
    seg.error = None
    manifest.save(manifest_path)
    typer.echo(f"已提交 task_id={submitted.task_id}")
    result = poll_task(
        client.status,
        submitted.task_id,
        interval_s=config.poll_interval_s,
        max_wait_s=config.poll_max_wait_s,
        on_update=lambda resp, state: typer.echo(f"轮询：{resp.status} -> {state}"),
    )
    _handle_polled_result(result.status, result.file_url, result.fail_reason, job_dir, seg, config.request_timeout_s)
    manifest.save(manifest_path)


def _handle_polled_result(status: str, file_url: str | None, fail_reason: str | None, job_dir: Path, seg: SegmentEntry, timeout_s: int) -> None:
    normalized = normalize_status(status)
    if normalized != "succeeded" or not file_url:
        reason = fail_reason or status
        if "real person" in reason.lower() or "human" in reason.lower() or "人像" in reason:
            reason = f"{reason}。含真人素材时请完成官方授权素材流程后重试，不要规避审核。"
        raise PipelineError(f"生成失败：{reason}")
    _save_generated_segment(file_url, job_dir, seg, timeout_s=timeout_s)


def _save_generated_segment(file_url: str, job_dir: Path, seg: SegmentEntry, *, timeout_s: int) -> None:
    output = job_dir / "remade" / f"{seg.index:03d}.mp4"
    download_file(file_url, output, timeout_s=timeout_s)
    seg.generated_url = file_url
    seg.remade_path = _relative(output, job_dir)
    seg.status = "succeeded"
    seg.error = None
    typer.echo(f"[OK] 已保存：{seg.remade_path}")


def _render_target_image(
    *,
    client: SeedreamClient,
    config: AppConfig,
    manifest: Manifest,
    prompt: str,
    size: str,
    output_image: Path,
) -> None:
    req = ImageGenerateRequest(
        model=config.seedream_model,
        prompt=prompt,
        size=size,
        response_format="url",
        watermark=False,
    )
    images = client.generate(req)
    save_generated_image(images[0], output_image, timeout_s=config.request_timeout_s)
    output_image.with_suffix(".prompt.txt").write_text(prompt, encoding="utf-8")


def _target_character_prompt(char: CharacterSpec, variant: AppearanceVariantSpec) -> str:
    return "\n".join(
        [
            "生成一个用于后续视频换角色的目标角色参考短视频，只用于抽取目标角色参考帧。",
            "画面中只出现一个人，避免多人、双胞胎、同脸复制或背景干扰。",
            f"角色 id：{char.id}。",
            f"妆造 variant id：{variant.id}。",
            f"目标角色设定：{char.prompt or '自然写实人物，身份稳定，脸型和体型清晰。'}",
            f"目标妆造设定：{variant.prompt or char.prompt or '自然现代日常妆造，发型、服装和体态清晰。'}",
            "参考帧要求：正面或三分之二视角，头发、脸、上半身和主要服装完整可见，写实电影感，清晰稳定。",
            "不要使用任何源视频人物外观，不要欧美脸、金发或原片服装细节，除非目标设定明确要求。",
        ]
    )


def _target_scene_prompt(scene: SceneSpec) -> str:
    return "\n".join(
        [
            "生成一个用于后续视频换场景的目标场景参考短视频，只用于抽取目标场景参考帧。",
            f"场景 id：{scene.id}。",
            f"目标场景设定：{scene.prompt or '写实室内空间，空间结构、材质、光照和陈设清晰稳定。'}",
            "画面不出现人物，避免文字、水印、字幕和不相关装饰。",
            "参考帧要求：广角或中景，能清楚看到空间布局、主要家具、光线方向、材质和色彩，写实电影感。",
            "不要复刻源视频场景陈设，除非目标设定明确要求；后续生成会只保留原片构图和空间动线。",
        ]
    )


def _has_image_reference(item: Any) -> bool:
    return bool(getattr(item, "image_path", None) or getattr(item, "image_uri", None))


def _has_missing_target_refs(manifest: Manifest) -> bool:
    for char in manifest.characters:
        for variant in char.appearance_variants:
            if not _has_image_reference(variant):
                return True
    return any(not _has_image_reference(scene) for scene in manifest.scenes)


def _reference_assets(manifest: Manifest, job_dir: Path, seg: SegmentEntry) -> list[ReferenceAsset]:
    assets: list[ReferenceAsset] = [
        ReferenceAsset(
            slot="视频1",
            kind="video",
            role="reference_video",
            uri=seg.reference_uri,
            bound_type="segment",
            bound_id=f"{seg.index:03d}",
            note="原视频片段，仅参考动作、站位、运镜、构图、剪辑节奏、对白语义和对白时序，不参考人物外观或场景陈设。",
        )
    ]
    image_index = 1
    scene_map = manifest.scene_map()
    variant_map = manifest.variant_map()

    for variant_id in seg.character_variant_ids:
        found = variant_map.get(variant_id)
        if found:
            char, variant = found
            uri = _image_reference_uri(variant, job_dir) or _image_reference_uri(char, job_dir)
            if uri:
                assets.append(
                    ReferenceAsset(
                        slot=f"图片{image_index}",
                        kind="image",
                        role="reference_image",
                        uri=uri,
                        bound_type="appearance_variant",
                        bound_id=variant.id,
                        note=f"{char.id}（{variant.id}）的目标角色外观参考。",
                    )
                )
                image_index += 1
    for scene_id in seg.scene_ids:
        if scene_id in scene_map:
            scene = scene_map[scene_id]
            uri = _image_reference_uri(scene, job_dir)
            if uri:
                assets.append(
                    ReferenceAsset(
                        slot=f"图片{image_index}",
                        kind="image",
                        role="reference_image",
                        uri=uri,
                        bound_type="scene",
                        bound_id=scene.id,
                        note=f"{scene.id} 的目标场景参考。",
                    )
                )
                image_index += 1

    audio_index = 1
    if seg.source_audio_uri:
        assets.append(
            ReferenceAsset(
                slot=f"音频{audio_index}",
                kind="audio",
                role="reference_audio",
                uri=seg.source_audio_uri,
                bound_type="segment",
                bound_id=f"{seg.index:03d}",
                note="原片段音频，仅参考对白节奏、停顿和情绪，不作为回退原音轨。",
            )
        )
        audio_index += 1
    voice_map = manifest.voice_map()
    for voice_id in seg.voice_ids:
        voice = voice_map.get(voice_id)
        if voice and voice.reference_uri:
            assets.append(
                ReferenceAsset(
                    slot=f"音频{audio_index}",
                    kind="audio",
                    role="reference_audio",
                    uri=voice.reference_uri,
                    bound_type="voice",
                    bound_id=voice.id,
                    note=f"{voice.id} 的目标音色参考。",
                )
            )
            audio_index += 1
    return assets


def _image_reference_uri(item: Any, job_dir: Path) -> str | None:
    uri = getattr(item, "image_uri", None)
    if uri:
        return uri
    path_value = getattr(item, "image_path", None)
    if path_value:
        path = _path_from_manifest(path_value, job_dir)
        if path and path.exists():
            return image_to_data_url(path)
    return None


def _reference_images(manifest: Manifest, job_dir: Path, seg: SegmentEntry) -> list[str]:
    return [asset.uri or "" for asset in _reference_assets(manifest, job_dir, seg) if asset.kind == "image" and (asset.uri or asset.path)]


def _reference_audios(manifest: Manifest, seg: SegmentEntry) -> list[str]:
    urls: list[str] = []
    if seg.source_audio_uri:
        urls.append(seg.source_audio_uri)
    voice_map = manifest.voice_map()
    for voice_id in seg.voice_ids:
        voice = voice_map.get(voice_id)
        if voice and voice.reference_uri:
            urls.append(voice.reference_uri)
    for char in manifest.characters:
        if char.id in seg.character_ids and char.voice_reference_uri:
            urls.append(char.voice_reference_uri)
    return urls


def _load_spec_or_defaults(
    spec_path: Path | None,
    characters: list[CharacterSpec],
    scenes: list[SceneSpec],
    character_prompt: str,
    scene_prompt: str,
    voice_prompt: str,
    prompt: str,
) -> dict[str, Any]:
    spec = load_spec(spec_path) if spec_path else {}
    if characters:
        spec["characters"] = [char.__dict__ for char in characters]
    if scenes:
        spec["scenes"] = [scene.__dict__ for scene in scenes]
    if not spec.get("characters") and character_prompt:
        spec["characters"] = [{"id": "character_1", "prompt": character_prompt, "voice_prompt": voice_prompt}]
    elif voice_prompt and spec.get("characters"):
        for item in spec["characters"]:
            item.setdefault("voice_prompt", voice_prompt)
    if not spec.get("scenes") and scene_prompt:
        spec["scenes"] = [{"id": "scene_1", "prompt": scene_prompt}]
    if prompt:
        spec["prompt"] = prompt
    spec.setdefault("prompt", "")
    spec.setdefault("dialogue_fidelity", "strict")
    spec.setdefault("audio_mode", "generated")
    spec.setdefault("generate_audio", True)
    voice_registry = spec.get("voice_registry", [])
    if not isinstance(voice_registry, list):
        voice_registry = []
    spec_characters = spec.get("characters", [])
    if not isinstance(spec_characters, list):
        spec_characters = []
    spec_scenes = spec.get("scenes", [])
    if not isinstance(spec_scenes, list):
        spec_scenes = []
    voice_ids = [item["id"] for item in voice_registry if isinstance(item, dict) and item.get("id")]
    spec.setdefault(
        "segments",
        {
            "default": {
                "characters": [item["id"] for item in spec_characters if isinstance(item, dict) and item.get("id")],
                "scenes": [item["id"] for item in spec_scenes if isinstance(item, dict) and item.get("id")],
                "voices": voice_ids,
            }
        },
    )
    return spec


def _segment_spec(spec: dict[str, Any], idx: int, default_segment: dict[str, Any]) -> dict[str, Any]:
    segments = spec.get("segments")
    if not isinstance(segments, dict):
        return default_segment
    return segments.get(str(idx)) or segments.get(f"{idx:03d}") or default_segment


def _voice_ids_for_characters(spec: dict[str, Any], char_ids: list[str]) -> list[str]:
    chars = spec.get("characters", [])
    if not isinstance(chars, list):
        return []
    ids: list[str] = []
    wanted = set(char_ids)
    for char in chars:
        if not isinstance(char, dict) or char.get("id") not in wanted:
            continue
        voice_id = char.get("voice_id")
        if voice_id and voice_id not in ids:
            ids.append(str(voice_id))
    return ids


def _normalize_characters(items: list[dict[str, Any]], *, base: Path) -> list[CharacterSpec]:
    chars: list[CharacterSpec] = []
    if not isinstance(items, list):
        return chars
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = {key: value for key, value in item.items() if key in CharacterSpec.__dataclass_fields__}
        raw["appearance_variants"] = _normalize_variants(item.get("appearance_variants", []), base=base)
        if not raw["appearance_variants"] and item.get("id"):
            raw["appearance_variants"] = [
                AppearanceVariantSpec(
                    id=f"{item['id']}_default",
                    source_hint=item.get("source_hint", ""),
                    image_path=_normalize_optional_path(item.get("image_path"), base),
                    image_uri=item.get("image_uri"),
                    prompt=item.get("prompt", ""),
                    approved=bool(item.get("approved", False)),
                )
            ]
        char = CharacterSpec(**raw)
        char.image_path = _normalize_optional_path(char.image_path, base)
        char.voice_reference_path = _normalize_optional_path(char.voice_reference_path, base)
        chars.append(char)
    return chars


def _normalize_scenes(items: list[dict[str, Any]], *, base: Path) -> list[SceneSpec]:
    scenes: list[SceneSpec] = []
    if not isinstance(items, list):
        return scenes
    for item in items:
        if not isinstance(item, dict):
            continue
        scene = SceneSpec(**{key: value for key, value in item.items() if key in SceneSpec.__dataclass_fields__})
        scene.image_path = _normalize_optional_path(scene.image_path, base)
        scenes.append(scene)
    return scenes


def _normalize_variants(items: list[dict[str, Any]], *, base: Path) -> list[AppearanceVariantSpec]:
    variants: list[AppearanceVariantSpec] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        variant = AppearanceVariantSpec(**{key: value for key, value in item.items() if key in AppearanceVariantSpec.__dataclass_fields__})
        variant.image_path = _normalize_optional_path(variant.image_path, base)
        variants.append(variant)
    return variants


def _normalize_voices(items: list[dict[str, Any]], *, base: Path) -> list[VoiceSpec]:
    voices: list[VoiceSpec] = []
    if not isinstance(items, list):
        return voices
    for item in items:
        if not isinstance(item, dict):
            continue
        voice = VoiceSpec(**{key: value for key, value in item.items() if key in VoiceSpec.__dataclass_fields__})
        voice.reference_path = _normalize_optional_path(voice.reference_path, base)
        voices.append(voice)
    return voices


def _normalize_preparation(item: dict[str, Any]) -> PreparationSpec:
    if not isinstance(item, dict):
        return PreparationSpec()
    return PreparationSpec(**{key: value for key, value in item.items() if key in PreparationSpec.__dataclass_fields__})


def _normalize_dataclass(cls: type, item: dict[str, Any]) -> Any:
    if not isinstance(item, dict):
        return cls()
    return cls(**{key: value for key, value in item.items() if key in cls.__dataclass_fields__})


def _normalize_optional_path(value: str | None, base: Path) -> str | None:
    if not value:
        return None
    path = Path(value)
    return str(path if path.is_absolute() else (base / path).resolve())
