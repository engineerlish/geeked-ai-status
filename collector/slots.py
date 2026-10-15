"""The 30-minute schedule slots, and how late or missed runs are detected.

GitHub may delay a scheduled run (it is documented to under load, most at the top of the hour)
or drop it. The collector records the real time it ran and the slot it was meant for, and
notes any slots that no run covered, instead of pretending they ran on time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

SLOT = timedelta(minutes=30)
LATE_AFTER = timedelta(minutes=10)
MAX_LISTED_MISSED = 48


def utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def slot_floor(dt: datetime) -> datetime:
    """The latest slot boundary at or before `dt` (UTC, :00 or :30)."""
    dt = utc(dt).replace(second=0, microsecond=0)
    return dt.replace(minute=0 if dt.minute < 30 else 30)


def fmt(dt: datetime) -> str:
    return utc(dt).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def missed_between(previous_slot: datetime | None, this_slot: datetime) -> list[datetime]:
    """Slots strictly between the previous snapshot's slot and this one."""
    if previous_slot is None or this_slot <= previous_slot:
        return []
    missed = []
    cursor = previous_slot + SLOT
    while cursor < this_slot:
        missed.append(cursor)
        cursor += SLOT
    return missed


def run_timing(now: datetime, previous_scheduled_for: str | None) -> dict:
    """The `scheduled_for` and the timing parts of the `run` block for a run starting at `now`."""
    slot = slot_floor(now)
    late_by = max(0, int((utc(now) - slot).total_seconds()))
    previous = parse(previous_scheduled_for) if previous_scheduled_for else None
    missed = missed_between(previous, slot)
    return {
        "scheduled_for": fmt(slot),
        "late": late_by > LATE_AFTER.total_seconds(),
        "late_seconds": late_by,
        "missed_slots": [fmt(m) for m in missed[-MAX_LISTED_MISSED:]],
        "missed_count": len(missed),
    }
