"""Thin CRM API client. Every write goes through `create_account` / `update_account`."""
from __future__ import annotations

import time
from typing import Any

import requests

from . import config


class CRMError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class CRMClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, session: requests.Session | None = None):
        self.base_url = (base_url or config.CRM_BASE_URL).rstrip("/")
        token = token or config.CRM_API_TOKEN
        if not token:
            raise CRMError("CRM_API_TOKEN is not set (put it in .env or the environment)")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    # ---------- low level ----------
    def _request(self, method: str, path: str, *, params=None, json=None, retries: int = 3) -> Any:
        url = f"{self.base_url}{path}"
        for attempt in range(retries + 1):
            try:
                resp = self.session.request(method, url, params=params, json=json, timeout=30)
            except requests.RequestException as exc:
                if attempt == retries:
                    raise CRMError(f"{method} {path} failed: {exc}") from exc
                time.sleep(2 ** attempt)
                continue
            # Retry only transient failures, and never retry a POST (it might have succeeded).
            if resp.status_code in (429, 502, 503, 504) and attempt < retries and method != "POST":
                time.sleep(2 ** attempt)
                continue
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
            if resp.status_code >= 400:
                detail = body.get("detail") if isinstance(body, dict) else body
                raise CRMError(f"{method} {path} -> {resp.status_code}: {detail}", resp.status_code, body)
            return body
        raise CRMError(f"{method} {path} exhausted retries")

    # ---------- reads ----------
    def _list_all(self, path: str, params: dict | None = None, page_size: int = 100) -> list[dict]:
        rows: list[dict] = []
        page = 1
        while True:
            body = self._request("GET", path, params={**(params or {}), "page": page, "page_size": page_size})
            data = body.get("data", [])
            rows.extend(data)
            total = body.get("total", len(rows))
            if not data or len(rows) >= total:
                return rows
            page += 1

    def list_accounts(self) -> list[dict]:
        return self._list_all("/accounts")

    def list_contacts(self) -> list[dict]:
        return self._list_all("/contacts")

    def get_account(self, account_id: str) -> dict:
        return self._request("GET", f"/accounts/{account_id}")

    def search_accounts(self, **params) -> list[dict]:
        return self._list_all("/accounts", params=params)

    # ---------- writes ----------
    def create_account(self, fields: dict) -> dict:
        _validate(fields)
        return self._request("POST", "/accounts", json=fields)

    def update_account(self, account_id: str, fields: dict) -> dict:
        _validate(fields)
        return self._request("PATCH", f"/accounts/{account_id}", json=fields)


def _validate(fields: dict) -> None:
    """Client-side guard for values the server does not check itself."""
    if "care_type" in fields and fields["care_type"] not in config.VALID_CARE_TYPES:
        raise CRMError(f"Refusing to write invalid care_type {fields['care_type']!r}")
    if "status" in fields and fields["status"] not in config.VALID_STATUSES:
        raise CRMError(f"Refusing to write invalid status {fields['status']!r}")
