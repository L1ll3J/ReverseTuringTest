"""Deterministic allocation and bounded evidence checks for NPC cameras."""

from dataclasses import FrozenInstanceError
import random
import unittest
from unittest.mock import Mock

from npc_items import NPCItemState


class NPCItemTests(unittest.TestCase):
    def test_allocation_can_leave_everyone_unequipped_or_choose_one_holder(self):
        rng = Mock()
        rng.random.return_value = 0.5
        self.assertEqual(dict(NPCItemState.allocate(("Jerry", "Hank"), rng).equipment), {})
        rng.choice.assert_not_called()
        rng.random.return_value = 0.49
        rng.choice.return_value = "Hank"
        self.assertEqual(dict(NPCItemState.allocate(("Jerry", "Hank"), rng).equipment), {"Hank": "camera"})

    def test_seeded_allocation_is_repeatable_capped_and_restricted_to_given_npcs(self):
        names = ("Jerry", "Hank", "Debra", "Hank")
        for seed in range(100):
            with self.subTest(seed=seed):
                first = NPCItemState.allocate(names, random.Random(seed))
                second = NPCItemState.allocate(names, random.Random(seed))
                self.assertEqual(dict(first.equipment), dict(second.equipment))
                self.assertLessEqual(len(first.equipment), 1)
                self.assertTrue(set(first.equipment).issubset(names))
        rng = Mock()
        self.assertEqual(dict(NPCItemState.allocate((), rng).equipment), {})
        rng.random.assert_not_called()

    def test_equipment_mapping_cannot_be_changed_or_exceed_the_camera_limit(self):
        source = {"Hank": "camera"}
        state = NPCItemState(source)
        source.clear()
        self.assertEqual(dict(state.equipment), {"Hank": "camera"})
        with self.assertRaises(TypeError):
            state.equipment["Jerry"] = "camera"
        with self.assertRaises(ValueError):
            NPCItemState({"Hank": "camera", "Jerry": "camera"})
        with self.assertRaises(ValueError):
            NPCItemState({"Hank": "unknown"})

    def test_later_equipment_display_does_not_claim_a_dead_owner_is_still_equipped(self):
        state = NPCItemState({"Hank": "camera"})
        self.assertIn("Hank is carrying a Camera", state.equipment_description(("Hank", "Jerry")))
        self.assertEqual(state.equipment_description(("Jerry",)),
                         "Known equipment: No special equipment is visible.")

    def test_photo_copies_scene_facts_and_does_not_change_after_death_or_possession(self):
        state = NPCItemState({"Hank": "camera"})
        rooms = {"Kitchen": ["Hank", "Jerry"], "Attic": ["Dave"]}
        living = ["Hank", "Jerry", "Dave"]
        photo, = state.capture_night(1, rooms, living)
        self.assertEqual((photo.round_number, photo.owner, photo.room), (1, "Hank", "Kitchen"))
        self.assertEqual(photo.occupants, ("Hank", "Jerry"))
        self.assertEqual(photo.capture_stage, "before_player_action")
        original = photo.public_description()
        rooms["Kitchen"][:] = ["Alex"]
        living.remove("Hank")
        self.assertEqual(state.capture_night(1, rooms, living), (photo,))
        self.assertEqual(photo.public_description(), original)
        with self.assertRaises(FrozenInstanceError):
            photo.owner = "Alex"
        self.assertEqual(state.capture_night(2, rooms, living), ())

    def test_photo_has_only_living_npcs_in_the_captured_room(self):
        state = NPCItemState({"Jerry": "camera"})
        photo, = state.capture_night(1, {
            "Library": ["Jerry", "Hank", "Ghost", "Alex"], "Kitchen": ["Dave"]
        }, ("Jerry", "Hank", "Dave"))
        self.assertEqual(photo.occupants, ("Hank", "Jerry"))
        self.assertNotIn("Ghost", photo.public_description())
        self.assertNotIn("Alex", photo.public_description())
        self.assertNotIn("Dave", photo.public_description())
        self.assertNotIn("dead rat", photo.public_description())

    def test_photographer_eligibility_does_not_remove_visible_human_subjects(self):
        state = NPCItemState({"Hank": "camera"})
        scene = {"Kitchen": ["Hank", "Alex"]}
        photo, = state.capture_night(1, scene, ("Hank", "Alex"), eligible_photographers=("Hank",))
        self.assertEqual(photo.owner, "Hank")
        self.assertEqual(photo.occupants, ("Alex", "Hank"))
        original = photo.public_description()
        self.assertEqual(state.capture_night(2, scene, ("Hank", "Alex"), eligible_photographers=()), ())
        self.assertEqual(photo.public_description(), original)

    def test_visible_flavour_is_bounded_and_separate_from_mechanical_scene(self):
        state = NPCItemState({"Hank": "camera"})
        photo, = state.capture_night(1, {"Kitchen": ["Hank"]}, ("Hank",))
        self.assertEqual(photo.occupants, ("Hank",))
        self.assertEqual(photo.flavour, (
            "A plain table is visible.",
            "Hank has a plate of steak and avocado with a garnish of parsley.",
        ))
        description = photo.public_description()
        self.assertIn("single still image", description)
        self.assertIn("shows no movements before or after", description)
        self.assertIn("No readable clock, date or writing", description)
        self.assertIn("No additional people, identifiable silhouettes or reflections", description)
        self.assertIn("Nothing else in the photograph can be distinguished", description)

    def test_presentation_occurs_once_and_does_not_require_a_living_owner(self):
        state = NPCItemState({"Hank": "camera"})
        published = []
        self.assertEqual(state.present_morning(1, published.append), ())
        photo, = state.capture_night(1, {"Kitchen": ["Hank"]}, ("Hank",))
        state.capture_night(2, {"Kitchen": []}, ())
        self.assertEqual(state.present_morning(1, published.append), (photo,))
        self.assertEqual(state.present_morning(1, published.append), ())
        self.assertEqual(state.present_morning(2, published.append), ())
        self.assertEqual(published, [photo.public_description()])

    def test_each_later_night_can_add_one_new_photo_with_a_living_holder(self):
        state = NPCItemState({"Debra": "camera"})
        first, = state.capture_night(1, {"Kitchen": ["Debra"]}, ("Debra",))
        second, = state.capture_night(2, {"Attic": ["Debra"]}, ("Debra",))
        published = []
        state.present_morning(1, published.append)
        state.present_morning(2, published.append)
        self.assertEqual(len(published), 2)
        self.assertEqual((first.room, second.room), ("Kitchen", "Attic"))
        self.assertIn("elaborate yoga pose", second.public_description())

    def test_failed_publication_can_be_retried_without_losing_the_photo(self):
        state = NPCItemState({"Hank": "camera"})
        photo, = state.capture_night(1, {"Kitchen": ["Hank"]}, ("Hank",))
        failing_sink = Mock(side_effect=RuntimeError("Not committed"))
        with self.assertRaises(RuntimeError):
            state.present_morning(1, failing_sink)
        published = []
        self.assertEqual(state.present_morning(1, published.append), (photo,))
        self.assertEqual(published, [photo.public_description()])

    def test_trace_projection_is_detached_and_distinguishes_pending_and_presented_photos(self):
        state = NPCItemState({"Hank": "camera"})
        state.capture_night(1, {"Kitchen": ["Hank", "Jerry"]}, ("Hank", "Jerry"))
        pending = state.trace_state()
        self.assertEqual(pending["equipment"], {"Hank": "camera"})
        self.assertEqual(pending["photos"][0]["room"], "Kitchen")
        self.assertEqual(pending["photos"][0]["occupants"], ("Hank", "Jerry"))
        self.assertEqual(pending["presented"], [])
        pending["equipment"].clear()
        pending["photos"][0]["room"] = "Attic"
        state.present_morning(1, lambda description: None)
        presented = state.trace_state()
        self.assertEqual(presented["equipment"], {"Hank": "camera"})
        self.assertEqual(presented["photos"][0]["room"], "Kitchen")
        self.assertEqual(presented["presented"], [{"round_number": 1, "owner": "Hank"}])

    def test_inconsistent_living_holder_assignment_is_rejected(self):
        for rooms in ({"Kitchen": []}, {"Kitchen": ["Hank"], "Attic": ["Hank"]}):
            with self.subTest(rooms=rooms), self.assertRaises(ValueError):
                NPCItemState({"Hank": "camera"}).capture_night(1, rooms, ("Hank",))


if __name__ == "__main__":
    unittest.main()
