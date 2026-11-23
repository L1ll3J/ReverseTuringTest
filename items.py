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
    requires_message: bool = False
    reusable: bool = False


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
    "throw_voice": ItemDefinition(
        "throw_voice", "Throw Voice", "Send one apparent message in a living suspect's voice.",
        "living_npc", True, requires_message=True,
    ),
    "dark_whisper": ItemDefinition(
        "dark_whisper", "Dark Whisper", "Send one apparent message from a dead character's voice.",
        "dead_body", True, requires_message=True,
    ),
    "forged_note": ItemDefinition(
        "forged_note", "Forged Note", "Prepare a signed note for the body of your next victim.",
        "self", True, requires_message=True,
    ),
    "divine_revelation": ItemDefinition(
        "divine_revelation", "Divine Revelation", "Send a private message that seems to come from a higher power.",
        "living_npc", True, requires_message=True,
    ),
    "mail_in_ballot": ItemDefinition(
        "mail_in_ballot", "Mail-in Ballot", "Choose a suspect whose ballot you will cast this round.",
        "living_npc", True,
    ),
    "watch_this_drive": ItemDefinition(
        "watch_this_drive", "Watch This Drive", "Mark a suspect. Kill their body next night to earn one extra action.",
        "living_npc", True,
    ),
    "lucky_match": ItemDefinition(
        "lucky_match", "Lucky Match", "Gain one extra single-slot reroll for this run.", "self", True,
    ),
    "deep_pockets": ItemDefinition(
        "deep_pockets", "Deep Pockets", "Take up to two different items from your next room offer.", "self", True,
    ),
    "pocket_gavel": ItemDefinition(
        "pocket_gavel", "Pocket Gavel", "Call the normal vote early after answering any required question.",
        "self", True,
    ),
    "listening_glass": ItemDefinition(
        "listening_glass", "Listening Glass", "Privately inspect one suspect's recorded experience from the latest night.",
        "living_npc", True,
    ),
    "stay_of_execution": ItemDefinition(
        "stay_of_execution", "Stay of Execution", "Protect one body from execution in the next actual vote only.",
        "living_body", True,
    ),
    "booby_trap": ItemDefinition(
        "booby_trap", "Booby Trap", "Prepare a room trap for next night. The next entering group triggers it once.",
        "room", False,
    ),
    "lucky_coin": ItemDefinition(
        "lucky_coin", "(Un)Lucky Coin", "Flip between your death and a chosen living body's death. Reusable.",
        "living_body", False, reusable=True,
    ),
    "spin_the_dagger": ItemDefinition(
        "spin_the_dagger", "Spin the Dagger", "Strike a random living person, possibly yourself.", "self", False,
    ),
    "presidential_decree": ItemDefinition(
        "presidential_decree", "Presidential Decree", "Skip the next vote without spending pending extra ballots.",
        "self", False,
    ),
    "occult_ritual": ItemDefinition(
        "occult_ritual", "Occult Ritual", "Curse a body to die at the action phase after next.", "living_body", False,
    ),
    "revolver": ItemDefinition(
        "revolver", "Revolver", "Shoot one living body immediately in public.", "living_body", False,
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
    picks_remaining: int = 1
    picked_item_ids: tuple[str, ...] = ()


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
        self._extra_pick_armed = False

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

    @property
    def extra_pick_armed(self) -> bool:
        return self._extra_pick_armed

    def add_rerolls(self, count: int = 1) -> None:
        if type(count) is not int or count < 1:
            raise InventoryError("Added rerolls must be a positive integer")
        self._rerolls_remaining += count

    def arm_extra_pick(self) -> None:
        if self._extra_pick_armed:
            raise InventoryError("An extra room choice is already armed")
        if len(self.eligible_item_ids) < 2:
            raise InventoryError("The item pool has no second distinct choice")
        self._extra_pick_armed = True

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
        picks = min(2 if self._extra_pick_armed else 1, len(options))
        offer = DraftOffer(uuid4().hex, room_id, options, picks)
        self._seen_rooms.add(room_id)
        self._pending_offer = offer
        self._extra_pick_armed = False
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
        alternatives = tuple(item_id for item_id in self.eligible_item_ids
                             if item_id not in offer.options and item_id not in offer.picked_item_ids)
        if not alternatives:
            raise InventoryError("No alternative items are available for this offer")
        options = list(offer.options)
        options[slot_index] = self._rng.choice(alternatives)
        updated = DraftOffer(uuid4().hex, offer.room_id, tuple(options),
                             offer.picks_remaining, offer.picked_item_ids)
        self._pending_offer = updated
        self._rerolls_remaining -= 1
        return updated

    def choose(self, offer_id: str, slot_index: int, inventory: Inventory) -> InventoryEntry:
        offer = self._require_offer(offer_id)
        self._require_slot(offer, slot_index)
        if inventory.owner_id != self.owner_id:
            raise InventoryError("This item offer belongs to another player")
        entry = inventory.grant(offer.options[slot_index])
        remaining = tuple(item_id for index, item_id in enumerate(offer.options) if index != slot_index)
        self._pending_offer = (
            DraftOffer(uuid4().hex, offer.room_id, remaining, offer.picks_remaining - 1,
                       (*offer.picked_item_ids, entry.item_id))
            if offer.picks_remaining > 1 and remaining else None
        )
        return entry

    def skip(self, offer_id: str) -> None:
        self._require_offer(offer_id)
        self._pending_offer = None
