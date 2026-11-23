"""Approved production unlock routes and retention of existing progress."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from content import CONTENT_CATALOGUE, UNLOCK_GOALS
from progression import ProfileStore, ProgressEvent


class ProductionProgressionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "profile.json"
        self.store = ProfileStore(self.path, CONTENT_CATALOGUE)

    def test_lifetime_kills_and_old_victory_do_not_grant_replaced_unlocks(self):
        for number in range(3):
            self.store.record(ProgressEvent(f"run-{number}", 1, "player_kill"))
        result = self.store.record(ProgressEvent("winner", 1, "bare_hands_no_item_victory"))
        self.assertIn("knife", result.profile.unlocked)
        self.assertNotIn("c4", result.profile.unlocked)
        self.assertNotIn("voodoo", result.profile.unlocked)

    def test_new_achievement_routes_grant_weapons_without_old_restrictions(self):
        result = self.store.record(ProgressEvent("run-a", 1, "three_kills_one_run"))
        self.assertIn("c4", result.profile.unlocked)
        result = self.store.record(ProgressEvent("run-a", 2, "soul_transfusion_used"))
        self.assertIn("voodoo", result.profile.unlocked)
        self.assertEqual(UNLOCK_GOALS["c4"], "Kill three victims in one run.")
        self.assertEqual(UNLOCK_GOALS["voodoo"], "Use Soul Transfusion.")

    def test_existing_owners_keep_unlocks_without_new_achievement(self):
        self.path.write_text(json.dumps({
            "version": 1, "counters": {"player_kill": 3},
            "unlocked": ["c4", "voodoo"], "disabled_items": [], "last_sequences": {},
            "future_setting": "preserve",
        }), encoding="utf-8")
        before = self.path.read_bytes()
        self.assertIn("c4", self.store.available_weapons())
        self.assertIn("voodoo", self.store.available_weapons())
        self.assertEqual(self.path.read_bytes(), before)
        result = self.store.record(ProgressEvent("new-run", 1, "banana_given"))
        self.assertTrue({"c4", "voodoo", "love"}.issubset(result.profile.unlocked))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["future_setting"], "preserve")


if __name__ == "__main__":
    unittest.main()
