"""Small FFmpeg/FFprobe helpers used by the pipeline."""

from __future__ import annotations

import base64
import json
import math
import mimetypes
import re
import subprocess
from pathlib import Path

from seedance_role_scene_remake.errors import PipelineError


def run_cmd(args: list[str]) -> str:
    try:
        proc = subprocess.run(args, check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise PipelineError(f"找不到命令：{args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip()
        raise PipelineError(f"命令执行失败：{' '.join(args)}\n{detail}") from exc
    return proc.stdout


def ffprobe_json(path: Path) -> dict:
    output = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
    )
    return json.loads(output)


def get_video_duration(path: Path) -> float:
    data = ffprobe_json(path)
    duration = data.get("format", {}).get("duration")
    if duration is None:
        raise PipelineError(f"无法读取视频时长：{path}")
    return float(duration)


def get_video_fps(path: Path) -> str | None:
    data = ffprobe_json(path)
    for stream in data.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        fps = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
        if fps and fps != "0/0":
            return str(fps)
    return None


def get_video_ratio(path: Path) -> str:
    data = ffprobe_json(path)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            if width and height:
                gcd = math.gcd(width, height)
                return f"{width // gcd}:{height // gcd}"
    return "auto"


def has_audio(path: Path) -> bool:
    data = ffprobe_json(path)
    return any(stream.get("codec_type") == "audio" for stream in data.get("streams", []))


def parse_ratio(value: str, source_ratio: str) -> str:
    if not value or value == "auto":
        return source_ratio
    return value


def duration_for_generation(path: Path) -> int:
    return max(1, min(15, int(math.ceil(get_video_duration(path)))))


def split_video(video: Path, output_dir: Path, segment_seconds: int) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = get_video_duration(video)
    paths: list[Path] = []
    start = 0.0
    index = 0
    while start < duration - 0.05:
        chunk_duration = min(float(segment_seconds), duration - start)
        out = output_dir / f"{index:03d}.mp4"
        run_cmd(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{chunk_duration:.3f}",
                "-i",
                str(video),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )
        paths.append(out)
        start += chunk_duration
        index += 1
    return paths


def extract_first_frame(video: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(["ffmpeg", "-y", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(output)])


def extract_last_frame(video: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.0, get_video_duration(video) - 0.25)
    run_cmd(["ffmpeg", "-y", "-ss", f"{duration:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(output)])


def extract_frame_at(video: Path, output: Path, timestamp: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    duration = get_video_duration(video)
    at = max(0.0, min(timestamp, max(0.0, duration - 0.05)))
    run_cmd(["ffmpeg", "-y", "-ss", f"{at:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(output)])


def crop_image(image: Path, output: Path, bbox: list[int]) -> None:
    if len(bbox) != 4:
        raise PipelineError(f"bbox 必须是 [x,y,w,h]：{bbox}")
    frame_width, frame_height = _media_dimensions(image)
    x, y, crop_width, crop_height = bbox
    if x + crop_width > frame_width or y + crop_height > frame_height:
        scale_x = frame_width / 720
        scale_y = frame_height / 1280
        x = int(x * scale_x)
        y = int(y * scale_y)
        crop_width = int(crop_width * scale_x)
        crop_height = int(crop_height * scale_y)
    x = max(0, min(int(x), max(0, frame_width - 1)))
    y = max(0, min(int(y), max(0, frame_height - 1)))
    crop_width = max(2, min(int(crop_width), frame_width - x))
    crop_height = max(2, min(int(crop_height), frame_height - y))
    if crop_width % 2:
        crop_width = max(2, crop_width - 1)
    if crop_height % 2:
        crop_height = max(2, crop_height - 1)
    output.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(image),
            "-vf",
            f"crop={crop_width}:{crop_height}:{x}:{y}",
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(output),
        ]
    )


def _media_dimensions(path: Path) -> tuple[int, int]:
    data = ffprobe_json(path)
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            if width > 0 and height > 0:
                return width, height
    raise PipelineError(f"无法读取图像尺寸：{path}")


def extract_audio(video: Path, output: Path) -> bool:
    if not has_audio(video):
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(["ffmpeg", "-y", "-i", str(video), "-vn", "-c:a", "aac", "-b:a", "192k", str(output)])
    return True


def extract_audio_for_asr(video: Path, output: Path) -> bool:
    if not has_audio(video):
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    return True


def extract_audio_clip(source: Path, output: Path, *, start: float, duration: float) -> bool:
    if not has_audio(source):
        return False
    output.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{max(0.0, start):.3f}",
            "-t",
            f"{max(0.1, duration):.3f}",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ]
    )
    return True


def detect_scene_timestamps(video: Path, *, threshold: float = 0.35) -> list[float]:
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(video),
                "-filter:v",
                f"select='gt(scene,{threshold})',showinfo",
                "-f",
                "null",
                "-",
            ],
            text=True,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise PipelineError("找不到命令：ffmpeg") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip()
        raise PipelineError(f"镜头检测失败：{video}\n{detail}") from exc
    times: list[float] = []
    for match in re.finditer(r"pts_time:([0-9.]+)", proc.stderr):
        value = float(match.group(1))
        if not times or abs(value - times[-1]) > 0.2:
            times.append(value)
    return times


def normalize_video_duration(video: Path, output: Path, target_duration: float, *, fps: str | None = None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    source_duration = max(0.001, get_video_duration(video))
    ratio = target_duration / source_duration
    vf = f"setpts={ratio:.8f}*PTS"
    if fps:
        vf += f",fps={fps}"
    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-an",
            "-vf",
            vf,
            "-t",
            f"{target_duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            str(output),
        ]
    )


def normalize_audio_duration(audio: Path, output: Path, target_duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(audio),
            "-af",
            "apad",
            "-t",
            f"{target_duration:.3f}",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output),
        ]
    )


def concat_videos(paths: list[Path], output: Path) -> None:
    _concat_media(paths, output, stream_type="video")


def concat_audios(paths: list[Path], output: Path) -> None:
    _concat_media(paths, output, stream_type="audio")


def mux_audio(video: Path, audio: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ]
    )


def image_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def image_rgb_embedding(path: Path, *, size: int = 8) -> list[float]:
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-vf",
                f"scale={size}:{size}:force_original_aspect_ratio=disable",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise PipelineError("找不到命令：ffmpeg") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="ignore").strip()
        raise PipelineError(f"无法计算图片嵌入：{path}\n{detail}") from exc
    expected = size * size * 3
    data = proc.stdout[:expected]
    if len(data) < expected:
        raise PipelineError(f"图片嵌入数据不足：{path}")
    return [byte / 255.0 for byte in data]


def _concat_media(paths: list[Path], output: Path, *, stream_type: str) -> None:
    if not paths:
        raise PipelineError(f"没有可拼接的{stream_type}片段。")
    output.parent.mkdir(parents=True, exist_ok=True)
    list_file = output.with_suffix(output.suffix + ".list.txt")
    list_file.write_text("".join(f"file '{path.resolve()}'\n" for path in paths), encoding="utf-8")
    try:
        run_cmd(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(output)])
    finally:
        if list_file.exists():
            list_file.unlink()
