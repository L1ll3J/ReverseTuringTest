"""Offline appearance, protection and ephemeral target-projection contracts."""
from dataclasses import replace
import random
import unittest

from ai_context import MemoryRecord
from game_rules import BodyState, ItemRuleError, ItemUseContext, PlayerItemRules
from identity_state import IdentityState
from items import Inventory
from npc_items import CameraSnapshot
from presentation_aliases import PresentationAliases
from voodoo_rules import VoodooResolver


class VoodooMirrorTests(unittest.TestCase):
    def setUp(self):
        self.bodies = (BodyState("p", "Alex", True, False),
                       BodyState("d", "Debra", True, True),
                       BodyState("h", "Hank", True, True))
        self.identity = IdentityState.from_bodies("player", "p", ("d", "h"),
                            appearance_names={"p": "Alex", "d": "Debra", "h": "Hank"})
        self.inventory = Inventory("player")
        self.rules = PlayerItemRules("player", self.inventory, guardian_scope="body")
        self.resolver = VoodooResolver()

    def arm(self, body_id):
        entry = self.inventory.grant("guardian_angel")
        self.rules.use_item(entry.instance_id, ItemUseContext(
            1, "conversation", False, "player", body_id, self.bodies))
        return entry

    def resolve(self, attempt="attack", identity=None, target="d", actor="p", bodies=None):
        return self.resolver.resolve(attempt, identity or self.identity, actor, target,
                                     bodies or self.bodies, 1, (self.rules,))

    def test_protected_target_consumes_only_its_shield_and_never_exchanges_minds(self):
        self.arm("p")
        self.arm("d")
        result = self.resolve()
        self.assertEqual(result.outcome, "mirrored")
        self.assertEqual(result.identity.occupants, self.identity.occupants)
        self.assertEqual(result.identity.appearance_name("p"), "Debra")
        self.assertEqual(result.identity.appearance_name("d"), "Debra")
        self.assertEqual(result.identity.appearance_name("h"), "Hank")
        self.assertEqual(self.identity.appearance_name("p"), "Alex")
        self.assertIsNone(result.discarded_body_id)
        self.assertTrue(result.protection.prevented)
        self.assertEqual(self.rules.guardian_body_ids, ("p",))
        self.assertTrue(all(body.alive for body in self.bodies))
        self.assertTrue(self.rules.attempt_death("later", self.bodies[1], 2).killed)

    def test_retry_after_consumption_cannot_exchange_or_overwrite_later_appearance(self):
        self.arm("d")
        initial = self.resolve()
        later = initial.identity.clone_appearance("p", "h")
        replay = self.resolve(identity=later)
        self.assertFalse(replay.is_new)
        self.assertEqual(replay.outcome, "mirrored")
        self.assertIs(replay.identity, later)
        self.assertEqual(replay.protection.events, ())
        self.assertFalse(replay.protection.is_new)
        self.assertEqual(self.rules.guardian_body_ids, ())
        with self.assertRaises(ItemRuleError):
            self.resolve(identity=later, target="h")

    def test_cached_first_mirror_application_recovers_identity_and_stable_protection_events(self):
        self.arm("d")
        original = self.resolve()
        cached = self.resolve(identity=self.identity)
        self.assertFalse(cached.is_new)
        self.assertTrue(cached.identity_application_required)
        self.assertEqual(cached.application_state, "pending")
        self.assertEqual(cached.identity, original.identity)
        self.assertEqual(cached.identity.appearance_name("p"), "Debra")
        self.assertFalse(cached.protection.is_new)
        self.assertEqual(cached.protection.events, original.protection.events)
        self.assertEqual(self.rules.guardian_body_ids, ())
        applied_events = set()
        published = []
        for payload in (cached, self.resolve(identity=cached.identity)):
            for event in payload.protection.events:
                if event.event_id not in applied_events:
                    published.append(event.public_text)
                    applied_events.add(event.event_id)
        self.assertEqual(published, ["Against all expectation, Debra survives."])
        current = self.resolve(identity=cached.identity)
        self.assertEqual(current.application_state, "current")
        self.assertFalse(current.identity_application_required)
        self.assertEqual(self.rules.guardian_body_ids, ())

    def test_cached_first_exchange_application_never_discards_current_player_body(self):
        original = self.resolve()
        cached = self.resolve(identity=self.identity)
        self.assertFalse(cached.is_new)
        self.assertTrue(cached.identity_application_required)
        self.assertEqual(cached.identity, original.identity)
        current = cached.identity
        self.assertEqual(current.player_body_id, "d")
        self.assertEqual(cached.discarded_body_id, "p")
        self.assertNotEqual(cached.discarded_body_id, current.player_body_id)
        death = self.rules.attempt_death("corpse:" + cached.attempt_id, self.bodies[0], 1)
        self.assertTrue(death.killed)
        replay = self.resolve(identity=current)
        self.assertEqual(replay.application_state, "current")
        repeated = self.rules.attempt_death("corpse:" + replay.attempt_id, self.bodies[0], 1)
        self.assertFalse(repeated.is_new)
        self.assertEqual(repeated.target_body_id, "p")
        self.assertFalse(repeated.killed)
        self.assertNotEqual(replay.discarded_body_id, replay.identity.player_body_id)

    def test_identity_application_before_corpse_failure_keeps_pending_death_recoverable(self):
        original = self.resolve()
        current = original.identity
        retry = self.resolve(identity=current)
        self.assertFalse(retry.is_new)
        self.assertEqual(retry.application_state, "current")
        self.assertFalse(retry.identity_application_required)
        self.assertEqual(retry.discarded_body_id, "p")
        self.assertNotEqual(retry.discarded_body_id, current.player_body_id)
        self.assertTrue(self.rules.attempt_death("corpse:" + retry.attempt_id, self.bodies[0], 1).killed)

    def test_exchange_cycle_back_to_original_occupancy_suppresses_stale_corpse(self):
        original = self.resolve()
        cycled = original.identity.exchange("p")
        self.assertEqual(cycled.occupants, self.identity.occupants)
        self.assertGreater(cycled.revision, original.identity.revision)
        retry = self.resolve(identity=cycled)
        self.assertEqual(retry.application_state, "superseded")
        self.assertIs(retry.identity, cycled)
        self.assertEqual(retry.identity.player_body_id, "p")
        self.assertIsNone(retry.discarded_body_id)
        self.assertFalse(retry.identity_application_required)

    def test_mirror_cycle_to_original_appearance_does_not_reapply_old_transformation(self):
        before = self.identity.clone_appearance("h", "p")
        self.arm("d")
        original = self.resolve(identity=before)
        cycled = original.identity.clone_appearance("p", "h")
        self.assertEqual(cycled.occupants, before.occupants)
        self.assertEqual(cycled.appearances, before.appearances)
        self.assertGreater(cycled.revision, before.revision)
        self.arm("d")
        retry = self.resolve(identity=cycled)
        self.assertEqual(retry.application_state, "superseded")
        self.assertIs(retry.identity, cycled)
        self.assertEqual(retry.identity.appearance_name("p"), "Alex")
        self.assertEqual(retry.protection.events, ())
        self.assertTrue(self.rules.guardian_protects("d"))

    def test_copying_same_appearance_still_identifies_pending_current_and_later_states(self):
        before = self.identity.clone_appearance("p", "d")
        self.arm("d")
        original = self.resolve(identity=before)
        self.assertEqual(original.identity.appearances, before.appearances)
        self.assertEqual(original.identity.revision, before.revision + 1)
        self.assertEqual(self.resolve(identity=before).application_state, "pending")
        self.assertEqual(self.resolve(identity=original.identity).application_state, "current")
        later = original.identity.clone_appearance("p", "d")
        self.assertEqual(later.appearances, before.appearances)
        self.assertEqual(self.resolve(identity=later).application_state, "superseded")

    def test_same_revision_divergence_rejects_without_spending_a_rearmed_guardian(self):
        self.arm("d")
        original = self.resolve()
        self.arm("d")
        for snapshot in (self.identity, original.identity):
            divergent = replace(snapshot, appearances=(("p", "Hank"), ("d", "Debra"), ("h", "Hank")))
            with self.subTest(revision=divergent.revision), self.assertRaisesRegex(ItemRuleError, "revision diverges"):
                self.resolve(identity=divergent)
            self.assertTrue(self.rules.guardian_protects("d"))

    def test_unprotected_target_exchanges_and_returns_original_body_for_existing_death_adapter(self):
        self.arm("p")
        result = self.resolve()
        self.assertEqual(result.outcome, "exchange")
        self.assertEqual(result.discarded_body_id, "p")
        self.assertEqual(result.identity.player_body_id, "d")
        self.assertEqual(result.identity.body_for_mind("d"), "p")
        self.assertEqual(result.identity.appearances, self.identity.appearances)
        self.assertTrue(self.rules.guardian_protects("p"))
        self.assertTrue(self.rules.attempt_death("old-body", self.bodies[0], 1).prevented)
        self.assertFalse(self.rules.guardian_protects("p"))
        self.assertTrue(self.rules.attempt_death("target-remains-alive", self.bodies[1], 2).killed)

    def test_guardian_event_names_current_appearance_instead_of_internal_handle(self):
        self.arm("h")
        disguised = self.identity.clone_appearance("h", "d")
        result = self.resolve(identity=disguised, target="h")
        self.assertEqual(result.protection.events[0].public_text, "Against all expectation, Debra survives.")
        self.assertEqual(result.identity.appearance_name("p"), "Debra")
        self.assertEqual(result.identity.mind_for_body("h"), "h")

    def test_unprotected_path_does_not_require_appearance_projection(self):
        legacy = replace(self.identity, appearances=())
        self.assertEqual(self.resolve(identity=legacy).outcome, "exchange")

    def test_invalid_target_or_missing_appearance_cannot_consume_a_guardian(self):
        self.arm("d")
        invalids = (("p", self.bodies, self.identity), ("missing", self.bodies, self.identity),
                    ("d", (self.bodies[0], replace(self.bodies[1], alive=False)), self.identity),
                    ("d", self.bodies, replace(self.identity, appearances=())))
        for target, bodies, identity in invalids:
            with self.subTest(target=target, bodies=bodies):
                with self.assertRaises((ItemRuleError, ValueError)):
                    self.resolve(target=target, bodies=bodies, identity=identity)
                self.assertTrue(self.rules.guardian_protects("d"))

    def test_multiple_body_shields_allow_rearming_only_after_changing_body(self):
        self.arm("p")
        duplicate = self.inventory.grant("guardian_angel")
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(duplicate.instance_id, ItemUseContext(
                1, "conversation", False, "player", "p", self.bodies))
        self.assertIn(duplicate, self.inventory.entries)
        self.rules.use_item(duplicate.instance_id, ItemUseContext(
            1, "conversation", False, "player", "d", self.bodies))
        self.assertEqual(self.rules.guardian_body_ids, ("d", "p"))
        self.assertTrue(self.rules.intercept_guardian("shield", self.bodies[1], 1).prevented)
        self.assertTrue(self.rules.guardian_protects("p"))
        self.assertFalse(self.rules.intercept_guardian("shield", self.bodies[1], 1).is_new)
        self.assertTrue(self.rules.guardian_protects("p"))

    def test_unprotected_preflight_does_not_mark_a_body_dead_or_record_a_death(self):
        self.assertIsNone(self.rules.intercept_guardian("preflight", self.bodies[1], 1))
        self.arm("d")
        self.assertTrue(self.rules.intercept_guardian("preflight", self.bodies[1], 1).prevented)

    def test_distinct_protection_providers_cannot_ambiguously_consume_shields(self):
        self.arm("d")
        other = PlayerItemRules("other", Inventory("other"), guardian_scope="body")
        entry = other.inventory.grant("guardian_angel")
        other.use_item(entry.instance_id, ItemUseContext(1, "conversation", False, "other", "d", self.bodies))
        with self.assertRaises(ItemRuleError):
            self.resolver.resolve("ambiguous", self.identity, "p", "d", self.bodies, 1,
                                  (self.rules, other))
        self.assertTrue(self.rules.guardian_protects("d"))
        self.assertTrue(other.guardian_protects("d"))

    def test_clones_and_exchanges_leave_historical_records_and_photos_literal(self):
        memory = MemoryRecord(1, "experience", "Debra was in the Kitchen.", "self")
        photo = CameraSnapshot(1, "Debra", "Kitchen", ("Alex", "Debra"), ())
        before = photo.public_description()
        changed = self.identity.clone_appearance("p", "d").exchange("h")
        labels = PresentationAliases(random.Random(4)).for_presence("conversation:2", changed, ("p", "d", "h"))
        self.assertEqual(memory.text, "Debra was in the Kitchen.")
        self.assertEqual(photo.public_description(), before)
        self.assertTrue(any("#" in label for _, label in labels.entries))
        self.assertTrue(all("#" not in name for _, name in changed.appearances))


