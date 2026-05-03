from pathlib import Path

from seedance_role_scene_remake.manifest import (
    AppearanceVariantSpec,
    CharacterSpec,
    LanguagePolicy,
    Manifest,
    ReferenceAsset,
    SceneSpec,
    SegmentEntry,
    VoiceSpec,
)
from seedance_role_scene_remake.prompts import build_generation_prompt, inline_character, inline_scene
from seedance_role_scene_remake.reference_composer import ReferenceComposer
from seedance_role_scene_remake.seedance import VideoGenerateRequest
from seedance_role_scene_remake.seedream import ImageGenerateRequest
from seedance_role_scene_remake.verify import build_quality_report


def test_prompt_contains_fidelity_constraints():
    manifest = Manifest(
        prompt="电影感",
        language_policy=LanguagePolicy(
            source_language="en",
            target_language="preserve_source",
            spoken_language="en",
            translate_dialogue=False,
            translate_visible_text=False,
        ),
        voice_registry=[VoiceSpec(id="hero_voice", character_id="hero", prompt="稳定年轻男声")],
        characters=[
            CharacterSpec(
                id="hero",
                source_hint="原片主角",
                prompt="短发青年",
                voice_prompt="年轻男声，台词逐字不改",
                voice_id="hero_voice",
                appearance_variants=[
                    AppearanceVariantSpec(
                        id="hero_home",
                        source_hint="居家妆造",
                        image_uri="https://example.com/hero.jpg",
                        prompt="短发青年，家居夹克",
                    )
                ],
            )
        ],
        scenes=[SceneSpec(id="street", source_hint="原片街景", image_uri="https://example.com/street.jpg", prompt="现代夜景")],
    )
    seg = SegmentEntry(
        index=0,
        start=0,
        duration=4,
        reference_duration=4,
        generation_duration=4,
        source_path="segments/000.mp4",
        frame_path="frames/000.jpg",
        reference_path="segments/000.mp4",
        character_ids=["hero"],
        character_variant_ids=["hero_home"],
        scene_ids=["street"],
        voice_ids=["hero_voice"],
    )

    refs = [
        ReferenceAsset(
            slot="视频1",
            kind="video",
            role="reference_video",
            uri="https://example.com/ref.mp4",
            bound_type="segment",
            bound_id="000",
            note="原片段，仅参考动作和时序。",
        ),
        ReferenceAsset(
            slot="图片1",
            kind="image",
            role="reference_image",
            uri="https://example.com/hero.jpg",
            bound_type="appearance_variant",
            bound_id="hero_home",
            note="hero 的目标外观。",
        ),
        ReferenceAsset(
            slot="图片2",
            kind="image",
            role="reference_image",
            uri="https://example.com/street.jpg",
            bound_type="scene",
            bound_id="street",
            note="street 的目标场景。",
        ),
        ReferenceAsset(
            slot="音频1",
            kind="audio",
            role="reference_audio",
            uri="https://example.com/ref.m4a",
            bound_type="segment",
            bound_id="000",
            note="原音频节奏参考。",
        ),
    ]
    prompt = build_generation_prompt(manifest, seg, reference_assets=refs)

    assert "参考素材指代表" in prompt
    assert "视频1" in prompt
    assert "音频1" in prompt
    assert "hero（图片1）" in prompt
    assert "street（图片2）" in prompt
    assert "剧情事件" in prompt
    assert "对白语义" in prompt
    assert "台词逐字不改" in prompt
    assert "角色映射必须稳定" in prompt
    assert "妆造一致性" in prompt
    assert "场景一致性" in prompt
    assert "声音一致性" in prompt
    assert "所有角色继续说英文" in prompt
    assert "hero_home" in prompt
    assert "稳定年轻男声" in prompt
    assert "现代夜景" in prompt


def test_inline_character_and_scene():
    char = inline_character("id=hero,image=./hero.png,voice=年轻男声,prompt=短发")
    scene = inline_scene("id=street,image=./street.png,prompt=夜景")

    assert char.id == "hero"
    assert char.image_path == "./hero.png"
    assert char.voice_prompt == "年轻男声"
    assert scene.id == "street"


def test_seedance_payload_includes_audio_and_generated_audio_flag():
    payload = VideoGenerateRequest(
        model="m",
        prompt="p",
        ratio="16:9",
        duration=4,
        video_urls=["https://example.com/ref.mp4"],
        images=["https://example.com/hero.png"],
        audio_urls=["https://example.com/ref.m4a"],
        generate_audio=True,
    ).to_payload()

    assert payload["generate_audio"] is True
    assert any(item["type"] == "video_url" for item in payload["content"])
    assert any(item["type"] == "image_url" for item in payload["content"])
    assert any(item["type"] == "audio_url" for item in payload["content"])


def test_seedance_payload_preserves_reference_asset_order():
    payload = VideoGenerateRequest(
        model="m",
        prompt="p",
        ratio="16:9",
        duration=4,
        reference_assets=[
            ReferenceAsset(slot="视频1", kind="video", role="reference_video", uri="https://example.com/ref.mp4"),
            ReferenceAsset(slot="图片1", kind="image", role="reference_image", uri="https://example.com/hero.png"),
            ReferenceAsset(slot="音频1", kind="audio", role="reference_audio", uri="https://example.com/ref.m4a"),
        ],
        generate_audio=True,
    ).to_payload()

    assert [item["type"] for item in payload["content"]] == ["text", "video_url", "image_url", "audio_url"]


