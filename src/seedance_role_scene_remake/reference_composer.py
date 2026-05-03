"""Seedance multi-reference composition."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from seedance_role_scene_remake.ffmpeg import image_to_data_url
from seedance_role_scene_remake.manifest import Manifest, ReferenceAsset, SegmentEntry

MAX_IMAGES = 9
MAX_VIDEOS = 3
MAX_AUDIOS = 3


@dataclass
class ContinuityReferences:
    previous_tail_video_uri: str | None = None
    previous_tail_frame_path: str | None = None
    context_video_uri: str | None = None


@dataclass
class ReferencePlan:
    assets: list[ReferenceAsset]
    report: dict[str, Any]


class ReferenceComposer:
    def __init__(self, *, strategy: str = "full") -> None:
        normalized = strategy.replace("_", "-").lower().strip() or "full"
        if normalized not in {"full", "safe", "script-only"}:
            raise ValueError(f"不支持的 reference strategy：{strategy}")
        self.strategy = normalized

    def compose(
        self,
        *,
        manifest: Manifest,
        job_dir: Path,
        segment: SegmentEntry,
        continuity: ContinuityReferences | None = None,
    ) -> ReferencePlan:
        continuity = continuity or ContinuityReferences()
        assets: list[ReferenceAsset] = []
        self._add_videos(assets, segment=segment, continuity=continuity)
        self._add_images(assets, manifest=manifest, job_dir=job_dir, segment=segment, continuity=continuity)
        self._add_audios(assets, manifest=manifest, segment=segment)
        _mark_trust(assets)
        report = build_reference_report(assets, strategy=self.strategy)
        return ReferencePlan(assets=assets, report=report)

    def _add_videos(
        self,
        assets: list[ReferenceAsset],
        *,
        segment: SegmentEntry,
        continuity: ContinuityReferences,
    ) -> None:
        if self.strategy not in {"full", "safe"}:
            return
        video_index = 1
        if self.strategy == "full" and segment.reference_uri:
            assets.append(
                ReferenceAsset(
                    slot=f"视频{video_index}",
                    kind="video",
                    role="reference_video",
                    uri=segment.reference_uri,
                    bound_type="segment",
                    bound_id=f"{segment.index:03d}",
                    note="当前源片段，仅参考动作、站位、运镜、构图、剪辑节奏和对白时序；不要参考源人物外观。",
                )
            )
            video_index += 1
        if video_index <= MAX_VIDEOS and continuity.previous_tail_video_uri:
            assets.append(
                ReferenceAsset(
                    slot=f"视频{video_index}",
                    kind="video",
                    role="reference_video",
                    uri=continuity.previous_tail_video_uri,
                    bound_type="continuity_tail",
                    bound_id=f"{segment.index - 1:03d}",
                    note="上一段生成视频尾部，用于角色、场景、光线和动作连续性。",
                )
            )
            video_index += 1
        if self.strategy == "full" and video_index <= MAX_VIDEOS and continuity.context_video_uri:
            assets.append(
                ReferenceAsset(
                    slot=f"视频{video_index}",
                    kind="video",
                    role="reference_video",
                    uri=continuity.context_video_uri,
                    bound_type="context_segment",
                    bound_id=f"{segment.index:03d}",
                    note="源视频前后文片段，仅参考上下文运镜、空间关系和动作节奏。",
                )
            )

    def _add_images(
        self,
        assets: list[ReferenceAsset],
        *,
        manifest: Manifest,
        job_dir: Path,
        segment: SegmentEntry,
        continuity: ContinuityReferences,
    ) -> None:
        image_index = 1
        char_map = manifest.character_map()
        variant_map = manifest.variant_map()
        scene_map = manifest.scene_map()

        for variant_id in segment.character_variant_ids:
            if image_index > MAX_IMAGES:
                return
            found = variant_map.get(variant_id)
            if not found:
                continue
            char, variant = found
            char_uri, char_path = _image_reference(char, job_dir)
            variant_uri, variant_path = _image_reference(variant, job_dir)
            if char_uri and char_uri != variant_uri:
                assets.append(
                    ReferenceAsset(
                        slot=f"图片{image_index}",
                        kind="image",
                        role="reference_image",
                        uri=char_uri,
                        path=char_path,
                        bound_type="character_identity",
                        bound_id=char.id,
                        note=f"{char.id} 的目标身份/脸型参考。",
                    )
                )
                image_index += 1
                if image_index > MAX_IMAGES:
                    return
            if variant_uri:
                assets.append(
                    ReferenceAsset(
                        slot=f"图片{image_index}",
                        kind="image",
                        role="reference_image",
                        uri=variant_uri,
                        path=variant_path,
                        bound_type="appearance_variant",
                        bound_id=variant.id,
                        note=f"{char.id}（{variant.id}）的目标角色妆造/全身外观参考。",
                    )
                )
                image_index += 1

        for char_id in segment.character_ids:
            if image_index > MAX_IMAGES:
                return
            char = char_map.get(char_id)
            if not char:
                continue
            uri, path = _image_reference(char, job_dir)
            if uri and not any(item.bound_id == char.id for item in assets):
                assets.append(
                    ReferenceAsset(
                        slot=f"图片{image_index}",
                        kind="image",
                        role="reference_image",
                        uri=uri,
                        path=path,
                        bound_type="character_identity",
                        bound_id=char.id,
                        note=f"{char.id} 的目标身份参考。",
                    )
                )
                image_index += 1

        for scene_id in segment.scene_ids:
            if image_index > MAX_IMAGES:
                return
            scene = scene_map.get(scene_id)
            if not scene:
                continue
            uri, path = _image_reference(scene, job_dir)
            if uri:
                assets.append(
                    ReferenceAsset(
                        slot=f"图片{image_index}",
                        kind="image",
                        role="reference_image",
                        uri=uri,
                        path=path,
                        bound_type="scene",
                        bound_id=scene.id,
                        note=f"{scene.id} 的目标场景参考。",
                    )
                )
                image_index += 1

        if image_index <= MAX_IMAGES and continuity.previous_tail_frame_path:
            path = _path_from_manifest(continuity.previous_tail_frame_path, job_dir)
            if path and path.exists():
                assets.append(
                    ReferenceAsset(
                        slot=f"图片{image_index}",
                        kind="image",
                        role="reference_image",
                        uri=image_to_data_url(path),
                        path=str(path),
                        bound_type="continuity_tail_frame",
                        bound_id=f"{segment.index - 1:03d}",
                        note="上一段生成视频尾帧，用于延续角色、场景、构图和光线。",
                    )
                )

    def _add_audios(self, assets: list[ReferenceAsset], *, manifest: Manifest, segment: SegmentEntry) -> None:
        if self.strategy != "full":
            return
        audio_index = 1
        if segment.source_audio_uri:
            assets.append(
                ReferenceAsset(
                    slot=f"音频{audio_index}",
                    kind="audio",
                    role="reference_audio",
                    uri=segment.source_audio_uri,
                    bound_type="segment_audio",
                    bound_id=f"{segment.index:03d}",
                    note="当前源音频片段，仅参考英文对白节奏、停顿和情绪；最终成片仍挂载完整源音轨。",
                )
            )
            audio_index += 1
        voice_map = manifest.voice_map()
        for voice_id in segment.voice_ids:
            if audio_index > MAX_AUDIOS:
                return
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


def build_reference_report(assets: list[ReferenceAsset], *, strategy: str, reference_privacy: str = "") -> dict[str, Any]:
    _mark_trust(assets)
    return {
        "strategy": strategy,
        "reference_privacy": reference_privacy,
        "counts": {
            "image": sum(1 for item in assets if item.kind == "image"),
            "video": sum(1 for item in assets if item.kind == "video"),
            "audio": sum(1 for item in assets if item.kind == "audio"),
        },
        "limits": {"image": MAX_IMAGES, "video": MAX_VIDEOS, "audio": MAX_AUDIOS},
        "assets": [
            {
                "slot": item.slot,
                "kind": item.kind,
                "role": item.role,
                "bound_type": item.bound_type,
                "bound_id": item.bound_id,
                "note": item.note,
                "has_uri": bool(item.uri),
                "has_path": bool(item.path),
                "trust_status": item.trust_status,
                "asset_group_type": item.asset_group_type,
                "asset_project_name": item.asset_project_name,
                "asset_status": item.asset_status,
                "assetization_error": item.assetization_error,
                "source_uri": item.source_uri,
            }
            for item in assets
        ],
    }


def _mark_trust(assets: list[ReferenceAsset]) -> None:
    for item in assets:
        if item.assetization_error:
            if item.trust_status in {"", "unknown"}:
                item.trust_status = "assetization_failed"
        elif item.uri and item.uri.startswith("asset://"):
            item.trust_status = "assetized"
            item.asset_status = item.asset_status or "Active"
        elif item.uri or item.path:
            if item.trust_status in {"", "unknown"}:
                item.trust_status = "raw"
        else:
            if item.trust_status in {"", "unknown"}:
                item.trust_status = "missing"


def _image_reference(item: Any, job_dir: Path) -> tuple[str | None, str | None]:
    uri = getattr(item, "image_uri", None)
    if uri:
        return uri, None
    path_value = getattr(item, "image_path", None)
    if path_value:
        path = _path_from_manifest(path_value, job_dir)
        if path and path.exists():
            return image_to_data_url(path), str(path)
    return None, None


def _path_from_manifest(value: str | None, job_dir: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return job_dir / path
