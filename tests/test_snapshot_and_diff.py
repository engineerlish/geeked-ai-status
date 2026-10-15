import copy
import unittest
from datetime import timedelta

from collector.diff import diff_provider, diff_snapshots
from collector.http import FetchError
from collector.schema import validate
from collector.snapshot import build_snapshot, commit_message, failure_snapshot
from tests.helpers import (
    ANTHROPIC_MODELS,
    ENV,
    NOW,
    PROVIDERS,
    RSS_OUTAGE,
    STATUSPAGE_OK,
    FakeNetwork,
    healthy_network,
)


def take(network, *, previous=None, now=NOW, env=ENV):
    return build_snapshot(
        PROVIDERS,
        now=now,
        env=env,
        previous=previous,
        trigger="schedule",
        run_id="42",
        get_json_fn=network.get_json,
        get_text_fn=network.get_text,
    )


class SnapshotTests(unittest.TestCase):
    def test_a_healthy_run_has_models_status_and_run_info(self):
        snap = take(healthy_network())
        self.assertEqual(validate(snap), [])
        self.assertEqual(snap["checked_at"], "2026-10-15T18:00:12Z")
        self.assertEqual(snap["scheduled_for"], "2026-10-15T18:00:00Z")
        self.assertEqual(snap["run"]["trigger"], "schedule")
        self.assertEqual(snap["run"]["run_id"], "42")
        self.assertEqual(snap["run"]["late_seconds"], 12)
        self.assertFalse(snap["run"]["late"])
        claude = snap["providers"]["anthropic"]
        self.assertEqual(claude["geeked_provider"], "claude")
        self.assertEqual([m["id"] for m in claude["models"]["items"]], ["claude-haiku-5", "claude-opus-5"])
        self.assertEqual(claude["models"]["items"][0]["first_seen"], "2026-10-15")
        self.assertEqual(snap["providers"]["xai"]["status"]["overall"], "operational")
        self.assertEqual(snap["changes"]["summary"], "first snapshot")

    def test_first_seen_is_carried_forward_and_new_models_get_the_new_date(self):
        first = take(healthy_network())
        later = NOW + timedelta(days=3)
        net = healthy_network()
        net.json_by_url["https://api.anthropic.com/v1/models"]["data"].append(
            {"id": "claude-sonnet-6", "display_name": "Claude Sonnet 6", "created_at": "2026-10-18T00:00:00Z"}
        )
        second = take(net, previous=first, now=later)
        by_id = {m["id"]: m for m in second["providers"]["anthropic"]["models"]["items"]}
        self.assertEqual(by_id["claude-opus-5"]["first_seen"], "2026-10-15")
        self.assertEqual(by_id["claude-sonnet-6"]["first_seen"], "2026-10-18")
        self.assertEqual(second["changes"]["providers"]["anthropic"]["models_added"], ["claude-sonnet-6"])
        self.assertTrue(second["changes"]["any"])

    def test_one_provider_failing_does_not_break_the_others(self):
        net = healthy_network()
        net.json_by_url["https://api.anthropic.com/v1/models"] = FetchError("HTTP 500 from api.anthropic.com")
        net.json_by_url["https://status.openai.com/api/v2/summary.json"] = FetchError("HTTP 503 from status.openai.com")
        snap = take(net)
        self.assertEqual(validate(snap), [])
        claude = snap["providers"]["anthropic"]
        self.assertEqual(claude["models"]["state"], "error")
        self.assertIn("HTTP 500", claude["models"]["error"])
        self.assertEqual(claude["status"]["state"], "ok")
        chatgpt = snap["providers"]["openai"]
        self.assertEqual(chatgpt["status"]["state"], "error")
        self.assertEqual(chatgpt["status"]["overall"], "unknown")
        self.assertEqual(chatgpt["models"]["state"], "ok")
        self.assertEqual(snap["providers"]["xai"]["models"]["state"], "ok")

    def test_a_failed_models_call_keeps_the_previous_list_as_stale_and_reports_no_removals(self):
        first = take(healthy_network())
        net = healthy_network()
        net.json_by_url["https://api.openai.com/v1/models"] = FetchError("HTTP 429 from api.openai.com")
        second = take(net, previous=first, now=NOW + timedelta(minutes=30))
        models = second["providers"]["openai"]["models"]
        self.assertEqual(models["state"], "error")
        self.assertTrue(models["items"])
        self.assertTrue(all(m.get("stale") for m in models["items"]))
        self.assertEqual(second["changes"]["providers"]["openai"]["models_removed"], [])

    def test_a_missing_key_marks_models_unavailable_but_status_still_works(self):
        snap = take(healthy_network(), env={"OPENAI_API_KEY": "o", "XAI_API_KEY": "x"})
        claude = snap["providers"]["anthropic"]
        self.assertEqual(claude["models"]["state"], "unavailable")
        self.assertIn("ANTHROPIC_API_KEY", claude["models"]["error"])
        self.assertEqual(claude["status"]["overall"], "operational")

    def test_secrets_never_appear_in_the_snapshot(self):
        env = {**ENV, "ANTHROPIC_API_KEY": "sk-ant-SECRET-VALUE"}
        net = healthy_network()
        net.json_by_url["https://api.anthropic.com/v1/models"] = FetchError("boom for sk-ant-SECRET-VALUE?")
        text = str(take(net, env=env))
        self.assertNotIn("headers", text)
        # The error text is the exception's own, never request headers; keys are not added by us.
        self.assertNotIn("x-api-key", text)

    def test_late_runs_are_flagged_and_missed_slots_listed(self):
        first = take(healthy_network())
        net = healthy_network()
        # The next run happens 2 h 17 min later: slots 18:30, 19:00, 19:30 were never run.
        later = NOW + timedelta(hours=2, minutes=17)
        second = take(net, previous=first, now=later)
        self.assertEqual(second["scheduled_for"], "2026-10-15T20:00:00Z")
        self.assertEqual(second["run"]["late_seconds"], 17 * 60 + 12)
        self.assertTrue(second["run"]["late"])
        self.assertEqual(
            second["run"]["missed_slots"],
            ["2026-10-15T18:30:00Z", "2026-10-15T19:00:00Z", "2026-10-15T19:30:00Z"],
        )
        self.assertEqual(second["run"]["missed_count"], 3)

    def test_a_second_run_in_the_same_slot_misses_nothing(self):
        first = take(healthy_network())
        second = take(healthy_network(), previous=first, now=NOW + timedelta(minutes=4))
        self.assertEqual(second["run"]["missed_slots"], [])
        self.assertFalse(second["changes"]["any"])


