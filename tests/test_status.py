import unittest
from datetime import datetime, timezone

from collector.status import normalize_rss, normalize_statuspage, worst
from tests.helpers import RSS_OUTAGE, RSS_QUIET, STATUSPAGE_OK

NOW = datetime(2026, 10, 15, 18, 0, tzinfo=timezone.utc)


def page(indicator: str, description: str = "", incidents=None, maintenances=None, components=None):
    return {
        "status": {"indicator": indicator, "description": description},
        "components": components or [],
        "incidents": incidents or [],
        "scheduled_maintenances": maintenances or [],
    }


def incident(**over):
    return {
        "id": "inc1",
        "name": "Elevated errors",
        "status": "investigating",
        "impact": "minor",
        "shortlink": "https://stspg.io/abc",
        "created_at": "2026-10-15T17:00:00.123Z",
        "updated_at": "2026-10-15T17:30:00.000Z",
        **over,
    }


class StatuspageTests(unittest.TestCase):
    def test_indicator_maps_to_the_normalized_scale(self):
        cases = {
            "none": "operational",
            "minor": "degraded",
            "major": "partial_outage",
            "critical": "major_outage",
            "maintenance": "maintenance",
        }
        for indicator, expected in cases.items():
            with self.subTest(indicator=indicator):
                got = normalize_statuspage(page(indicator), page_url="https://s")
                self.assertEqual(got["overall"], expected)

    def test_all_operational_has_no_incidents_or_affected_components(self):
        got = normalize_statuspage(STATUSPAGE_OK, page_url="https://status.claude.com")
        self.assertEqual(got["overall"], "operational")
        self.assertEqual(got["summary"], "All Systems Operational")
        self.assertEqual(got["incidents"], [])
        self.assertEqual(got["affected_components"], [])

    def test_unknown_indicator_is_unknown(self):
        self.assertEqual(normalize_statuspage(page("???"), page_url=None)["overall"], "unknown")
        self.assertEqual(normalize_statuspage(page(""), page_url=None)["overall"], "unknown")

    def test_incidents_are_listed_with_links_and_normalized_times(self):
        got = normalize_statuspage(page("minor", incidents=[incident()]), page_url="https://s")
        self.assertEqual(
            got["incidents"],
            [
                {
                    "id": "inc1",
                    "title": "Elevated errors",
                    "status": "investigating",
                    "impact": "minor",
                    "url": "https://stspg.io/abc",
                    "started_at": "2026-10-15T17:00:00Z",
                    "updated_at": "2026-10-15T17:30:00Z",
                }
            ],
        )

    def test_an_incident_can_raise_the_overall_state_but_never_lower_it(self):
        raised = normalize_statuspage(page("none", incidents=[incident(impact="critical")]), page_url=None)
        self.assertEqual(raised["overall"], "major_outage")
        kept = normalize_statuspage(page("critical", incidents=[incident(impact="minor")]), page_url=None)
        self.assertEqual(kept["overall"], "major_outage")

    def test_builds_a_link_when_the_page_gives_none(self):
        raw = incident(id="01ABC")
        del raw["shortlink"]
        got = normalize_statuspage(
            page("minor", incidents=[raw]), page_url="https://status.openai.com", incident_url="https://status.openai.com/incidents/{id}"
        )
        self.assertEqual(got["incidents"][0]["url"], "https://status.openai.com/incidents/01ABC")

    def test_maintenance_in_progress_is_maintenance(self):
        maintenance = {"id": "m1", "name": "Database upgrade", "status": "in_progress", "scheduled_for": "2026-10-15T17:00:00Z"}
        got = normalize_statuspage(page("none", maintenances=[maintenance]), page_url=None)
        self.assertEqual(got["overall"], "maintenance")
        self.assertEqual(got["incidents"][0]["impact"], "maintenance")
        scheduled = {**maintenance, "status": "scheduled"}
        self.assertEqual(normalize_statuspage(page("none", maintenances=[scheduled]), page_url=None)["overall"], "operational")

    def test_lists_only_components_that_are_not_operational(self):
        components = [
            {"name": "API", "status": "operational"},
            {"name": "Console", "status": "degraded_performance"},
        ]
        got = normalize_statuspage(page("minor", components=components), page_url=None)
        self.assertEqual(got["affected_components"], [{"name": "Console", "status": "degraded_performance"}])

    def test_rejects_something_that_is_not_a_statuspage_summary(self):
        with self.assertRaises(ValueError):
            normalize_statuspage({"hello": "world"}, page_url=None)


class RssTests(unittest.TestCase):
    def test_a_feed_of_only_resolved_incidents_is_operational(self):
        got = normalize_rss(RSS_QUIET, page_url="https://status.x.ai", now=NOW)
        self.assertEqual(got["overall"], "operational")
        self.assertEqual(got["incidents"], [])

    def test_an_unresolved_outage_is_a_major_outage_with_a_link(self):
        got = normalize_rss(RSS_OUTAGE, page_url="https://status.x.ai", now=NOW)
        self.assertEqual(got["overall"], "major_outage")
        self.assertEqual(len(got["incidents"]), 1)
        item = got["incidents"][0]
        self.assertEqual(item["id"], "ps_9")
        self.assertEqual(item["url"], "https://status.x.ai/grok-com/ps_9")
        self.assertEqual(item["status"], "investigating")
        self.assertEqual(item["started_at"], "2026-10-15T17:40:00Z")

    def test_a_disruption_is_degraded(self):
        xml = RSS_OUTAGE.replace("Severity: outage", "Severity: disruption").replace("<category>outage</category>", "")
        self.assertEqual(normalize_rss(xml, page_url=None, now=NOW)["overall"], "degraded")

    def test_maintenance_category_is_maintenance(self):
        xml = RSS_OUTAGE.replace("<category>outage</category>", "<category>maintenance</category>")
        self.assertEqual(normalize_rss(xml, page_url=None, now=NOW)["overall"], "maintenance")

    def test_a_long_unresolved_item_is_stale_not_live(self):
        later = datetime(2026, 11, 20, tzinfo=timezone.utc)
        self.assertEqual(normalize_rss(RSS_OUTAGE, page_url=None, now=later)["overall"], "operational")

    def test_rejects_things_that_are_not_rss(self):
        with self.assertRaises(ValueError):
            normalize_rss("<html>nope</html>", page_url=None, now=NOW)
        with self.assertRaises(ValueError):
            normalize_rss("not xml at all", page_url=None, now=NOW)


class WorstTests(unittest.TestCase):
    def test_picks_the_most_severe_known_state(self):
        self.assertEqual(worst("operational", "degraded", "maintenance"), "degraded")
        self.assertEqual(worst("partial_outage", "major_outage"), "major_outage")
        self.assertEqual(worst(), "unknown")
        self.assertEqual(worst("bogus"), "unknown")


if __name__ == "__main__":
    unittest.main()
