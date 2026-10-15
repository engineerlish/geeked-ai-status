"""Shared fixtures: provider configs and canned provider responses."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from collector.http import FetchError

NOW = datetime(2026, 10, 15, 18, 0, 12, tzinfo=timezone.utc)

PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "anthropic",
        "name": "Claude",
        "vendor": "Anthropic",
        "geeked_provider": "claude",
        "models": {"kind": "anthropic", "url": "https://api.anthropic.com/v1/models", "key_env": "ANTHROPIC_API_KEY"},
        "status": {"kind": "statuspage", "url": "https://status.claude.com/api/v2/summary.json", "page_url": "https://status.claude.com"},
    },
    {
        "id": "openai",
        "name": "ChatGPT",
        "vendor": "OpenAI",
        "geeked_provider": "openai",
        "models": {
            "kind": "openai",
            "url": "https://api.openai.com/v1/models",
            "key_env": "OPENAI_API_KEY",
            "exclude_prefixes": ["ft:"],
        },
        "status": {
            "kind": "statuspage",
            "url": "https://status.openai.com/api/v2/summary.json",
            "page_url": "https://status.openai.com",
            "incident_url": "https://status.openai.com/incidents/{id}",
        },
    },
    {
        "id": "xai",
        "name": "Grok",
        "vendor": "xAI",
        "geeked_provider": "xai",
        "models": {"kind": "openai", "url": "https://api.x.ai/v1/models", "key_env": "XAI_API_KEY"},
        "status": {"kind": "rss", "url": "https://status.x.ai/feed.xml", "page_url": "https://status.x.ai"},
    },
]

ENV = {"ANTHROPIC_API_KEY": "a", "OPENAI_API_KEY": "o", "XAI_API_KEY": "x"}

STATUSPAGE_OK = {
    "page": {"name": "Claude"},
    "status": {"indicator": "none", "description": "All Systems Operational"},
    "components": [{"name": "claude.ai", "status": "operational"}],
    "incidents": [],
    "scheduled_maintenances": [],
}

ANTHROPIC_MODELS = {
    "data": [
        {"type": "model", "id": "claude-opus-5", "display_name": "Claude Opus 5", "created_at": "2026-09-01T00:00:00Z"},
        {"type": "model", "id": "claude-haiku-5", "display_name": "Claude Haiku 5", "created_at": "2026-06-01T00:00:00Z"},
    ],
    "has_more": False,
    "first_id": "claude-opus-5",
    "last_id": "claude-haiku-5",
}

OPENAI_MODELS = {
    "object": "list",
    "data": [
        {"id": "gpt-5", "object": "model", "created": 1788220800, "owned_by": "system"},
        {"id": "ft:gpt-5:acme::abc", "object": "model", "created": 1788220801, "owned_by": "user"},
        {"id": "text-embedding-4", "object": "model", "created": 1780000000, "owned_by": "system"},
    ],
}

XAI_MODELS = {"object": "list", "data": [{"id": "grok-4.7", "object": "model", "created": 1790000000, "owned_by": "xai"}]}

RSS_QUIET = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0"><channel><title>xAI</title>
<item><title>[grok.com] grok.com</title><link>https://status.x.ai/grok-com/ps_1</link><guid>ps_1</guid>
<description><![CDATA[<h3>Status: RESOLVED</h3><p>Severity: outage</p>]]></description>
<pubDate>Wed, 07 Oct 2026 00:24:33 GMT</pubDate></item>
</channel></rss>"""

RSS_OUTAGE = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0"><channel><title>xAI</title>
<item><title>[grok.com] grok.com</title><link>https://status.x.ai/grok-com/ps_9</link><guid>ps_9</guid>
<description><![CDATA[<h3>Status: INVESTIGATING</h3><p>Severity: outage</p><hr/>]]></description>
<pubDate>Thu, 15 Oct 2026 17:40:00 GMT</pubDate><category>outage</category></item>
<item><title>[Voice] Enterprise Voice Degraded</title><link>https://status.x.ai/voice/ps_2</link><guid>ps_2</guid>
<description><![CDATA[<h3>Status: RESOLVED</h3><p>Severity: disruption</p>]]></description>
<pubDate>Tue, 06 Oct 2026 19:31:00 GMT</pubDate></item>
</channel></rss>"""


class FakeNetwork:
    """Canned responses by URL; any URL set to an exception instance raises it."""

    def __init__(self, json_by_url: dict[str, Any], text_by_url: dict[str, Any] | None = None):
        self.json_by_url = copy.deepcopy(json_by_url)
        self.text_by_url = dict(text_by_url or {})
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> Any:
        self.calls.append((url, headers or {}))
        key = next((u for u in self.json_by_url if url.startswith(u)), None)
        if key is None:
            raise FetchError(f"no canned response for {url}")
        value = self.json_by_url[key]
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)

    def get_text(self, url: str, headers: dict[str, str] | None = None) -> str:
        self.calls.append((url, headers or {}))
        value = self.text_by_url.get(url)
        if value is None:
            raise FetchError(f"no canned response for {url}")
        if isinstance(value, Exception):
            raise value
        return value


def healthy_network() -> FakeNetwork:
    return FakeNetwork(
        {
            "https://api.anthropic.com/v1/models": ANTHROPIC_MODELS,
            "https://status.claude.com/api/v2/summary.json": STATUSPAGE_OK,
            "https://api.openai.com/v1/models": OPENAI_MODELS,
            "https://status.openai.com/api/v2/summary.json": {**STATUSPAGE_OK, "page": {"name": "OpenAI"}},
            "https://api.x.ai/v1/models": XAI_MODELS,
        },
        {"https://status.x.ai/feed.xml": RSS_QUIET},
    )
