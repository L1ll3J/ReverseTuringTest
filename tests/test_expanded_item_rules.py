"""Mechanical item combinations and individually approved observations."""

from dataclasses import replace
import random
import unittest

from ai_context import MemoryRecord
from game_rules import BodyState, DeathCause, ItemRuleError, ItemUseContext, NightMemoryView, PlayerItemRules
from items import DraftState, Inventory, ITEM_CATALOGUE


class Draws:
    def __init__(self, *indices):
        self.indices = list(indices)
        self.calls = 0

    def choice(self, candidates):
        index = self.indices[self.calls]
        self.calls += 1
        return candidates[index]


class ExpandedItemRulesTests(unittest.TestCase):
    def setUp(self):
        self.player = BodyState("p", "Alex", True, False)
        self.jerry = BodyState("j", "Jerry", True, True)
        self.dave = BodyState("d", "Dave", True, True)
        self.dead = BodyState("x", "Hank", False, True)
        self.bodies = (self.player, self.jerry, self.dave, self.dead)
        self.voters = self.bodies[:3]
        self.inventory = Inventory("owner")
        self.rng = Draws(0, 1)
        self.rules = PlayerItemRules("owner", self.inventory, self.rng)
        self.context = ItemUseContext(1, "conversation", False, "owner", "p", self.bodies,
                                      ("Kitchen", "Cellar"))

    def grant_use(self, item_id, target=None, **kwargs):
        entry = self.inventory.grant(item_id)
        events = self.rules.use_item(entry.instance_id, self.context, target, **kwargs)
        return entry, events

    def ballots_for_player(self):
        return {"p": "NONE", "j": "p", "d": "p"}

    def assert_mechanical(self, events):
        self.assertTrue(events)
        for event in events:
            self.assertIsNone(event.public_text)
            self.assertIsNone(event.private_text)
            self.assertIsNone(event.disposition_text)

    def test_all_registered_new_items_have_real_mechanical_effects_without_unapproved_prose(self):
        originals = {"banana", "muffle", "spare_paper", "guardian_angel"}
        for item_id, definition in ITEM_CATALOGUE.items():
            if item_id in originals:
                continue
            with self.subTest(item=item_id):
                inventory = Inventory("owner")
                rules = PlayerItemRules("owner", inventory, random.Random(1))
                record = MemoryRecord(1, "experience", "A real supplied perception.", "self")
                context = replace(self.context, memory_views=(NightMemoryView("j", 1, (record,)),))
                target = {"living_npc": "j", "living_npc_room": "j", "living_body": "j", "dead_body": "x"}.get(definition.target_kind)
                kwargs = {"activation_id": item_id}
                if definition.requires_message:
                    kwargs["message"] = "I heard the window open."
                if definition.target_kind in {"room", "living_npc_room"}:
                    kwargs["room_id"] = "Cellar"
                if item_id in {"lucky_match", "deep_pockets"}:
                    kwargs["drafts"] = DraftState("owner", tuple(ITEM_CATALOGUE))
                events = rules.use_item(inventory.grant(item_id).instance_id, context, target, **kwargs)
                approved = {
                    "revolver": ("Alex fires a revolver at Jerry.", None),
                    "throw_voice": (None, 'You heard your own voice say: "I heard the window open." You did not say those words.'),
                    "divine_revelation": (None, 'You hear a voice inside your mind that seems to come from a higher power: "I heard the window open."'),
                    "dark_whisper": ('Everyone hears the unmistakeable voice of the dead Hank, loudly and clearly they speak:\n\n"I heard the window open."', None),
                }
                self.assertEqual((events[0].public_text, events[0].private_text), approved.get(item_id, (None, None)))
                self.assertIsNone(events[0].disposition_text)
                self.assert_mechanical(events[1:])

    def test_payload_validation_precedes_consumption_and_preserves_apostrophes(self):
        entry = self.inventory.grant("throw_voice")
        for message in (None, "", "   ", "x" * 2001, 42):
            with self.subTest(message=message), self.assertRaises(ItemRuleError):
                self.rules.use_item(entry.instance_id, self.context, "j", message=message)
            self.assertIn(entry, self.inventory.entries)
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(entry.instance_id, self.context, "x", message="Hello")
        text = "I wasn't there. <claim> & \"quotation\""
        events = self.rules.use_item(entry.instance_id, self.context, "j", message=text)
        self.assertEqual(events[0].message, text)
        self.assertEqual(events[0].origin, "projected_voice")
        self.assertEqual(events[0].actor_body_id, "p")
        self.assertEqual(events[0].target_body_id, "j")
        self.assertEqual(events[0].private_text,
                         'You heard your own voice say: "' + text + '" You did not say those words.')
        self.assertIsNone(events[0].public_text)
        self.assertIsNone(events[0].disposition_text)
        self.assert_mechanical(events[1:])

    def test_approved_private_observations_preserve_raw_payload_and_hide_true_actor(self):
        text = '  Don\'t trust "a quotation" <tag> & {braces}.\nSecond line; literal \\n stays.  '
        expected = {
            "throw_voice": 'You heard your own voice say: "' + text + '" You did not say those words.',
            "divine_revelation": 'You hear a voice inside your mind that seems to come from a higher power: "' + text + '"',
        }
        for item_id in expected:
            with self.subTest(item=item_id):
                entry, events = self.grant_use(item_id, "j", message=text)
                effect = events[0]
                self.assertEqual(effect.private_text, expected[item_id])
                self.assertEqual(effect.message, text)
                self.assertEqual(effect.actor_body_id, self.player.body_id)
                self.assertEqual(effect.target_body_id, self.jerry.body_id)
                self.assertEqual(effect.owner_id, "owner")
                self.assertIsNone(effect.public_text)
                self.assertIsNone(effect.disposition_text)
                self.assertNotIn(self.player.label, effect.private_text)
                self.assertNotIn("owner", effect.private_text)
                self.assertNotIn(entry.instance_id, effect.private_text)
                self.assert_mechanical(events[1:])

    def test_dark_whisper_uses_explicit_pronoun_and_exact_multiline_approved_observation(self):
        for pronoun, phrase in (("he", "he speaks"), ("she", "she speaks"), ("they", "they speak")):
            with self.subTest(pronoun=pronoun):
                dead = replace(self.dead, subject_pronoun=pronoun)
                context = replace(self.context, bodies=(*self.bodies[:3], dead))
                entry = self.inventory.grant("dark_whisper")
                events = self.rules.use_item(entry.instance_id, context, dead.body_id, message="Remember the Cellar.")
                effect = events[0]
                expected = ('Everyone hears the unmistakeable voice of the dead Hank, loudly and clearly '
                            + phrase + ':\n\n"Remember the Cellar."')
                self.assertEqual(effect.public_text, expected)
                self.assertEqual(effect.public_text.count('"'), 2)
                self.assertIn(':\n\n"', effect.public_text)
                self.assertNotIn(r"\n", effect.public_text)
                self.assertEqual(effect.message, "Remember the Cellar.")
                self.assertEqual(effect.target_body_id, dead.body_id)
                self.assertEqual(effect.actor_body_id, self.player.body_id)
                self.assertEqual(effect.origin, "paranormal_voice")
                self.assertIsNone(effect.private_text)
                self.assertIsNone(effect.disposition_text)
                self.assertNotIn(self.player.label, effect.public_text)
                self.assert_mechanical(events[1:])

    def test_unknown_pronoun_is_they_without_guessing_from_name_and_preserves_message_text(self):
        dead = BodyState("unknown", "Lady Mystery", False, True)
        self.assertEqual(dead.subject_pronoun, "they")
        context = replace(self.context, bodies=(*self.bodies, dead))
        message = '"Nested quotation", apostrophe\'s & <markup> {literal}\n\nNext paragraph.'
        entry = self.inventory.grant("dark_whisper")
        effect = self.rules.use_item(entry.instance_id, context, dead.body_id, message=message)[0]
        self.assertEqual(effect.public_text,
                         'Everyone hears the unmistakeable voice of the dead Lady Mystery, loudly and clearly they speak:\n\n"'
                         + message + '"')
        self.assertEqual(effect.message, message)
        for invalid in (None, "", "He", "it", "she/her", True):
            with self.subTest(pronoun=invalid), self.assertRaises(ItemRuleError):
                replace(dead, subject_pronoun=invalid)

    def test_observation_target_failures_never_consume_or_disclose_a_message(self):
        for item_id, invalid_targets, valid_target in (
            ("throw_voice", (None, "missing", "x", "p"), "j"),
            ("divine_revelation", (None, "missing", "x", "p"), "j"),
            ("dark_whisper", (None, "missing", "j", "p"), "x"),
        ):
            with self.subTest(item=item_id):
                entry = self.inventory.grant(item_id)
                for target in invalid_targets:
                    with self.subTest(target=target), self.assertRaises(ItemRuleError):
                        self.rules.use_item(entry.instance_id, self.context, target, message="A private payload.")
                    self.assertIn(entry, self.inventory.entries)
                effect = self.rules.use_item(entry.instance_id, self.context, valid_target,
                                             message="A private payload.")[0]
                self.assertEqual(effect.target_body_id, valid_target)
                self.assertEqual(effect.message, "A private payload.")

    def test_projected_and_paranormal_voice_do_not_change_physical_muffle(self):
        self.grant_use("muffle", "j")
        _, voice = self.grant_use("throw_voice", "j", message="An apparent clear voice.")
        _, ghost = self.grant_use("dark_whisper", "x", message="An apparent dead voice.")
        self.assertEqual(voice[0].origin, "projected_voice")
        self.assertEqual(ghost[0].origin, "paranormal_voice")
        self.assertIsNotNone(self.rules.speech_override("j"))
        self.assertIsNone(self.rules.speech_override("d"))

    def test_listening_glass_requires_target_night_provenance_and_never_publishes_it(self):
        record = MemoryRecord(1, "experience", "A blurred struggle, not verified guilt.", "self")
        context = replace(self.context, memory_views=(NightMemoryView("j", 1, (record,)),))
        entry = self.inventory.grant("listening_glass")
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(entry.instance_id, context, "d")
        self.assertIn(entry, self.inventory.entries)
        events = self.rules.use_item(entry.instance_id, context, "j")
        self.assertEqual(events[0].message, record.text)
        self.assertEqual(events[0].observed_day, 1)
        self.assertEqual(events[0].observed_source, "self")
        self.assertEqual(events[0].origin, "recorded_perception")
        self.assert_mechanical(events)
        with self.assertRaises(ItemRuleError):
            NightMemoryView("j", 2, (record,))

    def test_draft_utilities_change_real_offer_state_without_spending_on_invalid_use(self):
        other = DraftState("other", tuple(ITEM_CATALOGUE))
        drafts = DraftState("owner", tuple(ITEM_CATALOGUE), random.Random(5))
        entry = self.inventory.grant("lucky_match")
        for invalid in (None, other, DraftState("owner", ("banana", "muffle", "spare_paper"))):
            with self.assertRaises(ItemRuleError):
                self.rules.use_item(entry.instance_id, self.context, drafts=invalid)
            self.assertIn(entry, self.inventory.entries)
        self.rules.use_item(entry.instance_id, self.context, drafts=drafts)
        self.assertEqual(drafts.rerolls_remaining, 4)
        self.grant_use("deep_pockets", drafts=drafts)
        duplicate = self.inventory.grant("deep_pockets")
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(duplicate.instance_id, self.context, drafts=drafts)
        self.assertIn(duplicate, self.inventory.entries)
        offer = drafts.enter_room("Kitchen", self.inventory.snapshot())
        self.assertEqual(offer.picks_remaining, 3)
        drafts.choose(offer.offer_id, 0, self.inventory)
        self.assertIsNotNone(drafts.pending_offer)

    def test_reservation_validates_replaces_caps_and_expires_without_observations(self):
        third = BodyState("h", "Helen", True, True)
        context = replace(self.context, bodies=(*self.bodies, third))
        for target, room in ((None, "Kitchen"), ("p", "Kitchen"), ("x", "Kitchen"),
                             ("j", None), ("j", "Missing")):
            entry = self.inventory.grant("room_reservation")
            with self.assertRaises(ItemRuleError):
                self.rules.use_item(entry.instance_id, context, target, room_id=room)
            self.assertIn(entry, self.inventory.entries)
        for target in ("j", "d"):
            events = self.rules.use_item(self.inventory.grant("room_reservation").instance_id,
                                         context, target, room_id="Kitchen")
            self.assert_mechanical(events)
        entry = self.inventory.grant("room_reservation")
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(entry.instance_id, context, "h", room_id="Kitchen")
        self.assertIn(entry, self.inventory.entries)
        self.rules.use_item(self.inventory.grant("room_reservation").instance_id,
                            context, "j", room_id="Cellar")
        self.rules.use_item(entry.instance_id, context, "h", room_id="Kitchen")
        self.assertEqual(len(self.rules.reservations), 3)
        self.rules.begin_night(2, context.bodies)
        assignments = self.rules.reserved_room_assignments(2, context.bodies, ("Kitchen", "Cellar"))
        self.assertEqual(dict(assignments), {"j": "Cellar", "d": "Kitchen", "h": "Kitchen"})
        self.rules.begin_night(3, context.bodies)
        self.assertFalse(self.rules.reservations)
        self.assertFalse(self.rules.reserved_room_assignments(3, context.bodies, ("Kitchen", "Cellar")))

    def test_reservation_follows_mind_and_only_real_matching_player_kill_counts_once(self):
        for day, phase, room, owner, expected in (
                (2, "action", "Kitchen", "owner", True),
                (2, "action", "Cellar", "owner", False),
                (2, "conversation", "Kitchen", "owner", False),
                (2, "action", "Kitchen", "other", False),
                (3, "action", "Kitchen", "owner", False)):
            with self.subTest(day=day, phase=phase, room=room, owner=owner):
                inventory = Inventory("owner")
                rules = PlayerItemRules("owner", inventory)
                rules.use_item(inventory.grant("room_reservation").instance_id,
                               self.context, "j", room_id="Kitchen")
                changed = (self.player, replace(self.dave, mind_id="j"), replace(self.jerry, mind_id="d"))
                rules.begin_night(day, changed)
                assignments = rules.reserved_room_assignments(day, changed, ("Kitchen", "Cellar"))
                if day == 2:
                    self.assertEqual(dict(assignments), {"d": "Kitchen"})
                decision = rules.attempt_death("kill", changed[1], day, caused_by_owner_id=owner,
                                               phase=phase, room_id=room)
                self.assertEqual(any(event.event_type == "reserved_room_kill" for event in decision.events), expected)
                replay = rules.attempt_death("kill", changed[1], day, caused_by_owner_id=owner,
                                             phase=phase, room_id=room)
                self.assertFalse(replay.is_new)
                self.assertEqual(replay.events, decision.events)
                self.assertFalse(rules.reservations)

    def test_dead_reservation_cannot_occupy_capacity_or_move_a_body(self):
        self.grant_use("room_reservation", "j", room_id="Kitchen")
        self.rules.attempt_death("earlier", self.jerry, 1)
        self.assertFalse(self.rules.reservations)
        self.rules.begin_night(2, self.bodies)
        self.assertFalse(self.rules.reserved_room_assignments(2, self.bodies, ("Kitchen",)))

    def test_gavel_bypasses_only_length_not_required_reply_busy_phase_or_life(self):
        entry = self.inventory.grant("pocket_gavel")
        self.assertFalse(self.rules.can_open_vote(self.context, 1, 20))
        for invalid in (replace(self.context, required_player_reply=True), replace(self.context, ai_busy=True)):
            with self.assertRaises(ItemRuleError):
                self.rules.use_item(entry.instance_id, invalid)
            self.assertIn(entry, self.inventory.entries)
        self.rules.use_item(entry.instance_id, self.context)
        self.assertTrue(self.rules.can_open_vote(self.context, 1, 20))
        for invalid in (replace(self.context, required_player_reply=True), replace(self.context, ai_busy=True),
                        replace(self.context, phase="action"), replace(self.context, owner_id="other"),
                        replace(self.context, actor_body_id="x")):
            self.assertFalse(self.rules.can_open_vote(invalid, 1, 20))

    def test_mail_proxy_freezes_at_open_skips_normal_vote_and_rejects_conflicting_reply(self):
        self.grant_use("mail_in_ballot", "j")
        with self.assertRaises(ItemRuleError):
            self.rules.open_vote("vote", self.voters, "p", day=1)
        snapshot = self.rules.open_vote("vote", self.voters, "p", day=1, proxy_ballots={"j": "d"})
        self.assertEqual(snapshot.proxy_ballots, {"j": "d"})
        with self.assertRaises(TypeError):
            snapshot.proxy_ballots["j"] = "p"
        with self.assertRaises(ItemRuleError):
            self.rules.resolve_vote("vote", {"p": "NONE", "j": "p", "d": "p"})
        result = self.rules.resolve_vote("vote", {"p": "NONE", "d": "p"})
        self.assertEqual(result.counts, {"NONE": 1, "d": 1, "p": 1})
        self.assertEqual(result.events[0].event_type, "proxy_ballot_counted")
        self.assert_mechanical(result.events)
        replay = self.rules.resolve_vote("vote", {"p": "NONE", "j": "d", "d": "p"})
        self.assertFalse(replay.is_new)
        self.assertEqual(replay.events, result.events)

    def test_skipped_vote_expires_this_round_proxy_but_preserves_next_actual_ballot_effects(self):
        self.grant_use("spare_paper")
        self.grant_use("stay_of_execution", "p")
        self.grant_use("mail_in_ballot", "j")
        self.grant_use("presidential_decree")
        duplicate = self.inventory.grant("presidential_decree")
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(duplicate.instance_id, self.context)
        self.assertIn(duplicate, self.inventory.entries)
        skipped = self.rules.open_vote("skip", self.voters, "p", day=1)
        self.assertTrue(skipped.skipped)
        with self.assertRaises(ItemRuleError):
            self.rules.resolve_vote("skip", {"p": "NONE"})
        result = self.rules.resolve_vote("skip", {})
        self.assertEqual(result.outcome, "skipped")
        self.assertFalse(self.rules.skip_next_vote)
        self.assertEqual(self.rules.proxy_voters, {})
        self.assertEqual(self.rules.pending_ballot_bonus, 2)
        self.assertEqual(self.rules.execution_stays, ("p",))
        cached = self.rules.resolve_vote("skip", {})
        self.assertFalse(cached.is_new)
        self.assertEqual(cached.events, result.events)
        self.rules.begin_night(2, self.bodies)
        actual = self.rules.open_vote("actual", self.voters, "p", day=2)
        self.assertEqual(actual.weights["p"], 3)
        self.assertEqual(actual.execution_stays, ("p",))
        self.rules.resolve_vote("actual", {body.body_id: "NONE" for body in self.voters})
        self.assertEqual(self.rules.pending_ballot_bonus, 0)
        self.assertEqual(self.rules.execution_stays, ())

    def test_execution_stay_uses_typed_vote_source_and_preserves_broader_guardian(self):
        self.grant_use("guardian_angel")
        self.grant_use("stay_of_execution", "p")
        self.rules.open_vote("first", self.voters, "p", day=1)
        self.rules.resolve_vote("first", self.ballots_for_player())
        with self.assertRaises(ItemRuleError):
            self.rules.attempt_death("bad", self.player, 1, "owner", cause=DeathCause.EXECUTION)
        saved = self.rules.attempt_death("first-execution", self.player, 1, "owner",
                                         cause=DeathCause.EXECUTION, source_id="first")
        self.assertTrue(saved.prevented)
        self.assertEqual(saved.protection_item_id, "stay_of_execution")
        self.assertTrue(self.rules.protection_armed)
        self.assert_mechanical(saved.events)
        replay = self.rules.attempt_death("duplicate-execution", self.player, 1, "owner",
                                          cause=DeathCause.EXECUTION, source_id="first")
        self.assertFalse(replay.is_new)
        self.assertEqual(replay.events, saved.events)
        self.assertTrue(self.rules.protection_armed)
        self.rules.open_vote("second", self.voters, "p", day=1)
        self.rules.resolve_vote("second", self.ballots_for_player())
        saved_again = self.rules.attempt_death("second-execution", self.player, 1, "owner",
                                               cause=DeathCause.EXECUTION, source_id="second")
        self.assertEqual(saved_again.protection_item_id, "guardian_angel")
        self.assertFalse(self.rules.protection_armed)

    def test_execution_stay_does_not_prevent_a_shot_or_establish_innocence(self):
        self.grant_use("stay_of_execution", "j")
        _, events = self.grant_use("revolver", "j")
        self.assertTrue(events[0].death_decision.killed)
        self.assertEqual(events[0].death_decision.cause, DeathCause.ATTACK)
        self.assertEqual(self.rules.execution_stays, ("j",))
        self.assertEqual(events[0].public_text, "Alex fires a revolver at Jerry.")
        self.assert_mechanical(events[1:])

    def test_coin_reuses_inventory_and_caches_each_random_activation(self):
        self.grant_use("guardian_angel")
        coin = self.inventory.grant("lucky_coin")
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(coin.instance_id, self.context, "j")
        first = self.rules.use_item(coin.instance_id, self.context, "j", activation_id="flip-1")
        self.assertTrue(first[0].death_decision.prevented)
        self.assertEqual(first[0].subject_body_id, "p")
        self.assertIn(coin, self.inventory.entries)
        replay = self.rules.use_item(coin.instance_id, self.context, "j", activation_id="flip-1")
        self.assertEqual([event.event_id for event in replay], [event.event_id for event in first])
        self.assertFalse(replay[0].death_decision.is_new)
        self.assertEqual(replay[0].death_decision.events, first[0].death_decision.events)
        self.assertEqual(self.rng.calls, 1)
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(coin.instance_id, self.context, "d", activation_id="flip-1")
        second = self.rules.use_item(coin.instance_id, self.context, "j", activation_id="flip-2")
        self.assertTrue(second[0].death_decision.killed)
        self.assertEqual(second[0].subject_body_id, "j")
        self.assertIn(coin, self.inventory.entries)
        self.assertEqual(self.rng.calls, 2)
        # Even a stale caller snapshot cannot roll against the body just killed.
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(coin.instance_id, self.context, "j", activation_id="flip-3")
        self.assertEqual(self.rng.calls, 2)

    def test_random_dagger_can_hit_player_and_does_not_roll_again_after_retry(self):
        dagger = self.inventory.grant("spin_the_dagger")
        events = self.rules.use_item(dagger.instance_id, self.context, activation_id="spin")
        self.assertEqual(events[0].subject_body_id, "p")
        self.assertTrue(events[0].death_decision.killed)
        replay = self.rules.use_item(dagger.instance_id, self.context, activation_id="spin")
        self.assertEqual([event.event_id for event in replay], [event.event_id for event in events])
        self.assertFalse(replay[0].death_decision.is_new)
        self.assertEqual(self.rng.calls, 1)
        self.assertNotIn(dagger, self.inventory.entries)

    def test_trap_is_scheduled_then_consumed_once_for_the_whole_entry_group(self):
        trap, _ = self.grant_use("booby_trap", room_id="Cellar")
        too_early = self.rules.resolve_entry("early", "Cellar", (self.jerry,), 1)
        self.assertEqual(too_early.decisions, ())
        self.assertEqual(len(self.rules.traps), 1)
        result = self.rules.resolve_entry("arrivals", "Cellar", (self.jerry, self.dave), 2)
        self.assertEqual({decision.target_body_id for decision in result.decisions}, {"j", "d"})
        self.assertTrue(all(decision.killed and decision.cause == DeathCause.TRAP for decision in result.decisions))
        self.assertEqual(self.rules.traps, ())
        self.grant_use("booby_trap", room_id="Cellar")
        self.grant_use("guardian_angel")
        retry = self.rules.resolve_entry("arrivals", "Cellar", (replace(self.jerry, alive=False), replace(self.dave, alive=False)), 2)
        self.assertFalse(retry.is_new)
        self.assertTrue(all(not decision.is_new for decision in retry.decisions))
        self.assertEqual(retry.events, result.events)
        self.assertEqual(tuple(decision.events for decision in retry.decisions),
                         tuple(decision.events for decision in result.decisions))
        self.assertEqual(len(self.rules.traps), 1)
        self.assertTrue(self.rules.protection_armed)
        self.assertNotIn(trap, self.inventory.entries)

    def test_trap_can_hit_player_and_guardian_is_owner_bound(self):
        self.grant_use("booby_trap", room_id="Cellar")
        self.grant_use("guardian_angel")
        # Current ownership is supplied explicitly; the physical body can change.
        result = self.rules.resolve_entry("player-entry", "Cellar", (self.jerry,), 2, owners={"j": "owner"})
        self.assertTrue(result.decisions[0].prevented)
        self.assertEqual(result.decisions[0].protection_item_id, "guardian_angel")
        self.assertFalse(self.rules.protection_armed)
        self.assertEqual(self.rules.traps, ())

    def test_invalid_trap_room_or_entry_group_never_spends_the_trap(self):
        trap = self.inventory.grant("booby_trap")
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(trap.instance_id, self.context, room_id="closed")
        self.assertIn(trap, self.inventory.entries)
        self.rules.use_item(trap.instance_id, self.context, room_id="Cellar")
        for entrants in ((), (self.dead,), (self.jerry, self.jerry)):
            with self.assertRaises(ItemRuleError):
                self.rules.resolve_entry("invalid", "Cellar", entrants, 2)
            self.assertEqual(len(self.rules.traps), 1)

    def test_curse_waits_until_action_after_next_and_uses_current_body_ownership(self):
        _, events = self.grant_use("occult_ritual", "j")
        self.assertEqual(events[0].due_day, 3)
        self.assertEqual(self.rules.resolve_due_curses("night-2", 2, self.bodies).decisions, ())
        self.grant_use("guardian_angel")
        due = self.rules.resolve_due_curses("night-3", 3, self.bodies, owners={"j": "owner"})
        self.assertEqual(len(due.decisions), 1)
        self.assertTrue(due.decisions[0].prevented)
        self.assertEqual(due.decisions[0].cause, DeathCause.CURSE)
        self.assertEqual(self.rules.curses, ())
        self.assertFalse(self.rules.resolve_due_curses("night-3", 3, self.bodies, owners={"j": "owner"}).is_new)

    def test_due_curse_validates_all_snapshots_before_any_death_and_never_double_kills(self):
        self.grant_use("occult_ritual", "j")
        duplicate = self.inventory.grant("occult_ritual")
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(duplicate.instance_id, self.context, "j")
        self.assertIn(duplicate, self.inventory.entries)
        context = replace(self.context, day=2)
        self.rules.use_item(duplicate.instance_id, context, "j")
        with self.assertRaises(ItemRuleError):
            self.rules.resolve_due_curses("missing", 4, (self.player,))
        self.assertEqual(len(self.rules.curses), 2)
        result = self.rules.resolve_due_curses("catchup", 4, self.bodies)
        self.assertEqual([decision.outcome for decision in result.decisions], ["killed", "already_dead"])
        self.assertEqual(self.rules.curses, ())

    def test_note_binds_once_to_actual_corpse_and_mark_grants_only_entitlement(self):
        self.grant_use("forged_note", message="I know whose voice I heard.")
        self.grant_use("watch_this_drive", "j")
        prevented = self.rules.attempt_death("unrelated", self.dead, 2, caused_by_owner_id="owner", phase="action")
        self.assertEqual(prevented.outcome, "already_dead")
        self.assertIsNotNone(self.rules.prepared_note)
        killed = self.rules.attempt_death("night-attack", self.jerry, 2,
                                          caused_by_owner_id="owner", phase="action")
        self.assertEqual([event.event_type for event in killed.events], ["prepared_note_bound", "extra_action_earned"])
        self.assertEqual(killed.events[0].target_body_id, "j")
        self.assertEqual(killed.events[0].message, "I know whose voice I heard.")
        self.assertIsNone(self.rules.prepared_note)
        self.assertIsNone(self.rules.marked_target)
        self.assertEqual(self.rules.bonus_actions, 1)
        repeat = self.rules.attempt_death("night-attack", replace(self.jerry, alive=False), 2,
                                          caused_by_owner_id="owner", phase="action")
        self.assertFalse(repeat.is_new)
        self.assertEqual(repeat.events, killed.events)
        self.assertEqual(self.rules.bonus_actions, 1)
        self.assertIsNone(self.rules.prepared_note)
        self.assertIsNone(self.rules.marked_target)
        self.assertTrue(self.rules.consume_bonus_action("chosen-action", 2))
        self.assertFalse(self.rules.consume_bonus_action("chosen-action", 2))
        with self.assertRaises(ItemRuleError):
            self.rules.consume_bonus_action("another-action", 2)
        self.assert_mechanical(killed.events)

    def test_mark_requires_actual_marked_body_next_night_and_expires(self):
        self.grant_use("watch_this_drive", "j")
        # A different corpse does not satisfy a marked body's death.
        self.rules.attempt_death("different-body", self.dave, 2, caused_by_owner_id="owner", phase="action")
        self.assertEqual(self.rules.bonus_actions, 0)
        self.assertIsNotNone(self.rules.marked_target)
        self.rules.begin_night(3, (self.player, self.jerry, replace(self.dave, alive=False), self.dead))
        self.assertIsNone(self.rules.marked_target)
        self.rules.attempt_death("late", self.jerry, 3, caused_by_owner_id="owner", phase="action")
        self.assertEqual(self.rules.bonus_actions, 0)

    def test_same_boundary_curses_follow_activation_order_for_a_prepared_note(self):
        self.grant_use("forged_note", message="A prepared message.")
        self.grant_use("occult_ritual", "j")
        self.grant_use("occult_ritual", "d")
        result = self.rules.resolve_due_curses("night-3", 3, self.bodies)
        self.assertEqual([decision.target_body_id for decision in result.decisions], ["j", "d"])
        notes = [event for event in result.events if event.event_type == "prepared_note_bound"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].target_body_id, "j")


if __name__ == "__main__":
    unittest.main()
