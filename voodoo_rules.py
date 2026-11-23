"""Mechanical Voodoo transitions, intentionally independent of NPC narration."""

from dataclasses import dataclass, replace
from typing import Literal

from game_rules import BodyState, DeathDecision, ItemRuleError, PlayerItemRules
from identity_state import IdentityState


@dataclass(frozen=True)
class VoodooResolution:
    """A decision and a separately classified application payload.

    ``is_new`` describes resolution creation, never whether an adapter applied it.
    ``application_state`` classifies the identity transition, not death/event completion:
    pending means not installed, current means installed, superseded means replaced.
    Pending applications install ``identity`` before processing the corpse obligation.
    Pending/current applications replay stable protection events and the discarded-body
    death through the adapter's event/attempt receipts, even when ``is_new`` is false.
    Superseded applications contain no remaining side effects and retain current state.
    """
    attempt_id: str
    outcome: str
    identity: IdentityState
    discarded_body_id: str | None = None
    protection: DeathDecision | None = None
    is_new: bool = True
    application_state: Literal["pending", "current", "superseded"] = "pending"

    @property
    def identity_application_required(self) -> bool:
        return self.application_state == "pending"


class VoodooResolver:
    def __init__(self):
        self._receipts = {}

    def _reconcile(self, before: IdentityState, result: VoodooResolution,
                   current: IdentityState) -> VoodooResolution:
        if current == before:
            state, identity = "pending", result.identity
        elif current == result.identity:
            state, identity = "current", current
        elif current.revision > result.identity.revision:
            state, identity = "superseded", current
        else:
            raise ItemRuleError("The identity revision diverges from this Voodoo attempt's recorded states")
        protection = result.protection
        if protection is not None:
            protection = replace(protection, is_new=False,
                                 events=() if state == "superseded" else protection.events)
        return replace(result, identity=identity, protection=protection, is_new=False,
                       discarded_body_id=None if state == "superseded" else result.discarded_body_id,
                       application_state=state)

    def resolve(self, attempt_id: str, identity: IdentityState, actor_body_id: str,
                target_body_id: str, bodies: tuple[BodyState, ...], day: int,
                guardians: tuple[PlayerItemRules, ...] = ()) -> VoodooResolution:
        if not isinstance(attempt_id, str) or not attempt_id.strip() or type(day) is not int or day < 1:
            raise ItemRuleError("A Voodoo attempt requires a nonempty identifier and positive day")
        request = actor_body_id, target_body_id, day
        if attempt_id in self._receipts:
            original, before, result = self._receipts[attempt_id]
            if original != request:
                raise ItemRuleError("This Voodoo attempt already identifies another attack")
            return self._reconcile(before, result, identity)
        by_id = {body.body_id: body for body in bodies}
        actor, target = by_id.get(actor_body_id), by_id.get(target_body_id)
        if (len(by_id) != len(bodies) or actor is None or target is None
                or not actor.alive or not target.alive or actor_body_id == target_body_id
                or actor_body_id != identity.player_body_id
                or identity.mind_for_body(target_body_id) is None):
            raise ItemRuleError("Voodoo requires the player's living body and another living occupied body")
        target_owner = identity.mind_for_body(target_body_id)
        providers = [rules for rules in guardians if rules.guardian_protects(target_body_id, target_owner)]
        if len(providers) > 1:
            raise ItemRuleError("A body's Guardian must have one authoritative protection provider")
        mirrored = identity.clone_appearance(actor_body_id, target_body_id) if providers else None
        perceived_target = replace(target, label=identity.appearance_name(target_body_id)) if providers else target
        protection = (providers[0].intercept_guardian(attempt_id, perceived_target, day, target_owner)
                      if providers else None)
        if providers and protection is None:
            raise ItemRuleError("The protection provider rejects this living target snapshot")
        if protection is not None:
            result = VoodooResolution(attempt_id, "mirrored", mirrored, protection=protection)
        else:
            result = VoodooResolution(attempt_id, "exchange", identity.exchange(target_body_id), actor_body_id)
        self._receipts[attempt_id] = request, identity, result
        return result
