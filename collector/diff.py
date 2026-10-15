"""What changed between two snapshots."""

from __future__ import annotations

from typing import Any


def _ids(items: list[dict[str, Any]]) -> set[str]:
    return {i["id"] for i in items}


def diff_provider(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Changes for one provider. `previous` is None for a provider seen for the first time,
    which reports no changes (everything would otherwise count as new)."""
    empty: dict[str, Any] = {
        "models_added": [],
        "models_removed": [],
        "status_changed": None,
        "incidents_started": [],
        "incidents_resolved": [],
    }
    if previous is None:
        return empty

    result = dict(empty)

    # Model changes only count when the models were read successfully this time; a failed or
    # unconfigured call carries the previous (stale) list and must not look like a mass removal.
    if current["models"]["state"] == "ok":
        before = _ids(previous["models"]["items"])
        after = _ids(current["models"]["items"])
        if previous["models"]["items"] or previous["models"]["state"] == "ok":
            result["models_added"] = sorted(after - before)
            result["models_removed"] = sorted(before - after)

    before_state = previous["status"]["overall"]
    after_state = current["status"]["overall"]
    if before_state != after_state:
        result["status_changed"] = {"from": before_state, "to": after_state}

    if previous["status"]["state"] == "ok" and current["status"]["state"] == "ok":
        before_incidents = {i["id"]: i for i in previous["status"]["incidents"]}
        after_incidents = {i["id"]: i for i in current["status"]["incidents"]}
        result["incidents_started"] = [
            {"id": i["id"], "title": i["title"]} for k, i in after_incidents.items() if k not in before_incidents
        ]
        result["incidents_resolved"] = [
            {"id": i["id"], "title": i["title"]} for k, i in before_incidents.items() if k not in after_incidents
        ]
    return result


def _provider_changed(change: dict[str, Any]) -> bool:
    return bool(
        change["models_added"]
        or change["models_removed"]
        or change["status_changed"]
        or change["incidents_started"]
        or change["incidents_resolved"]
    )


def _describe(name: str, change: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    added = change["models_added"]
    if added:
        shown = ", ".join(added[:2]) + (f" +{len(added) - 2} more" if len(added) > 2 else "")
        parts.append(f"new model{'s' if len(added) > 1 else ''} {shown}")
    removed = change["models_removed"]
    if removed:
        parts.append(f"{len(removed)} model{'s' if len(removed) > 1 else ''} removed")
    status = change["status_changed"]
    if status:
        parts.append(f"{name} {status['to'].replace('_', ' ')} (was {status['from'].replace('_', ' ')})")
    if change["incidents_started"]:
        parts.append(f"{name}: {len(change['incidents_started'])} new incident(s)")
    if change["incidents_resolved"]:
        parts.append(f"{name}: {len(change['incidents_resolved'])} incident(s) resolved")
    return parts


def diff_snapshots(previous: dict[str, Any] | None, providers: dict[str, Any]) -> dict[str, Any]:
    """The `changes` block for a new snapshot's `providers` against the previous snapshot."""
    previous_providers = (previous or {}).get("providers", {})
    per_provider = {pid: diff_provider(previous_providers.get(pid), cur) for pid, cur in providers.items()}

    summary_parts: list[str] = []
    for pid, change in per_provider.items():
        summary_parts.extend(_describe(providers[pid]["name"], change))

    if previous is None:
        summary = "first snapshot"
        any_change = False
    else:
        any_change = any(_provider_changed(c) for c in per_provider.values())
        summary = "; ".join(summary_parts) if summary_parts else "no changes"

    return {
        "any": any_change,
        "summary": summary,
        "previous_checked_at": (previous or {}).get("checked_at"),
        "providers": per_provider,
    }
