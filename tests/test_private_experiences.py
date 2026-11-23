"""Offline provenance and lifecycle checks for durable emotional experiences."""

from dataclasses import FrozenInstanceError, replace
import unittest

from ai_context import CurrentFeeling, FeelingKind
from love_potion import LoveDose, LovePotionHistory, PAST_HANGOVER, PAST_INFATUATION
from private_experiences import PrivateExperienceStage, PrivateExperienceStore


class PrivateExperienceTests(unittest.TestCase):
    def stage(self, **changes):
        values = dict(stage_id="episode:stage", round_number=2,
                      current_feeling=CurrentFeeling(FeelingKind.INFATUATION, "Alex"),
                      past_text="A remembered emotion.", subject_id="body-original")
        return PrivateExperienceStage(**(values | changes))

    def test_stage_projects_once_and_preserves_original_round_when_finished_same_day(self):
        stage = self.stage()
        started = PrivateExperienceStore().start(stage)
        before = started.project()
        self.assertEqual(before.experiences, ())
        self.assertEqual(before.current_feelings, (stage.current_feeling,))
        finished = started.finish(stage.stage_id, 2)
        after = finished.project()
        self.assertEqual(after.current_feelings, ())
        self.assertEqual([(record.round_number, record.kind, record.text, record.source)
                          for record in after.experiences], [(2, "experience", "A remembered emotion.", "self")])
        self.assertEqual(started.project(), before)
        with self.assertRaises(FrozenInstanceError):
            stage.past_text = "rewritten"

    def test_start_and_finish_replays_never_reopen_or_duplicate_an_experience(self):
        stage = self.stage()
        started = PrivateExperienceStore().start(stage)
        self.assertIs(started.start(stage), started)
        finished = started.finish(stage.stage_id, 3)
        self.assertIs(finished.finish(stage.stage_id, 3), finished)
        self.assertIs(finished.start(stage), finished)
        self.assertEqual(len(finished.project().experiences), 1)
        for change in (dict(round_number=1), dict(subject_id="other"), dict(past_text="different"),
                       dict(current_feeling=CurrentFeeling(FeelingKind.BITTERNESS, "Other"))):
            with self.subTest(change=change), self.assertRaises(ValueError):
                finished.start(self.stage(**change))
        with self.assertRaises(ValueError):
            finished.finish(stage.stage_id, 4)

    def test_invalid_input_does_not_modify_snapshots(self):
        for change in (dict(stage_id=""), dict(subject_id=" "), dict(round_number=True),
                       dict(round_number=0), dict(current_feeling="policy text"),
                       dict(past_text=""), dict(closed_round=1)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.stage(**change)
        store = PrivateExperienceStore().start(self.stage())
        for stage_id, day in (("missing", 3), ("episode:stage", 1), ("episode:stage", True)):
            with self.subTest(stage_id=stage_id, day=day), self.assertRaises(ValueError):
                store.finish(stage_id, day)
        self.assertEqual(store.stages, (self.stage(),))
        with self.assertRaises(ValueError):
            PrivateExperienceStore((self.stage(), self.stage()))

    def test_separate_caller_owned_stores_do_not_share_experiences(self):
        first = PrivateExperienceStore().start(self.stage()).finish("episode:stage", 4)
        second = PrivateExperienceStore()
        self.assertEqual(len(first.project().experiences), 1)
        self.assertEqual(second.project().experiences, ())
        self.assertEqual(first.project().experiences[0].round_number, 2)

    def test_recall_boundary_preserves_ledger_and_active_state_without_reviving_completed_stages(self):
        closed = self.stage(stage_id="closed", round_number=1, closed_round=2)
        active = self.stage(stage_id="active", round_number=2)
        store = PrivateExperienceStore((closed, active))
        forgotten = store.forget_recollections(3)
        self.assertEqual(forgotten.stages, store.stages)
        self.assertFalse(forgotten.project().experiences)
        self.assertEqual(forgotten.project().current_feelings, (active.current_feeling,))
        self.assertIs(forgotten.start(self.stage(stage_id="closed", round_number=1)), forgotten)
        ended = forgotten.finish("active", 4)
        self.assertEqual([record.round_number for record in ended.project().experiences], [3])
        self.assertEqual(len(store.project().experiences), 1)
        twice = ended.forget_recollections(4)
        self.assertFalse(twice.project().experiences)
        self.assertEqual(twice.stages, ended.stages)
        for day in (0, True, 2):
            with self.subTest(day=day), self.assertRaises(ValueError):
                forgotten.forget_recollections(day)


class LovePotionLifecycleTests(unittest.TestCase):
    def dose(self, dose_id="first", day=1, giver="Alex", body="original-body"):
        return LoveDose(dose_id, day, body, giver)

    def test_actual_three_stage_lifecycle_keeps_approved_recollections(self):
        history = LovePotionHistory().begin_night(1).begin_discussion(1, alive=True)
        history = history.apply(self.dose())
        self.assertEqual(history.experiences.project().current_feelings,
                         (CurrentFeeling(FeelingKind.INFATUATION, "Alex"),))
        self.assertEqual(history.experiences.project().experiences, ())
        night = history.begin_night(2)
        self.assertEqual(night.experiences.project().current_feelings, ())
        self.assertEqual(night.experiences.project().experiences[0].text,
                         PAST_INFATUATION.format(giver="Alex"))
        discussion = night.begin_discussion(2, alive=True)
        self.assertEqual(discussion.experiences.project().current_feelings,
                         (CurrentFeeling(FeelingKind.BITTERNESS, "Alex"),))
        complete = discussion.begin_night(3).begin_discussion(3, alive=True)
        memories = complete.experiences.project().experiences
        self.assertEqual([memory.round_number for memory in memories], [1, 2])
        self.assertEqual([memory.text for memory in memories],
                         [PAST_INFATUATION.format(giver="Alex"), PAST_HANGOVER.format(giver="Alex")])
        self.assertEqual(complete.experiences.project().current_feelings, ())

    def test_hangover_starts_only_in_an_actual_surviving_discussion(self):
        history = LovePotionHistory().apply(self.dose()).begin_night(2)
        self.assertEqual(len(history.experiences.stages), 1)
        self.assertEqual(history.experiences.project().current_feelings, ())
        dead = history.begin_discussion(2, alive=False).begin_night(3)
        self.assertEqual(len(dead.experiences.stages), 1)
        self.assertIsNone(dead.pending)
        delayed = history.begin_night(3).begin_discussion(3, alive=True)
        self.assertEqual([stage.round_number for stage in delayed.experiences.stages], [1, 3])

    def test_newest_dose_replaces_infatuation_without_erasing_actual_experience(self):
        old = self.dose()
        history = LovePotionHistory().apply(old)
        for giver, body in (("Alex", "original-body"), ("Dave", "new-body"), ("Alex", "changed-body")):
            with self.subTest(giver=giver, body=body):
                newer = self.dose("new", giver=giver, body=body)
                updated = history.apply(newer)
                self.assertEqual(updated.experiences.project().current_feelings,
                                 (CurrentFeeling(FeelingKind.INFATUATION, giver),))
                self.assertEqual([record.text for record in updated.experiences.project().experiences],
                                 [PAST_INFATUATION.format(giver="Alex")])
                self.assertEqual(updated.experiences.stages[0].closed_round, 1)
                tomorrow = updated.begin_night(2).begin_discussion(2, alive=True)
                self.assertEqual(tomorrow.experiences.project().current_feelings,
                                 (CurrentFeeling(FeelingKind.BITTERNESS, giver),))
                self.assertEqual(len(tomorrow.experiences.stages), 3)
                self.assertIs(updated.apply(old), updated)

    def test_new_dose_during_bitterness_starts_its_own_normal_sequence(self):
        history = LovePotionHistory().apply(self.dose()).begin_night(2).begin_discussion(2, alive=True)
        updated = history.apply(self.dose("second", 2, "Dave", "different-body"))
        memories = updated.experiences.project().experiences
        self.assertEqual([record.round_number for record in memories], [1, 2])
        self.assertEqual(memories[1].text, PAST_HANGOVER.format(giver="Alex"))
        self.assertEqual(updated.experiences.project().current_feelings[0].giver, "Dave")
        final = updated.begin_night(3).begin_discussion(3, alive=True).begin_night(4)
        self.assertEqual([record.round_number for record in final.experiences.project().experiences], [1, 2, 2, 3])
        self.assertEqual(final.experiences.project().current_feelings, ())

    def test_replays_are_noops_and_conflicting_or_old_events_cannot_rewind_history(self):
        dose = self.dose()
        history = LovePotionHistory().apply(dose).begin_night(2).begin_discussion(2, alive=True)
        self.assertIs(history.apply(dose), history)
        self.assertIs(history.begin_discussion(2, alive=True), history)
        self.assertIs(history.begin_night(2), history)
        with self.assertRaises(ValueError):
            history.apply(replace(dose, giver="Other"))
        with self.assertRaises(ValueError):
            history.apply(self.dose("stale"))
        with self.assertRaises(ValueError):
            history.begin_night(1)
        self.assertEqual(len(history.experiences.stages), 2)


if __name__ == "__main__":
    unittest.main()
