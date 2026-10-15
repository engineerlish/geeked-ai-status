"""Reads each provider's official models-list endpoint into `{id, display_name, created_at}` items.

Listing models does not consume tokens. Each call needs the provider's API key, which the
workflow passes in through an Actions secret and this module reads only from the environment.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from .http import get_json

JsonGetter = Callable[..., Any]

ANTHROPIC_VERSION = "2023-06-01"
MAX_PAGES = 20


class MissingKey(Exception):
    """The API key secret for this provider is not configured."""


def _epoch_to_iso(value: Any) -> str | None:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _iso(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_anthropic_page(page: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for raw in page.get("data") or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        items.append(
            {
                "id": raw["id"],
                "display_name": str(raw.get("display_name") or raw["id"]),
                "created_at": _iso(raw.get("created_at")),
            }
        )
    return items


def parse_openai_list(body: dict[str, Any], exclude_prefixes: list[str] | None = None) -> list[dict[str, Any]]:
    """OpenAI's and xAI's (OpenAI-compatible) `GET /v1/models` response."""
    excluded = tuple(exclude_prefixes or [])
    items = []
    for raw in body.get("data") or []:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        if excluded and raw["id"].startswith(excluded):
            continue
        items.append(
            {
                "id": raw["id"],
                # These APIs publish no display name; the id is the name.
                "display_name": raw["id"],
                "created_at": _epoch_to_iso(raw.get("created")),
            }
        )
    return items


def fetch_models(config: dict[str, Any], env: dict[str, str], get_json_fn: JsonGetter = get_json) -> list[dict[str, Any]]:
    """Returns the provider's models sorted by id. Raises MissingKey, or FetchError on failure."""
    key = env.get(config["key_env"], "").strip()
    if not key:
        raise MissingKey(f"secret {config['key_env']} is not set")

    kind = config["kind"]
    if kind == "anthropic":
        headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
        items: list[dict[str, Any]] = []
        after: str | None = None
        for _ in range(MAX_PAGES):
            url = f"{config['url']}?limit=1000" + (f"&after_id={after}" if after else "")
            page = get_json_fn(url, headers)
            items.extend(parse_anthropic_page(page))
            if not page.get("has_more") or not page.get("last_id"):
                break
            after = str(page["last_id"])
        return sorted(_unique(items), key=lambda m: m["id"])

    if kind == "openai":
        body = get_json_fn(config["url"], {"Authorization": f"Bearer {key}"})
        return sorted(_unique(parse_openai_list(body, config.get("exclude_prefixes"))), key=lambda m: m["id"])

    raise ValueError(f"unknown models kind: {kind}")


def _unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for item in items:
        seen.setdefault(item["id"], item)
    return list(seen.values())
