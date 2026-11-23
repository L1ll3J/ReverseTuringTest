"""Pure arrival scheduling, room capacity and retry boundaries."""
import random
import unittest

from night_arrivals import NightArrivals, assign_guest_rooms, flight_destination, room_capacity
from npc_items import NPCItemState


class NightArrivalsTests(unittest.TestCase):
    def test_initial_guests_timestamps_and_stable_body_ids(self):
        placements = {f'body:{index}': room for index, room in enumerate(('Attic','Attic','Kitchen','Library','Cellar'))}
        first = NightArrivals(3, placements, rng=random.Random(12))
        repeat = NightArrivals(3, placements, rng=random.Random(12))
        self.assertEqual(first.events, repeat.events)
        self.assertIn(len(first.initial), (1, 2, 3))
        self.assertEqual(set(event.body_id for event in first.events), set(placements))
        self.assertEqual(first.events[0].time, '19:30')
        self.assertEqual(sorted(event.minute for event in first.events), [event.minute for event in first.events])
        self.assertEqual(len({event.event_id for event in first.events}), 5)

    def test_waiting_receipt_is_replayed_without_consuming_the_next_guest(self):
        state = NightArrivals(1, {'one':'Attic','two':'Library','three':'Kitchen'},
                              rng=random.Random(2), initial_count=1)
        event = state.advance('wait:1')
        self.assertEqual(state.cursor, 2)
        self.assertIs(state.advance('wait:1'), event)
        self.assertEqual(state.cursor, 2)
        self.assertEqual(state.clock, event.time)
        state.advance('wait:2')
        self.assertTrue(state.complete)
        self.assertIsNone(state.advance('wait:3'))

    def test_commit_stops_waiting_and_finish_is_once_only(self):
        state = NightArrivals(1, {'one':'Attic','two':'Kitchen'}, rng=random.Random(3), initial_count=1)
        with self.assertRaises(ValueError):
            state.finish()
        self.assertTrue(state.commit('Library'))
        self.assertFalse(state.commit('Attic'))
        self.assertIsNone(state.advance('late-click'))
        self.assertEqual(len(state.finish()), 1)
        self.assertTrue(state.complete)
        self.assertEqual(state.finish(), ())

    def test_initial_completion_is_not_an_observation_until_waiting_checks_it(self):
        state = NightArrivals(1, {'one': 'Attic'}, initial_count=1)
        self.assertTrue(state.complete)
        self.assertFalse(state.exhausted_observed)
        self.assertIsNone(state.advance('check-outside'))
        self.assertTrue(state.exhausted_observed)

    def test_camera_boundary_includes_committed_player_after_all_arrivals(self):
        state = NightArrivals(2, {'camera-body': 'Kitchen', 'guest-body': 'Kitchen'},
                              rng=random.Random(4), initial_count=1)
        state.commit('Kitchen')
        state.finish()
        labels = {'camera-body': 'Hank', 'guest-body': 'Dave', 'player-body': 'Alex'}
        assignments = {room: [] for room in ('Attic', 'Kitchen', 'Library', 'Basement', 'Cellar')}
        for event in state.arrived:
            assignments[event.room].append(labels[event.body_id])
        assignments[state.committed_room].append(labels['player-body'])
        cameras = NPCItemState({'Hank': 'camera'})
        identities = {label: body for body, label in labels.items()}
        photos = cameras.capture_night(state.night, assignments, labels.values(), body_ids=identities)
        self.assertEqual(len(photos), 1)
        photo = photos[0]
        self.assertEqual(photo.capture_stage, 'before_player_action')
        self.assertEqual(photo.room, 'Kitchen')
        self.assertEqual(set(photo.occupants), {'Hank', 'Dave', 'Alex'})
        self.assertEqual(set(photo.occupant_body_ids), set(labels))
        self.assertEqual(photo.owner_body_id, 'camera-body')
        assignments['Kitchen'].clear()
        self.assertIs(cameras.capture_night(state.night, assignments, (), body_ids=identities), photos)

    def test_capacity_reopens_only_minimum_oldest_rooms(self):
        rooms = ('Attic','Kitchen','Library','Basement','Cellar')
        available, reopened = room_capacity(rooms, ('Kitchen','Attic','Library','Basement'), 5)
        self.assertEqual(reopened, ('Kitchen','Attic'))
        self.assertEqual(available, ('Attic','Kitchen','Cellar'))
        self.assertEqual(room_capacity(rooms, ('Kitchen','Attic'), 5)[1], ())
        self.assertEqual(room_capacity(rooms, rooms, 1)[1], ('Attic',))

    def test_flight_reserves_capacity_for_guests_who_have_not_arrived(self):
        rooms = ('Attic', 'Kitchen', 'Library', 'Basement', 'Cellar')
        bookings = {'flee':'Attic', 'here':'Kitchen', 'future1':'Kitchen',
                    'future2':'Basement', 'future3':'Basement'}
        room, reopened = flight_destination(rooms, ('Library',), bookings,
                                            bookings, 'flee', {'Attic'}, rng=random.Random(1))
        self.assertEqual((room, reopened), ('Cellar', ()))
        room, reopened = flight_destination(rooms, ('Library','Cellar'), bookings,
                                            bookings, 'flee', {'Attic'}, rng=random.Random(1))
        self.assertEqual((room, reopened), ('Library', ('Library',)))
        room, reopened = flight_destination(rooms, ('Library','Cellar'), bookings,
                                            bookings, 'flee', {'Attic','Library'}, rng=random.Random(1))
        self.assertEqual((room, reopened), ('Cellar', ('Cellar',)))

    def test_reservations_are_honored_with_two_guest_limit_and_capacity_pressure(self):
        guests = ('body:1','body:2','body:3','body:4','body:5')
        rooms = ('Attic','Library','Kitchen')
        assigned = assign_guest_rooms(guests, rooms, {'body:1':'Library','body:2':'Library'},
                                      rng=random.Random(8), first_night=True)
        self.assertEqual(assigned['body:1'], 'Library')
        self.assertEqual(assigned['body:2'], 'Library')
        self.assertTrue(all(sum(value == room for value in assigned.values()) <= 2 for room in rooms))
        with self.assertRaises(ValueError):
            assign_guest_rooms(guests, ('Attic',))
        with self.assertRaises(ValueError):
            assign_guest_rooms(guests, rooms, {key:'Attic' for key in guests[:3]})
