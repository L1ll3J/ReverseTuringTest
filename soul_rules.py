"""Replay-safe Soul exchanges, independent of presentation and private memories."""

from dataclasses import dataclass, replace
from typing import Literal

from game_rules import BodyState, ItemRuleError
from identity_state import IdentityState


@dataclass(frozen=True)
class SoulResolution:
    activation_id: str
    identity: IdentityState
    old_body_id: str
    target_body_id: str
    target_mind_id: str
    original_name: str
    target_original_name: str
    old_body_name: str
    new_body_name: str
    day: int
    is_new: bool = True
    application_state: Literal["pending", "current", "superseded"] = "pending"

    @property
    def identity_application_required(self) -> bool:
        return self.application_state == "pending"

    @property
    def following_night(self) -> int:
        return self.day + 1


class SoulResolver:
    def __init__(self):
        self._receipts = {}

    def resolve(self, activation_id: str, identity: IdentityState, actor_body_id: str,
                target_body_id: str, bodies: tuple[BodyState, ...], day: int) -> SoulResolution:
        if (not isinstance(activation_id, str) or not activation_id.strip()
                or type(day) is not int or day < 1):
            raise ItemRuleError("A Soul exchange requires a nonempty identifier and positive day")
        request = actor_body_id, target_body_id, day
        if activation_id in self._receipts:
            original, before, result = self._receipts[activation_id]
            if request != original:
                raise ItemRuleError("This Soul activation already identifies another exchange")
            if identity == before:
                state, current = "pending", result.identity
            elif identity == result.identity:
                state, current = "current", identity
            elif identity.revision > result.identity.revision:
                state, current = "superseded", identity
            else:
                raise ItemRuleError("The identity revision diverges from this Soul activation's recorded states")
            return replace(result, identity=current, is_new=False, application_state=state)
        by_id = {body.body_id: body for body in bodies}
        actor, target = by_id.get(actor_body_id), by_id.get(target_body_id)
        if (len(by_id) != len(bodies) or actor is None or target is None
                or not actor.alive or not target.alive or not target.is_npc
                or actor_body_id != identity.player_body_id or actor_body_id == target_body_id
                or actor.mind_identity != identity.player_mind_id
                or identity.mind_for_body(target_body_id) is None
                or target.mind_identity != identity.mind_for_body(target_body_id)):
            raise ItemRuleError("Soul requires the player's living body and another living suspect")
        target_mind = identity.mind_for_body(target_body_id)
        original_name = identity.mind_name(identity.player_mind_id)
        target_name = identity.mind_name(target_mind)
        if original_name is None or target_name is None:
            raise ItemRuleError("Soul requires original names for both exchanging minds")
        result = SoulResolution(activation_id, identity.exchange(target_body_id), actor_body_id,
                                target_body_id, target_mind, original_name, target_name,
                                actor.perceived_name, target.perceived_name, day)
        self._receipts[activation_id] = request, identity, result
        return result
