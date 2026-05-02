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
from seedance_role_scene_remake.seedance import VideoGenerateRequest
from seedance_role_scene_remake.seedream import ImageGenerateRequest


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