def test_seedream_payload_uses_image_generation_endpoint_shape():
    payload = ImageGenerateRequest(
        model="doubao-seedream-5-0-lite-260128",
        prompt="现代中式家居",
        size="2K",
        reference_images=["https://example.com/ref.jpg"],
        watermark=False,
    ).to_payload()

    assert payload["model"] == "doubao-seedream-5-0-lite-260128"
    assert payload["prompt"] == "现代中式家居"
    assert payload["image"] == "https://example.com/ref.jpg"
    assert payload["response_format"] == "url"
    assert payload["watermark"] is False


def test_reference_composer_respects_multimodal_limits():
    manifest = Manifest(
        characters=[
            CharacterSpec(
                id=f"c{idx:03d}",
                appearance_variants=[
                    AppearanceVariantSpec(id=f"c{idx:03d}_look", image_uri=f"https://example.com/c{idx}.jpg")
                ],
                voice_id=f"voice_{idx}",
            )
            for idx in range(12)
        ],
        scenes=[SceneSpec(id="scene", image_uri="https://example.com/scene.jpg")],
        voice_registry=[VoiceSpec(id=f"voice_{idx}", reference_uri=f"https://example.com/v{idx}.m4a") for idx in range(5)],
    )
    seg = SegmentEntry(
        index=0,
        start=0,
        duration=8,
        reference_duration=8,
        generation_duration=8,
        source_path="segments/000.mp4",
        frame_path="frames/000.jpg",
        reference_path="segments/000.mp4",
        reference_uri="https://example.com/ref.mp4",
        source_audio_uri="https://example.com/source.m4a",
        character_ids=[f"c{idx:03d}" for idx in range(12)],
        character_variant_ids=[f"c{idx:03d}_look" for idx in range(12)],
        scene_ids=["scene"],
        voice_ids=[f"voice_{idx}" for idx in range(5)],
    )

    plan = ReferenceComposer(strategy="full").compose(manifest=manifest, job_dir=Path("."), segment=seg)

    assert plan.report["counts"]["image"] == 9
    assert plan.report["counts"]["video"] <= 3
    assert plan.report["counts"]["audio"] == 3
    assert [item.slot for item in plan.assets if item.kind == "image"] == [f"图片{idx}" for idx in range(1, 10)]
    assert [item.slot for item in plan.assets if item.kind == "audio"] == ["音频1", "音频2", "音频3"]


def test_reference_composer_marks_raw_visual_references():
    manifest = Manifest(
        characters=[
            CharacterSpec(
                id="c001",
                appearance_variants=[AppearanceVariantSpec(id="c001_look", image_uri="https://example.com/c001.jpg")],
            )
        ],
        scenes=[SceneSpec(id="scene", image_uri="asset://asset-scene")],
    )
    seg = SegmentEntry(
        index=0,
        start=0,
        duration=4,
        reference_duration=4,
        generation_duration=4,
        source_path="segments/000.mp4",
        frame_path="frames/000.jpg",
        reference_path="segments/000.mp4",
        reference_uri="https://example.com/ref.mp4",
        character_ids=["c001"],
        character_variant_ids=["c001_look"],
        scene_ids=["scene"],
    )

    plan = ReferenceComposer(strategy="full").compose(manifest=manifest, job_dir=Path("."), segment=seg)

    raw_visuals = [item for item in plan.report["assets"] if item["kind"] in {"image", "video"} and item["trust_status"] == "raw"]
    assert raw_visuals
    assert any(item["slot"] == "视频1" for item in raw_visuals)


def test_verify_marks_raw_visuals_in_assetized_mode(tmp_path: Path):
    manifest = Manifest(
        segments=[
            SegmentEntry(
                index=0,
                start=0,
                duration=4,
                reference_duration=4,
                generation_duration=4,
                source_path="segments/000.mp4",
                frame_path="frames/000.jpg",
                reference_path="segments/000.mp4",
                status="succeeded",
                reference_report={
                    "strategy": "full",
                    "reference_privacy": "assetized",
                    "assets": [
                        {"slot": "视频1", "kind": "video", "trust_status": "raw"},
                        {"slot": "图片1", "kind": "image", "trust_status": "assetized"},
                    ],
                },
            )
        ]
    )

    report = build_quality_report(manifest, job_dir=tmp_path)

    assert report["issue"] is True
    assert report["issues"][0]["type"] == "reference_privacy"
    assert report["segments"][0]["asset_coverage"]["raw_visual_slots"] == ["视频1"]


def test_prompt_includes_dialogue_timing_table():
    manifest = Manifest(
        audio_mode="source",
        generate_audio=False,
        language_policy=LanguagePolicy(source_language="en", target_language="preserve_source", spoken_language="en"),
        characters=[
            CharacterSpec(
                id="c001",
                appearance_variants=[AppearanceVariantSpec(id="c001_look", image_uri="https://example.com/c001.jpg")],
            )
        ],
        scenes=[SceneSpec(id="scene", image_uri="https://example.com/scene.jpg")],
    )
    seg = SegmentEntry(
        index=0,
        start=0,
        duration=4,
        reference_duration=4,
        generation_duration=4,
        source_path="segments/000.mp4",
        frame_path="frames/000.jpg",
        reference_path="segments/000.mp4",
        character_ids=["c001"],
        character_variant_ids=["c001_look"],
        scene_ids=["scene"],
        dialogue_timings=[{"start": 0.44, "end": 1.0, "speaker": "c001", "text": "You need to leave."}],
    )
    refs = ReferenceComposer(strategy="script-only").compose(manifest=manifest, job_dir=Path("."), segment=seg).assets

    prompt = build_generation_prompt(manifest, seg, reference_assets=refs)

    assert "口型/对白时间表" in prompt
    assert '0.44-1.00s：c001（图片1）' in prompt
    assert "You need to leave." in prompt
    assert "必须闭口" in prompt
    assert "不要把台词渲染成画面文字或字幕" in prompt
