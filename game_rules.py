"""Validated player-item effects and shared speech, death and ballot rules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import random
from types import MappingProxyType
from typing import Iterable, Literal, Mapping
from uuid import uuid4

from ai_context import MemoryRecord, SpeechProposal
from items import ITEM_CATALOGUE, DraftState, Inventory, InventoryError


BANANA_PUBLIC = "{giver} gave {recipient} a banana."
BANANA_DISPOSITION = (
    "{giver} gave you a delicious banana on day {day}. "
    "You remember the friendly gesture and feel a little warmer towards them."
)
MUFFLE_PRIVATE = "You cannot form intelligible words. Your attempts to speak come out as muffled sounds."
MUFFLED_SPEECH = "Mmpphff... hnngg brrrrff gnf."
MUFFLE_EXPIRED = (
    "You can speak clearly again. You remember being unable to speak during the previous discussion."
)
WEIGHTED_BALLOT_PUBLIC = "{voter}'s vote for {target} counted as {weight} votes."
GUARDIAN_PUBLIC = "Against all expectation, {target} survives."
THROW_VOICE_PRIVATE = 'You heard your own voice say: "{message}" You did not say those words.'
DIVINE_REVELATION_PRIVATE = 'You hear a voice inside your mind that seems to come from a higher power: "{message}"'
DARK_WHISPER_PUBLIC = (
    "Everyone hears the unmistakeable voice of the dead {apparent_speaker}, "
    'loudly and clearly {subject_pronoun} {speak_verb}:\n\n"{message}"'
)


class ItemRuleError(ValueError):
    pass


class DeathCause(str, Enum):
    ATTACK = "attack"
    EXECUTION = "execution"
    TRAP = "trap"
    CURSE = "curse"
    GAMBLE = "gamble"


@dataclass(frozen=True)
class NightMemoryView:
    body_id: str
    night: int
    experiences: tuple[MemoryRecord, ...]

    def __post_init__(self):
        object.__setattr__(self, "experiences", tuple(self.experiences))
        if not self.body_id or self.night < 1 or any(not isinstance(record, MemoryRecord) or record.kind != "experience"
                                   or record.round_number != self.night
                                   for record in self.experiences):
            raise ItemRuleError("A night memory view requires a body, night and matching experience records")


@dataclass(frozen=True)
class BodyState:
    body_id: str
    label: str
    alive: bool
    is_npc: bool
    subject_pronoun: Literal["he", "she", "they"] = "they"
    mind_id: str | None = None

    @property
    def mind_identity(self) -> str:
        return self.mind_id if self.mind_id is not None else self.body_id

    def __post_init__(self):
        if not self.body_id or self.body_id == "NONE" or not self.label:
            raise ItemRuleError("Each body requires an identifier and a label")
        if self.mind_id is not None and (not isinstance(self.mind_id, str) or not self.mind_id):
            raise ItemRuleError("A mind identifier must be a nonempty string")
        if self.subject_pronoun not in ("he", "she", "they"):
            raise ItemRuleError("A body subject pronoun must be he, she or they")


@dataclass(frozen=True)
class ItemUseContext:
    day: int
    phase: str
    ai_busy: bool
    owner_id: str
    actor_body_id: str
    bodies: tuple[BodyState, ...]
    available_rooms: tuple[str, ...] = ()
    required_player_reply: bool = False
    memory_views: tuple[NightMemoryView, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "bodies", tuple(self.bodies))
        object.__setattr__(self, "available_rooms", tuple(self.available_rooms))
        object.__setattr__(self, "memory_views", tuple(self.memory_views))


@dataclass(frozen=True)
class ItemEvent:
    event_id: str
    event_type: str
    day: int
    item_id: str | None = None
    owner_id: str | None = None
    actor_body_id: str | None = None
    target_body_id: str | None = None
    subject_body_id: str | None = None
    public_text: str | None = None
    private_text: str | None = None
    disposition_text: str | None = None
    room_id: str | None = None
    message: str | None = None
    amount: int = 0
    due_day: int | None = None
    origin: str | None = None
    observed_day: int | None = None
    observed_source: str | None = None
    death_decision: DeathDecision | None = None
    giver_label: str | None = None


@dataclass(frozen=True)
class AffinityEffect:
    day: int
    recipient_body_id: str
    subject_body_id: str
    disposition_text: str


@dataclass(frozen=True)
class MuffleEffect:
    target_body_id: str
    applied_day: int
    expires_day: int


@dataclass(frozen=True)
class DeathDecision:
    attempt_id: str
    target_body_id: str
    outcome: str
    is_new: bool
    events: tuple[ItemEvent, ...] = ()
    cause: DeathCause = DeathCause.ATTACK
    source_id: str | None = None
    protection_item_id: str | None = None
    day: int = 0

    @property
    def killed(self) -> bool:
        return self.is_new and self.outcome == "killed"

    @property
    def prevented(self) -> bool:
        return self.outcome == "prevented"


@dataclass(frozen=True)
class VoteSnapshot:
    vote_id: str
    voter_bodies: tuple[BodyState, ...]
    owner_body_id: str
    day: int
    bonus: int
    proxy_votes: tuple[tuple[str, str], ...] = ()
    execution_stays: tuple[str, ...] = ()
    skipped: bool = False

    @property
    def weights(self) -> Mapping[str, int]:
        return MappingProxyType({body.body_id: 1 + (self.bonus if body.body_id == self.owner_body_id else 0)
                                 for body in self.voter_bodies})

    @property
    def proxy_ballots(self) -> Mapping[str, str]:
        return MappingProxyType(dict(self.proxy_votes))


@dataclass(frozen=True)
class RoomReservation:
    mind_id: str
    room_id: str
    due_day: int


@dataclass(frozen=True)
class RoomTrap:
    effect_id: str
    room_id: str
    owner_id: str
    actor_body_id: str
    armed_day: int


@dataclass(frozen=True)
class BodyCurse:
    effect_id: str
    target_body_id: str
    owner_id: str
    actor_body_id: str
    due_day: int


@dataclass(frozen=True)
class PreparedNote:
    message: str
    actor_body_id: str
    armed_day: int


@dataclass(frozen=True)
class MarkedTarget:
    target_body_id: str
    due_day: int


@dataclass(frozen=True)
class EffectResolution:
    decisions: tuple[DeathDecision, ...] = ()
    events: tuple[ItemEvent, ...] = ()
    is_new: bool = True


@dataclass(frozen=True)
class VoteResult:
    vote_id: str
    counts: Mapping[str, int]
    outcome: str
    executed_body_id: str | None
    tied_targets: tuple[str, ...]
    is_new: bool
    events: tuple[ItemEvent, ...]

    def __post_init__(self):
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))


def _body_map(bodies: Iterable[BodyState]) -> dict[str, BodyState]:
    result = {}
    for body in bodies:
        if body.body_id in result:
            raise ItemRuleError("Body identifiers must be unique")
        result[body.body_id] = body
    return result


class PlayerItemRules:
    """Mutate on the authoritative game thread; workers receive snapshots only.

    New item effects emit mechanical payloads. Their NPC-facing rendering belongs
    to an independently approved narrative adapter, not this rules component.
    """

    def __init__(self, owner_id: str, inventory: Inventory, rng=None, *, guardian_scope="owner"):
        if guardian_scope not in {"owner", "body"}:
            raise ItemRuleError("Guardian protection requires owner or body scope")
        self._guardian_scope = guardian_scope
        self._body_protections: set[str] = set()
        if not owner_id or inventory.owner_id != owner_id:
            raise ItemRuleError("Item rules and inventory must have the same owner")
        self._owner_id = owner_id
        self._inventory = inventory
        self._namespace = uuid4().hex
        self._event_sequence = 0
        self._protection_armed = False
        self._pending_ballot_bonus = 0
        self._muffles: dict[str, MuffleEffect] = {}
        self._affinities: list[AffinityEffect] = []
        self._death_decisions: dict[str, tuple[tuple, DeathDecision]] = {}
        self._votes: dict[str, VoteSnapshot] = {}
        self._resolved_votes: dict[str, tuple[tuple, VoteResult]] = {}
        self._active_vote_id: str | None = None
        self._rng = rng if rng is not None else random.Random()
        self._activation_receipts: dict[str, tuple[tuple, tuple[ItemEvent, ...]]] = {}
        self._known_dead: set[str] = set()
        self._execution_stays: set[str] = set()
        self._execution_attempts: dict[str, DeathDecision] = {}
        self._proxy_voters: dict[str, int] = {}
        self._skip_next_vote = False
        self._early_vote_day: int | None = None
        self._reservations: dict[str, RoomReservation] = {}
        self._reserved_assignments: dict[str, RoomReservation] = {}
        self._traps: dict[str, RoomTrap] = {}
        self._curses: dict[str, BodyCurse] = {}
        self._entry_receipts: dict[str, tuple[tuple, EffectResolution]] = {}
        self._curse_receipts: dict[str, tuple[tuple, EffectResolution]] = {}
        self._prepared_note: PreparedNote | None = None
        self._marked_target: MarkedTarget | None = None
        self._bonus_actions = 0
        self._bonus_action_day: int | None = None
        self._bonus_receipts: dict[str, int] = {}

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def inventory(self) -> Inventory:
        return self._inventory

    @property
    def protection_armed(self) -> bool:
        return bool(self._body_protections) if self._guardian_scope == "body" else self._protection_armed

    @property
    def guardian_body_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._body_protections))

    @property
    def pending_ballot_bonus(self) -> int:
        return self._pending_ballot_bonus

    @property
    def affinities(self) -> tuple[AffinityEffect, ...]:
        return tuple(self._affinities)

    @property
    def muffles(self) -> tuple[MuffleEffect, ...]:
        return tuple(self._muffles.values())

    @property
    def execution_stays(self) -> tuple[str, ...]:
        return tuple(sorted(self._execution_stays))

    @property
    def proxy_voters(self) -> Mapping[str, int]:
        return MappingProxyType(dict(self._proxy_voters))

    @property
    def skip_next_vote(self) -> bool:
        return self._skip_next_vote

    @property
    def reservations(self) -> tuple[RoomReservation, ...]:
        return tuple(self._reservations.values())

    def reserved_room_assignments(self, day: int, bodies: Iterable[BodyState],
                                  available_rooms: Iterable[str]) -> Mapping[str, str]:
        body_map = _body_map(bodies)
        rooms = set(available_rooms)
        assigned = {}
        for body in body_map.values():
            reservation = self._reservations.get(body.mind_identity)
            if (reservation is not None and reservation.due_day == day and body.alive
                    and body.is_npc and body.body_id not in self._known_dead and reservation.room_id in rooms):
                assigned[body.body_id] = reservation.room_id
                self._reserved_assignments[body.body_id] = reservation
        return MappingProxyType(assigned)

    @property
    def traps(self) -> tuple[RoomTrap, ...]:
        return tuple(self._traps.values())

    @property
    def curses(self) -> tuple[BodyCurse, ...]:
        return tuple(self._curses.values())

    @property
    def prepared_note(self) -> PreparedNote | None:
        return self._prepared_note

    @property
    def marked_target(self) -> MarkedTarget | None:
        return self._marked_target

    @property
    def bonus_actions(self) -> int:
        return self._bonus_actions

    def _event(self, event_type: str, day: int, **values) -> ItemEvent:
        self._event_sequence += 1
        return ItemEvent(f"{self._namespace}:{self._event_sequence}", event_type, day,
                         owner_id=self.owner_id, **values)

    def use_item(
        self,
        instance_id: str,
        context: ItemUseContext,
        target_body_id: str | None = None,
        *,
        room_id: str | None = None,
        message: str | None = None,
        drafts: DraftState | None = None,
        activation_id: str | None = None,
    ) -> tuple[ItemEvent, ...]:
        if activation_id is not None and (not isinstance(activation_id, str) or not activation_id):
            raise ItemRuleError("An activation identifier must be a nonempty string")
        identity = (instance_id, context.day, context.phase, context.owner_id,
                    context.actor_body_id, target_body_id, room_id, message)
        if activation_id is not None and activation_id in self._activation_receipts:
            original, _events = self._activation_receipts[activation_id]
            if original != identity:
                raise ItemRuleError("This activation identifier already describes another use")
            return ()
        if (context.phase != "conversation" or context.ai_busy or context.day < 1
                or context.owner_id != self.owner_id or self._active_vote_id is not None):
            raise ItemRuleError("Items can only be used at a conversation decision point")
        bodies = _body_map(context.bodies)
        actor = bodies.get(context.actor_body_id)
        if actor is None or not actor.alive or actor.body_id in self._known_dead:
            raise ItemRuleError("A living player is required to use an item")
        try:
            entry = self.inventory.get(instance_id)
        except InventoryError as error:
            raise ItemRuleError(str(error)) from None
        definition = ITEM_CATALOGUE[entry.item_id]
        target = bodies.get(target_body_id) if target_body_id is not None else None
        if definition.target_kind in {"living_npc", "living_body", "living_npc_room"}:
            if target is None or not target.alive or target.body_id in self._known_dead:
                raise ItemRuleError("Choose a living body")
            if definition.target_kind in {"living_npc", "living_npc_room"} and (not target.is_npc or target.body_id == actor.body_id):
                raise ItemRuleError("Choose a living suspect other than yourself")
        elif definition.target_kind == "dead_body":
            if target is None or (target.alive and target.body_id not in self._known_dead):
                raise ItemRuleError("Choose a dead body")
        elif target_body_id is not None:
            raise ItemRuleError("This item does not take a target")
        if definition.target_kind in {"room", "living_npc_room"}:
            if room_id is None or room_id not in context.available_rooms:
                raise ItemRuleError("Choose a currently available room")
        elif room_id is not None:
            raise ItemRuleError("This item does not take a room")
        if definition.requires_message:
            if not isinstance(message, str) or not message.strip() or len(message) > 2000:
                raise ItemRuleError("Write a message between one and 2000 characters")
        elif message is not None:
            raise ItemRuleError("This item does not take a message")
        if entry.item_id == "room_reservation":
            occupied = sum(reservation.room_id == room_id and reservation.due_day == context.day + 1
                           and mind_id != target.mind_identity
                           and any(body.alive and body.body_id not in self._known_dead
                                   and body.mind_identity == mind_id for body in bodies.values())
                           for mind_id, reservation in self._reservations.items())
            if occupied >= 2:
                raise ItemRuleError("This room already has two reservations for next night")
        if entry.item_id == "muffle" and target.body_id in self._muffles:
            raise ItemRuleError("This suspect is already muffled")
        if entry.item_id == "guardian_angel" and self.guardian_protects(actor.body_id, self.owner_id):
            raise ItemRuleError("Guardian Angel protection is already armed")
        if entry.item_id == "stay_of_execution" and target.body_id in self._execution_stays:
            raise ItemRuleError("This body already has a stay of execution")
        if entry.item_id == "mail_in_ballot" and target.body_id in self._proxy_voters:
            raise ItemRuleError("This body's ballot is already reserved")
        if entry.item_id == "presidential_decree" and self._skip_next_vote:
            raise ItemRuleError("The next vote is already scheduled to be skipped")
        if entry.item_id == "pocket_gavel" and (context.required_player_reply or self._early_vote_day == context.day):
            raise ItemRuleError("Answer the required question before calling an early vote")
        if entry.item_id == "forged_note" and self._prepared_note is not None:
            raise ItemRuleError("A note is already prepared for your next victim")
        if entry.item_id == "watch_this_drive" and self._marked_target is not None:
            raise ItemRuleError("A target is already marked for next night")
        if entry.item_id == "booby_trap" and room_id in self._traps:
            raise ItemRuleError("This room already has a prepared trap")
        if entry.item_id == "occult_ritual" and any(
                curse.target_body_id == target.body_id and curse.due_day == context.day + 2
                for curse in self._curses.values()):
            raise ItemRuleError("This body is already cursed for that night")
        memory = None
        if entry.item_id == "listening_glass":
            views = {view.body_id: view for view in context.memory_views}
            if len(views) != len(context.memory_views):
                raise ItemRuleError("A body cannot have competing memory snapshots")
            records = views.get(target.body_id)
            experiences = records.experiences if records is not None and records.night == context.day else ()
            if not experiences:
                raise ItemRuleError("This suspect has no supplied experience from the latest night")
            memory = experiences[-1]
        if entry.item_id in {"lucky_match", "deep_pockets"}:
            if drafts is None or drafts.owner_id != self.owner_id:
                raise ItemRuleError("This item requires your current run's room offers")
            if entry.item_id == "lucky_match" and len(drafts.eligible_item_ids) <= 3:
                raise ItemRuleError("The run's pool has no alternative for a reroll")
            if entry.item_id == "deep_pockets" and (drafts.extra_pick_armed or len(drafts.eligible_item_ids) < 2):
                raise ItemRuleError("An extra room choice cannot be armed now")
        if definition.reusable and activation_id is None:
            raise ItemRuleError("A reusable activation requires a stable identifier")

        if not definition.reusable:
            self.inventory.consume(instance_id)
        values = {"item_id": entry.item_id, "actor_body_id": actor.body_id,
                  "target_body_id": target.body_id if target is not None else None}
        consequences = ()
        if entry.item_id == "banana":
            disposition = BANANA_DISPOSITION.format(giver=actor.label, day=context.day)
            self._affinities.append(AffinityEffect(context.day, target.body_id, actor.body_id, disposition))
            effect = self._event("banana_given", context.day, **values,
                                 subject_body_id=actor.body_id,
                                 public_text=BANANA_PUBLIC.format(giver=actor.label, recipient=target.label),
                                 disposition_text=disposition)
        elif entry.item_id == "muffle":
            self._muffles[target.body_id] = MuffleEffect(target.body_id, context.day, context.day + 1)
            effect = self._event("muffle_applied", context.day, **values, private_text=MUFFLE_PRIVATE)
        elif entry.item_id == "mass_discombobulator":
            effect = self._event("npc_recollections_cleared", context.day, **values)
        elif entry.item_id == "love_potion":
            effect = self._event("love_potion_applied", context.day, **values,
                                 subject_body_id=actor.body_id, giver_label=actor.label)
        elif entry.item_id == "spare_paper":
            self._pending_ballot_bonus += 2
            effect = self._event("extra_ballots_added", context.day, **values)
        elif entry.item_id == "guardian_angel":
            if self._guardian_scope == "body":
                self._body_protections.add(actor.body_id)
            else:
                self._protection_armed = True
            effect = self._event("guardian_armed", context.day, **values)
        elif entry.item_id == "room_reservation":
            self._reservations[target.mind_identity] = RoomReservation(target.mind_identity, room_id, context.day + 1)
            effect = self._event("room_reserved", context.day, **values, room_id=room_id, due_day=context.day + 1)
        elif entry.item_id == "lucky_match":
            drafts.add_rerolls(3)
            effect = self._event("reroll_added", context.day, **values, amount=3)
        elif entry.item_id == "deep_pockets":
            drafts.arm_extra_pick()
            effect = self._event("extra_pick_armed", context.day, **values, amount=2)
        elif entry.item_id == "pocket_gavel":
            self._early_vote_day = context.day
            effect = self._event("early_vote_requested", context.day, **values)
        elif entry.item_id == "stay_of_execution":
            self._execution_stays.add(target.body_id)
            effect = self._event("execution_stay_armed", context.day, **values)
        elif entry.item_id == "mail_in_ballot":
            self._proxy_voters[target.body_id] = context.day
            effect = self._event("proxy_ballot_reserved", context.day, **values)
        elif entry.item_id == "presidential_decree":
            self._skip_next_vote = True
            effect = self._event("vote_skip_armed", context.day, **values)
        elif entry.item_id == "forged_note":
            self._prepared_note = PreparedNote(message, actor.body_id, context.day)
            effect = self._event("note_prepared", context.day, **values, message=message)
        elif entry.item_id == "watch_this_drive":
            self._marked_target = MarkedTarget(target.body_id, context.day + 1)
            effect = self._event("target_marked", context.day, **values, due_day=context.day + 1)
        elif entry.item_id == "booby_trap":
            effect_id = uuid4().hex
            self._traps[room_id] = RoomTrap(effect_id, room_id, self.owner_id, actor.body_id, context.day + 1)
            effect = self._event("room_trap_prepared", context.day, **values,
                                 room_id=room_id, due_day=context.day + 1)
        elif entry.item_id == "occult_ritual":
            effect_id = uuid4().hex
            self._curses[effect_id] = BodyCurse(effect_id, target.body_id, self.owner_id,
                                              actor.body_id, context.day + 2)
            effect = self._event("body_cursed", context.day, **values, due_day=context.day + 2)
        elif entry.item_id in {"throw_voice", "dark_whisper", "divine_revelation"}:
            kind, origin = {
                "throw_voice": ("apparent_speech", "projected_voice"),
                "dark_whisper": ("apparent_speech", "paranormal_voice"),
                "divine_revelation": ("private_revelation", "apparent_divine_voice"),
            }[entry.item_id]
            observation = {}
            if entry.item_id == "throw_voice":
                observation["private_text"] = THROW_VOICE_PRIVATE.format(message=message)
            elif entry.item_id == "divine_revelation":
                observation["private_text"] = DIVINE_REVELATION_PRIVATE.format(message=message)
            else:
                observation["public_text"] = DARK_WHISPER_PUBLIC.format(
                    apparent_speaker=target.label, subject_pronoun=target.subject_pronoun,
                    speak_verb="speak" if target.subject_pronoun == "they" else "speaks", message=message)
            effect = self._event(kind, context.day, **values, message=message, origin=origin, **observation)
        elif entry.item_id == "listening_glass":
            effect = self._event("private_memory_revealed", context.day, **values,
                                 message=memory.text, observed_day=memory.round_number,
                                 observed_source=memory.source, origin="recorded_perception")
        elif entry.item_id in {"revolver", "lucky_coin", "spin_the_dagger"}:
            if entry.item_id == "lucky_coin":
                loser = self._rng.choice((actor, target))
            elif entry.item_id == "spin_the_dagger":
                loser = self._rng.choice(tuple(body for body in bodies.values()
                                               if body.alive and body.body_id not in self._known_dead))
            else:
                loser = target
            cause = DeathCause.GAMBLE if entry.item_id != "revolver" else DeathCause.ATTACK
            decision = self.attempt_death(
                "item:" + (activation_id or uuid4().hex), loser, context.day,
                self.owner_id if loser.body_id == actor.body_id else None, cause=cause,
                caused_by_owner_id=self.owner_id, phase=context.phase)
            effect = self._event("immediate_death_resolved", context.day, **values,
                                 subject_body_id=loser.body_id, death_decision=decision)
            consequences = decision.events
        else:
            raise AssertionError("A registered item has no effect implementation")
        events = (effect, *consequences, self._event("item_used", context.day, **values))
        if activation_id is not None:
            self._activation_receipts[activation_id] = identity, events
        return events

    def can_open_vote(self, context: ItemUseContext, conversation_entries: int, minimum: int) -> bool:
        actor = next((body for body in context.bodies if body.body_id == context.actor_body_id), None)
        return (context.phase == "conversation" and not context.ai_busy and not context.required_player_reply
                and context.owner_id == self.owner_id and context.day >= 1
                and actor is not None and actor.alive and actor.body_id not in self._known_dead
                and self._active_vote_id is None
                and (conversation_entries >= minimum or self._early_vote_day == context.day))

    def consume_bonus_action(self, action_id: str, day: int) -> bool:
        if not isinstance(action_id, str) or not action_id:
            raise ItemRuleError("An extra action requires a stable identifier")
        if action_id in self._bonus_receipts:
            if self._bonus_receipts[action_id] != day:
                raise ItemRuleError("This extra action belongs to another night")
            return False
        if self._bonus_actions < 1 or self._bonus_action_day != day:
            raise ItemRuleError("There is no extra action available for this night")
        self._bonus_actions -= 1
        self._bonus_receipts[action_id] = day
        return True

    def _on_kill(self, decision: DeathDecision, corpse: BodyState, phase: str | None, room_id: str | None) -> tuple[ItemEvent, ...]:
        events = []
        if not corpse.is_npc or decision.cause == DeathCause.EXECUTION:
            return ()
        reservation = next((value for value in self._reserved_assignments.values()
                            if value.mind_id == corpse.mind_identity), None)
        if (reservation is not None and phase == "action" and reservation.due_day == decision.day
                and reservation.room_id == room_id):
            events.append(self._event("reserved_room_kill", decision.day, item_id="room_reservation",
                                      target_body_id=corpse.body_id, room_id=room_id))
        if self._prepared_note is not None:
            note = self._prepared_note
            self._prepared_note = None
            events.append(self._event("prepared_note_bound", decision.day, item_id="forged_note",
                                      actor_body_id=note.actor_body_id, target_body_id=corpse.body_id,
                                      message=note.message))
        if (self._marked_target is not None and phase == "action"
                and self._marked_target.due_day == decision.day
                and self._marked_target.target_body_id == corpse.body_id):
            self._marked_target = None
            self._bonus_actions += 1
            self._bonus_action_day = decision.day
            events.append(self._event("extra_action_earned", decision.day, item_id="watch_this_drive",
                                      target_body_id=corpse.body_id, amount=1, due_day=decision.day))
        return tuple(events)

    @staticmethod
    def _owners(owners: Mapping[str, str] | None, bodies: Mapping[str, BodyState]) -> dict[str, str]:
        values = {} if owners is None else dict(owners)
        if any(body_id not in bodies or not isinstance(owner, str) or not owner
               for body_id, owner in values.items()):
            raise ItemRuleError("Ownership must identify supplied bodies and nonempty owners")
        return values

    def resolve_entry(
        self, entry_id: str, room_id: str, entrants: Iterable[BodyState], day: int,
        *, owners: Mapping[str, str] | None = None,
    ) -> EffectResolution:
        bodies = _body_map(entrants)
        ownership = self._owners(owners, bodies)
        if not isinstance(entry_id, str) or not entry_id or not room_id or day < 1 or not bodies:
            raise ItemRuleError("A room entry requires an identifier, a room, a day and entrants")
        identity = (room_id, day, tuple(sorted((body.body_id, body.label, body.is_npc) for body in bodies.values())),
                    tuple(sorted(ownership.items())))
        if entry_id in self._entry_receipts:
            previous, original = self._entry_receipts[entry_id]
            if previous != identity:
                raise ItemRuleError("This entry identifier already describes another group")
            return EffectResolution(tuple(replace(decision, is_new=False, events=())
                                          for decision in original.decisions), (), False)
        if any(not body.alive or body.body_id in self._known_dead for body in bodies.values()):
            raise ItemRuleError("Only living bodies can enter a room")
        trap = self._traps.get(room_id)
        if trap is None or trap.armed_day > day:
            result = EffectResolution()
        else:
            del self._traps[room_id]
            decisions = tuple(self.attempt_death(
                f"trap:{trap.effect_id}:{body.body_id}", body, day, ownership.get(body.body_id),
                cause=DeathCause.TRAP, source_id=entry_id,
                caused_by_owner_id=trap.owner_id, phase="action", room_id=room_id)
                for body in sorted(bodies.values(), key=lambda value: value.body_id))
            trigger = self._event("room_trap_triggered", day, item_id="booby_trap",
                                  actor_body_id=trap.actor_body_id, room_id=room_id)
            result = EffectResolution(decisions, (trigger, *(event for decision in decisions for event in decision.events)))
        self._entry_receipts[entry_id] = identity, result
        return result

    def resolve_due_curses(
        self, phase_id: str, day: int, bodies: Iterable[BodyState],
        *, owners: Mapping[str, str] | None = None,
    ) -> EffectResolution:
        body_map = _body_map(bodies)
        ownership = self._owners(owners, body_map)
        if not isinstance(phase_id, str) or not phase_id or day < 1:
            raise ItemRuleError("A curse boundary requires a stable identifier and day")
        identity = (day, tuple(sorted((body.body_id, body.label, body.is_npc) for body in body_map.values())),
                    tuple(sorted(ownership.items())))
        if phase_id in self._curse_receipts:
            previous, original = self._curse_receipts[phase_id]
            if previous != identity:
                raise ItemRuleError("This boundary identifier already describes another state")
            return EffectResolution(tuple(replace(decision, is_new=False, events=())
                                          for decision in original.decisions), (), False)
        due = sorted((curse for curse in self._curses.values() if curse.due_day <= day),
                     key=lambda curse: curse.due_day)
        if any(curse.target_body_id not in body_map for curse in due):
            raise ItemRuleError("Every due curse requires its target body snapshot")
        decisions, events = [], []
        for curse in due:
            body = body_map[curse.target_body_id]
            decision = self.attempt_death(
                "curse:" + curse.effect_id, body, day, ownership.get(body.body_id),
                cause=DeathCause.CURSE, source_id=phase_id,
                caused_by_owner_id=curse.owner_id, phase="action")
            del self._curses[curse.effect_id]
            decisions.append(decision)
            events.append(self._event("body_curse_resolved", day, item_id="occult_ritual",
                                      actor_body_id=curse.actor_body_id, target_body_id=body.body_id,
                                      death_decision=decision))
            events.extend(decision.events)
        result = EffectResolution(tuple(decisions), tuple(events))
        self._curse_receipts[phase_id] = identity, result
        return result

    def is_muffled(self, body_id: str) -> bool:
        return body_id in self._muffles

    def speech_override(self, body_id: str) -> SpeechProposal | None:
        if body_id in self._muffles:
            return SpeechProposal(None, MUFFLED_SPEECH)
        return None

    def begin_night(self, day: int, bodies: Iterable[BodyState]) -> tuple[ItemEvent, ...]:
        if day < 1 or self._active_vote_id is not None:
            raise ItemRuleError("The next night cannot begin before voting is resolved")
        snapshots = _body_map(bodies)
        living = {body_id for body_id, body in snapshots.items() if body.alive and body_id not in self._known_dead}
        living_minds = {body.mind_identity for body in snapshots.values() if body.body_id in living}
        self._reservations = {mind: reservation for mind, reservation in self._reservations.items()
                              if mind in living_minds and reservation.due_day >= day}
        self._reserved_assignments = {body_id: reservation for body_id, reservation in self._reserved_assignments.items()
                                      if body_id in living and reservation.due_day >= day}
        self._proxy_voters = {body_id: round_number for body_id, round_number in self._proxy_voters.items()
                              if round_number >= day}
        if self._early_vote_day is not None and self._early_vote_day < day:
            self._early_vote_day = None
        if self._marked_target is not None and self._marked_target.due_day < day:
            self._marked_target = None
        if self._bonus_action_day is not None and self._bonus_action_day < day:
            self._bonus_actions = 0
            self._bonus_action_day = None
        events = []
        for body_id, effect in tuple(self._muffles.items()):
            if effect.expires_day <= day:
                del self._muffles[body_id]
                if body_id in living:
                    events.append(self._event("muffle_expired", day, item_id="muffle",
                                              target_body_id=body_id, private_text=MUFFLE_EXPIRED))
        return tuple(events)

    def guardian_protects(self, body_id: str, target_owner_id: str | None = None) -> bool:
        return (body_id in self._body_protections if self._guardian_scope == "body"
                else self._protection_armed and target_owner_id == self.owner_id)

    def intercept_guardian(self, attempt_id: str, target: BodyState, day: int,
                           target_owner_id: str | None = None) -> DeathDecision | None:
        """Resolve protection alone; an unprotected target never enters the death path."""
        if not attempt_id or day < 1:
            raise ItemRuleError("A protection attempt requires an identifier and a day")
        previous = self._death_decisions.get(attempt_id)
        if previous is not None:
            if previous[1].protection_item_id != "guardian_angel":
                raise ItemRuleError("This attempt already describes another death resolution")
            return self.attempt_death(attempt_id, target, day, target_owner_id)
        if (not target.alive or target.body_id in self._known_dead
                or not self.guardian_protects(target.body_id, target_owner_id)):
            return None
        return self.attempt_death(attempt_id, target, day, target_owner_id)

    def attempt_death(
        self,
        attempt_id: str,
        target: BodyState,
        day: int,
        target_owner_id: str | None = None,
        *,
        cause: DeathCause = DeathCause.ATTACK,
        source_id: str | None = None,
        caused_by_owner_id: str | None = None,
        phase: str | None = None,
        room_id: str | None = None,
    ) -> DeathDecision:
        if not attempt_id or day < 1:
            raise ItemRuleError("A death attempt requires an identifier and a day")
        try:
            cause = DeathCause(cause)
        except (TypeError, ValueError):
            raise ItemRuleError("A death attempt requires a known cause") from None
        if source_id is not None and (not isinstance(source_id, str) or not source_id):
            raise ItemRuleError("A death source identifier must be a nonempty string")
        if caused_by_owner_id is not None and (not isinstance(caused_by_owner_id, str) or not caused_by_owner_id
                                               or phase not in {"action", "conversation", "voting"}):
            raise ItemRuleError("A caused death requires its owner and phase")
        identity = (target.body_id, target.label, day, target_owner_id, cause, source_id, caused_by_owner_id, phase, room_id)
        if attempt_id in self._death_decisions:
            original_identity, original = self._death_decisions[attempt_id]
            if identity != original_identity:
                raise ItemRuleError("This death attempt has already been resolved for another target")
            return replace(original, is_new=False, events=())
        if cause == DeathCause.EXECUTION:
            resolved = self._resolved_votes.get(source_id)
            if (resolved is None or resolved[1].outcome != "execution"
                    or resolved[1].executed_body_id != target.body_id
                    or self._votes[source_id].day != day):
                raise ItemRuleError("An execution must identify its resolved vote and selected body")
            if source_id in self._execution_attempts:
                original = self._execution_attempts[source_id]
                if self._death_decisions[original.attempt_id][0] != identity:
                    raise ItemRuleError("This execution has already resolved with another identity")
                self._death_decisions[attempt_id] = identity, original
                return replace(original, is_new=False, events=())
        events = ()
        protection = None
        if not target.alive or target.body_id in self._known_dead:
            outcome = "already_dead"
        elif cause == DeathCause.EXECUTION and target.body_id in self._votes[source_id].execution_stays:
            outcome = "prevented"
            protection = "stay_of_execution"
            events = (self._event("execution_stayed", day, item_id=protection, target_body_id=target.body_id),)
        elif self.guardian_protects(target.body_id, target_owner_id):
            if self._guardian_scope == "body":
                self._body_protections.remove(target.body_id)
            else:
                self._protection_armed = False
            outcome = "prevented"
            protection = "guardian_angel"
            events = (self._event("guardian_intervened", day, item_id="guardian_angel",
                                  target_body_id=target.body_id,
                                  public_text=GUARDIAN_PUBLIC.format(target=target.label)),)
        else:
            outcome = "killed"
            self._known_dead.add(target.body_id)
        decision = DeathDecision(attempt_id, target.body_id, outcome, True, events,
                                 cause, source_id, protection, day)
        if decision.killed and caused_by_owner_id == self.owner_id:
            decision = replace(decision, events=(*decision.events, *self._on_kill(decision, target, phase, room_id)))
        if decision.killed:
            self._reservations.pop(target.mind_identity, None)
            self._reserved_assignments.pop(target.body_id, None)
        self._death_decisions[attempt_id] = identity, decision
        if cause == DeathCause.EXECUTION:
            self._execution_attempts[source_id] = decision
        return decision

    def open_vote(
        self,
        vote_id: str,
        voter_bodies: Iterable[BodyState],
        owner_body_id: str,
        *,
        day: int,
        proxy_ballots: Mapping[str, str] | None = None,
    ) -> VoteSnapshot:
        bodies = tuple(sorted(_body_map(voter_bodies).values(), key=lambda body: body.body_id))
        if not vote_id or day < 1 or not bodies or any(not body.alive or body.body_id in self._known_dead for body in bodies):
            raise ItemRuleError("Voting requires a valid identifier and living voters")
        if owner_body_id not in {body.body_id for body in bodies}:
            raise ItemRuleError("The player's current body must be among the voters")
        if vote_id in self._votes:
            snapshot = self._votes[vote_id]
            if (snapshot.voter_bodies, snapshot.owner_body_id, snapshot.day) != (bodies, owner_body_id, day):
                raise ItemRuleError("This vote has already opened with different voters")
            if proxy_ballots is not None and dict(proxy_ballots) != dict(snapshot.proxy_votes):
                raise ItemRuleError("This vote has already opened with different proxy ballots")
            return snapshot
        if self._active_vote_id is not None:
            raise ItemRuleError("Resolve the current vote before opening another")
        voters = {body.body_id for body in bodies}
        proxies = {} if proxy_ballots is None else dict(proxy_ballots)
        expected_proxies = {body_id for body_id, round_number in self._proxy_voters.items()
                            if round_number == day and body_id in voters}
        if self._skip_next_vote:
            if proxies:
                raise ItemRuleError("A skipped vote does not take proxy ballots")
        elif (set(proxies) != expected_proxies
              or any(not isinstance(target, str) or target == voter
                     or (target != "NONE" and target not in voters) for voter, target in proxies.items())):
            raise ItemRuleError("Choose one legal proxy ballot for each reserved living voter")
        snapshot = VoteSnapshot(
            vote_id, bodies, owner_body_id, day,
            0 if self._skip_next_vote else self._pending_ballot_bonus,
            tuple(sorted(proxies.items())),
            () if self._skip_next_vote else tuple(sorted(self._execution_stays & voters)),
            self._skip_next_vote)
        self._votes[vote_id] = snapshot
        self._active_vote_id = vote_id
        return snapshot

    def resolve_vote(self, vote_id: str, ballots: Mapping[str, str]) -> VoteResult:
        snapshot = self._votes.get(vote_id)
        if snapshot is None:
            raise ItemRuleError("This vote has not opened")
        labels = {body.body_id: body.label for body in snapshot.voter_bodies}
        ballots = dict(ballots)
        if snapshot.skipped:
            if ballots:
                raise ItemRuleError("A skipped vote cannot contain ballots")
            if vote_id in self._resolved_votes:
                return replace(self._resolved_votes[vote_id][1], is_new=False, events=())
            event = self._event("vote_skipped", snapshot.day, item_id="presidential_decree")
            result = VoteResult(vote_id, {}, "skipped", None, (), True, (event,))
            self._skip_next_vote = False
            self._proxy_voters = {body_id: day for body_id, day in self._proxy_voters.items() if day > snapshot.day}
            self._early_vote_day = None
            self._active_vote_id = None
            self._resolved_votes[vote_id] = (), result
            return result
        for voter, target in snapshot.proxy_votes:
            if voter in ballots and ballots[voter] != target:
                raise ItemRuleError("A reserved proxy ballot cannot be replaced by another response")
            ballots[voter] = target
        if set(ballots) != set(labels):
            raise ItemRuleError("Every living voter must submit one ballot")
        if any(not isinstance(target, str) or target == voter or (target != "NONE" and target not in labels)
               for voter, target in ballots.items()):
            raise ItemRuleError("A ballot must name another living suspect or NONE")
        identity = tuple(sorted(ballots.items()))
        if vote_id in self._resolved_votes:
            original_identity, original = self._resolved_votes[vote_id]
            if identity != original_identity:
                raise ItemRuleError("This vote has already resolved with different ballots")
            return replace(original, is_new=False, events=())
        counts = {}
        events = []
        weights = snapshot.weights
        for voter, target in identity:
            weight = weights[voter]
            counts[target] = counts.get(target, 0) + weight
            if voter in snapshot.proxy_ballots:
                events.append(self._event("proxy_ballot_counted", snapshot.day, item_id="mail_in_ballot",
                                          target_body_id=voter, subject_body_id=target if target != "NONE" else None,
                                          amount=weight))
            if weight > 1:
                events.append(self._event("weighted_vote", snapshot.day, item_id="spare_paper", target_body_id=voter,
                                          public_text=WEIGHTED_BALLOT_PUBLIC.format(
                                              voter=labels[voter], target=labels.get(target, "NONE"), weight=weight)))
        leaders = tuple(sorted(target for target, count in counts.items() if count == max(counts.values())))
        outcome = "tie" if len(leaders) > 1 else ("skip" if leaders[0] == "NONE" else "execution")
        executed = leaders[0] if outcome == "execution" else None
        result = VoteResult(vote_id, counts, outcome, executed, leaders if outcome == "tie" else (), True, tuple(events))
        self._pending_ballot_bonus -= snapshot.bonus
        self._execution_stays.clear()
        self._proxy_voters = {body_id: day for body_id, day in self._proxy_voters.items() if day > snapshot.day}
        self._early_vote_day = None
        self._active_vote_id = None
        self._resolved_votes[vote_id] = identity, result
        return result