class PresentationAliasTests(unittest.TestCase):
    def identity(self):
        return IdentityState.from_bodies("player", "p", ("d", "h"),
                    appearance_names={"p": "Debra", "d": "Debra", "h": "Debra"})

    def test_three_identical_faces_have_unique_stable_body_targets(self):
        identity = self.identity()
        aliases = PresentationAliases(random.Random(9))
        snapshot = aliases.for_presence("conversation:1", identity, ("d", "p", "h"))
        self.assertEqual({label for _, label in snapshot.entries}, {"Debra #1", "Debra #2", "Debra #3"})
        self.assertIs(snapshot, aliases.for_presence("conversation:1", identity.exchange("d"), ("p", "h", "d")))
        for body, label in snapshot.entries:
            self.assertEqual(snapshot.body_for_label(label), body)
            self.assertEqual(snapshot.label_for_body(body), label)
        self.assertIsNone(snapshot.body_for_label("Debra"))
        self.assertIsNone(snapshot.body_for_label("unknown"))

    def test_original_face_is_not_always_number_one(self):
        labels = {PresentationAliases(random.Random(seed)).for_presence(
            "conversation:1", self.identity(), ("p", "d", "h")).label_for_body("d")
                  for seed in range(20)}
        self.assertEqual(labels, {"Debra #1", "Debra #2", "Debra #3"})

    def test_single_remaining_face_loses_number_and_new_phase_has_own_snapshot(self):
        aliases = PresentationAliases(random.Random(4))
        identity = self.identity()
        first = aliases.for_presence("conversation:1", identity, ("p", "d"))
        second = aliases.for_presence("vote:1", identity, ("p", "d"))
        self.assertIsNot(first, second)
        self.assertEqual(aliases.for_presence("conversation:1", identity, ("d",)).label_for_body("d"), "Debra")
        self.assertIs(first, aliases.for_presence("conversation:1", identity, ("d", "p")))

    def test_name_collisions_and_invalid_inputs_never_create_ambiguous_actions(self):
        identity = replace(self.identity(), appearances=(("p", "Debra"), ("d", "Debra"), ("h", "Debra #1")))
        aliases = PresentationAliases(random.Random(0))
        snapshot = aliases.for_presence("vote", identity, ("p", "d", "h"))
        self.assertEqual(len({label for _, label in snapshot.entries}), 3)
        for phase, bodies in (("", ("p",)), ("vote", ("p", "p")), ("vote", ("unknown",))):
            with self.assertRaises(ValueError):
                aliases.for_presence(phase, identity, bodies)
