"""Seedance task client and payload builders."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from seedance_role_scene_remake.errors import ArkError
from seedance_role_scene_remake.manifest import ReferenceAsset


@dataclass
class VideoGenerateRequest:
    model: str
    prompt: str
    ratio: str
    duration: int
    resolution: str = "720p"
    video_urls: list[str] | None = None
    images: list[str] | None = None
    audio_urls: list[str] | None = None
    reference_assets: list[ReferenceAsset] | None = None
    generate_audio: bool = True

    def to_payload(self) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if self.prompt.strip():
            content.append({"type": "text", "text": self.prompt.strip()})
        if self.reference_assets:
            for asset in self.reference_assets:
                url = asset.uri or asset.path
                if not url:
                    continue
                if asset.kind == "video":
                    content.append({"type": "video_url", "video_url": {"url": url}, "role": asset.role or "reference_video"})
                elif asset.kind == "image":
                    content.append({"type": "image_url", "image_url": {"url": url}, "role": asset.role or "reference_image"})
                elif asset.kind == "audio":
                    content.append({"type": "audio_url", "audio_url": {"url": url}, "role": asset.role or "reference_audio"})
            return {
                "model": self.model,
                "content": content,
                "ratio": self.ratio,
                "duration": self.duration,
                "resolution": self.resolution,
                "watermark": False,
                "generate_audio": self.generate_audio,
            }
        for url in self.video_urls or []:
            content.append({"type": "video_url", "video_url": {"url": url}, "role": "reference_video"})
        for image in self.images or []:
            content.append({"type": "image_url", "image_url": {"url": image}, "role": "reference_image"})
        for url in self.audio_urls or []:
            content.append({"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"})
        return {
            "model": self.model,
            "content": content,
            "ratio": self.ratio,
            "duration": self.duration,
            "resolution": self.resolution,
            "watermark": False,
            "generate_audio": self.generate_audio,
        }


@dataclass
class TaskSubmission:
    task_id: str


@dataclass
class TaskStatus:
    task_id: str
    status: str
    file_url: str | None = None
    fail_reason: str | None = None
    request_id: str | None = None


class SeedanceClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        submit_endpoint: str,
        status_endpoint_template: str,
        timeout_s: int,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.submit_endpoint = submit_endpoint
        self.status_endpoint_template = status_endpoint_template
        self.timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def submit(self, request: VideoGenerateRequest) -> TaskSubmission:
        url = f"{self.base_url}{self.submit_endpoint}"
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                response = client.post(url, headers=self._headers(), json=request.to_payload())
        except httpx.HTTPError as exc:
            raise ArkError(f"Seedance 提交失败：{exc}") from exc
        data = _json_or_error(response)
        task_id = _pick(data, "id", "task_id", "taskId") or _pick(data.get("data", {}), "id", "task_id", "taskId")
        if not task_id:
            raise ArkError(f"Seedance 响应缺少 task_id：{data}", request_id=response.headers.get("x-request-id"))
        return TaskSubmission(task_id=str(task_id))

    def status(self, task_id: str) -> TaskStatus:
        path = self.status_endpoint_template.format(task_id=task_id)
        url = f"{self.base_url}{path}"
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                response = client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ArkError(f"Seedance 查询失败：{exc}") from exc
        data = _json_or_error(response)
        root = data.get("data", data)
        return TaskStatus(
            task_id=task_id,
            status=str(_pick(root, "status", "state") or ""),
            file_url=_extract_file_url(root),
            fail_reason=_pick(root, "fail_reason", "failure_reason", "error", "message"),
            request_id=response.headers.get("x-request-id"),
        )


def normalize_status(status: str) -> str:
    value = status.lower()
    if value in {"succeeded", "success", "done", "completed"}:
        return "succeeded"
    if value in {"failed", "fail", "error", "cancelled", "canceled"}:
        return "failed"
    if value in {"queued", "pending", "running", "processing", "created"}:
        return "running"
    return value


def poll_task(
    fetcher: Callable[[str], TaskStatus],
    task_id: str,
    *,
    interval_s: int,
    max_wait_s: int,
    on_update: Callable[[TaskStatus, str], None] | None = None,
) -> TaskStatus:
    deadline = time.monotonic() + max_wait_s
    while True:
        status = fetcher(task_id)
        normalized = normalize_status(status.status)
        if on_update:
            on_update(status, normalized)
        if normalized in {"succeeded", "failed"}:
            return status
        if time.monotonic() >= deadline:
            raise ArkError(f"任务轮询超时：{task_id}")
        time.sleep(interval_s)


def download_file(url: str, output: Path, *, timeout_s: int = 300) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.stream("GET", url, timeout=timeout_s) as response:
            response.raise_for_status()
            with output.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        raise ArkError(f"下载生成视频失败：{url}：{exc}") from exc
    return output


def _json_or_error(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise ArkError(f"Seedance 返回非 JSON：HTTP {response.status_code}", request_id=response.headers.get("x-request-id")) from exc
    if response.status_code >= 400:
        message = data.get("message") or data.get("error") or response.text
        raise ArkError(f"Seedance HTTP {response.status_code}：{message}", request_id=response.headers.get("x-request-id"))
    return data


def _pick(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _extract_file_url(data: dict[str, Any]) -> str | None:
    direct = _pick(data, "file_url", "video_url", "url", "output_url")
    if direct:
        return str(direct)
    content = data.get("content")
    if isinstance(content, dict):
        video_url = content.get("video_url")
        if isinstance(video_url, dict) and video_url.get("url"):
            return str(video_url["url"])
        if isinstance(video_url, str):
            return video_url
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            video_url = item.get("video_url")
            if isinstance(video_url, dict) and video_url.get("url"):
                return str(video_url["url"])
            if isinstance(video_url, str):
                return video_url
    result = data.get("result")
    if isinstance(result, dict):
        return _extract_file_url(result)
    return None
