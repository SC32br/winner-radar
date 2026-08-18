"""Load settings from .env. All knobs are documented there in Russian."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _req(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"В .env не задано обязательное поле {name}")
    return value


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _idna_host(host: str) -> str:
    """Browser often sends punycode Host for .рф — allow both spellings."""
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


SITE_HOST = _req("SITE_HOST")
SITE_PUBLIC_URL = os.getenv("SITE_PUBLIC_URL", f"https://{SITE_HOST}").rstrip("/")
APP_BIND = os.getenv("APP_BIND", "127.0.0.1").strip() or "127.0.0.1"
APP_PORT = _int("APP_PORT", 8000)
DASHBOARD_LOGIN = _req("DASHBOARD_LOGIN")
DASHBOARD_PASSWORD = _req("DASHBOARD_PASSWORD")
SESSION_SECRET = _req("SESSION_SECRET")
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "radar_session").strip()
SESSION_HOURS = _int("SESSION_HOURS", 12)
_https_raw = os.getenv("SESSION_HTTPS_ONLY", "false").strip().lower()
SESSION_HTTPS_ONLY = _https_raw in {"1", "true", "yes", "on"}

DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data"))).expanduser()
DB_PATH = DATA_DIR / "radar.db"
LOG_DIR = DATA_DIR / "logs"
FILTERS_PATH = ROOT / "config.yaml"

DADATA_API_KEY = os.getenv("DADATA_API_KEY", "").strip()
DADATA_SECRET_KEY = os.getenv("DADATA_SECRET_KEY", "").strip()
CHECKO_API_KEY = os.getenv("CHECKO_API_KEY", "").strip()

LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL",
    "https://api.kie.ai/gemini-3-6-flash-openai/v1",
).strip()
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3-6-flash").strip()
LLM_FALLBACK_BASE_URL = os.getenv(
    "LLM_FALLBACK_BASE_URL",
    "https://api.kie.ai/gemini-2.5-flash/v1",
).strip()
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "gemini-2.5-flash").strip()

EIS_PROXY = os.getenv("EIS_PROXY", "").strip()
EIS_USER_AGENT = os.getenv(
    "EIS_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
).strip()
EIS_BASE = "https://zakupki.gov.ru"

_raw_hosts = [
    item.strip()
    for item in os.getenv("TRUSTED_HOSTS", SITE_HOST).split(",")
    if item.strip()
]
TRUSTED_HOSTS: list[str] = []
for _host in _raw_hosts:
    if _host not in TRUSTED_HOSTS:
        TRUSTED_HOSTS.append(_host)
    _ascii = _idna_host(_host)
    if _ascii not in TRUSTED_HOSTS:
        TRUSTED_HOSTS.append(_ascii)
