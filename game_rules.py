"""Validated player-item effects and shared speech, death and ballot rules."""

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Iterable, Mapping
from uuid import uuid4

from ai_context import SpeechProposal
from items import ITEM_CATALOGUE, Inventory, InventoryError


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


class ItemRuleError(ValueError):
    pass


@dataclass(frozen=True)
class BodyState:
    body_id: str
    label: str
    alive: bool
    is_npc: bool

    def __post_init__(self):
        if not self.body_id or self.body_id == "NONE" or not self.label:
            raise ItemRuleError("Each body requires an identifier and a label")


@dataclass(frozen=True)
class ItemUseContext:
    day: int
    phase: str
    ai_busy: bool
    owner_id: str
    actor_body_id: str
    bodies: tuple[BodyState, ...]

    def __post_init__(self):
        object.__setattr__(self, "bodies", tuple(self.bodies))


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

    @property
    def weights(self) -> Mapping[str, int]:
        return MappingProxyType({body.body_id: 1 + (self.bonus if body.body_id == self.owner_body_id else 0)
                                 for body in self.voter_bodies})


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
    def __init__(self, owner_id: str, inventory: Inventory):
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

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def inventory(self) -> Inventory:
        return self._inventory

    @property
    def protection_armed(self) -> bool:
        return self._protection_armed

    @property
    def pending_ballot_bonus(self) -> int:
        return self._pending_ballot_bonus

    @property
    def affinities(self) -> tuple[AffinityEffect, ...]:
        return tuple(self._affinities)

    @property
    def muffles(self) -> tuple[MuffleEffect, ...]:
        return tuple(self._muffles.values())

    def _event(self, event_type: str, day: int, **values) -> ItemEvent:
        self._event_sequence += 1
        return ItemEvent(f"{self._namespace}:{self._event_sequence}", event_type, day,
                         owner_id=self.owner_id, **values)

    def use_item(
        self,
        instance_id: str,
        context: ItemUseContext,
        target_body_id: str | None = None,
    ) -> tuple[ItemEvent, ...]:
        if (context.phase != "conversation" or context.ai_busy or context.day < 1
                or context.owner_id != self.owner_id or self._active_vote_id is not None):
            raise ItemRuleError("Items can only be used at a conversation decision point")
        bodies = _body_map(context.bodies)
        actor = bodies.get(context.actor_body_id)
        if actor is None or not actor.alive:
            raise ItemRuleError("A living player is required to use an item")
        try:
            entry = self.inventory.get(instance_id)
        except InventoryError as error:
            raise ItemRuleError(str(error)) from None
        definition = ITEM_CATALOGUE[entry.item_id]
        if entry.item_id not in {"banana", "muffle", "spare_paper", "guardian_angel"}:
            raise ItemRuleError("This item has no effect implementation")
        target = bodies.get(target_body_id) if target_body_id is not None else None
        if definition.target_kind == "living_npc":
            if target is None or not target.alive or not target.is_npc or target.body_id == actor.body_id:
                raise ItemRuleError("Choose a living suspect other than yourself")
        elif target_body_id is not None:
            raise ItemRuleError("This item does not take a target")
        if entry.item_id == "muffle" and target.body_id in self._muffles:
            raise ItemRuleError("This suspect is already muffled")
        if entry.item_id == "guardian_angel" and self._protection_armed:
            raise ItemRuleError("Guardian Angel protection is already armed")

        self.inventory.consume(instance_id)
        values = {"item_id": entry.item_id, "actor_body_id": actor.body_id,
                  "target_body_id": target.body_id if target is not None else None}
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
        elif entry.item_id == "spare_paper":
            self._pending_ballot_bonus += 2
            effect = self._event("extra_ballots_added", context.day, **values)
        else:
            self._protection_armed = True
            effect = self._event("guardian_armed", context.day, **values)
        return effect, self._event("item_used", context.day, **values)

    def speech_override(self, body_id: str) -> SpeechProposal | None:
        if body_id in self._muffles:
            return SpeechProposal(None, MUFFLED_SPEECH)
        return None

    def begin_night(self, day: int, bodies: Iterable[BodyState]) -> tuple[ItemEvent, ...]:
        if day < 1 or self._active_vote_id is not None:
            raise ItemRuleError("The next night cannot begin before voting is resolved")
        living = {body_id for body_id, body in _body_map(bodies).items() if body.alive}
        events = []
        for body_id, effect in tuple(self._muffles.items()):
            if effect.expires_day <= day:
                del self._muffles[body_id]
                if body_id in living:
                    events.append(self._event("muffle_expired", day, item_id="muffle",
                                              target_body_id=body_id, private_text=MUFFLE_EXPIRED))
        return tuple(events)

    def attempt_death(
        self,
        attempt_id: str,
        target: BodyState,
        day: int,
        target_owner_id: str | None = None,
    ) -> DeathDecision:
        if not attempt_id or day < 1:
            raise ItemRuleError("A death attempt requires an identifier and a day")
        identity = (target.body_id, target.label, day, target_owner_id)
        if attempt_id in self._death_decisions:
            original_identity, original = self._death_decisions[attempt_id]
            if identity != original_identity:
                raise ItemRuleError("This death attempt has already been resolved for another target")
            return replace(original, is_new=False, events=())
        events = ()
        if not target.alive:
            outcome = "already_dead"
        elif target_owner_id == self.owner_id and self._protection_armed:
            self._protection_armed = False
            outcome = "prevented"
            events = (self._event("guardian_intervened", day, item_id="guardian_angel",
                                  target_body_id=target.body_id,
                                  public_text=GUARDIAN_PUBLIC.format(target=target.label)),)
        else:
            outcome = "killed"
        decision = DeathDecision(attempt_id, target.body_id, outcome, True, events)
        self._death_decisions[attempt_id] = identity, decision
        return decision

    def open_vote(
        self,
        vote_id: str,
        voter_bodies: Iterable[BodyState],
        owner_body_id: str,
        *,
        day: int,
    ) -> VoteSnapshot:
        bodies = tuple(sorted(_body_map(voter_bodies).values(), key=lambda body: body.body_id))
        if not vote_id or day < 1 or not bodies or any(not body.alive for body in bodies):
            raise ItemRuleError("Voting requires a valid identifier and living voters")
        if owner_body_id not in {body.body_id for body in bodies}:
            raise ItemRuleError("The player's current body must be among the voters")
        if vote_id in self._votes:
            snapshot = self._votes[vote_id]
            if (snapshot.voter_bodies, snapshot.owner_body_id, snapshot.day) != (bodies, owner_body_id, day):
                raise ItemRuleError("This vote has already opened with different voters")
            return snapshot
        if self._active_vote_id is not None:
            raise ItemRuleError("Resolve the current vote before opening another")
        snapshot = VoteSnapshot(vote_id, bodies, owner_body_id, day, self._pending_ballot_bonus)
        self._votes[vote_id] = snapshot
        self._active_vote_id = vote_id
        return snapshot

    def resolve_vote(self, vote_id: str, ballots: Mapping[str, str]) -> VoteResult:
        snapshot = self._votes.get(vote_id)
        if snapshot is None:
            raise ItemRuleError("This vote has not opened")
        labels = {body.body_id: body.label for body in snapshot.voter_bodies}
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
            if weight > 1:
                events.append(self._event("weighted_vote", snapshot.day, item_id="spare_paper", target_body_id=voter,
                                          public_text=WEIGHTED_BALLOT_PUBLIC.format(
                                              voter=labels[voter], target=labels.get(target, "NONE"), weight=weight)))
        leaders = tuple(sorted(target for target, count in counts.items() if count == max(counts.values())))
        outcome = "tie" if len(leaders) > 1 else ("skip" if leaders[0] == "NONE" else "execution")
        executed = leaders[0] if outcome == "execution" else None
        result = VoteResult(vote_id, counts, outcome, executed, leaders if outcome == "tie" else (), True, tuple(events))
        self._pending_ballot_bonus -= snapshot.bonus
        self._active_vote_id = None
        self._resolved_votes[vote_id] = identity, result
        return result
