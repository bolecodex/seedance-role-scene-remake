"""Project-specific exceptions."""

from __future__ import annotations


class RoleSceneError(Exception):
    """Base error for this package."""


class ConfigError(RoleSceneError):
    """Invalid environment or CLI configuration."""


class ManifestError(RoleSceneError):
    """Invalid or unusable manifest."""


class PipelineError(RoleSceneError):
    """Pipeline execution failed."""


class UploadError(RoleSceneError):
    """Object storage upload failed."""


class ArkError(RoleSceneError):
    """Volcengine Ark or Seedance request failed."""

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id

