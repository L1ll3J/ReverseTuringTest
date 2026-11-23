"""Production item metadata, durable unlock routes, and old-profile compatibility."""

import json
from pathlib import Path
import tempfile
import unittest

from content import CONTENT_CATALOGUE, UNLOCK_GOALS, available_content
from items import ITEM_CATALOGUE, eligible_item_pool
from progression import ProfileSnapshot, ProfileStore, ProgressEvent


class ContentMetadataTests(unittest.TestCase):
    def test_metadata_matches_every_implemented_item_without_unfinished_entries(self):
        items = {entry.content_id: entry for entry in CONTENT_CATALOGUE if entry.category == "item"}
        self.assertEqual(tuple(items), tuple(ITEM_CATALOGUE))
        self.assertEqual(len(items), 23)
        self.assertEqual(sum(entry.default for entry in items.values()), 16)
        self.assertTrue(items["love_potion"].default)
        self.assertTrue({"soul_transfusion", "mass_discombobulator"}.isdisjoint(items))
        for item_id, entry in items.items():
            self.assertEqual(entry.default, ITEM_CATALOGUE[item_id].default)
            if not entry.default:
                self.assertTrue(UNLOCK_GOALS[item_id])
                self.assertIsNotNone(entry.unlock_rule)
                self.assertEqual(entry.unlock_rule.required_count, 1)
        self.assertEqual(available_content(ProfileSnapshot(), "item"), eligible_item_pool())

    def test_optional_routes_use_completed_conditions_and_enable_only_after_unlock(self):
        routes = {
            "guardian_angel": "survived_vote",
            "booby_trap": "marked_kill_completed",
            "presidential_decree": "survived_spare_paper_vote",
            "occult_ritual": "reserved_room_kill",
            "spin_the_dagger": "three_item_types_victory",
            "revolver": "three_kills_one_run",
            "lucky_coin": "guardian_intervention_run_survived",
        }
        for item_id, event_type in routes.items():
            with self.subTest(item=item_id), tempfile.TemporaryDirectory() as directory:
                store = ProfileStore(Path(directory) / "profile.json", CONTENT_CATALOGUE)
                self.assertNotIn(item_id, store.item_pool())
                update = store.record(ProgressEvent("run", 1, event_type))
                self.assertIn(item_id, update.newly_unlocked)
                self.assertIn(item_id, store.item_pool())
                store.set_item_enabled(item_id, False)
                self.assertNotIn(item_id, store.item_pool())
                store.set_item_enabled(item_id, True)
                self.assertIn(item_id, store.item_pool())

    def test_fresh_pool_and_legacy_earned_unlocks_survive_changed_requirements(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            store = ProfileStore(path, CONTENT_CATALOGUE)
            self.assertEqual(store.item_pool(), eligible_item_pool())
            self.assertFalse(path.exists())
            document = {
                "version": 1, "counters": {"player_kill": 3, "bare_hands_no_item_victory": 1},
                "unlocked": ["c4", "voodoo", "guardian_angel"], "disabled_items": ["guardian_angel"],
                "last_sequences": {"legacy": 2}, "custom_local_data": {"keep": True},
            }
            path.write_text(json.dumps(document), encoding="utf-8")
            original = path.read_bytes()
            self.assertEqual(store.available_weapons(), ("bare", "c4", "voodoo"))
            self.assertEqual(store.item_pool(), eligible_item_pool())
            self.assertEqual(path.read_bytes(), original)
            update = store.record(ProgressEvent("new_run", 1, "survived_vote"))
            self.assertTrue({"c4", "voodoo", "guardian_angel"} <= update.profile.unlocked)
            self.assertIn("guardian_angel", update.profile.disabled_items)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["custom_local_data"], {"keep": True})

    def test_legacy_lifetime_counts_do_not_unlock_new_run_conditions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProfileStore(Path(directory) / "profile.json", CONTENT_CATALOGUE)
            update = store.record(ProgressEvent("run", 1, "player_kill", 100))
            self.assertEqual(update.newly_unlocked, ("knife",))
            update = store.record(ProgressEvent("run", 2, "bare_hands_no_item_victory"))
            self.assertNotIn("voodoo", update.profile.unlocked)
            update = store.record(ProgressEvent("run", 3, "three_kills_one_run"))
            self.assertEqual(update.newly_unlocked, ("c4", "revolver"))
            update = store.record(ProgressEvent("run", 4, "soul_transfusion_used"))
            self.assertEqual(update.newly_unlocked, ("voodoo",))


if __name__ == "__main__":
    unittest.main()
