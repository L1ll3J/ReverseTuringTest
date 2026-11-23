"""Offline production arrival ordering, private observations and settled camera facts."""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from test_item_integration import ItemIntegrationTests
import main as game
from items import DraftState
from night_arrivals import NightArrivals
from npc_items import NPCItemState


class ArrivalRng:
    def shuffle(self, values):
        pass

    def randint(self, low, high):
        return low


class NightArrivalIntegrationTests(unittest.TestCase):
    setUpClass = ItemIntegrationTests.__dict__['setUpClass']
    setUp = ItemIntegrationTests.setUp
    tearDown = ItemIntegrationTests.tearDown
    weapon = ItemIntegrationTests.weapon
    conversation = ItemIntegrationTests.conversation
    arm_optional = ItemIntegrationTests.arm_optional

    def open_night(self, *, initial_count=1, normal=True, plan=None, actual_assignment=False):
        self.window.show_room_allocations = normal
        plan = plan or {'Hank':'Kitchen', 'Dave':'Kitchen', 'Jerry':'Attic', 'Debra':'Library', 'Owen':'Cellar'}
        placements = {self.window.body_id(name): room for name, room in plan.items()}
        self.window.selected_weapon = self.weapon()
        assignment_patch = ({'wraps': game.assign_guest_rooms} if actual_assignment else {'return_value': placements})
        with patch.object(game, 'assign_guest_rooms', **assignment_patch), patch.object(
                game, 'NightArrivals', side_effect=lambda night, assigned: NightArrivals(
                    night, assigned, rng=ArrivalRng(), initial_count=initial_count)):
            if self.window.phase == "weapon_select":
                self.window.select_weapon_and_start(self.weapon())
            else:
                self.window.weapon_scroll.hide()
                self.window.run_action_phase()
        self.window.item_drafts = DraftState(self.window.player_owner_id, ('banana','muffle','spare_paper'))
        self.window.show()
        self.app.processEvents()
        return self.window.night_arrivals

    def test_normal_initial_subset_and_hard_unknown_projection_without_count_leak(self):
        state = self.open_night(initial_count=2)
        self.assertEqual(sum(map(len, self.window.room_assignments.values())), 2)
        self.assertEqual(self.window.house_view.observations['kitchen'].known_names, ('Hank','Dave'))
        self.assertEqual(self.window.arrival_clock.text(), '19:45')
        self.assertFalse(self.window.npc_items._photos_by_night)
        self.window.show_room_allocations = False
        state = self.open_night(initial_count=1, normal=False)
        self.assertEqual(self.window.arrival_clock.text(), '19:45')
        self.assertTrue(all(not fact.known_names and fact.unknown_remainder
                            for fact in self.window.house_view.observations.values()))
        self.assertNotIn('Hank', '\n'.join(record.text for record in self.window.history_records
                                          if record.round_number == state.night))
        self.assertNotIn('1', self.window.arrival_status.text())

    def test_loiter_advances_clock_reveals_one_guest_and_private_observation_only_once(self):
        state = self.open_night(normal=False)
        cursor = state.cursor
        event = state.events[cursor]
        self.window.loiter_button.click()
        self.assertEqual(state.cursor, cursor + 1)
        self.assertEqual(self.window.arrival_clock.text(), event.time)
        self.assertEqual(self.window.house_view.observations['kitchen'].known_names, ('Dave',))
        memories = self.window.characters['Dave']['experiences']
        expected = f'At {event.time}, you saw Alex outside the house as you went through the front door towards the kitchen. They glanced towards you.'
        self.assertEqual([record.text for record in memories], [expected])
        self.window.loiter_outside(state.night, cursor)
        self.assertEqual(state.cursor, cursor + 1)
        self.assertEqual([record.text for record in memories], [expected])
        self.assertFalse(self.window.characters['Hank']['experiences'])
        self.assertFalse(self.window.characters['Jerry']['experiences'])

    def test_room_commits_before_rewards_and_camera_contains_final_human_room_before_attack(self):
        self.window.npc_items = NPCItemState({'Hank':'camera'})
        state = self.open_night(normal=False)
        self.window.choose_room('Kitchen')
        self.assertEqual(state.committed_room, 'Kitchen')
        self.assertTrue(state.complete)
        self.assertIsNotNone(self.window.pending_room_action)
        self.assertEqual(self.window.night_attacks, [])
        photo, = self.window.npc_items._photos_by_night[state.night]
        self.assertEqual(set(photo.occupants), {'Alex','Dave','Hank'})
        self.assertEqual(set(photo.occupant_body_ids), {self.window.body_id(name) for name in photo.occupants})
        self.assertEqual(photo.capture_stage, 'before_player_action')
        self.assertTrue(self.window.house_view.observations['attic'].unknown_remainder)
        self.assertFalse(self.window.house_view.observations['attic'].known_names)
        transcript = '\n'.join(record.text for record in self.window.history_records)
        self.assertNotIn('Jerry entered', transcript)
        self.window.loiter_outside(state.night, 1)
        self.assertFalse(self.window.characters['Jerry']['experiences'])
        self.window.draft_page.leave_button.click()
        self.assertIs(self.window.npc_items._photos_by_night[state.night][0], photo)
        self.assertEqual(self.window.night_attacks, [])

    def test_solo_trap_then_flight_tells_only_actual_copresent_guest_and_camera_settles(self):
        self.conversation()
        self.arm_optional('booby_trap', room='Attic')
        self.window.npc_items = NPCItemState({'Hank':'camera'})
        state = self.open_night(initial_count=2, normal=False, plan={
            'Dave':'Attic', 'Hank':'Kitchen', 'Jerry':'Attic', 'Debra':'Library', 'Owen':'Cellar'})
        self.assertFalse(self.window.characters['Dave']['alive'])
        self.assertNotIn('Dave', '\n'.join(record.text for record in self.window.history_records
                                          if record.round_number == state.night))
        with patch.object(game, 'flight_destination', return_value=('Kitchen', ())):
            self.window.choose_room('Kitchen')
        self.assertTrue(self.window.characters['Jerry']['alive'])
        self.assertEqual(len(self.window.item_deaths), 1)
        self.assertEqual(self.window.room_assignments['Attic'], ['Dave'])
        self.assertEqual(self.window.room_assignments['Kitchen'], ['Hank','Jerry'])
        discoverer = self.window.characters['Jerry']['experiences']
        recipient = self.window.characters['Hank']['experiences']
        self.assertEqual(len(discoverer), 1)
        self.assertEqual(discoverer[0].source, 'eyewitness')
        self.assertIn('you recognised Dave; their body had been torn apart.', discoverer[0].text)
        self.assertIn('hurried to the Kitchen.', discoverer[0].text)
        self.assertEqual(len(recipient), 1)
        self.assertEqual(recipient[0].source, 'hearsay')
        self.assertIn('Jerry told you what they had found in the Attic.', recipient[0].text)
        self.assertFalse(self.window.characters['Debra']['experiences'])
        self.assertFalse(self.window.characters['Owen']['experiences'])
        photo, = self.window.npc_items._photos_by_night[state.night]
        self.assertEqual(set(photo.occupants), {'Alex','Hank','Jerry'})
        self.assertNotIn('Dave', photo.occupants)
        self.assertFalse(self.window.house_view.observations['attic'].known_names)
        event = state.events[2]
        before = (len(discoverer), len(recipient))
        self.window.apply_arrival(event)
        self.assertEqual((len(discoverer), len(recipient)), before)
        captured_mind = self.window.mind_id('Hank')
        captured_memories = self.window.mind_data[captured_mind]['experiences']
        self.window.exchange_player_mind(self.window.body_id('Hank'))
        self.assertIs(self.window.characters['Alex']['experiences'], captured_memories)
        self.assertIs(captured_memories[0], recipient[0])

    def test_capacity_pressure_reopens_oldest_minimum_before_reservations(self):
        self.window.old_crime_scenes = ['Kitchen','Attic','Basement','Library']
        state = self.open_night(actual_assignment=True)
        self.assertTrue(all(room in self.window.night_rooms for room in state.destinations.values()))
        self.assertTrue(all(sum(value == room for value in state.destinations.values()) <= 2
                            for room in self.window.night_rooms))
        self.assertEqual(self.window.old_crime_scenes, ['Basement','Library'])
        self.assertEqual(self.window.night_rooms, ('Attic','Kitchen','Cellar'))
        self.assertTrue(self.window.room_buttons['Kitchen'].isEnabled())
        self.assertFalse(self.window.room_buttons['Basement'].isEnabled())

    def test_reservation_follows_its_mind_after_a_body_exchange(self):
        self.conversation()
        old_player_body = self.window.body_id('Alex')
        old_dave_body = self.window.body_id('Dave')
        entry = self.window.inventory.grant('room_reservation')
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, old_dave_body, room_id='Attic'))
        self.window.exchange_player_mind(old_dave_body)
        self.window.selected_weapon = self.weapon()
        with patch.object(game, 'NightArrivals', side_effect=lambda night, assigned: NightArrivals(
                night, assigned, rng=ArrivalRng(), initial_count=1)):
            self.window.run_action_phase()
        self.assertEqual(self.window.night_arrivals.destinations[old_player_body], 'Attic')
        self.assertNotIn(old_dave_body, self.window.night_arrivals.destinations)
        self.assertIn('Alex', self.window.room_assignments['Attic'])

    def test_due_curse_resolves_before_any_arrival_is_scheduled(self):
        self.conversation()
        self.arm_optional('occult_ritual', target='Dave')
        target = self.window.body_id('Dave')
        self.window.round_number = 2
        state = self.open_night(actual_assignment=True)
        self.assertFalse(self.window.characters['Dave']['alive'])
        self.assertNotIn(target, state.destinations)
        self.assertFalse(any(event.body_id == target for event in state.events))
        self.assertEqual(self.window.night_attacks, [])

    def test_waiting_controls_remain_visible_in_sidebar_at_small_and_large_sizes(self):
        self.open_night(normal=False)
        for width, height in ((800, 600), (1280, 800), (2560, 1440)):
            self.window.resize(width, height)
            self.window.position_corner_controls()
            self.app.processEvents()
            viewport = self.window.house_sidebar.viewport()
            control = self.window.loiter_button
            point = control.mapTo(viewport, control.rect().topLeft())
            self.assertGreaterEqual(point.x(), 0)
            self.assertGreaterEqual(point.y(), 0)
            self.assertLessEqual(point.x() + control.width(), viewport.width())
            self.assertLessEqual(point.y() + control.height(), viewport.height())
            self.assertGreaterEqual(control.height(), 44)
            self.assertTrue(control.isEnabled())

    def test_bonus_room_choice_never_restarts_arrivals_or_creates_another_reward(self):
        state = self.open_night()
        self.assertTrue(self.window.prepare_room_entry('Kitchen'))
        cursor = state.cursor
        self.window.bonus_action_pending = True
        with patch.object(self.window, 'offer_room_item') as reward, patch.object(self.window, 'finish_room_choice') as finish:
            self.window.choose_room('Library')
        reward.assert_not_called()
        finish.assert_called_once_with('Library')
        self.assertEqual(state.cursor, cursor)
        self.assertEqual(sum(map(len, self.window.room_assignments.values())), 5)


del ItemIntegrationTests
