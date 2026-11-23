"""Offline Soul activation and revision-safe identity application contracts."""
from dataclasses import replace
import unittest

from game_rules import BodyState, ItemRuleError, ItemUseContext, PlayerItemRules, SOUL_PRIVATE
from identity_state import IdentityState
from items import ITEM_CATALOGUE, Inventory, eligible_item_pool
from soul_rules import SoulResolver


class SoulTests(unittest.TestCase):
    def setUp(self):
        self.bodies = (BodyState("p", "Alex", True, False, mind_id="player"),
                       BodyState("d", "Dave", True, True),
                       BodyState("h", "Hank", True, True))
        self.identity = IdentityState.from_bodies("player", "p", ("d", "h"),
                            appearance_names={"p": "Alex", "d": "Dave", "h": "Hank"})
        self.resolver = SoulResolver()

    def resolve(self, identity=None, target="d", actor="p", bodies=None, activation="soul", day=1):
        return self.resolver.resolve(activation, identity or self.identity, actor, target,
                                     bodies or self.bodies, day)

    def test_catalogue_entry_is_the_twenty_fifth_gold_default(self):
        self.assertEqual(tuple(ITEM_CATALOGUE)[24], "soul")
        definition = ITEM_CATALOGUE["soul"]
        self.assertEqual((definition.rarity, definition.target_kind, definition.default),
                         ("gold", "living_npc", True))
        self.assertEqual(len(eligible_item_pool()), 18)

    def test_use_consumes_once_and_emits_private_request_with_captured_recipient(self):
        inventory = Inventory("player")
        rules = PlayerItemRules("player", inventory, guardian_scope="body")
        soul = inventory.grant("soul")
        other = inventory.grant("banana")
        context = ItemUseContext(1, "conversation", False, "player", "p", self.bodies)
        events = rules.use_item(soul.instance_id, context, "d", activation_id="activation")
        request, used = events
        self.assertEqual((request.event_type, used.event_type), ("soul_exchange_requested", "item_used"))
        self.assertEqual((request.actor_body_id, request.target_body_id, request.recipient_mind_id,
                          request.target_name, request.due_day), ("p", "d", "d", "Dave", 2))
        self.assertIsNone(request.public_text)
        self.assertIsNone(request.private_text)
        self.assertEqual(inventory.entries, (other,))
        self.assertEqual(rules.use_item(soul.instance_id, context, "d", activation_id="activation"), events)
        self.assertEqual(self.identity.player_body_id, "p")

    def test_invalid_target_does_not_spend_soul(self):
        inventory = Inventory("player")
        rules = PlayerItemRules("player", inventory)
        soul = inventory.grant("soul")
        context = ItemUseContext(1, "conversation", False, "player", "p", self.bodies)
        for target, bodies in (("p", self.bodies), ("missing", self.bodies),
                               ("d", (self.bodies[0], replace(self.bodies[1], alive=False)))):
            with self.subTest(target=target), self.assertRaises(ItemRuleError):
                rules.use_item(soul.instance_id, replace(context, bodies=bodies), target)
            self.assertEqual(inventory.entries, (soul,))

    def test_permanent_exchange_preserves_bodies_appearance_and_original_names(self):
        result = self.resolve()
        self.assertEqual(result.identity.player_body_id, "d")
        self.assertEqual(result.identity.body_for_mind("d"), "p")
        self.assertEqual(result.identity.appearances, self.identity.appearances)
        self.assertEqual(result.identity.mind_names, self.identity.mind_names)
        self.assertEqual((result.old_body_id, result.target_mind_id, result.original_name,
                          result.target_original_name, result.old_body_name, result.new_body_name,
                          result.following_night), ("p", "d", "Alex", "Dave", "Alex", "Dave", 2))
        self.assertTrue(result.identity_application_required)
        self.assertEqual(self.identity.player_body_id, "p")

    def test_pre_post_and_same_occupancy_cycle_replay_never_reexchange(self):
        result = self.resolve()
        pending = self.resolve(identity=self.identity)
        self.assertEqual(pending.identity, result.identity)
        self.assertEqual(pending.application_state, "pending")
        self.assertFalse(pending.is_new)
        current = self.resolve(identity=result.identity)
        self.assertEqual(current.application_state, "current")
        self.assertFalse(current.identity_application_required)
        cycled = result.identity.exchange("p")
        self.assertEqual(cycled.occupants, self.identity.occupants)
        superseded = self.resolve(identity=cycled)
        self.assertEqual(superseded.application_state, "superseded")
        self.assertIs(superseded.identity, cycled)
        self.assertFalse(superseded.identity_application_required)
        copied = result.identity.clone_appearance("d", "h")
        self.assertEqual(self.resolve(identity=copied).application_state, "superseded")

    def test_divergent_or_repurposed_receipts_fail(self):
        result = self.resolve()
        for identity in (self.identity, result.identity):
            divergent = replace(identity, appearances=(("p", "Hank"), ("d", "Dave"), ("h", "Hank")))
            with self.subTest(revision=identity.revision), self.assertRaises(ItemRuleError):
                self.resolve(identity=divergent)
        with self.assertRaises(ItemRuleError):
            self.resolve(target="h")
        with self.assertRaises(ItemRuleError):
            self.resolve(day=2)

    def test_distinct_activations_exchange_again_without_changing_original_names(self):
        first = self.resolve()
        bodies = (replace(self.bodies[0], is_npc=True, mind_id="d"),
                  replace(self.bodies[1], is_npc=False, mind_id="player"), self.bodies[2])
        second = self.resolve(identity=first.identity, actor="d", target="p", bodies=bodies, activation="second")
        self.assertEqual(second.identity.occupants, self.identity.occupants)
        self.assertEqual(second.original_name, "Alex")
        self.assertEqual(second.target_original_name, "Dave")
        self.assertEqual(second.new_body_name, "Alex")
        self.assertEqual(self.resolve(identity=second.identity).application_state, "superseded")

    def test_soul_never_spends_or_moves_a_physical_guardian(self):
        inventory = Inventory("player")
        rules = PlayerItemRules("player", inventory, guardian_scope="body")
        for body_id in ("p", "d"):
            entry = inventory.grant("guardian_angel")
            rules.use_item(entry.instance_id, ItemUseContext(1, "conversation", False,
                           "player", body_id, self.bodies))
        result = self.resolve()
        self.assertEqual(result.identity.player_body_id, "d")
        self.assertEqual(rules.guardian_body_ids, ("d", "p"))
        self.assertTrue(rules.intercept_guardian("old", self.bodies[0], 2).prevented)
        self.assertEqual(rules.guardian_body_ids, ("d",))

    def test_stale_mind_snapshot_cannot_create_a_new_transition_receipt(self):
        stale = (self.bodies[0], replace(self.bodies[1], mind_id="h"), self.bodies[2])
        with self.assertRaises(ItemRuleError):
            self.resolve(bodies=stale)
        self.assertTrue(self.resolve().is_new)

    def test_missing_original_names_never_become_opaque_identity_text(self):
        with self.assertRaises(ItemRuleError):
            self.resolve(identity=replace(self.identity, mind_names=()))
        self.assertEqual(self.resolve().original_name, "Alex")

    def test_appearance_names_are_captured_without_replacing_original_identity(self):
        bodies = (replace(self.bodies[0], appearance_name="Hank"),
                  replace(self.bodies[1], appearance_name="Hank"), self.bodies[2])
        result = self.resolve(bodies=bodies)
        self.assertEqual((result.old_body_name, result.new_body_name), ("Hank", "Hank"))
        self.assertEqual((result.original_name, result.target_original_name), ("Alex", "Dave"))

    def test_private_text_is_the_individually_approved_template(self):
        self.assertEqual(SOUL_PRIVATE,
            "A white flash swallows your sight. For an instant, you cannot feel your body at all. "
            "Then your weight returns, but it sits wrong: unfamiliar hands, a different shape beneath your clothes, "
            "breath moving through a chest that is not yours. This is {new_body}'s body. "
            "You are still {original_name}. Your memories are your own.")
