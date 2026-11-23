"""Deterministic, body-based night arrival scheduling without UI or item effects."""

from dataclasses import dataclass
import random


ARRIVAL_ROUTES = {
    "Attic": "headed upstairs towards the attic",
    "Kitchen": "went through the front door towards the kitchen",
    "Library": "went through the front door towards the library",
    "Basement": "went down the steps towards the basement",
    "Cellar": "went down the outside steps into the cellar",
}


def room_capacity(rooms, closed_rooms, guest_count):
    """Reopen only the oldest closures needed to hold at most two guests per room."""
    rooms = tuple(rooms)
    closed = tuple(dict.fromkeys(room for room in closed_rooms if room in rooms))
    if guest_count < 0 or guest_count > 2 * len(rooms):
        raise ValueError("Guest count exceeds house capacity")
    available = [room for room in rooms if room not in closed]
    needed = max(0, (guest_count + 1) // 2 - len(available))
    reopened = closed[:needed]
    return tuple(room for room in rooms if room in available or room in reopened), reopened


def assign_guest_rooms(body_ids, rooms, reservations=None, *, rng=None, first_night=False):
    """Apply caller-resolved reservations before random, capacity-bounded placements."""
    rng = rng if rng is not None else random
    guests, rooms = tuple(body_ids), tuple(rooms)
    if len(set(guests)) != len(guests) or not rooms or len(guests) > 2 * len(rooms):
        raise ValueError("Guest placement requires unique guests and sufficient rooms")
    reservations = dict(reservations or {})
    if set(reservations) - set(guests) or set(reservations.values()) - set(rooms):
        raise ValueError("Reservations must identify supplied guests and available rooms")
    assigned = dict(reservations)
    counts = {room: sum(value == room for value in assigned.values()) for room in rooms}
    if any(count > 2 for count in counts.values()):
        raise ValueError("Reservations exceed room capacity")
    single_pair = first_night and len(rooms) >= len(guests) - 1
    for body_id in guests:
        if body_id in assigned:
            continue
        candidates = [room for room in rooms if counts[room] < 2]
        if single_pair and 2 in counts.values():
            candidates = [room for room in candidates if counts[room] == 0]
        room = rng.choice(candidates)
        assigned[body_id] = room
        counts[room] += 1
    return assigned


def flight_destination(rooms, closed_rooms, bookings, living_body_ids, fleeing_body_id,
                       excluded_rooms, *, rng=None):
    """Reserve flight space against present guests and every still-pending booking."""
    rng = rng if rng is not None else random
    rooms, closed = tuple(rooms), tuple(dict.fromkeys(closed_rooms))
    living, excluded = set(living_body_ids), set(excluded_rooms)
    counts = {room: sum(body in living and body != fleeing_body_id and destination == room
                        for body, destination in bookings.items()) for room in rooms}
    candidates = [room for room in rooms if room not in closed and room not in excluded and counts[room] < 2]
    if candidates:
        return rng.choice(candidates), ()
    reopened = next((room for room in closed if room in rooms and room not in excluded and counts[room] < 2), None)
    if reopened is None:
        raise ValueError("No safe room has space for this fleeing guest")
    return reopened, (reopened,)


@dataclass(frozen=True)
class Arrival:
    event_id: str
    body_id: str
    room: str
    minute: int
    route: str

    @property
    def time(self):
        return f"{self.minute // 60:02d}:{self.minute % 60:02d}"


class NightArrivals:
    """One schedule with replay-safe waiting and a single irreversible room commitment."""

    def __init__(self, night, assignments, *, rng=None, initial_count=None):
        rng = rng if rng is not None else random
        if type(night) is not int or night < 1:
            raise ValueError("A night must be a positive integer")
        assignments = dict(assignments)
        if any(not body_id or room not in ARRIVAL_ROUTES for body_id, room in assignments.items()):
            raise ValueError("Arrivals require body identifiers and known rooms")
        if any(sum(value == room for value in assignments.values()) > 2 for room in ARRIVAL_ROUTES):
            raise ValueError("No room can receive more than two guests")
        order = list(assignments)
        rng.shuffle(order)
        if initial_count is None:
            initial_count = rng.randint(1, min(3, len(order))) if order else 0
        if type(initial_count) is not int or not 0 <= initial_count <= min(3, len(order)):
            raise ValueError("The initial batch must contain between one and three available guests")
        if order and not initial_count:
            raise ValueError("An occupied night must have an initial guest")
        minute = 19 * 60 + 30
        events = []
        for index, body_id in enumerate(order):
            if index:
                minute = max(minute, 19 * 60 + 45) if index == initial_count else minute
                minute += rng.randint(2, 7)
            room = assignments[body_id]
            events.append(Arrival(f"night:{night}:arrival:{index}", body_id, room, minute, ARRIVAL_ROUTES[room]))
        self.night = night
        self.events = tuple(events)
        self.destinations = dict(assignments)
        self.initial_count = initial_count
        self.cursor = initial_count
        self.committed_room = None
        self._wait_receipts = {}
        self.exhausted_observed = False
        self._clock = "19:45"

    @property
    def initial(self):
        return self.events[:self.initial_count]

    @property
    def arrived(self):
        return self.events[:self.cursor]

    @property
    def complete(self):
        return self.cursor == len(self.events)

    @property
    def clock(self):
        return self._clock

    def advance(self, request_id):
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("Waiting requires a stable request identifier")
        if request_id in self._wait_receipts:
            return self._wait_receipts[request_id]
        event = None
        if self.committed_room is None and not self.complete:
            event = self.events[self.cursor]
            self.cursor += 1
            self._clock = event.time
        elif self.committed_room is None:
            self.exhausted_observed = True
        self._wait_receipts[request_id] = event
        return event

    def commit(self, room):
        if room not in ARRIVAL_ROUTES:
            raise ValueError("A room commitment requires a known room")
        if self.committed_room is not None:
            return self.committed_room == room
        self.committed_room = room
        return True

    def finish(self):
        if self.committed_room is None:
            raise ValueError("Enter a room before resolving unseen arrivals")
        remaining = self.events[self.cursor:]
        self.cursor = len(self.events)
        if remaining:
            self._clock = remaining[-1].time
        return remaining
