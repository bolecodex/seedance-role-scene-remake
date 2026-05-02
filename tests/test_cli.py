from pathlib import Path

from typer.testing import CliRunner

from seedance_role_scene_remake.cli import app
from seedance_role_scene_remake.manifest import Manifest, SegmentEntry

runner = CliRunner()


def test_version_command_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)

    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "seedance-role-scene-remake" in result.stdout


def test_run_accepts_spec_and_generated_audio_default(tmp_path: Path, monkeypatch):
    calls = {}

    def fake_run_job(**kwargs):
        calls.update(kwargs)
        return tmp_path / "final.mp4"

    monkeypatch.setattr("seedance_role_scene_remake.cli.run_job", fake_run_job)

    result = runner.invoke(
        app,
        [
            "run",
            str(tmp_path / "input.mp4"),
            "-o",
            str(tmp_path / "job"),
            "--spec",
            str(tmp_path / "spec.yaml"),
        ],
    )

    assert result.exit_code == 0
    assert calls["spec_path"] == tmp_path / "spec.yaml"
    assert calls["no_upload"] is False
    assert calls["allow_unprepared"] is False


def test_run_rejects_no_generated_audio(tmp_path: Path):
    result = runner.invoke(
        app,
        [
            "run",
            str(tmp_path / "input.mp4"),
            "-o",
            str(tmp_path / "job"),
            "--no-generated-audio",
        ],
    )

    assert result.exit_code != 0
    assert "不支持 --no-generated-audio" in str(result.exception) or "Invalid value" in result.stdout


def test_status_reads_local_manifest_without_api_key(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    path = tmp_path / "manifest.json"
    Manifest(
        source="/tmp/input.mp4",
        source_ratio="16:9",
        target_ratio="16:9",
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
            )
        ],
    ).save(path)

    result = runner.invoke(app, ["status", str(path)])

    assert result.exit_code == 0
    assert "音频：generated" in result.stdout


def test_prepare_command_passes_language_options(tmp_path: Path, monkeypatch):
    calls = {}

    def fake_prepare_job(**kwargs):
        calls.update(kwargs)
        return tmp_path / "job" / "manifest.json"

    monkeypatch.setattr("seedance_role_scene_remake.cli.prepare_job", fake_prepare_job)

    result = runner.invoke(
        app,
        [
            "prepare",
            str(tmp_path / "input.mp4"),
            "-o",
            str(tmp_path / "job"),
            "--source-language",
            "en",
            "--target-language",
            "zh-CN",
            "--spoken-language",
            "en",
            "--character-prompt",
            "自然现代中国人",
            "--scene-prompt",
            "现代中式家居",
            "--prompt",
            "剧情不变",
            "--target-size",
            "2K",
        ],
    )

    assert result.exit_code == 0
    assert calls["source_language"] == "en"
    assert calls["target_language"] == "zh-CN"
    assert calls["spoken_language"] == "en"
    assert calls["character_prompt"] == "自然现代中国人"
    assert calls["scene_prompt"] == "现代中式家居"
    assert calls["prompt"] == "剧情不变"
    assert calls["auto_render_targets"] is True
    assert calls["target_size"] == "2K"
    assert calls["analysis_path"] is None


def test_analyze_command_passes_options(tmp_path: Path, monkeypatch):
    calls = {}

    def fake_analyze_job(**kwargs):
        calls.update(kwargs)
        return tmp_path / "job" / "analysis" / "analysis.json"

    monkeypatch.setattr("seedance_role_scene_remake.cli.analyze_job", fake_analyze_job)

    result = runner.invoke(
        app,
        [
            "analyze",
            str(tmp_path / "input.mp4"),
            "-o",
            str(tmp_path / "job"),
            "--analysis-model",
            "vlm",
            "--asr-model",
            "asr",
            "--sample-seconds",
            "1.5",
            "--scene-threshold",
            "0.42",
            "--script-detail",
            "standard",
            "--script-min-action-beats",
            "3",
            "--script-quality-json",
            str(tmp_path / "quality.json"),
            "--allow-skeleton",
        ],
    )

    assert result.exit_code == 0
    assert calls["analysis_model"] == "vlm"
    assert calls["asr_model"] == "asr"
    assert calls["sample_seconds"] == 1.5
    assert calls["scene_threshold"] == 0.42
    assert calls["script_detail"] == "standard"
    assert calls["script_min_action_beats"] == 3
    assert calls["script_quality_json"] == tmp_path / "quality.json"
    assert calls["allow_skeleton"] is True


def test_analyze_manifest_command_reads_summary(tmp_path: Path, monkeypatch):
    calls = {}

    def fake_analyze_manifest_job(**kwargs):
        calls.update(kwargs)
        return ["ok"]

    monkeypatch.setattr("seedance_role_scene_remake.cli.analyze_manifest_job", fake_analyze_manifest_job)

    result = runner.invoke(app, ["analyze-manifest", str(tmp_path / "analysis.json")])

    assert result.exit_code == 0
    assert calls["analysis_path"] == tmp_path / "analysis.json"


def test_render_targets_command_passes_options(tmp_path: Path, monkeypatch):
    calls = {}

    def fake_render_targets_job(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr("seedance_role_scene_remake.cli.render_targets_job", fake_render_targets_job)

    result = runner.invoke(
        app,
        [
            "render-targets",
            str(tmp_path / "manifest.json"),
            "--no-scenes",
            "--size",
            "2K",
            "--overwrite",
        ],
    )

    assert result.exit_code == 0
    assert calls["characters"] is True
    assert calls["scenes"] is False
    assert calls["size"] == "2K"
    assert calls["overwrite"] is True


def test_remake_passes_allow_unprepared(tmp_path: Path, monkeypatch):
    calls = {}

    def fake_remake_job(**kwargs):
        calls.update(kwargs)

    monkeypatch.setattr("seedance_role_scene_remake.cli.remake_job", fake_remake_job)

    result = runner.invoke(app, ["remake", str(tmp_path / "manifest.json"), "--allow-unprepared"])

    assert result.exit_code == 0
    assert calls["allow_unprepared"] is True


def test_verify_command_passes_audio_options(tmp_path: Path, monkeypatch):
    calls = {}

    def fake_verify_job(**kwargs):
        calls.update(kwargs)
        return tmp_path / "report.html"

    monkeypatch.setattr("seedance_role_scene_remake.cli.verify_job", fake_verify_job)

    result = runner.invoke(
        app,
        [
            "verify",
            str(tmp_path / "manifest.json"),
            "--audio-report",
            "--continuity-report",
            "--identity-report",
            "--scene-report",
            "--language-report",
            "--voice-report",
            "--target-report",
            "--quality-json",
            str(tmp_path / "quality.json"),
        ],
    )

    assert result.exit_code == 0
    assert calls["audio_report"] is True
    assert calls["continuity_report"] is True
    assert calls["identity_report"] is True
    assert calls["scene_report"] is True
    assert calls["language_report"] is True
    assert calls["voice_report"] is True
    assert calls["target_report"] is True
    assert calls["quality_json"] == tmp_path / "quality.json"
