"""Stable identity and independently selectable protection ownership."""
import unittest
from dataclasses import FrozenInstanceError, replace
from game_rules import BodyState, ItemUseContext, PlayerItemRules
from identity_state import IdentityState
from items import Inventory
from presentation_aliases import PresentationAliases


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

    def test_revisions_distinguish_cycles_and_reject_invalid_values(self):
        initial = IdentityState.from_bodies("player", "original", ("dave",),
                                           appearance_names={"original": "Alex", "dave": "Dave"})
        cycled = initial.exchange("dave").exchange("original")
        self.assertEqual(initial.occupants, cycled.occupants)
        self.assertEqual((initial.revision, cycled.revision), (0, 2))
        self.assertNotEqual(initial, cycled)
        copied = initial.clone_appearance("original", "dave")
        repeated = copied.clone_appearance("original", "dave")
        self.assertEqual(copied.appearances, repeated.appearances)
        self.assertEqual((copied.revision, repeated.revision), (1, 2))
        for revision in (-1, True, 1.5, "1"):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                replace(initial, revision=revision)

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

    def test_original_names_follow_minds_through_copy_exchange_and_cycles(self):
        names = {"original": "Alex", "dave": "Dave", "hank": "Hank"}
        initial = IdentityState.from_bodies("player", "original", ("dave", "hank"), appearance_names=names)
        names["original"] = "Changed outside the registry"
        self.assertEqual(dict(initial.mind_names), {"player": "Alex", "dave": "Dave", "hank": "Hank"})
        changed = initial.clone_appearance("original", "dave").exchange("hank").exchange("original")
        self.assertEqual(changed.mind_names, initial.mind_names)
        self.assertEqual(changed.mind_name("player"), "Alex")
        self.assertEqual(changed.appearance_name("original"), "Dave")
        self.assertEqual(changed.mind_name("dave"), "Dave")
        self.assertEqual(changed.mind_name("hank"), "Hank")
        self.assertIsNone(changed.mind_name("unknown"))
        with self.assertRaises(FrozenInstanceError):
            changed.mind_names = ()

    def test_unprovided_original_names_never_fall_back_to_opaque_identifiers(self):
        initial = IdentityState.from_bodies("player", "opaque-body", ("opaque-npc",))
        self.assertIsNone(initial.mind_name("player"))
        self.assertIsNone(initial.mind_name("opaque-npc"))
        self.assertEqual(initial.mind_names, ())
        for invalid in ((("missing-mind", "Name"),), (("player", "Alex"), ("player", "Another")),
                        (("player", " "),)):
            with self.subTest(names=invalid), self.assertRaises(ValueError):
                replace(initial, mind_names=invalid)

    def test_private_original_names_are_not_used_as_current_appearance_labels(self):
        initial = IdentityState.from_bodies("player", "original", ("dave",),
                                           appearance_names={"original": "Alex", "dave": "Dave"})
        copied = initial.clone_appearance("original", "dave")
        snapshot = PresentationAliases().for_presence("discussion", copied, ("original", "dave"))
        self.assertEqual(copied.mind_name("player"), "Alex")
        self.assertTrue(all(label.startswith("Dave #") for _, label in snapshot.entries))
        self.assertTrue(all("Alex" not in label for _, label in snapshot.entries))
