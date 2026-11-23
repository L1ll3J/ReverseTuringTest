"""Immutable weapon aftermath and replay-safe delayed observations, without UI."""

from dataclasses import dataclass
import random
from types import MappingProxyType


PHYSICAL_WEAPONS = frozenset({"pen", "knife", "c4", "stopwatch"})
OBJECT_NAMES = MappingProxyType({"pen": "fountain pen", "knife": "kitchen knife",
                                 "c4": "improvised plastic explosive", "stopwatch": "silver stopwatch"})
OBJECT_DESCRIPTIONS = MappingProxyType({
    "pen": "It is a fountain pen with a dark barrel and a metal nib. There is blood on it.",
    "knife": "It is a kitchen knife with a pointed steel blade. There is blood on it.",
    "c4": "It is an improvised plastic explosive, wrapped in tape with wires running between its parts.",
    "stopwatch": "It is a silver stopwatch on a golden chain. Its steady rhythm is deeply calming to watch.",
})
PUBLIC_FINDING = "During this morning's search of the {room}, a {object_name} was found. {description}"
PRIVATE_FINDING = "You reach into your pocket and find a {object_name}. {description}"


@dataclass(frozen=True)
class NightFacts:
    night: int
    weapon_id: str
    room: str | None
    sleep_room: str | None
    pen_note: str | None = None
    pen_signature: str | None = None
    bloodlust: bool = False


@dataclass(frozen=True)
class DroppedWeapon:
    receipt_id: str
    night: int
    body_id: str
    room: str
    weapon_id: str
    description: str | None


@dataclass(frozen=True)
class PocketDiscovery:
    receipt_id: str
    body_id: str
    mind_id: str
    weapon_id: str
    due_turn: int
    description: str | None


class WeaponAftermath:
    def __init__(self, rng=None):
        self.rng = rng if rng is not None else random
        self.nights = {}
        self.current_rooms = {}
        self._visits = {}
        self.decided_nights = set()
        self.drop = None
        self.turn = 0
        self._spoken = set()
        self._pockets = {}
        self._published = set()

    def visit(self, receipt_id, night, room):
        request = night, room
        if receipt_id in self._visits:
            if self._visits[receipt_id] != request:
                raise ValueError("This room receipt already identifies another visit")
            return
        self._visits[receipt_id] = request
        self.current_rooms[night] = room

    def freeze(self, facts):
        previous = self.nights.get(facts.night)
        if previous is not None and previous != facts:
            raise ValueError("Committed night aftermath cannot be replaced")
        self.nights[facts.night] = facts
        return facts

    def can_ditch(self, weapon_id):
        return self.drop is None and weapon_id in PHYSICAL_WEAPONS

    def ditch(self, receipt_id, night, body_id, weapon_id, *, description=None):
        room = self.current_rooms.get(night)
        request = receipt_id, night, body_id, weapon_id, description
        if self.drop is not None:
            if request != (self.drop.receipt_id, self.drop.night, self.drop.body_id,
                           self.drop.weapon_id, self.drop.description):
                raise ValueError("The run's weapon ditch has already been used")
            return self.drop
        if not self.can_ditch(weapon_id) or not room:
            raise ValueError("Ditching requires an ordinary held weapon and a committed room")
        self.drop = DroppedWeapon(receipt_id, night, body_id, room, weapon_id, description)
        return self.drop

    def schedule_pocket(self, receipt_id, body_id, mind_id, weapon_id, *, description=None):
        if weapon_id not in PHYSICAL_WEAPONS:
            return None
        existing = self._pockets.get(receipt_id)
        if existing is not None:
            if (existing.body_id, existing.mind_id, existing.weapon_id) != (body_id, mind_id, weapon_id):
                raise ValueError("This pocket receipt already identifies another possession")
            return existing
        ticket = PocketDiscovery(receipt_id, body_id, mind_id, weapon_id,
                                 self.turn + self.rng.randint(2, 8), description)
        self._pockets[receipt_id] = ticket
        return ticket

    def spoken(self, receipt_id):
        if receipt_id not in self._spoken:
            self._spoken.add(receipt_id)
            self.turn += 1

    def due_pockets(self, identity, bodies, body_weapons):
        living = {body.body_id for body in bodies if body.alive and body.is_npc}
        due = []
        for ticket in self._pockets.values():
            if ticket.receipt_id in self._published:
                continue
            weapon = body_weapons.get(ticket.body_id)
            if (ticket.body_id not in living or identity.mind_for_body(ticket.body_id) != ticket.mind_id
                    or weapon is None or weapon["id"] != ticket.weapon_id):
                self._published.add(ticket.receipt_id)
                continue
            if ticket.due_turn <= self.turn:
                due.append(ticket)
        return tuple(due)

    def is_published(self, receipt_id):
        return receipt_id in self._published

    def published(self, receipt_id):
        self._published.add(receipt_id)
