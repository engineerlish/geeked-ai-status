import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from collector import __main__ as cli
from collector.history import read_index, render_index_md, snapshot_path, update_index
from collector.schema import validate
from collector.slots import missed_between, run_timing, slot_floor
from tests.helpers import ENV, NOW, PROVIDERS, RSS_OUTAGE, healthy_network
from collector.snapshot import build_snapshot


class SlotTests(unittest.TestCase):
    def test_floor_to_half_hours(self):
        t = lambda h, m: datetime(2026, 10, 15, h, m, 59, tzinfo=timezone.utc)  # noqa: E731
        self.assertEqual(slot_floor(t(18, 0)).strftime("%H:%M"), "18:00")
        self.assertEqual(slot_floor(t(18, 29)).strftime("%H:%M"), "18:00")
        self.assertEqual(slot_floor(t(18, 30)).strftime("%H:%M"), "18:30")
        self.assertEqual(slot_floor(t(18, 59)).strftime("%H:%M"), "18:30")

    def test_missed_slots_are_the_gap_between_previous_and_current(self):
        a = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(missed_between(a, a + timedelta(minutes=30)), [])
        self.assertEqual(len(missed_between(a, a + timedelta(hours=2))), 3)
        self.assertEqual(missed_between(None, a), [])
        self.assertEqual(missed_between(a, a - timedelta(hours=1)), [])

    def test_lateness_threshold_is_ten_minutes(self):
        timing = run_timing(datetime(2026, 10, 15, 18, 10, 0, tzinfo=timezone.utc), None)
        self.assertFalse(timing["late"])
        timing = run_timing(datetime(2026, 10, 15, 18, 10, 1, tzinfo=timezone.utc), None)
        self.assertTrue(timing["late"])

    def test_the_missed_list_is_capped_but_counted(self):
        a = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
        timing = run_timing(a + timedelta(days=10), "2026-10-01T00:00:00Z")
        self.assertEqual(len(timing["missed_slots"]), 48)
        self.assertEqual(timing["missed_count"], 10 * 48 - 1)


class PathTests(unittest.TestCase):
    def test_layout_and_never_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = snapshot_path(root, "2026-10-15T18:00:12Z")
            self.assertEqual(first.relative_to(root).as_posix(), "history/2026/10/15/1800.json")
            first.parent.mkdir(parents=True)
            first.write_text("{}")
            second = snapshot_path(root, "2026-10-15T18:00:47Z")
            self.assertEqual(second.name, "180047.json")
            second.write_text("{}")
            third = snapshot_path(root, "2026-10-15T18:00:47Z")
            self.assertEqual(third.name, "180047-2.json")


