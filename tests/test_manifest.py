from pathlib import Path

import pytest

from seedance_role_scene_remake.errors import ManifestError
from seedance_role_scene_remake.manifest import (
    AppearanceVariantSpec,
    LanguagePolicy,
    MANIFEST_VERSION,
    CharacterSpec,
    Manifest,
    PersonCandidateSpec,
    PreparationSpec,
    ReferenceAsset,
    SceneCandidateSpec,
    SceneSpec,
    SegmentEntry,
    SourceAnalysisSpec,
    VoiceSpec,
    load_spec,
    spec_template,
)


def test_manifest_round_trips(tmp_path: Path):
    path = tmp_path / "manifest.json"
    manifest = Manifest(
        source="/tmp/input.mp4",
        source_ratio="16:9",
        target_ratio="16:9",
        prompt="prompt",
        preparation=PreparationSpec(status="approved"),
        source_analysis=SourceAnalysisSpec(
            status="reviewed",
            analysis_json_path="analysis/analysis.json",
            script_path="analysis/script/剧本.md",
            character_index=[{"id": "source_hero", "confirmed": True}],
        ),
        language_policy=LanguagePolicy(source_language="en", target_language="preserve_source", spoken_language="en", approved=True),
        person_candidates=[
            PersonCandidateSpec(
                id="person_0",
                segment_index=0,
                timestamp=0.2,
                image_path="preparation/person_candidates/person_0.jpg",
                bbox=[1, 2, 3, 4],
                needs_better_reference=False,
                character_id="hero",
                appearance_variant_id="hero_default",
            )
        ],
        scene_candidates=[
            SceneCandidateSpec(
                id="scene_0",
                segment_index=0,
                timestamp=0.2,
                image_path="preparation/scene_candidates/scene_0.jpg",
                scene_id="street",
            )
        ],
        reference_assets=[ReferenceAsset(slot="视频1", kind="video", role="reference_video", uri="https://example.com/ref.mp4")],
        voice_registry=[VoiceSpec(id="hero_voice", character_id="hero", prompt="new voice", approved=True)],
        characters=[
            CharacterSpec(
                id="hero",
                prompt="new hero",
                voice_id="hero_voice",
                approved=True,
                appearance_variants=[
                    AppearanceVariantSpec(
                        id="hero_default",
                        image_path="target_refs/characters/hero_default.jpg",
                        prompt="new hero outfit",
                        approved=True,
                    )
                ],
            )
        ],
        scenes=[SceneSpec(id="street", image_path="target_refs/scenes/street.jpg", prompt="new street", approved=True)],
        segments=[
            SegmentEntry(
                index=0,
                start=0,
                duration=2.1,
                reference_duration=2.1,
                generation_duration=3,
                source_path="segments/000.mp4",
                frame_path="frames/000.jpg",
                reference_path="segments/000.mp4",
                source_audio_path="source_audio/000.m4a",
                character_ids=["hero"],
                scene_ids=["street"],
                character_variant_ids=["hero_default"],
                voice_ids=["hero_voice"],
            )
        ],
    )

    manifest.save(path)
    loaded = Manifest.load(path)

    assert loaded.version == MANIFEST_VERSION
    assert loaded.generate_audio is True
    assert loaded.dialogue_fidelity == "strict"
    assert loaded.characters[0].id == "hero"
    assert loaded.characters[0].appearance_variants[0].id == "hero_default"
    assert loaded.voice_registry[0].id == "hero_voice"
    assert loaded.person_candidates[0].character_id == "hero"
    assert loaded.scene_candidates[0].scene_id == "street"
    assert loaded.reference_assets[0].slot == "视频1"
    assert loaded.preparation.status == "approved"
    assert loaded.source_analysis.status == "reviewed"
    assert loaded.source_analysis.script_path == "analysis/script/剧本.md"
    assert loaded.segments[0].aligned_audio_path is None
    assert loaded.pending_segments()[0].index == 0


def test_manifest_upgrades_v1(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text(
        """{
          "version": 1,
          "source": "/tmp/input.mp4",
          "segments": [{
            "index": 0,
            "start": 0,
            "duration": 4,
            "reference_duration": 4,
            "generation_duration": 4,
            "source_path": "segments/000.mp4",
            "frame_path": "frames/000.jpg",
            "reference_path": "segments/000.mp4"
          }]
        }""",
        encoding="utf-8",
    )

    loaded = Manifest.load(path)

    assert loaded.version == MANIFEST_VERSION
    assert loaded.dialogue_fidelity == "strict"
    assert loaded.audio_mode == "generated"
    assert loaded.generate_audio is True
    assert loaded.preparation.status == "unprepared"
    assert loaded.language_policy.target_language == "preserve_source"
    assert loaded.language_policy.spoken_language == "preserve_source"
    assert loaded.person_candidates == []
    assert loaded.scene_candidates == []
    assert loaded.reference_assets == []
    assert loaded.repair_history == []
    assert loaded.source_analysis.status == "missing"


def test_manifest_upgrades_v4_with_source_analysis_default(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text(
        """{
          "version": 4,
          "source": "/tmp/input.mp4",
          "segments": [{
            "index": 0,
            "start": 0,
            "duration": 4,
            "reference_duration": 4,
            "generation_duration": 4,
            "source_path": "segments/000.mp4",
            "frame_path": "frames/000.jpg",
            "reference_path": "segments/000.mp4"
          }]
        }""",
        encoding="utf-8",
    )

    loaded = Manifest.load(path)

    assert loaded.version == MANIFEST_VERSION
    assert loaded.source_analysis.status == "missing"


def test_manifest_rejects_unknown_version(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text('{"version": 999, "segments": []}', encoding="utf-8")

    with pytest.raises(ManifestError):
        Manifest.load(path)


def test_spec_template_and_parser(tmp_path: Path):
    path = tmp_path / "spec.yaml"
    path.write_text(spec_template(tmp_path / "input.mp4"), encoding="utf-8")

    loaded = load_spec(path)

    assert loaded["generate_audio"] is True
    assert loaded["dialogue_fidelity"] == "strict"
    assert loaded["preparation"]["status"] == "draft"
    assert loaded["source_analysis"]["status"] == "missing"
    assert loaded["language_policy"]["target_language"] == "preserve_source"
    assert loaded["language_policy"]["spoken_language"] == "preserve_source"
    assert loaded["characters"][0]["id"] == "hero"
    assert loaded["characters"][0]["appearance_variants"][0]["id"] == "hero_default"
    assert loaded["voice_registry"][0]["id"] == "hero_voice"
    assert loaded["segments"]["default"]["characters"] == ["hero"]
