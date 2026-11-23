"""Replay-safe physical custody observations and immutable completed night facts."""
from dataclasses import FrozenInstanceError, replace
import unittest
from unittest.mock import Mock

from game_rules import BodyState
from identity_state import IdentityState
from weapon_aftermath import WeaponAftermath, NightFacts, OBJECT_DESCRIPTIONS


class WeaponAftermathTests(unittest.TestCase):
    def setUp(self):
        self.rng = Mock()
        self.rng.randint.return_value = 2
        self.state = WeaponAftermath(self.rng)
        self.identity = IdentityState.from_bodies("player", "p", ("n",))
        self.bodies = (BodyState("p", "Alex", True, False, mind_id="player"),
                       BodyState("n", "Dave", True, True))
        self.weapons = {"n": {"id": "knife"}}

    def test_ditch_is_once_per_run_replay_safe_and_uses_actual_room(self):
        self.state.visit("entry", 1, "Attic")
        self.state.visit("knife-entry", 1, "Kitchen")
        drop = self.state.ditch("ditch", 1, "p", "knife", description=OBJECT_DESCRIPTIONS["knife"])
        self.assertEqual(drop.room, "Kitchen")
        self.state.visit("later", 2, "Library")
        self.assertIs(self.state.ditch("ditch", 1, "p", "knife", description=drop.description), drop)
        for weapon in ("pen", "knife", "c4", "stopwatch", "voodoo"):
            self.assertFalse(self.state.can_ditch(weapon))
        with self.assertRaises(ValueError):
            self.state.ditch("second", 2, "p", "pen")
        with self.assertRaises(FrozenInstanceError):
            drop.room = "Cellar"

    def test_only_physical_weapons_and_committed_rooms_can_be_ditched(self):
        for weapon in ("bare", "voodoo", "love", "blackmagic"):
            self.assertFalse(self.state.can_ditch(weapon))
        self.assertTrue(self.state.can_ditch("stopwatch"))
        with self.assertRaises(ValueError):
            self.state.ditch("ditch", 1, "p", "stopwatch")
        self.assertIsNone(self.state.drop)

    def test_old_visit_replay_cannot_move_player_back_from_later_physical_entry(self):
        self.state.visit("first", 1, "Attic")
        self.state.visit("second", 1, "Kitchen")
        self.state.visit("first", 1, "Attic")
        self.assertEqual(self.state.current_rooms[1], "Kitchen")
        with self.assertRaises(ValueError):
            self.state.visit("first", 1, "Cellar")

    def test_night_facts_are_immutable_and_cannot_be_replaced_by_new_custody(self):
        facts = NightFacts(1, "pen", "Attic", "Attic", "A note.", "Dave", True)
        self.assertIs(self.state.freeze(facts), facts)
        self.assertEqual(self.state.freeze(facts), facts)
        with self.assertRaises(ValueError):
            self.state.freeze(replace(facts, weapon_id="bare"))
        with self.assertRaises(FrozenInstanceError):
            facts.pen_note = "Altered."

    def test_pocket_delay_is_randomized_once_and_counts_only_unique_committed_turns(self):
        ticket = self.state.schedule_pocket("exchange", "n", "n", "knife", description=OBJECT_DESCRIPTIONS["knife"])
        self.assertEqual(ticket.due_turn, 2)
        self.rng.randint.assert_called_once_with(2, 8)
        self.assertIs(self.state.schedule_pocket("exchange", "n", "n", "knife"), ticket)
        self.rng.randint.assert_called_once()
        self.state.spoken("turn1")
        self.state.spoken("turn1")
        self.assertEqual(self.state.due_pockets(self.identity, self.bodies, self.weapons), ())
        self.state.spoken("turn2")
        self.assertEqual(self.state.due_pockets(self.identity, self.bodies, self.weapons), (ticket,))
        self.state.published(ticket.receipt_id)
        self.assertEqual(self.state.due_pockets(self.identity, self.bodies, self.weapons), ())

    def test_moved_mind_dead_body_or_removed_object_never_receive_future_memory(self):
        for kind in ("mind", "death", "weapon"):
            with self.subTest(kind=kind):
                state = WeaponAftermath(self.rng)
                state.schedule_pocket("exchange", "n", "n", "knife")
                identity, bodies, weapons = self.identity, self.bodies, self.weapons
                if kind == "mind":
                    identity = identity.exchange("n")
                elif kind == "death":
                    bodies = (self.bodies[0], replace(self.bodies[1], alive=False))
                else:
                    weapons = {}
                self.assertEqual(state.due_pockets(identity, bodies, weapons), ())
                state.spoken("1"); state.spoken("2")
                self.assertEqual(state.due_pockets(self.identity, self.bodies, self.weapons), ())

    def test_voodoo_never_creates_a_pocket_ticket(self):
        self.assertIsNone(self.state.schedule_pocket("v", "n", "n", "voodoo"))
        self.rng.randint.assert_not_called()
