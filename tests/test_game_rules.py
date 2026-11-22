"""Pure checks for item validation, stable subjects and shared resolution hooks."""

from dataclasses import replace
import unittest

from game_rules import (
    BANANA_DISPOSITION, GUARDIAN_PUBLIC, MUFFLE_EXPIRED, MUFFLE_PRIVATE, MUFFLED_SPEECH,
    BodyState, ItemRuleError, ItemUseContext, PlayerItemRules,
)
from items import Inventory


class PlayerItemRulesTests(unittest.TestCase):
    def setUp(self):
        self.player = BodyState("body-player", "Josh", True, False)
        self.jerry = BodyState("body-jerry", "Jerry", True, True)
        self.dave = BodyState("body-dave", "Dave", True, True)
        self.dead = BodyState("body-dead", "Hank", False, True)
        self.bodies = (self.player, self.jerry, self.dave, self.dead)
        self.voters = (self.player, self.jerry, self.dave)
        self.inventory = Inventory("owner-player")
        self.rules = PlayerItemRules("owner-player", self.inventory)
        self.context = ItemUseContext(1, "conversation", False, "owner-player", self.player.body_id, self.bodies)

    def use(self, item_id, target=None, context=None):
        entry = self.inventory.grant(item_id)
        return self.rules.use_item(entry.instance_id, context or self.context, target)

    def skip_ballots(self):
        return {body.body_id: "NONE" for body in self.voters}

    def test_wrong_phase_busy_owner_or_actor_rejects_without_consumption(self):
        entry = self.inventory.grant("guardian_angel")
        invalid = [replace(self.context, phase=phase) for phase in ("action", "voting", "closed", "start")]
        invalid += [replace(self.context, ai_busy=True), replace(self.context, owner_id="other"),
                    replace(self.context, actor_body_id="missing"), replace(self.context, actor_body_id=self.dead.body_id),
                    replace(self.context, day=0), replace(self.context, bodies=self.bodies + (self.player,))]
        for context in invalid:
            with self.subTest(context=context), self.assertRaises(ItemRuleError):
                self.rules.use_item(entry.instance_id, context)
            self.assertEqual(self.inventory.entries, (entry,))
            self.assertFalse(self.rules.protection_armed)
        with self.assertRaises(AttributeError):
            self.rules.owner_id = "other"
        with self.assertRaises(ItemRuleError):
            BodyState("NONE", "A name", True, True)

    def test_target_validation_and_repeated_use_never_spend_another_item(self):
        entry = self.inventory.grant("banana")
        visitor = BodyState("visitor", "Visitor", True, False)
        context = replace(self.context, bodies=self.bodies + (visitor,))
        for target in (None, "missing", self.dead.body_id, self.player.body_id, visitor.body_id):
            with self.subTest(target=target), self.assertRaises(ItemRuleError):
                self.rules.use_item(entry.instance_id, context, target)
            self.assertEqual(self.inventory.entries, (entry,))
        self.rules.use_item(entry.instance_id, self.context, self.jerry.body_id)
        other = self.inventory.grant("banana")
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(entry.instance_id, self.context, self.jerry.body_id)
        self.assertEqual(self.inventory.entries, (other,))
        for item_id in ("spare_paper", "guardian_angel"):
            targeted = self.inventory.grant(item_id)
            with self.assertRaises(ItemRuleError):
                self.rules.use_item(targeted.instance_id, self.context, self.jerry.body_id)
            self.assertIn(targeted, self.inventory.entries)

    def test_banana_records_original_giver_body_and_exact_approved_wording(self):
        events = self.use("banana", self.dave.body_id)
        self.assertEqual([event.event_type for event in events], ["banana_given", "item_used"])
        gift = events[0]
        self.assertEqual(gift.public_text, "Josh gave Dave a banana.")
        self.assertEqual(gift.disposition_text, BANANA_DISPOSITION.format(giver="Josh", day=1))
        self.assertEqual(gift.target_body_id, self.dave.body_id)
        self.assertEqual(gift.subject_body_id, self.player.body_id)
        possessed = replace(self.context, actor_body_id=self.jerry.body_id,
                            bodies=(replace(self.player, alive=False), self.jerry, self.dave, self.dead))
        self.use("banana", self.dave.body_id, possessed)
        self.assertEqual([effect.subject_body_id for effect in self.rules.affinities],
                         [self.player.body_id, self.jerry.body_id])
        self.assertIn("Josh", self.rules.affinities[0].disposition_text)
        self.assertIn("Jerry", self.rules.affinities[1].disposition_text)
        self.assertEqual(self.inventory.owner_id, "owner-player")

    def test_muffle_is_enforced_for_each_speech_check_and_expires_at_next_night(self):
        applied = self.use("muffle", self.jerry.body_id)[0]
        self.assertEqual(applied.private_text, MUFFLE_PRIVATE)
        for _ in range(2):
            masked = self.rules.speech_override(self.jerry.body_id)
            self.assertEqual(masked.speech, MUFFLED_SPEECH)
            self.assertIsNone(masked.addressee)
        self.assertIsNone(self.rules.speech_override(self.dave.body_id))
        self.assertEqual(self.rules.begin_night(1, self.bodies), ())
        expired = self.rules.begin_night(2, self.bodies)
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].target_body_id, self.jerry.body_id)
        self.assertEqual(expired[0].private_text, MUFFLE_EXPIRED)
        self.assertIsNone(self.rules.speech_override(self.jerry.body_id))
        self.assertEqual(self.rules.begin_night(2, self.bodies), ())

    def test_existing_muffle_rejects_without_consumption_and_does_not_move_to_another_body(self):
        self.use("muffle", self.jerry.body_id)
        duplicate = self.inventory.grant("muffle")
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(duplicate.instance_id, self.context, self.jerry.body_id)
        self.assertEqual(self.inventory.entries, (duplicate,))
        self.assertEqual(self.rules.muffles[0].target_body_id, self.jerry.body_id)
        self.assertIsNotNone(self.rules.speech_override(self.jerry.body_id))
        self.assertIsNone(self.rules.speech_override(self.player.body_id))
        dead_after_vote = tuple(replace(body, alive=False) if body.body_id == self.jerry.body_id else body
                                for body in self.bodies)
        self.assertEqual(self.rules.begin_night(2, dead_after_vote), ())
        self.assertEqual(self.rules.muffles, ())

    def test_muffled_npc_still_has_a_normal_ballot(self):
        self.use("muffle", self.jerry.body_id)
        snapshot = self.rules.open_vote("vote-1", self.voters, self.player.body_id, day=1)
        self.assertEqual(snapshot.weights[self.jerry.body_id], 1)
        ballots = self.skip_ballots()
        ballots[self.jerry.body_id] = self.player.body_id
        result = self.rules.resolve_vote("vote-1", ballots)
        self.assertEqual(result.counts[self.player.body_id], 1)

    def test_guardian_requires_manual_activation_and_cannot_be_stacked(self):
        first = self.inventory.grant("guardian_angel")
        second = self.inventory.grant("guardian_angel")
        self.assertFalse(self.rules.protection_armed)
        armed = self.rules.use_item(first.instance_id, self.context)
        self.assertEqual(armed[0].event_type, "guardian_armed")
        self.assertIsNone(armed[0].public_text)
        self.assertTrue(self.rules.protection_armed)
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(second.instance_id, self.context)
        self.assertEqual(self.inventory.entries, (second,))

    def test_guardian_follows_owner_through_possession_and_death_retries_are_idempotent(self):
        self.use("guardian_angel")
        old_body_death = self.rules.attempt_death("old-body", self.player, 1)
        self.assertTrue(old_body_death.killed)
        self.assertTrue(self.rules.protection_armed)
        saved = self.rules.attempt_death("execution", self.jerry, 1, "owner-player")
        self.assertTrue(saved.prevented)
        self.assertFalse(saved.killed)
        self.assertFalse(self.rules.protection_armed)
        self.assertEqual(saved.events[0].public_text, GUARDIAN_PUBLIC.format(target="Jerry"))
        possessed = replace(self.context, actor_body_id=self.jerry.body_id)
        self.use("guardian_angel", context=possessed)
        repeated = self.rules.attempt_death("execution", self.jerry, 1, "owner-player")
        self.assertFalse(repeated.is_new)
        self.assertEqual(repeated.events, ())
        self.assertTrue(self.rules.protection_armed)
        self.rules.attempt_death("next-execution", self.jerry, 1, "owner-player")
        self.assertFalse(self.rules.protection_armed)
        fatal = self.rules.attempt_death("final-execution", self.jerry, 1, "owner-player")
        self.assertTrue(fatal.killed)
        replay = self.rules.attempt_death("final-execution", replace(self.jerry, alive=False), 1, "owner-player")
        self.assertFalse(replay.killed)
        self.assertFalse(replay.is_new)

    def test_already_dead_and_conflicting_attempts_do_not_spend_protection(self):
        self.use("guardian_angel")
        result = self.rules.attempt_death("dead", self.dead, 1, "owner-player")
        self.assertEqual(result.outcome, "already_dead")
        self.assertFalse(result.killed)
        self.assertTrue(self.rules.protection_armed)
        with self.assertRaises(ItemRuleError):
            self.rules.attempt_death("dead", self.player, 1, "owner-player")
        self.assertTrue(self.rules.protection_armed)

    def test_extra_papers_stack_freeze_at_open_and_consume_only_on_resolution(self):
        self.use("spare_paper")
        self.use("spare_paper")
        snapshot = self.rules.open_vote("vote-1", self.voters, self.player.body_id, day=1)
        self.assertEqual(snapshot.weights, {self.player.body_id: 5, self.jerry.body_id: 1, self.dave.body_id: 1})
        self.assertEqual(self.rules.pending_ballot_bonus, 4)
        with self.assertRaises(TypeError):
            snapshot.weights[self.player.body_id] = 1
        waiting = self.inventory.grant("spare_paper")
        with self.assertRaises(ItemRuleError):
            self.rules.use_item(waiting.instance_id, self.context)
        self.assertIn(waiting, self.inventory.entries)
        ballots = {self.player.body_id: self.jerry.body_id,
                   self.jerry.body_id: self.player.body_id, self.dave.body_id: self.player.body_id}
        result = self.rules.resolve_vote("vote-1", ballots)
        self.assertEqual(result.counts, {self.jerry.body_id: 5, self.player.body_id: 2})
        self.assertEqual((result.outcome, result.executed_body_id), ("execution", self.jerry.body_id))
        self.assertEqual(self.rules.pending_ballot_bonus, 0)
        self.assertEqual(result.events[0].public_text, "Josh's vote for Jerry counted as 5 votes.")
        self.assertEqual(result.events[0].day, 1)
        with self.assertRaises(TypeError):
            result.counts[self.jerry.body_id] = 0
        repeated = self.rules.resolve_vote("vote-1", dict(reversed(tuple(ballots.items()))))
        self.assertFalse(repeated.is_new)
        self.assertEqual(repeated.events, ())
        self.assertEqual(repeated.counts, result.counts)
        next_vote = self.rules.open_vote("vote-2", self.voters, self.player.body_id, day=2)
        self.assertEqual(next_vote.weights[self.player.body_id], 1)

    def test_papers_survive_nights_without_a_vote_and_invalid_ballots_consume_nothing(self):
        self.use("spare_paper")
        self.rules.begin_night(2, self.bodies)
        self.assertEqual(self.rules.pending_ballot_bonus, 2)
        self.rules.open_vote("vote-2", self.voters, self.player.body_id, day=2)
        invalid = [
            {self.player.body_id: "NONE"},
            {**self.skip_ballots(), self.player.body_id: self.player.body_id},
            {**self.skip_ballots(), self.player.body_id: self.dead.body_id},
            {**self.skip_ballots(), self.player.body_id: [self.jerry.body_id]},
            {**self.skip_ballots(), "unknown": "NONE"},
        ]
        for ballots in invalid:
            with self.subTest(ballots=ballots), self.assertRaises(ItemRuleError):
                self.rules.resolve_vote("vote-2", ballots)
            self.assertEqual(self.rules.pending_ballot_bonus, 2)
        result = self.rules.resolve_vote("vote-2", self.skip_ballots())
        self.assertEqual(result.outcome, "skip")
        self.assertIsNone(result.executed_body_id)
        self.assertEqual(result.counts["NONE"], 5)
        self.assertEqual(self.rules.pending_ballot_bonus, 0)

    def test_vote_identity_conflicts_and_early_night_transition_are_rejected(self):
        first = self.rules.open_vote("vote-1", self.voters, self.player.body_id, day=1)
        self.assertIs(self.rules.open_vote("vote-1", reversed(self.voters), self.player.body_id, day=1), first)
        for operation in (
            lambda: self.rules.open_vote("vote-1", self.voters, self.jerry.body_id, day=1),
            lambda: self.rules.open_vote("vote-1", self.voters, self.player.body_id, day=2),
            lambda: self.rules.open_vote("vote-other", self.voters, self.player.body_id, day=1),
            lambda: self.rules.begin_night(2, self.bodies),
        ):
            with self.assertRaises(ItemRuleError):
                operation()
        self.rules.resolve_vote("vote-1", self.skip_ballots())
        changed = self.skip_ballots()
        changed[self.player.body_id] = self.jerry.body_id
        with self.assertRaises(ItemRuleError):
            self.rules.resolve_vote("vote-1", changed)

    def test_equal_counts_produce_a_tie_without_an_execution(self):
        self.rules.open_vote("vote-1", self.voters, self.player.body_id, day=1)
        result = self.rules.resolve_vote("vote-1", {
            self.player.body_id: self.jerry.body_id,
            self.jerry.body_id: self.dave.body_id,
            self.dave.body_id: self.player.body_id,
        })
        self.assertEqual(result.outcome, "tie")
        self.assertIsNone(result.executed_body_id)
        self.assertEqual(set(result.tied_targets), {body.body_id for body in self.voters})
        self.assertEqual(result.events, ())


if __name__ == "__main__":
    unittest.main()
