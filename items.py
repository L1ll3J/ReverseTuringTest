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
    flavour: str = ""


ITEM_CATALOGUE = MappingProxyType({
    "banana": ItemDefinition(
        "banana", "Banana of Friendship",
        "Give a living suspect a banana. They will remember your friendly gesture.",
        "living_npc", True,
        flavour="A modest contribution to a deeply suspicious friendship.",
    ),
    "muffle": ItemDefinition(
        "muffle", "Muffle",
        "A living suspect can only make muffled sounds until the next night. They can still vote.",
        "living_npc", True,
        flavour="They may object. It will be difficult to tell.",
    ),
    "spare_paper": ItemDefinition(
        "spare_paper", "Spare Paper",
        "Add two extra copies of your next ballot. Extra copies stack.",
        "self", True,
        flavour="One person, one vote. A little more paper helps.",
    ),
    "guardian_angel": ItemDefinition(
        "guardian_angel", "Guardian Angel",
        "Arm one protection against your next death. Protection follows you through possession.",
        "self", False,
        flavour="Death can wait. Once.",
    ),
    "throw_voice": ItemDefinition(
        "throw_voice", "Throw Voice", "Send one apparent message in a living suspect's voice.",
        "living_npc", True, requires_message=True,
        flavour="Let someone else put your foot in their mouth.",
    ),
    "dark_whisper": ItemDefinition(
        "dark_whisper", "Dark Whisper", "Send one apparent message from a dead character's voice.",
        "dead_body", True, requires_message=True,
        flavour="Death needn't have the last word.",
    ),
    "love_potion": ItemDefinition(
        "love_potion", "Love Potion",
        "Make a living suspect adore you for this discussion. Their devotion becomes bitterness next discussion.",
        "living_npc", True, flavour="Sweet on the tongue. Bitter by morning.",
    ),
    "forged_note": ItemDefinition(
        "forged_note", "Forged Note", "Prepare a signed note for the body of your next victim.",
        "self", True, requires_message=True,
        flavour="Your victim has one last thing to say. You wrote it.",
    ),
    "divine_revelation": ItemDefinition(
        "divine_revelation", "Divine Revelation", "Send a private message that seems to come from a higher power.",
        "living_npc", True, requires_message=True, flavour="Funny how the heavens agree with you.",
    ),
    "mail_in_ballot": ItemDefinition(
        "mail_in_ballot", "Mail-in Ballot", "Choose a suspect whose ballot you will cast this round.",
        "living_npc", True,
        flavour="Their civic duty is in your capable hands.",
    ),
    "watch_this_drive": ItemDefinition(
        "watch_this_drive", "Watch This Drive", "Mark a suspect. Kill them next night to earn one extra basic room attack.",
        "living_npc", True, flavour="One good murder deserves another.",
    ),
    "room_reservation": ItemDefinition(
        "room_reservation", "Room Reservation",
        "Send a living suspect to a chosen available room next night. Each room holds up to two suspects.",
        "living_npc_room", True,
    ),
    "lucky_match": ItemDefinition(
        "lucky_match", "Lucky Match", "Gain three extra single-slot rerolls for this run.", "self", True,
        flavour="Perhaps the next disappointment will suit you better.",
    ),
    "deep_pockets": ItemDefinition(
        "deep_pockets", "Deep Pockets", "Take up to three different items from your next room offer.", "self", True,
        flavour="Greed is easier with room for everything.",
    ),
    "pocket_gavel": ItemDefinition(
        "pocket_gavel", "Pocket Gavel", "Call the normal vote early after answering any required question.",
        "self", True,
        flavour="Enough discussion. Time for regrettable decisions.",
    ),
    "listening_glass": ItemDefinition(
        "listening_glass", "Listening Glass", "Privately inspect one suspect's recorded experience from the latest night.",
        "living_npc", True,
        flavour="Some nights bear listening to twice.",
    ),
    "stay_of_execution": ItemDefinition(
        "stay_of_execution", "Stay of Execution", "Protect one body from execution in the next actual vote only.",
        "living_body", True,
        flavour="A postponement can feel remarkably like a pardon.",
    ),
    "booby_trap": ItemDefinition(
        "booby_trap", "Booby Trap", "Prepare a room trap for next night. The next entering group triggers it once.",
        "room", False,
        flavour="Do come in. Everyone at once.",
    ),
    "lucky_coin": ItemDefinition(
        "lucky_coin", "(Un)Lucky Coin", "Flip between your death and a chosen living body's death. Reusable.",
        "living_body", False, reusable=True,
        flavour="A little weight in the hand. A great deal hanging in the balance.",
    ),
    "spin_the_dagger": ItemDefinition(
        "spin_the_dagger", "Spin the Dagger", "Strike a random living person, possibly yourself.", "self", False,
        flavour="At last, a fair chance to make this your own problem.",
    ),
    "presidential_decree": ItemDefinition(
        "presidential_decree", "Presidential Decree", "Skip the next vote without spending pending extra ballots.",
        "self", False,
        flavour="The people have spoken quite enough.",
    ),
    "occult_ritual": ItemDefinition(
        "occult_ritual", "Occult Ritual", "Curse a body to die at the action phase after next.", "living_body", False,
        flavour="Some appointments are best forgotten. This one isn't.",
    ),
    "mass_discombobulator": ItemDefinition(
        "mass_discombobulator", "Mass Discombobulator",
        "Erase every suspect's memories of this run. Personalities, physical state and active item effects remain.",
        "self", False,
        flavour="A fresh start for everyone who knew too much.",
    ),
    "revolver": ItemDefinition(
        "revolver", "Revolver", "Shoot one living body immediately in public.", "living_body", False,
        flavour="A rather loud way to end a discussion.",
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


@dataclass(frozen=True)
class InventorySnapshot:
    """Immutable current holdings supplied explicitly to room-offer operations."""

    owner_id: str
    item_ids: frozenset[str]

    def __post_init__(self):
        if not isinstance(self.owner_id, str) or not self.owner_id.strip():
            raise InventoryError("An inventory snapshot requires an owner")
        if not isinstance(self.item_ids, (tuple, list, set, frozenset)):
            raise InventoryError("Inventory item types must be a collection")
        if any(not isinstance(item_id, str) or item_id not in ITEM_CATALOGUE for item_id in self.item_ids):
            raise InventoryError("Inventory snapshots require known item types")
        object.__setattr__(self, "item_ids", frozenset(self.item_ids))


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

    def snapshot(self) -> InventorySnapshot:
        return InventorySnapshot(self.owner_id, frozenset(entry.item_id for entry in self.entries))

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

    def _owned_types(self, inventory: InventorySnapshot) -> frozenset[str]:
        if not isinstance(inventory, InventorySnapshot):
            raise InventoryError("A current inventory snapshot is required")
        if inventory.owner_id != self.owner_id:
            raise InventoryError("This item offer belongs to another player")
        return inventory.item_ids

    def enter_room(self, room_id: str, inventory: InventorySnapshot) -> DraftOffer | None:
        owned = self._owned_types(inventory)
        if not room_id:
            raise InventoryError("A room identifier is required")
        if self._pending_offer is not None:
            if self._pending_offer.room_id == room_id:
                offer = self._pending_offer
                options = tuple(item_id for item_id in offer.options if item_id not in owned)
                if options != offer.options:
                    self._pending_offer = (
                        DraftOffer(uuid4().hex, room_id, options, min(offer.picks_remaining, len(options)),
                                   offer.picked_item_ids) if options else None
                    )
                return self._pending_offer
            raise InventoryError("Choose or skip the current item offer first")
        if room_id in self._seen_rooms:
            return None
        eligible = tuple(item_id for item_id in self.eligible_item_ids if item_id not in owned)
        self._seen_rooms.add(room_id)
        if not eligible:
            return None
        options = tuple(self._rng.sample(eligible, min(3, len(eligible))))
        picks = min(3 if self._extra_pick_armed else 1, len(options))
        offer = DraftOffer(uuid4().hex, room_id, options, picks)
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

    def reroll_alternatives(self, offer_id: str, inventory: InventorySnapshot) -> tuple[str, ...]:
        offer = self._require_offer(offer_id)
        owned = self._owned_types(inventory)
        return tuple(item_id for item_id in self.eligible_item_ids
                     if item_id not in offer.options and item_id not in offer.picked_item_ids
                     and item_id not in owned)

    def reroll(self, offer_id: str, slot_index: int, inventory: InventorySnapshot) -> DraftOffer:
        offer = self._require_offer(offer_id)
        self._require_slot(offer, slot_index)
        alternatives = self.reroll_alternatives(offer_id, inventory)
        if not self._rerolls_remaining:
            raise InventoryError("The run's reroll has already been used")
        if not alternatives:
            raise InventoryError("No alternative items are available for this offer")
        options = list(offer.options)
        options[slot_index] = self._rng.choice(alternatives)
        options = tuple(item_id for item_id in options if item_id not in inventory.item_ids)
        updated = DraftOffer(uuid4().hex, offer.room_id, options,
                             min(offer.picks_remaining, len(options)), offer.picked_item_ids)
        self._pending_offer = updated
        self._rerolls_remaining -= 1
        return updated

    def choose(self, offer_id: str, slot_index: int, inventory: Inventory) -> InventoryEntry:
        offer = self._require_offer(offer_id)
        self._require_slot(offer, slot_index)
        if inventory.owner_id != self.owner_id:
            raise InventoryError("This item offer belongs to another player")
        if offer.options[slot_index] in inventory.snapshot().item_ids:
            raise InventoryError("This item type is already in your inventory")
        entry = inventory.grant(offer.options[slot_index])
        owned = inventory.snapshot().item_ids
        remaining = tuple(item_id for item_id in offer.options if item_id not in owned)
        self._pending_offer = (
            DraftOffer(uuid4().hex, offer.room_id, remaining, min(offer.picks_remaining - 1, len(remaining)),
                       (*offer.picked_item_ids, entry.item_id))
            if offer.picks_remaining > 1 and remaining else None
        )
        return entry

    def skip(self, offer_id: str) -> None:
        self._require_offer(offer_id)
        self._pending_offer = None
