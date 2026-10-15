import unittest

from collector.models import MissingKey, fetch_models, parse_anthropic_page, parse_openai_list
from tests.helpers import ANTHROPIC_MODELS, OPENAI_MODELS, PROVIDERS, XAI_MODELS


class ParseTests(unittest.TestCase):
    def test_anthropic_items_carry_display_names(self):
        items = parse_anthropic_page(ANTHROPIC_MODELS)
        self.assertEqual(items[0], {"id": "claude-opus-5", "display_name": "Claude Opus 5", "created_at": "2026-09-01T00:00:00Z"})

    def test_openai_compatible_lists_use_the_id_as_the_name_and_convert_epoch_times(self):
        items = parse_openai_list(XAI_MODELS)
        self.assertEqual(items, [{"id": "grok-4.7", "display_name": "grok-4.7", "created_at": "2026-09-21T14:13:20Z"}])

    def test_fine_tunes_are_excluded_by_prefix(self):
        ids = [m["id"] for m in parse_openai_list(OPENAI_MODELS, ["ft:"])]
        self.assertEqual(ids, ["gpt-5", "text-embedding-4"])

    def test_junk_entries_are_skipped(self):
        items = parse_openai_list({"data": [None, {"nope": 1}, {"id": 5}, {"id": "ok"}]})
        self.assertEqual([m["id"] for m in items], ["ok"])


class FetchTests(unittest.TestCase):
    def test_missing_key_is_reported_without_a_request(self):
        calls = []
        with self.assertRaises(MissingKey):
            fetch_models(PROVIDERS[0]["models"], {}, lambda *a: calls.append(a))
        self.assertEqual(calls, [])

    def test_anthropic_sends_its_headers_and_follows_pagination(self):
        pages = [
            {"data": [{"id": "claude-b", "display_name": "B"}], "has_more": True, "last_id": "claude-b"},
            {"data": [{"id": "claude-a", "display_name": "A"}], "has_more": False, "last_id": "claude-a"},
        ]
        seen = []

        def getter(url, headers):
            seen.append((url, headers))
            return pages[len(seen) - 1]

        items = fetch_models(PROVIDERS[0]["models"], {"ANTHROPIC_API_KEY": "sk-ant"}, getter)
        self.assertEqual([m["id"] for m in items], ["claude-a", "claude-b"])  # sorted by id
        self.assertEqual(len(seen), 2)
        self.assertIn("after_id=claude-b", seen[1][0])
        self.assertEqual(seen[0][1]["x-api-key"], "sk-ant")
        self.assertEqual(seen[0][1]["anthropic-version"], "2023-06-01")

    def test_openai_compatible_uses_a_bearer_token(self):
        seen = []

        def getter(url, headers):
            seen.append(headers)
            return OPENAI_MODELS

        items = fetch_models(PROVIDERS[1]["models"], {"OPENAI_API_KEY": " sk-o "}, getter)
        self.assertEqual(seen[0], {"Authorization": "Bearer sk-o"})
        self.assertEqual([m["id"] for m in items], ["gpt-5", "text-embedding-4"])

    def test_duplicate_ids_collapse(self):
        body = {"data": [{"id": "a"}, {"id": "a"}, {"id": "b"}]}
        items = fetch_models(PROVIDERS[2]["models"], {"XAI_API_KEY": "k"}, lambda *a: body)
        self.assertEqual([m["id"] for m in items], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