class CliTests(unittest.TestCase):
    """Runs the real entry point against a temporary checkout with canned network responses."""

    def run_once(self, root: Path, now: datetime, network, env=None):
        message = root / "message.txt"
        with mock.patch("collector.snapshot.get_json", network.get_json), mock.patch(
            "collector.snapshot.get_text", network.get_text
        ), mock.patch("collector.models.get_json", network.get_json):
            code = cli.run(root, root / "providers.json", now, env or dict(ENV), message)
        self.assertEqual(code, 0)
        return message.read_text().strip()

    def make_root(self, tmp: str) -> Path:
        root = Path(tmp)
        (root / "providers.json").write_text(json.dumps({"providers": PROVIDERS}))
        return root

    def patched_run(self, root, now, network, env=None):
        # `run` builds the snapshot with the module-level fetchers; route them to the fake.
        def build(*args, **kwargs):
            kwargs.setdefault("get_json_fn", network.get_json)
            kwargs.setdefault("get_text_fn", network.get_text)
            return build_snapshot(*args, **kwargs)

        message = root / "message.txt"
        with mock.patch.object(cli, "build_snapshot", build):
            code = cli.run(root, root / "providers.json", now, env or dict(ENV), message)
        self.assertEqual(code, 0)
        return message.read_text().strip()

    def test_every_run_writes_a_snapshot_latest_and_the_index_even_when_nothing_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp)
            first_msg = self.patched_run(root, NOW, healthy_network())
            second_msg = self.patched_run(root, NOW + timedelta(minutes=30), healthy_network())

            self.assertTrue(first_msg.endswith("first snapshot"))
            self.assertEqual(second_msg, "chore(status): 2026-10-15 18:30 UTC, all operational, no changes")

            files = sorted(p.relative_to(root).as_posix() for p in (root / "history").rglob("*.json") if p.name != "index.json")
            self.assertEqual(files, ["history/2026/10/15/1800.json", "history/2026/10/15/1830.json"])

            latest = json.loads((root / "latest.json").read_text())
            self.assertEqual(latest["checked_at"], "2026-10-15T18:30:12Z")
            self.assertEqual(validate(latest), [])
            self.assertEqual(
                json.loads((root / "history/2026/10/15/1830.json").read_text()), latest
            )

            index = read_index(root)
            self.assertEqual(index["snapshots_total"], 2)
            self.assertEqual(index["days"]["2026-10-15"]["snapshots"], 2)
            self.assertEqual(index["days"]["2026-10-15"]["changed"], 0)
            markdown = (root / "history/INDEX.md").read_text()
            self.assertIn("| 2026-10-15 | 2 | 0 | 0 | 0 | 0 |", markdown)

    def test_changes_late_runs_and_missed_slots_reach_the_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp)
            scheduled = {**ENV, "GITHUB_EVENT_NAME": "schedule"}
            self.patched_run(root, NOW, healthy_network(), scheduled)
            outage = healthy_network()
            outage.text_by_url["https://status.x.ai/feed.xml"] = RSS_OUTAGE
            message = self.patched_run(root, NOW + timedelta(hours=1, minutes=25), outage, scheduled)
            self.assertIn("Grok major outage", message)

            index = read_index(root)
            day = index["days"]["2026-10-15"]
            self.assertEqual((day["snapshots"], day["changed"], day["late"], day["missed"]), (2, 1, 1, 1))
            self.assertEqual(len(index["changes"]), 1)
            self.assertEqual(index["changes"][0]["path"], "history/2026/10/15/192512.json".replace("192512", "1925"))
            self.assertEqual([m["slot"] for m in index["missed"]], ["2026-10-15T18:30:00Z"])
            markdown = (root / "history/INDEX.md").read_text()
            self.assertIn("Grok major outage", markdown)
            self.assertIn("2026-10-15T18:30:00Z", markdown)

    def test_a_snapshot_that_fails_validation_is_still_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp)
            self.patched_run(root, NOW, healthy_network())

            def broken(*args, **kwargs):
                snap = build_snapshot(*args, **kwargs)
                snap["providers"]["xai"]["status"]["overall"] = "on_fire"
                return snap

            message = root / "message.txt"
            with mock.patch.object(cli, "build_snapshot", broken):
                code = cli.run(root, root / "providers.json", NOW + timedelta(minutes=30), dict(ENV), message)
            self.assertEqual(code, 0)
            latest = json.loads((root / "latest.json").read_text())
            self.assertEqual(validate(latest), [])
            self.assertTrue(latest["run"]["validation_errors"])
            self.assertIn("validation failed", message.read_text())
            self.assertEqual(read_index(root)["days"]["2026-10-15"]["failed"], 1)

    def test_a_corrupt_latest_does_not_block_the_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.make_root(tmp)
            (root / "latest.json").write_text("{not json")
            message = self.patched_run(root, NOW, healthy_network())
            self.assertTrue(message.endswith("first snapshot"))

    def test_index_is_pure_to_update(self):
        snap = build_snapshot(
            PROVIDERS, now=NOW, env=ENV, previous=None,
            get_json_fn=healthy_network().get_json, get_text_fn=healthy_network().get_text,
        )
        index = {"index_version": 1, "updated_at": None, "snapshots_total": 0, "days": {}, "changes": [], "missed": []}
        update_index(index, snap, "history/2026/10/15/1800.json")
        self.assertEqual(index["snapshots_total"], 1)
        self.assertIn("History index", render_index_md(index))


if __name__ == "__main__":
    unittest.main()
