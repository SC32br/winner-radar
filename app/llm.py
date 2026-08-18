"""Kie.ai OpenAI-совместимый чат. Ключ только из .env, в лог не пишем."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app import config

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def enabled() -> bool:
    return bool(config.LLM_API_KEY and config.LLM_BASE_URL)


def chat_json(system: str, user: str, *, timeout: float = 60.0) -> dict[str, Any] | None:
    raw = chat_text(system, user, timeout=timeout)
    if not raw:
        return None
    text = _FENCE.sub("", raw.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


LAST_ERROR = ""


def _endpoints() -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if config.LLM_BASE_URL:
        rows.append((config.LLM_BASE_URL.rstrip("/"), config.LLM_MODEL))
    fallback_url = (config.LLM_FALLBACK_BASE_URL or "").rstrip("/")
    fallback_model = config.LLM_FALLBACK_MODEL
    if fallback_url and (fallback_url, fallback_model) not in rows:
        rows.append((fallback_url, fallback_model))
    return rows


def chat_text(system: str, user: str, *, timeout: float = 60.0) -> str | None:
    global LAST_ERROR
    LAST_ERROR = ""
    if not enabled():
        LAST_ERROR = "no_key"
        return None
    errors: list[str] = []
    for base, model in _endpoints():
        text, err = _post_once(base, model, system, user, timeout=timeout)
        if text:
            LAST_ERROR = ""
            return text
        if err:
            errors.append(f"{model}:{err}")
            if err.startswith("http_401") or err.startswith("http_402"):
                LAST_ERROR = "; ".join(errors)
                return None
    LAST_ERROR = "; ".join(errors) or "empty_content"
    return None


def _post_once(
    base: str,
    model: str,
    system: str,
    user: str,
    *,
    timeout: float,
) -> tuple[str | None, str]:
    url = base + "/chat/completions"
    payload = {
        "model": model,
        "stream": False,
        "include_thoughts": False,
        "reasoning_effort": "low",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {config.LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        return None, f"http:{type(exc).__name__}"
    if response.status_code >= 400:
        return None, f"http_{response.status_code}:{response.text[:180]}"
    try:
        body = response.json()
    except ValueError:
        return None, "bad_json"
    text = _content_from_body(body)
    if not text:
        return None, "empty_content"
    return text, ""


def _content_from_body(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    choices = body.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            got = _flatten_content(message.get("content"))
            if got:
                return got
    candidates = body.get("candidates")
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        content = candidates[0].get("content")
        if isinstance(content, dict):
            parts = content.get("parts")
            if isinstance(parts, list):
                texts = [str(p.get("text") or "") for p in parts if isinstance(p, dict)]
                joined = "\n".join(t for t in texts if t).strip()
                if joined:
                    return joined
    return None


def _flatten_content(content: Any) -> str | None:
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        parts = [str(item.get("text") or "") for item in content if isinstance(item, dict)]
        joined = "\n".join(part for part in parts if part).strip()
        return joined or None
    return None
