"""HTTP к ЕИС и прочим источникам. Пауза, retry на пустое тело, UA обязателен."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app import config


class FetchError(RuntimeError):
    pass


class Http:
    def __init__(
        self,
        *,
        verify: bool = True,
        proxy: str | None = None,
        user_agent: str | None = None,
        sleep_sec: float = 0.35,
        timeout: float = 30.0,
        accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    ) -> None:
        headers = {
            "User-Agent": user_agent or config.EIS_USER_AGENT,
            "Accept": accept,
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        }
        kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": timeout,
            "follow_redirects": True,
            "verify": verify,
            "trust_env": False,
        }
        if proxy:
            kwargs["proxy"] = proxy
        self._client = httpx.Client(**kwargs)
        self._sleep = max(0.15, float(sleep_sec))
        self._last = 0.0

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, *, params: dict | None = None, retries: int = 3) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(retries):
            self._throttle()
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_exc = exc
                time.sleep(1.2 * (attempt + 1))
                continue
            if len(response.content) == 0 and attempt < retries - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            return response
        raise FetchError(f"не удалось скачать {url}: {last_exc}")

    def get_text(self, url: str, *, params: dict | None = None) -> str:
        response = self.get(url, params=params)
        if response.status_code >= 400:
            raise FetchError(f"{url} → HTTP {response.status_code}")
        return response.text

    def get_bytes(self, url: str, *, max_bytes: int) -> tuple[bytes, dict[str, str]]:
        response = self.get(url)
        if response.status_code >= 400:
            raise FetchError(f"{url} → HTTP {response.status_code}")
        length = response.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_bytes:
            raise FetchError(f"{url} слишком большой: {length} байт")
        data = response.content
        if len(data) > max_bytes:
            raise FetchError(f"{url} слишком большой: {len(data)} байт")
        headers = {
            "content-type": str(response.headers.get("content-type") or ""),
            "content-disposition": str(response.headers.get("content-disposition") or ""),
        }
        return data, headers

    def get_json(self, url: str, *, params: dict | None = None) -> Any:
        response = self.get(url, params=params)
        if response.status_code >= 400:
            raise FetchError(f"{url} → HTTP {response.status_code}")
        return response.json()

    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self._sleep - (now - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()


def eis_http(sleep_sec: float, timeout: float = 30.0) -> Http:
    return Http(
        verify=False,
        proxy=config.EIS_PROXY or None,
        sleep_sec=sleep_sec,
        timeout=timeout,
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    )


def json_http(
    sleep_sec: float,
    *,
    verify: bool = True,
    proxy: str | None = None,
    timeout: float = 30.0,
) -> Http:
    return Http(
        verify=verify,
        proxy=proxy,
        sleep_sec=sleep_sec,
        timeout=timeout,
        accept="application/json,text/plain,*/*",
    )
