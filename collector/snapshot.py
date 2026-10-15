"""Builds one snapshot: every provider's models and status, plus what changed."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .diff import diff_snapshots
from .http import get_json, get_text
from .models import MissingKey, fetch_models
from .slots import fmt, run_timing, utc
from .status import normalize_rss, normalize_statuspage

SCHEMA_VERSION = 1
MAX_ERROR_LENGTH = 300


def load_config(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    providers = data["providers"]
    ids = [p["id"] for p in providers]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate provider ids in the configuration")
    return providers


def _error_text(err: BaseException) -> str:
    text = f"{type(err).__name__}: {err}"
    return text if len(text) <= MAX_ERROR_LENGTH else text[: MAX_ERROR_LENGTH - 1] + "…"


def _models_section(
    config: dict[str, Any],
    env: dict[str, str],
    previous: dict[str, Any] | None,
    today: str,
    get_json_fn: Callable[..., Any],
) -> dict[str, Any]:
    spec = config["models"]
    previous_items = (previous or {}).get("models", {}).get("items", [])
    first_seen = {m["id"]: m["first_seen"] for m in previous_items}

    def carried() -> list[dict[str, Any]]:
        return [{**m, "stale": True} for m in previous_items]

    try:
        listed = fetch_models(spec, env, get_json_fn)
    except MissingKey as err:
        return {"state": "unavailable", "source": spec["url"], "error": str(err), "items": carried()}
    except Exception as err:  # noqa: BLE001 - one provider's failure must never break the run
        return {"state": "error", "source": spec["url"], "error": _error_text(err), "items": carried()}

    items = [
        {
            "id": m["id"],
            "display_name": m["display_name"],
            "created_at": m["created_at"],
            "first_seen": first_seen.get(m["id"], today),
        }
        for m in listed
    ]
    return {"state": "ok", "source": spec["url"], "error": None, "items": items}


def _status_section(
    config: dict[str, Any],
    now: datetime,
    get_json_fn: Callable[..., Any],
    get_text_fn: Callable[..., str],
) -> dict[str, Any]:
    spec = config["status"]
    base = {"source": spec["url"], "page_url": spec.get("page_url")}
    try:
        if spec["kind"] == "statuspage":
            normalized = normalize_statuspage(
                get_json_fn(spec["url"]), page_url=spec.get("page_url"), incident_url=spec.get("incident_url")
            )
        elif spec["kind"] == "rss":
            normalized = normalize_rss(get_text_fn(spec["url"]), page_url=spec.get("page_url"), now=now)
        else:
            raise ValueError(f"unknown status kind: {spec['kind']}")
    except Exception as err:  # noqa: BLE001
        return {
            "state": "error",
            **base,
            "error": _error_text(err),
            "overall": "unknown",
            "summary": "status unavailable",
            "incidents": [],
            "affected_components": [],
        }
    normalized.pop("page_url", None)
    return {"state": "ok", **base, "error": None, **normalized}


def build_snapshot(
    providers_config: list[dict[str, Any]],
    *,
    now: datetime,
    env: dict[str, str],
    previous: dict[str, Any] | None,
    trigger: str = "local",
    run_id: str | None = None,
    get_json_fn: Callable[..., Any] = get_json,
    get_text_fn: Callable[..., str] = get_text,
) -> dict[str, Any]:
    now = utc(now).replace(microsecond=0)
    today = now.strftime("%Y-%m-%d")
    previous_providers = (previous or {}).get("providers", {})

    providers: dict[str, Any] = {}
    for config in providers_config:
        before = previous_providers.get(config["id"])
        providers[config["id"]] = {
            "id": config["id"],
            "name": config["name"],
            "vendor": config["vendor"],
            "geeked_provider": config.get("geeked_provider"),
            "models": _models_section(config, env, before, today, get_json_fn),
            "status": _status_section(config, now, get_json_fn, get_text_fn),
        }

    timing = run_timing(now, (previous or {}).get("scheduled_for"))
    if trigger != "schedule":
        # A person (or a test) started this run, so it was never "late" for its slot.
        timing["late"] = False
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at": fmt(now),
        "scheduled_for": timing.pop("scheduled_for"),
        "run": {"trigger": trigger, "run_id": run_id, **timing, "collector_version": __version__},
        "providers": providers,
        "changes": diff_snapshots(previous, providers),
    }


def failure_snapshot(
    providers_config: list[dict[str, Any]],
    *,
    now: datetime,
    previous: dict[str, Any] | None,
    errors: list[str],
    trigger: str = "local",
    run_id: str | None = None,
) -> dict[str, Any]:
    """A snapshot that records that this run failed, so the backlog has no gap.

    Built from nothing but the configuration and the previous snapshot, so it cannot itself fail
    the same validation: every provider is `unknown` and carries its previous models, stale.
    """
    now = utc(now).replace(microsecond=0)
    previous_providers = (previous or {}).get("providers", {})
    message = "snapshot failed validation; see run.validation_errors"
    providers: dict[str, Any] = {}
    for config in providers_config:
        before = previous_providers.get(config["id"], {})
        items = [
            {
                "id": m["id"],
                "display_name": m["display_name"],
                "created_at": m["created_at"],
                "first_seen": m["first_seen"],
                "stale": True,
            }
            for m in before.get("models", {}).get("items", [])
        ]
        providers[config["id"]] = {
            "id": config["id"],
            "name": config["name"],
            "vendor": config["vendor"],
            "geeked_provider": config.get("geeked_provider"),
            "models": {"state": "error", "source": config["models"]["url"], "error": message, "items": items},
            "status": {
                "state": "error",
                "source": config["status"]["url"],
                "page_url": config["status"].get("page_url"),
                "error": message,
                "overall": "unknown",
                "summary": "status unavailable",
                "incidents": [],
                "affected_components": [],
            },
        }
    timing = run_timing(now, (previous or {}).get("scheduled_for"))
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at": fmt(now),
        "scheduled_for": timing.pop("scheduled_for"),
        "run": {
            "trigger": trigger,
            "run_id": run_id,
            **timing,
            "collector_version": __version__,
            "validation_errors": [e[:300] for e in errors[:20]],
        },
        "providers": providers,
        "changes": {
            "any": False,
            "summary": "collector failed validation",
            "previous_checked_at": (previous or {}).get("checked_at"),
            "providers": {
                pid: {
                    "models_added": [],
                    "models_removed": [],
                    "status_changed": None,
                    "incidents_started": [],
                    "incidents_resolved": [],
                }
                for pid in providers
            },
        },
    }


STATE_LABEL = {
    "operational": "operational",
    "degraded": "degraded",
    "partial_outage": "partial outage",
    "major_outage": "major outage",
    "maintenance": "maintenance",
    "unknown": "unknown",
}


def commit_message(snapshot: dict[str, Any]) -> str:
    """`chore(status): 2026-10-15 18:30 UTC, Claude degraded, new model claude-x`"""
    when = snapshot["checked_at"].replace("T", " ")[:16]
    providers = snapshot["providers"].values()
    problems = [f"{p['name']} {STATE_LABEL[p['status']['overall']]}" for p in providers if p["status"]["overall"] != "operational"]
    state = ", ".join(problems) if problems else "all operational"
    changes = snapshot["changes"]
    detail = "no changes" if not changes["any"] else changes["summary"]
    if changes["summary"] == "first snapshot":
        detail = "first snapshot"
    if snapshot["run"].get("validation_errors"):
        detail = "validation failed, failure recorded"
    return f"chore(status): {when} UTC, {state}, {detail}"
