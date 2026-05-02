"""Seedream image generation client."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from seedance_role_scene_remake.errors import ArkError


@dataclass
class ImageGenerateRequest:
    model: str
    prompt: str
    size: str = "2K"
    response_format: str = "url"
    reference_images: list[str] | None = None
    watermark: bool = False

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": self.prompt,
            "size": self.size,
            "response_format": self.response_format,
            "watermark": self.watermark,
            "sequential_image_generation": "disabled",
        }
        if self.reference_images:
            payload["image"] = self.reference_images[0] if len(self.reference_images) == 1 else self.reference_images
        return payload


@dataclass
class GeneratedImage:
    url: str | None = None
    b64_json: str | None = None


class SeedreamClient:
    def __init__(self, *, api_key: str, base_url: str, image_endpoint: str, timeout_s: int) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.image_endpoint = image_endpoint
        self.timeout_s = timeout_s

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def generate(self, request: ImageGenerateRequest) -> list[GeneratedImage]:
        url = f"{self.base_url}{self.image_endpoint}"
        try:
            with httpx.Client(timeout=self.timeout_s) as client:
                response = client.post(url, headers=self._headers(), json=request.to_payload())
        except httpx.HTTPError as exc:
            raise ArkError(f"Seedream 图片生成提交失败：{exc}") from exc
        data = _json_or_error(response)
        images = _extract_images(data)
        if not images:
            raise ArkError(f"Seedream 响应缺少图片 URL/base64：{data}", request_id=response.headers.get("x-request-id"))
        return images


def save_generated_image(image: GeneratedImage, output: Path, *, timeout_s: int = 300) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    if image.b64_json:
        output.write_bytes(base64.b64decode(image.b64_json))
        return output
    if not image.url:
        raise ArkError("Seedream 图片结果缺少 URL/base64。")
    try:
        with httpx.stream("GET", image.url, timeout=timeout_s) as response:
            response.raise_for_status()
            with output.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        raise ArkError(f"下载 Seedream 图片失败：{image.url}：{exc}") from exc
    return output


def _json_or_error(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise ArkError(f"Seedream 返回非 JSON：HTTP {response.status_code}", request_id=response.headers.get("x-request-id")) from exc
    if response.status_code >= 400:
        message = data.get("message") or data.get("error") or response.text
        raise ArkError(f"Seedream HTTP {response.status_code}：{message}", request_id=response.headers.get("x-request-id"))
    return data


def _extract_images(data: dict[str, Any]) -> list[GeneratedImage]:
    root = data.get("data", data)
    if isinstance(root, list):
        images: list[GeneratedImage] = []
        for item in root:
            images.extend(_extract_images(item) if isinstance(item, dict) else [])
        return images
    if not isinstance(root, dict):
        return []
    images: list[GeneratedImage] = []
    if root.get("url") or root.get("b64_json"):
        images.append(GeneratedImage(url=root.get("url"), b64_json=root.get("b64_json")))
    if root.get("image_url"):
        images.append(GeneratedImage(url=str(root["image_url"])))
    if root.get("images"):
        for item in root["images"]:
            if isinstance(item, str):
                images.append(GeneratedImage(url=item))
            elif isinstance(item, dict):
                images.extend(_extract_images(item))
    if root.get("result"):
        result = root["result"]
        if isinstance(result, dict):
            images.extend(_extract_images(result))
        elif isinstance(result, list):
            for item in result:
                if isinstance(item, str):
                    images.append(GeneratedImage(url=item))
                elif isinstance(item, dict):
                    images.extend(_extract_images(item))
    return images
