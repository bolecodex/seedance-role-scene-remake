from pathlib import Path

import pytest

from seedance_role_scene_remake.config import AppConfig
from seedance_role_scene_remake.errors import ManifestError, PipelineError
from seedance_role_scene_remake.manifest import (
    AppearanceVariantSpec,
    CharacterSpec,
    LanguagePolicy,
    Manifest,
    PersonCandidateSpec,
    PreparationSpec,
    SceneCandidateSpec,
    SceneSpec,
    SegmentEntry,
    VoiceSpec,
)
from seedance_role_scene_remake.pipeline import approve_job, remake_job, render_targets_job
from seedance_role_scene_remake.preparation import preparation_issues


def _complete_manifest() -> Manifest:
    return Manifest(
        source="/tmp/input.mp4",
        preparation=PreparationSpec(status="draft"),
        language_policy=LanguagePolicy(source_language="en", target_language="preserve_source", spoken_language="en", approved=True),
        person_candidates=[
            PersonCandidateSpec(
                id="person_0",
                segment_index=0,
                timestamp=0.1,
                image_path="preparation/person_candidates/person_0.jpg",
                bbox=[0, 0, 100, 100],
                needs_better_reference=False,
                character_id="hero",
                appearance_variant_id="hero_home",
            )
        ],
        scene_candidates=[
            SceneCandidateSpec(
                id="scene_0",
                segment_index=0,
                timestamp=0.1,
                image_path="preparation/scene_candidates/scene_0.jpg",
                scene_id="home",
            )
        ],
        voice_registry=[VoiceSpec(id="hero_voice", character_id="hero", prompt="稳定男声", approved=True)],
        characters=[
            CharacterSpec(
                id="hero",
                prompt="中国青年",
                voice_id="hero_voice",
                approved=True,
                appearance_variants=[
                    AppearanceVariantSpec(
                        id="hero_home",
                        image_path="target_refs/characters/hero_home.jpg",
                        prompt="现代家居装",
                        approved=True,
                    )
                ],
            )
        ],
        scenes=[SceneSpec(id="home", image_path="target_refs/scenes/home.jpg", prompt="现代中式家居", approved=True)],
        segments=[
            SegmentEntry(
                index=0,
                start=0,
                duration=1,
                reference_duration=1,
                generation_duration=1,
                source_path="segments/000.mp4",
                frame_path="frames/000.jpg",
                reference_path="segments/000.mp4",
                character_ids=["hero"],
                character_variant_ids=["hero_home"],
                scene_ids=["home"],
                voice_ids=["hero_voice"],
            )
        ],
    )


def test_approve_requires_complete_bible(tmp_path: Path):
    path = tmp_path / "manifest.json"
    manifest = _complete_manifest()
    manifest.characters[0].appearance_variants[0].approved = False
    manifest.save(path)

    with pytest.raises(ManifestError):
        approve_job(manifest_path=path)


def test_approve_marks_manifest_ready(tmp_path: Path):
    path = tmp_path / "manifest.json"
    _complete_manifest().save(path)

    approve_job(manifest_path=path)

    loaded = Manifest.load(path)
    assert loaded.preparation.status == "approved"
    assert preparation_issues(loaded) == []


def test_remake_refuses_unapproved_manifest_before_api(tmp_path: Path):
    path = tmp_path / "manifest.json"
    _complete_manifest().save(path)

    with pytest.raises(PipelineError, match="尚未 approved"):
        remake_job(config=AppConfig(api_key="fake"), manifest_path=path)


def test_render_targets_fills_missing_reference_images(tmp_path: Path, monkeypatch):
    path = tmp_path / "manifest.json"
    manifest = _complete_manifest()
    manifest.characters[0].appearance_variants[0].image_path = None
    manifest.scenes[0].image_path = None
    manifest.save(path)

    monkeypatch.setattr("seedance_role_scene_remake.pipeline._seedream_client", lambda _config: object())

    def fake_render_target_image(**kwargs):
        kwargs["output_image"].parent.mkdir(parents=True, exist_ok=True)
        kwargs["output_image"].write_bytes(b"fake image")

    monkeypatch.setattr("seedance_role_scene_remake.pipeline._render_target_image", fake_render_target_image)

    render_targets_job(config=AppConfig(api_key="fake"), manifest_path=path)

    loaded = Manifest.load(path)
    assert loaded.characters[0].appearance_variants[0].image_path == "target_refs/characters/hero_home.jpg"
    assert loaded.scenes[0].image_path == "target_refs/scenes/home.jpg"
    assert loaded.preparation.status == "draft"
