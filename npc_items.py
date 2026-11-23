"""Run-local NPC equipment and immutable, bounded photographic evidence."""

from dataclasses import asdict, dataclass, field
import random
from types import MappingProxyType
from typing import Callable, Iterable, Mapping


@dataclass(frozen=True)
class NPCItemDefinition:
    item_id: str
    name: str
    allocation_chance: float
    max_per_run: int


NPC_ITEM_REGISTRY = MappingProxyType({
    "camera": NPCItemDefinition("camera", "Camera", 0.5, 1),
})

ROOM_DETAILS = MappingProxyType({
    "Attic": "A wooden storage chest is visible.",
    "Kitchen": "A plain table is visible.",
    "Library": "Closed books line a shelf; their titles cannot be read.",
    "Basement": "A bare wooden bench is visible.",
    "Cellar": "A row of empty shelves is visible.",
})

OWNER_DETAILS = MappingProxyType({
    "Hank": "{owner} has a plate of steak and avocado with a garnish of parsley.",
    "Debra": "{owner} holds an elaborate yoga pose.",
})


@dataclass(frozen=True)
class CameraSnapshot:
    round_number: int
    owner: str
    room: str
    occupants: tuple[str, ...]
    flavour: tuple[str, ...]
    capture_stage: str = field(default="before_player_action", init=False)
    owner_body_id: str | None = None
    occupant_body_ids: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "occupants", tuple(self.occupants))
        object.__setattr__(self, "flavour", tuple(self.flavour))
        object.__setattr__(self, "occupant_body_ids", tuple(self.occupant_body_ids))

    def public_description(self) -> str:
        people = " and ".join(self.occupants)
        scene = " ".join(self.flavour)
        return (
            f"The Detective presents a genuine photograph taken by {self.owner} "
            f"during night {self.round_number}. Visible room: {self.room}. "
            f"Visible people: {people}. {scene} "
            "It is a single still image of that moment; it shows no movements "
            "before or after. No readable clock, date or writing is visible. "
            "No additional people, identifiable silhouettes or reflections are visible. "
            "Nothing else in the photograph can be distinguished."
        )


class NPCItemState:
    def __init__(self, equipment: Mapping[str, str] | None = None):
        equipment = dict(equipment or {})
        for item_id in equipment.values():
            if item_id not in NPC_ITEM_REGISTRY:
                raise ValueError("Unknown NPC equipment")
        for definition in NPC_ITEM_REGISTRY.values():
            if sum(item == definition.item_id for item in equipment.values()) > definition.max_per_run:
                raise ValueError("NPC equipment exceeds its run limit")
        self.equipment = MappingProxyType(equipment)
        self._photos_by_night: dict[int, tuple[CameraSnapshot, ...]] = {}
        self._presented: set[tuple[int, str]] = set()

    def trace_state(self):
        """Return a detached developer projection, never character context."""
        return {
            "equipment": dict(self.equipment),
            "photos": [asdict(photo) for night in sorted(self._photos_by_night)
                       for photo in self._photos_by_night[night]],
            "presented": [{"round_number": night, "owner": owner}
                          for night, owner in sorted(self._presented)],
        }

    @classmethod
    def allocate(cls, living_npcs: Iterable[str], rng=None):
        rng = rng if rng is not None else random
        available = list(dict.fromkeys(living_npcs))
        equipment = {}
        for definition in NPC_ITEM_REGISTRY.values():
            if not available or rng.random() >= definition.allocation_chance:
                continue
            for _ in range(min(definition.max_per_run, len(available))):
                owner = rng.choice(available)
                available.remove(owner)
                equipment[owner] = definition.item_id
        return cls(equipment)

    def equipment_description(self, living_npcs: Iterable[str] | None = None, *,
                              appearance_names: Mapping[str, str] | None = None) -> str:
        living = set(living_npcs) if living_npcs is not None else None
        descriptions = [
            f"{(appearance_names or {}).get(owner, owner)} is carrying a {NPC_ITEM_REGISTRY[item_id].name}."
            for owner, item_id in self.equipment.items()
            if living is None or owner in living
        ]
        if not descriptions:
            return "Known equipment: No special equipment is visible."
        return "Known equipment: " + " ".join(descriptions)

    def capture_night(
        self,
        round_number: int,
        room_assignments: Mapping[str, Iterable[str]],
        living_npcs: Iterable[str],
        *,
        appearance_names: Mapping[str, str] | None = None,
        body_ids: Mapping[str, str] | None = None,
    ) -> tuple[CameraSnapshot, ...]:
        if round_number in self._photos_by_night:
            return self._photos_by_night[round_number]
        living = set(living_npcs)
        names = dict(appearance_names or {})
        identifiers = dict(body_ids or {})
        rooms = {room: tuple(occupants) for room, occupants in room_assignments.items()}
        photos = []
        for owner, item_id in self.equipment.items():
            if item_id != "camera" or owner not in living:
                continue
            locations = [room for room, occupants in rooms.items() if owner in occupants]
            if len(locations) != 1:
                raise ValueError("A living camera holder must occupy exactly one room")
            room = locations[0]
            occupants = tuple(sorted(set(rooms[room]) & living))
            canonical_owner = names.get(owner, owner)
            owner_detail = OWNER_DETAILS.get(owner)
            flavour = tuple(detail for detail in (
                ROOM_DETAILS.get(room), owner_detail.format(owner=canonical_owner) if owner_detail else None) if detail)
            photos.append(CameraSnapshot(
                round_number, canonical_owner, room, tuple(names.get(person, person) for person in occupants), flavour,
                owner_body_id=identifiers.get(owner),
                occupant_body_ids=tuple(identifiers[person] for person in occupants) if identifiers else ()))
        result = tuple(photos)
        self._photos_by_night[round_number] = result
        return result

    def present_morning(self, round_number: int, publish: Callable[[str], None]) -> tuple[CameraSnapshot, ...]:
        published = []
        for photo in self._photos_by_night.get(round_number, ()):
            identity = (photo.round_number, photo.owner_body_id or photo.owner)
            if identity in self._presented:
                continue
            publish(photo.public_description())
            self._presented.add(identity)
            published.append(photo)
        return tuple(published)
