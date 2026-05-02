"""Configuration loading with dotenv precedence."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from seedance_role_scene_remake.errors import ConfigError

DEFAULT_MODEL = "doubao-seedance-2-0-260128"
DEFAULT_SEEDREAM_MODEL = "doubao-seedream-5-0-lite-260128"
DEFAULT_ANALYSIS_ENDPOINT = "/api/v3/chat/completions"
DEFAULT_ASR_ENDPOINT = "/api/v3/audio/transcriptions"


@dataclass
class AppConfig:
    api_key: str
    base_url: str = "https://ark.cn-beijing.volces.com"
    submit_endpoint: str = "/api/v3/contents/generations/tasks"
    status_endpoint_template: str = "/api/v3/contents/generations/tasks/{task_id}"
    model: str = DEFAULT_MODEL
    resolution: str = "720p"
    seedream_model: str = DEFAULT_SEEDREAM_MODEL
    seedream_image_endpoint: str = "/api/v3/images/generations"
    seedream_size: str = "2K"
    analysis_model: str = ""
    analysis_endpoint: str = DEFAULT_ANALYSIS_ENDPOINT
    asr_model: str = ""
    asr_endpoint: str = DEFAULT_ASR_ENDPOINT
    request_timeout_s: int = 120
    poll_interval_s: int = 10
    poll_max_wait_s: int = 1800
    tos_access_key: str = ""
    tos_secret_key: str = ""
    tos_bucket: str = ""
    tos_endpoint: str = "tos-cn-beijing.volces.com"
    tos_region: str = "cn-beijing"
    tos_presign_expires_s: int = 604800

    @property
    def tos_available(self) -> bool:
        return bool(self.tos_access_key and self.tos_secret_key and self.tos_bucket)


def _load_dotenv_files() -> None:
    explicit = os.getenv("SEEDANCE_ROLE_SCENE_ENV")
    if explicit:
        load_dotenv(explicit, override=False)
        return

    for candidate in [Path.cwd(), *Path.cwd().parents]:
        env_file = candidate / ".env"
        if env_file.is_file():
            load_dotenv(env_file, override=False)
            break

    install_env = (
        Path(os.getenv("SEEDANCE_ROLE_SCENE_HOME", "~/.local/share/seedance-role-scene-remake"))
        .expanduser()
        / ".env"
    )
    if install_env.is_file():
        load_dotenv(install_env, override=False)


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数：{value}") from exc


def _model_env() -> str:
    return (
        os.getenv("SEEDANCE_ROLE_SCENE_MODEL")
        or os.getenv("SEEDANCE_MODEL")
        or os.getenv("SEEDANCE_ENDPOINT")
        or DEFAULT_MODEL
    )


def load_config(overrides: dict[str, Any] | None = None) -> AppConfig:
    _load_dotenv_files()
    overrides = overrides or {}
    merged: dict[str, Any] = {
        "api_key": overrides.get("api_key") or os.getenv("ARK_API_KEY", ""),
        "base_url": os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com"),
        "model": _model_env(),
        "resolution": os.getenv("SEEDANCE_ROLE_SCENE_RESOLUTION", "720p"),
        "seedream_model": os.getenv("SEEDANCE_ROLE_SCENE_SEEDREAM_MODEL") or os.getenv("SEEDREAM_MODEL") or DEFAULT_SEEDREAM_MODEL,
        "seedream_image_endpoint": os.getenv("SEEDANCE_ROLE_SCENE_SEEDREAM_ENDPOINT", "/api/v3/images/generations"),
        "seedream_size": os.getenv("SEEDANCE_ROLE_SCENE_SEEDREAM_SIZE", "2K"),
        "analysis_model": os.getenv("SEEDANCE_ROLE_SCENE_ANALYSIS_MODEL", ""),
        "analysis_endpoint": os.getenv("SEEDANCE_ROLE_SCENE_ANALYSIS_ENDPOINT", DEFAULT_ANALYSIS_ENDPOINT),
        "asr_model": os.getenv("SEEDANCE_ROLE_SCENE_ASR_MODEL", ""),
        "asr_endpoint": os.getenv("SEEDANCE_ROLE_SCENE_ASR_ENDPOINT", DEFAULT_ASR_ENDPOINT),
        "request_timeout_s": _int_env("SEEDANCE_ROLE_SCENE_REQUEST_TIMEOUT", 120),
        "poll_interval_s": _int_env("SEEDANCE_ROLE_SCENE_POLL_INTERVAL", 10),
        "poll_max_wait_s": _int_env("SEEDANCE_ROLE_SCENE_POLL_MAX_WAIT", 1800),
        "tos_access_key": os.getenv("VOLC_ACCESSKEY", ""),
        "tos_secret_key": os.getenv("VOLC_SECRETKEY", ""),
        "tos_bucket": os.getenv("TOS_BUCKET", ""),
        "tos_endpoint": os.getenv("TOS_ENDPOINT", "tos-cn-beijing.volces.com"),
        "tos_region": os.getenv("TOS_REGION") or os.getenv("OS_REGION", "cn-beijing"),
        "tos_presign_expires_s": _int_env("TOS_PRESIGN_EXPIRES", 604800),
    }
    merged.update({key: value for key, value in overrides.items() if value not in (None, "")})
    return AppConfig(**merged)
