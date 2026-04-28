from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

CONNECT_API_PORT = 8080


class ConnectClient:
    """1Password Connect REST API client."""

    def __init__(self, token: str, port: int = CONNECT_API_PORT) -> None:
        self._base_url = f"http://localhost:{port}"
        self._token = token
        self._vault_cache: dict[str, str] = {}
        self._item_cache: dict[tuple[str, str], dict] = {}

    def _request(self, path: str) -> list | dict:
        req = urllib.request.Request(
            f"{self._base_url}{path}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def wait_for_ready(self, timeout: int = 60) -> None:
        """Poll /heartbeat until the API is ready."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                req = urllib.request.Request(f"{self._base_url}/heartbeat")
                with urllib.request.urlopen(req, timeout=5):
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(2)
        msg = f"Connect API not ready after {timeout}s"
        raise TimeoutError(msg)

    def _resolve_vault_id(self, vault_name: str) -> str:
        if vault_name in self._vault_cache:
            return self._vault_cache[vault_name]
        vaults = self._request("/v1/vaults")
        for v in vaults:
            if v["name"] == vault_name:
                self._vault_cache[vault_name] = v["id"]
                return v["id"]
        msg = f"Vault not found: {vault_name}"
        raise ValueError(msg)

    def _get_item(self, vault_name: str, item_title: str) -> dict:
        cache_key = (vault_name, item_title)
        if cache_key in self._item_cache:
            return self._item_cache[cache_key]
        vault_id = self._resolve_vault_id(vault_name)
        params = urlencode({"filter": f'title eq "{item_title}"'})
        items = self._request(f"/v1/vaults/{vault_id}/items?{params}")
        if not items:
            msg = f"Item not found: {item_title} in {vault_name}"
            raise ValueError(msg)
        item = self._request(f"/v1/vaults/{vault_id}/items/{items[0]['id']}")
        self._item_cache[cache_key] = item
        return item

    def get_field(
        self,
        vault_name: str,
        item_title: str,
        field_label: str,
        *,
        section: str | None = None,
    ) -> str:
        item = self._get_item(vault_name, item_title)
        section_id: str | None = None
        if section is not None:
            for s in item.get("sections", []):
                if s.get("label") == section:
                    section_id = s.get("id")
                    break
            if section_id is None:
                msg = f"Section '{section}' not found in {item_title}"
                raise ValueError(msg)
        for field in item.get("fields", []):
            if field.get("label") != field_label:
                continue
            field_section = (field.get("section") or {}).get("id")
            if section is None and field_section is None:
                return field.get("value", "")
            if section is not None and field_section == section_id:
                return field.get("value", "")
        loc = f"{section}/{field_label}" if section else field_label
        msg = f"Field '{loc}' not found in {item_title}"
        raise ValueError(msg)
