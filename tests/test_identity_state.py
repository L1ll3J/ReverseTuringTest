"""Stable identity and independently selectable protection ownership."""
import unittest
from dataclasses import replace
from game_rules import BodyState, ItemUseContext, PlayerItemRules
from identity_state import IdentityState
from items import Inventory


class IdentityTests(unittest.TestCase):
    def test_repeated_exchanges_preserve_every_mind_and_body(self):
        initial = IdentityState.from_bodies("player", "original", ("dave", "hank"))
        changed = initial.exchange("dave").exchange("hank")
        self.assertEqual(changed.body_for_mind("player"), "hank")
        self.assertEqual(changed.body_for_mind("dave"), "original")
        self.assertEqual(changed.body_for_mind("hank"), "dave")
        self.assertEqual(initial.player_body_id, "original")
        self.assertEqual(changed.exchange("original").body_for_mind("dave"), "hank")
        self.assertIsNone(changed.body_for_mind("unknown"))
        for invalid in ("hank", "missing"):
            with self.assertRaises(ValueError):
                changed.exchange(invalid)

    def test_occupancy_rejects_duplicates_and_missing_player(self):
        for pairs in ((("a", "player"), ("a", "npc")),
                      (("a", "player"), ("b", "player")), (("a", "npc"),)):
            with self.assertRaises(ValueError):
                IdentityState("player", pairs)

    def test_body_guardian_stays_on_old_body_independent_of_current_controller(self):
        inventory = Inventory("player")
        rules = PlayerItemRules("player", inventory, guardian_scope="body")
        old = BodyState("old", "Alex", True, False)
        new = BodyState("new", "Dave", True, True)
        entry = inventory.grant("guardian_angel")
        rules.use_item(entry.instance_id, ItemUseContext(1, "conversation", False,
                       "player", "old", (old, new)))
        self.assertTrue(rules.attempt_death("new-death", new, 1, "player").killed)
        self.assertTrue(rules.protection_armed)
        self.assertTrue(rules.attempt_death("old-death", replace(old, is_npc=True), 1, None).prevented)
        self.assertFalse(rules.protection_armed)
