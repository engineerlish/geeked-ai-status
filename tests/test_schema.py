import copy
import unittest

from collector.schema import load_schema, validate
from collector.snapshot import build_snapshot
from tests.helpers import ENV, NOW, PROVIDERS, healthy_network


def good_snapshot():
    net = healthy_network()
    return build_snapshot(
        PROVIDERS, now=NOW, env=ENV, previous=None, get_json_fn=net.get_json, get_text_fn=net.get_text
    )


class ValidatorTests(unittest.TestCase):
    def test_a_real_snapshot_is_valid(self):
        self.assertEqual(validate(good_snapshot()), [])

    def test_the_published_schema_is_the_one_in_use(self):
        schema = load_schema()
        self.assertEqual(schema["title"], "geeked-ai-status snapshot")
        self.assertEqual(schema["properties"]["schema_version"], {"const": 1})

    def test_missing_required_property(self):
        broken = good_snapshot()
        del broken["run"]
        self.assertTrue(any("missing required property 'run'" in e for e in validate(broken)))

    def test_wrong_type_and_enum_and_pattern(self):
        broken = good_snapshot()
        broken["providers"]["xai"]["status"]["overall"] = "on_fire"
        broken["checked_at"] = "yesterday"
        broken["run"]["late_seconds"] = "slow"
        errors = validate(broken)
        self.assertTrue(any("providers.xai.status.overall" in e and "not one of" in e for e in errors))
        self.assertTrue(any(e.startswith("checked_at") and "does not match" in e for e in errors))
        self.assertTrue(any("run.late_seconds" in e and "expected integer" in e for e in errors))

    def test_unexpected_properties_are_rejected(self):
        broken = good_snapshot()
        broken["surprise"] = True
        broken["providers"]["xai"]["models"]["items"][0]["extra"] = 1
        errors = validate(broken)
        self.assertTrue(any("surprise: unexpected property" in e for e in errors))
        self.assertTrue(any(e.endswith("extra: unexpected property") for e in errors))

    def test_the_version_is_pinned(self):
        broken = good_snapshot()
        broken["schema_version"] = 2
        self.assertTrue(any("schema_version" in e for e in validate(broken)))

    def test_nulls_where_allowed_and_rejected_elsewhere(self):
        fine = good_snapshot()
        fine["providers"]["xai"]["status"]["page_url"] = None
        self.assertEqual(validate(fine), [])
        broken = copy.deepcopy(fine)
        broken["providers"]["xai"]["name"] = None
        self.assertTrue(any("providers.xai.name" in e for e in validate(broken)))

    def test_one_of_for_status_changes(self):
        snap = good_snapshot()
        snap["changes"]["providers"]["xai"]["status_changed"] = {"from": "operational", "to": "degraded"}
        self.assertEqual(validate(snap), [])
        snap["changes"]["providers"]["xai"]["status_changed"] = {"from": "operational"}
        self.assertTrue(validate(snap))


if __name__ == "__main__":
    unittest.main()
