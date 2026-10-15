"""Turns each provider's status source into one normalized shape.

Normalized overall states, worst last:
    operational < maintenance < degraded < partial_outage < major_outage
and `unknown` when the source could not be read.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

SEVERITY = {
    "operational": 0,
    "maintenance": 1,
    "degraded": 2,
    "partial_outage": 3,
    "major_outage": 4,
}

# Atlassian Statuspage (and compatible pages such as OpenAI's) overall indicator.
INDICATOR = {
    "none": "operational",
    "minor": "degraded",
    "major": "partial_outage",
    "critical": "major_outage",
    "maintenance": "maintenance",
}
# An incident's own impact, folded into the same scale.
IMPACT = {
    "none": "operational",
    "minor": "degraded",
    "major": "partial_outage",
    "critical": "major_outage",
    "maintenance": "maintenance",
}

# Items older than this that are still "unresolved" in an RSS feed are treated as stale
# leftovers, not live incidents.
RSS_STALE_AFTER = timedelta(days=14)


def worst(*states: str) -> str:
    known = [s for s in states if s in SEVERITY]
    return max(known, key=lambda s: SEVERITY[s]) if known else "unknown"


def _iso(value: Any) -> str | None:
    """Normalizes a Statuspage timestamp (`2026-10-14T20:00:36.123Z` or `+00:00`) to second-precision UTC."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_statuspage(summary: dict[str, Any], *, page_url: str | None, incident_url: str | None = None) -> dict[str, Any]:
    """Atlassian Statuspage `summary.json` (and look-alikes) to the normalized status object."""
    if not isinstance(summary, dict) or "status" not in summary:
        raise ValueError("not a Statuspage summary")

    indicator = str((summary.get("status") or {}).get("indicator", "")).lower()
    description = str((summary.get("status") or {}).get("description") or "")

    incidents = []
    for raw in summary.get("incidents") or []:
        if not isinstance(raw, dict):
            continue
        impact = str(raw.get("impact", "unknown")).lower()
        incident_id = str(raw.get("id", ""))
        url = raw.get("shortlink")
        if not url and incident_url and incident_id:
            url = incident_url.format(id=incident_id)
        incidents.append(
            {
                "id": incident_id,
                "title": str(raw.get("name", "Untitled incident")),
                "status": str(raw.get("status", "unknown")),
                "impact": impact if impact in {"none", "minor", "major", "critical", "maintenance"} else "unknown",
                "url": url if isinstance(url, str) else None,
                "started_at": _iso(raw.get("created_at")),
                "updated_at": _iso(raw.get("updated_at")),
            }
        )

    # Scheduled maintenance that is happening now counts as maintenance.
    for raw in summary.get("scheduled_maintenances") or []:
        if isinstance(raw, dict) and str(raw.get("status", "")).lower() == "in_progress":
            incidents.append(
                {
                    "id": str(raw.get("id", "")),
                    "title": str(raw.get("name", "Scheduled maintenance")),
                    "status": "in_progress",
                    "impact": "maintenance",
                    "url": raw.get("shortlink") if isinstance(raw.get("shortlink"), str) else None,
                    "started_at": _iso(raw.get("scheduled_for") or raw.get("created_at")),
                    "updated_at": _iso(raw.get("updated_at")),
                }
            )

    # The page's own indicator is authoritative; an unresolved incident can only raise it. With
    # no readable indicator and no incidents there is nothing to go on: unknown.
    from_incidents = [IMPACT.get(i["impact"], "operational") for i in incidents]
    base = INDICATOR.get(indicator)
    overall = worst(*([base] if base else []), *from_incidents)

    components = []
    for raw in summary.get("components") or []:
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "")).lower()
        if status and status != "operational":
            components.append({"name": str(raw.get("name", "?")), "status": status})

    return {
        "overall": overall,
        "summary": description or overall.replace("_", " "),
        "incidents": incidents,
        "affected_components": components,
        "page_url": page_url,
    }


# Each field sits in its own element (`<h3>Status: RESOLVED</h3><p>Severity: outage</p>`), so a
# value runs up to the next tag or the end of the line.
_STATUS_RE = re.compile(r"Status:\s*([^<\n]+?)\s*(?:<|\n|$)", re.IGNORECASE)
_SEVERITY_RE = re.compile(r"Severity:\s*([^<\n]+?)\s*(?:<|\n|$)", re.IGNORECASE)


def _rss_overall(severity: str, categories: list[str]) -> str:
    severity = severity.strip().lower()
    cats = {c.lower() for c in categories}
    if "maintenance" in cats or severity == "maintenance":
        return "maintenance"
    if severity in {"outage", "major", "critical"}:
        return "major_outage"
    if severity in {"partial", "partial_outage"}:
        return "partial_outage"
    # "disruption", "degraded", "minor" and anything unrecognized: a visible, non-fatal problem.
    return "degraded"


def normalize_rss(xml_text: str, *, page_url: str | None, now: datetime) -> dict[str, Any]:
    """An incident-history RSS feed (xAI's status page) to the normalized status object.

    Every item carries `Status: <STATE>` and `Severity: <level>` in its description. Items whose
    state is anything but RESOLVED, and that are not stale, are the live incidents. With none
    live the provider is operational.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        raise ValueError(f"not a valid RSS document: {err}") from err
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS document has no channel")

    incidents = []
    states = []
    for item in channel.findall("item"):
        text = item.findtext("description") or ""
        status_match = _STATUS_RE.search(text)
        if not status_match:
            continue
        state = status_match.group(1).strip().lower()
        if state == "resolved":
            continue
        published = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                parsed = parsedate_to_datetime(pub)
                published = parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                published = None
        if published is not None and now - published > RSS_STALE_AFTER:
            continue
        severity_match = _SEVERITY_RE.search(text)
        severity = severity_match.group(1).strip() if severity_match else ""
        categories = [c.text or "" for c in item.findall("category")]
        overall = _rss_overall(severity, categories)
        states.append(overall)
        incident_id = (item.findtext("guid") or item.findtext("link") or "").strip()
        incidents.append(
            {
                "id": incident_id,
                "title": (item.findtext("title") or "Untitled incident").strip(),
                "status": state.replace(" ", "_"),
                "impact": {"major_outage": "critical", "partial_outage": "major", "maintenance": "maintenance"}.get(overall, "minor"),
                "url": (item.findtext("link") or "").strip() or None,
                "started_at": published.strftime("%Y-%m-%dT%H:%M:%SZ") if published else None,
                "updated_at": published.strftime("%Y-%m-%dT%H:%M:%SZ") if published else None,
            }
        )

    overall = worst("operational", *states)
    return {
        "overall": overall,
        "summary": "All systems operational" if not incidents else f"{len(incidents)} active incident(s)",
        "incidents": incidents,
        "affected_components": [],
        "page_url": page_url,
    }