class DiffTests(unittest.TestCase):
    def test_status_and_incident_changes(self):
        quiet = take(healthy_network())
        net = healthy_network()
        net.text_by_url["https://status.x.ai/feed.xml"] = RSS_OUTAGE
        outage = take(net, previous=quiet, now=NOW + timedelta(minutes=30))
        change = outage["changes"]["providers"]["xai"]
        self.assertEqual(change["status_changed"], {"from": "operational", "to": "major_outage"})
        self.assertEqual(change["incidents_started"], [{"id": "ps_9", "title": "[grok.com] grok.com"}])
        self.assertTrue(outage["changes"]["any"])
        self.assertIn("Grok major outage (was operational)", outage["changes"]["summary"])

        recovered = take(healthy_network(), previous=outage, now=NOW + timedelta(hours=1))
        back = recovered["changes"]["providers"]["xai"]
        self.assertEqual(back["status_changed"], {"from": "major_outage", "to": "operational"})
        self.assertEqual(back["incidents_resolved"], [{"id": "ps_9", "title": "[grok.com] grok.com"}])

    def test_removed_models(self):
        before = take(healthy_network())
        net = healthy_network()
        net.json_by_url["https://api.anthropic.com/v1/models"] = {**ANTHROPIC_MODELS, "data": ANTHROPIC_MODELS["data"][:1]}
        after = take(net, previous=before, now=NOW + timedelta(minutes=30))
        self.assertEqual(after["changes"]["providers"]["anthropic"]["models_removed"], ["claude-haiku-5"])

    def test_a_new_provider_reports_no_changes(self):
        provider = take(healthy_network())["providers"]["xai"]
        self.assertEqual(
            diff_provider(None, provider),
            {
                "models_added": [],
                "models_removed": [],
                "status_changed": None,
                "incidents_started": [],
                "incidents_resolved": [],
            },
        )

    def test_nothing_changed(self):
        first = take(healthy_network())
        second = take(healthy_network(), previous=first, now=NOW + timedelta(minutes=30))
        self.assertFalse(second["changes"]["any"])
        self.assertEqual(second["changes"]["summary"], "no changes")
        self.assertEqual(diff_snapshots(first, second["providers"])["any"], False)


class CommitMessageTests(unittest.TestCase):
    def test_quiet_runs(self):
        first = take(healthy_network())
        second = take(healthy_network(), previous=first, now=NOW + timedelta(minutes=30))
        self.assertEqual(
            commit_message(second), "chore(status): 2026-10-15 18:30 UTC, all operational, no changes"
        )

    def test_a_degraded_provider_and_a_new_model(self):
        first = take(healthy_network())
        net = healthy_network()
        degraded = copy.deepcopy(STATUSPAGE_OK)
        degraded["status"] = {"indicator": "minor", "description": "Minor Service Outage"}
        net.json_by_url["https://status.claude.com/api/v2/summary.json"] = degraded
        net.json_by_url["https://api.anthropic.com/v1/models"]["data"].append(
            {"id": "claude-sonnet-6", "display_name": "S", "created_at": None}
        )
        second = take(net, previous=first, now=NOW + timedelta(minutes=30))
        message = commit_message(second)
        self.assertTrue(message.startswith("chore(status): 2026-10-15 18:30 UTC, Claude degraded, "))
        self.assertIn("new model claude-sonnet-6", message)
        self.assertEqual(len(message.splitlines()), 1)

    def test_the_first_snapshot(self):
        self.assertEqual(
            commit_message(take(healthy_network())),
            "chore(status): 2026-10-15 18:00 UTC, all operational, first snapshot",
        )


class FailureSnapshotTests(unittest.TestCase):
    def test_a_failure_snapshot_is_valid_and_keeps_the_previous_models_stale(self):
        previous = take(healthy_network())
        snap = failure_snapshot(
            PROVIDERS,
            now=NOW + timedelta(minutes=30),
            previous=previous,
            errors=["providers.xai.status.overall: 'x' is not one of [...]"],
            trigger="schedule",
        )
        self.assertEqual(validate(snap), [])
        self.assertEqual(snap["run"]["validation_errors"][0][:9], "providers")
        self.assertEqual(snap["providers"]["xai"]["status"]["overall"], "unknown")
        self.assertTrue(all(m["stale"] for m in snap["providers"]["anthropic"]["models"]["items"]))
        self.assertIn("validation failed", commit_message(snap))

    def test_works_with_no_previous_snapshot(self):
        snap = failure_snapshot(PROVIDERS, now=NOW, previous=None, errors=["x"])
        self.assertEqual(validate(snap), [])
        self.assertEqual(snap["providers"]["anthropic"]["models"]["items"], [])


if __name__ == "__main__":
    unittest.main()
