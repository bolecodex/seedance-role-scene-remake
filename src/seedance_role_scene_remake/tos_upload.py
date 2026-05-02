"""TOS upload helper."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from seedance_role_scene_remake.errors import UploadError


@dataclass
class TOSConfig:
    access_key: str
    secret_key: str
    bucket: str
    endpoint: str
    region: str
    presign_expires_s: int = 604800


def upload_file(path: Path, *, prefix: str, config: TOSConfig) -> str:
    if not path.exists():
        raise UploadError(f"上传文件不存在：{path}")
    try:
        import tos
    except ImportError as exc:
        raise UploadError("缺少 tos 依赖，请先安装项目依赖。") from exc
    key = f"{prefix.rstrip('/')}/{uuid4().hex}_{path.name}"
    try:
        client = tos.TosClientV2(
            ak=config.access_key,
            sk=config.secret_key,
            endpoint=config.endpoint,
            region=config.region,
        )
        client.put_object_from_file(config.bucket, key, str(path))
        signed = client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Get,
            config.bucket,
            key,
            expires=config.presign_expires_s,
        )
    except Exception as exc:
        raise UploadError(f"TOS 上传失败：{path}：{exc}") from exc
    return signed.signed_url
