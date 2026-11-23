"""Conversation policy and bounded slot contracts without Qt or live requests."""

from dataclasses import replace
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_context import CharacterContext, DialogueLine, MemoryRecord, PublicState, SpeechProposal
from conversation_flow import DirectReplyPolicy, GroupTurnSnapshot, TurnPreparation


def snapshot():
    state = PublicState(1, ("Alex", "Dave"), (), ("Attic",))
    dialogue = (DialogueLine(1, "Dave", "My account."),)
    context = CharacterContext("Dave", (), state, (), (), (), dialogue)
    return GroupTurnSnapshot(1, 1, "Alex", (context,), state, (), dialogue, (),
                             (("Alex", "player-body"), ("Dave", "dave-body")), (), ())


class DirectReplyPolicyTests(unittest.TestCase):
    def test_only_successful_commits_count_and_fourth_direct_reply_is_rejected(self):
        policy = DirectReplyPolicy()
        for count in range(3):
            self.assertTrue(policy.can_direct("dave-body"))
            self.assertTrue(policy.can_direct("hank-body"))
            self.assertEqual(policy.count_for("dave-body"), count)
            policy.committed("dave-body", direct=True)
        self.assertFalse(policy.can_direct("dave-body"))
        with self.assertRaises(ValueError):
            policy.committed("dave-body", direct=True)
        self.assertEqual(policy.count_for("dave-body"), 3)

    def test_same_speaker_group_line_and_forgery_do_not_reset_streak(self):
        policy = DirectReplyPolicy()
        policy.committed("dave-body", direct=True)
        policy.committed("dave-body")
        policy.committed("hank-body", ordinary=False)
        self.assertEqual(policy.count_for("dave-body"), 1)
        policy.committed("hank-body")
        self.assertEqual(policy.count_for("dave-body"), 0)
        policy.committed("hank-body", direct=True)
        self.assertEqual(policy.count_for("hank-body"), 1)
        policy.begin_discussion("next")
        self.assertEqual(policy.count_for("hank-body"), 0)

    def test_reentering_same_discussion_does_not_reset(self):
        policy = DirectReplyPolicy()
        policy.begin_discussion(1)
        policy.committed("dave-body", direct=True)
        policy.begin_discussion(1)
        self.assertEqual(policy.count_for("dave-body"), 1)
        policy.begin_discussion(2)
        self.assertEqual(policy.count_for("dave-body"), 0)


class TurnPreparationTests(unittest.TestCase):
    def test_slot_held_until_both_stage_workers_finish_even_after_invalidation(self):
        state = TurnPreparation()
        original = snapshot()
        ticket = state.reserve(original)
        state.worker_started(ticket)
        state.worker_started(ticket)
        state.invalidate()
        self.assertTrue(ticket.cancelled.is_set())
        self.assertIsNone(state.reserve(original))
        self.assertFalse(state.worker_finished(ticket))
        self.assertIsNone(state.reserve(original))
        self.assertTrue(state.worker_finished(ticket))
        self.assertIsNotNone(state.reserve(original))

    def test_prepared_reply_stays_private_until_taken_and_cannot_be_taken_twice(self):
        state = TurnPreparation()
        original = snapshot()
        ticket = state.reserve(original)
        state.worker_started(ticket)
        proposal = SpeechProposal(None, "A prepared account.")
        self.assertTrue(state.publish(ticket, original, "Dave", proposal))
        state.worker_finished(ticket)
        self.assertIsNone(state.reserve(original))
        self.assertIsNone(state.take(replace(original, revision=2)))
        prepared = state.take(original)
        self.assertEqual((prepared.speaker, prepared.proposal), ("Dave", proposal))
        self.assertIsNone(state.take(original))

    def test_changed_memory_effect_or_identity_cannot_publish(self):
        original = snapshot()
        memory = replace(original.contexts[0], experiences=(MemoryRecord(1, "experience", "New fact"),))
        changes = (replace(original, contexts=(memory,)), replace(original, effects=("new-effect",)),
                   replace(original, identities=(("Dave", "another-body"),)))
        for changed in changes:
            with self.subTest(changed=changed):
                state = TurnPreparation()
                ticket = state.reserve(original)
                self.assertFalse(state.publish(ticket, changed, "Dave", SpeechProposal(None, "Old")))
                self.assertIsNone(state.ready)

    def test_cancelled_pipeline_cannot_cache_failure_or_result(self):
        state = TurnPreparation()
        original = snapshot()
        ticket = state.reserve(original)
        state.invalidate()
        self.assertFalse(state.fail(ticket, original, "Old failure"))
        self.assertFalse(state.publish(ticket, original, "Dave", SpeechProposal(None, "Old")))
        self.assertIsNone(state.failure)
        self.assertIsNone(state.ready)


if __name__ == "__main__":
    unittest.main()
