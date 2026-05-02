"""Volcengine Ark asset-library client for trusted reference media."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from seedance_role_scene_remake.errors import ArkError, ConfigError


class AssetsClient:
    """Minimal Ark Assets OpenAPI client using Volcengine v4 signing."""

    SERVICE = "ark"
    VERSION = "2024-01-01"
    HOST = "open.volcengineapi.com"

    def __init__(self, access_key: str, secret_key: str, region: str = "cn-beijing") -> None:
        if not access_key or not secret_key:
            raise ConfigError("资产库 API 需要 VOLC_ACCESSKEY 和 VOLC_SECRETKEY。")
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region

    def create_asset_group(self, name: str, description: str = "", group_type: str = "AIGC") -> str:
        response = self._call("CreateAssetGroup", {"Name": name, "Description": description, "GroupType": group_type})
        result = response.get("Result", response)
        return str(result["Id"])

    def list_asset_groups(self, group_type: str = "AIGC") -> list[dict[str, Any]]:
        response = self._call("ListAssetGroups", {"Filter": {"GroupType": group_type}, "PageNumber": 1, "PageSize": 100})
        result = response.get("Result", response)
        return list(result.get("Items", []))

    def create_asset(self, group_id: str, url: str, asset_type: str = "Video", name: str = "") -> str:
        body: dict[str, Any] = {"GroupId": group_id, "URL": url, "AssetType": asset_type}
        if name:
            body["Name"] = name
        response = self._call("CreateAsset", body)
        result = response.get("Result", response)
        asset_id = result.get("Id") or result.get("AssetId")
        if not asset_id:
            raise ArkError(f"资产库响应缺少 asset_id：{response}")
        return str(asset_id)

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        response = self._call("GetAsset", {"Id": asset_id})
        return dict(response.get("Result", response))

    def wait_asset_active(self, asset_id: str, *, interval: float = 5, timeout: float = 300) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            info = self.get_asset(asset_id)
            status = str(info.get("Status", ""))
            if status == "Active":
                return info
            if status == "Failed":
                error = info.get("Error", {})
                message = error.get("Message", "") if isinstance(error, dict) else str(error)
                raise ArkError(f"资产 {asset_id} 处理失败：{message}")
            if time.monotonic() >= deadline:
                raise ArkError(f"资产 {asset_id} 等待超时，当前状态：{status}")
            time.sleep(interval)

    def _call(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        date = now.strftime("%Y%m%dT%H%M%SZ")
        short_date = now.strftime("%Y%m%d")
        body_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        body_hash = hashlib.sha256(body_bytes).hexdigest()
        query = f"Action={action}&Version={self.VERSION}"
        headers = {"Host": self.HOST, "Content-Type": "application/json", "X-Date": date}
        signed_headers = "content-type;host;x-date"
        canonical_headers = f"content-type:application/json\nhost:{self.HOST}\nx-date:{date}\n"
        canonical_request = "\n".join(["POST", "/", query, canonical_headers, signed_headers, body_hash])
        credential_scope = f"{short_date}/{self.region}/{self.SERVICE}/request"
        string_to_sign = "\n".join(
            [
                "HMAC-SHA256",
                date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        k_date = self._hmac(short_date.encode("utf-8"), self.secret_key.encode("utf-8"))
        k_region = self._hmac(self.region.encode("utf-8"), k_date)
        k_service = self._hmac(self.SERVICE.encode("utf-8"), k_region)
        k_signing = self._hmac(b"request", k_service)
        signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["Authorization"] = (
            f"HMAC-SHA256 Credential={self.access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(f"https://{self.HOST}/?{query}", content=body_bytes, headers=headers)
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ArkError(f"资产库 API 调用失败：{action}：{exc}") from exc
        metadata = data.get("ResponseMetadata", {})
        error = metadata.get("Error")
        if error:
            raise ArkError(f"资产库 API 错误 [{error.get('Code')}]: {error.get('Message')}")
        return data

    @staticmethod
    def _hmac(data: bytes, key: bytes) -> bytes:
        return hmac.new(key, data, hashlib.sha256).digest()

