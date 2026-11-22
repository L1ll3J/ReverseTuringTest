"""Player inventory and once-per-room item offers, independent of the UI."""

from dataclasses import dataclass
import random
from types import MappingProxyType
from typing import Iterable
from uuid import uuid4


class InventoryError(ValueError):
    pass


@dataclass(frozen=True)
class ItemDefinition:
    item_id: str
    name: str
    description: str
    target_kind: str
    default: bool


ITEM_CATALOGUE = MappingProxyType({
    "banana": ItemDefinition(
        "banana", "Banana of Friendship",
        "Give a living suspect a banana. They will remember your friendly gesture.",
        "living_npc", True,
    ),
    "muffle": ItemDefinition(
        "muffle", "Muffle",
        "A living suspect can only make muffled sounds until the next night. They can still vote.",
        "living_npc", True,
    ),
    "spare_paper": ItemDefinition(
        "spare_paper", "Spare Paper",
        "Add two extra copies of your next ballot. Extra copies stack.",
        "self", True,
    ),
    "guardian_angel": ItemDefinition(
        "guardian_angel", "Guardian Angel",
        "Arm one protection against your next death. Protection follows you through possession.",
        "self", False,
    ),
})


def eligible_item_pool(
    unlocked_ids: Iterable[str] = (),
    enabled_unlocked_ids: Iterable[str] | None = None,
) -> tuple[str, ...]:
    unlocked = set(unlocked_ids)
    enabled = unlocked if enabled_unlocked_ids is None else set(enabled_unlocked_ids) & unlocked
    return tuple(item_id for item_id, item in ITEM_CATALOGUE.items()
                 if item.default or item_id in enabled)


@dataclass(frozen=True)
class InventoryEntry:
    instance_id: str
    item_id: str


class Inventory:
    def __init__(self, owner_id: str):
        if not owner_id:
            raise InventoryError("An inventory requires an owner")
        self._owner_id = owner_id
        self._namespace = uuid4().hex
        self._next_entry = 1
        self._entries: dict[str, InventoryEntry] = {}

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def entries(self) -> tuple[InventoryEntry, ...]:
        return tuple(self._entries.values())

    def grant(self, item_id: str) -> InventoryEntry:
        if item_id not in ITEM_CATALOGUE:
            raise InventoryError("Unknown item")
        entry = InventoryEntry(f"{self._namespace}:{self._next_entry}", item_id)
        self._next_entry += 1
        self._entries[entry.instance_id] = entry
        return entry

    def get(self, instance_id: str) -> InventoryEntry:
        try:
            return self._entries[instance_id]
        except KeyError:
            raise InventoryError("This item is no longer in your inventory") from None

    def consume(self, instance_id: str) -> InventoryEntry:
        entry = self.get(instance_id)
        del self._entries[instance_id]
        return entry


@dataclass(frozen=True)
class DraftOffer:
    offer_id: str
    room_id: str
    options: tuple[str, ...]


class DraftState:
    def __init__(self, owner_id: str, eligible_item_ids: Iterable[str], rng=None):
        pool = tuple(dict.fromkeys(eligible_item_ids))
        if not owner_id or not pool or any(item_id not in ITEM_CATALOGUE for item_id in pool):
            raise InventoryError("An item offer requires an owner and a valid item pool")
        self._owner_id = owner_id
        self._eligible_item_ids = pool
        self._rng = rng if rng is not None else random.Random()
        self._pending_offer: DraftOffer | None = None
        self._seen_rooms: set[str] = set()
        self._rerolls_remaining = 1

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def eligible_item_ids(self) -> tuple[str, ...]:
        return self._eligible_item_ids

    @property
    def pending_offer(self) -> DraftOffer | None:
        return self._pending_offer

    @property
    def seen_rooms(self) -> frozenset[str]:
        return frozenset(self._seen_rooms)

    @property
    def rerolls_remaining(self) -> int:
        return self._rerolls_remaining

    def enter_room(self, room_id: str) -> DraftOffer | None:
        if not room_id:
            raise InventoryError("A room identifier is required")
        if self._pending_offer is not None:
            if self._pending_offer.room_id == room_id:
                return self._pending_offer
            raise InventoryError("Choose or skip the current item offer first")
        if room_id in self._seen_rooms:
            return None
        options = tuple(self._rng.sample(self.eligible_item_ids, min(3, len(self.eligible_item_ids))))
        offer = DraftOffer(uuid4().hex, room_id, options)
        self._seen_rooms.add(room_id)
        self._pending_offer = offer
        return offer

    def _require_offer(self, offer_id: str) -> DraftOffer:
        offer = self._pending_offer
        if offer is None or offer.offer_id != offer_id:
            raise InventoryError("This item offer is no longer available")
        return offer

    @staticmethod
    def _require_slot(offer: DraftOffer, slot_index: int) -> None:
        if type(slot_index) is not int or not 0 <= slot_index < len(offer.options):
            raise InventoryError("Choose a valid item slot")

    def reroll(self, offer_id: str, slot_index: int) -> DraftOffer:
        offer = self._require_offer(offer_id)
        self._require_slot(offer, slot_index)
        if not self._rerolls_remaining:
            raise InventoryError("The run's reroll has already been used")
        alternatives = tuple(item_id for item_id in self.eligible_item_ids if item_id not in offer.options)
        if not alternatives:
            raise InventoryError("No alternative items are available for this offer")
        options = list(offer.options)
        options[slot_index] = self._rng.choice(alternatives)
        updated = DraftOffer(uuid4().hex, offer.room_id, tuple(options))
        self._pending_offer = updated
        self._rerolls_remaining -= 1
        return updated

    def choose(self, offer_id: str, slot_index: int, inventory: Inventory) -> InventoryEntry:
        offer = self._require_offer(offer_id)
        self._require_slot(offer, slot_index)
        if inventory.owner_id != self.owner_id:
            raise InventoryError("This item offer belongs to another player")
        entry = inventory.grant(offer.options[slot_index])
        self._pending_offer = None
        return entry

    def skip(self, offer_id: str) -> None:
        self._require_offer(offer_id)
        self._pending_offer = None
