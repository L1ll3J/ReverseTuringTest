"""Love Potion episodes owned by the NPC who experienced them."""

from dataclasses import dataclass, replace

from ai_context import CurrentFeeling, FeelingKind
from private_experiences import PrivateExperienceStage, PrivateExperienceStore, _identifier, _round


PAST_INFATUATION = (
    "You were madly in love with {giver}. Your devotion felt natural and deeply personal, "
    "and you wanted to protect them, praise them and win their affection."
)
PAST_HANGOVER = (
    "Following your intense infatuation with {giver} subsiding, you initially felt both embarrassed "
    "by your devotion and stripped of your autonomy so deeply that it brought you an intensity of "
    "bitterness that you have never felt before. You are not entirely sure what came over you, "
    "but it was an intense whirlwind of emotion, and it seemed to lay completely out of your control."
)


@dataclass(frozen=True)
class LoveDose:
    dose_id: str
    day: int
    giver_body_id: str
    giver: str

    def __post_init__(self):
        for value in (self.dose_id, self.giver_body_id, self.giver):
            _identifier(value)
        _round(self.day)

    def stage(self, kind, day):
        text = PAST_INFATUATION if kind == FeelingKind.INFATUATION else PAST_HANGOVER
        return PrivateExperienceStage(
            self.dose_id + ":" + kind.value, day, CurrentFeeling(kind, self.giver),
            text.format(giver=self.giver), self.giver_body_id,
        )


@dataclass(frozen=True)
class LovePotionHistory:
    experiences: PrivateExperienceStore = PrivateExperienceStore()
    doses: tuple[LoveDose, ...] = ()
    pending: LoveDose | None = None
    last_night: int = 0
    last_discussion: int = 0

    def apply(self, dose: LoveDose) -> "LovePotionHistory":
        if not isinstance(dose, LoveDose):
            raise ValueError("A Love Potion requires a captured dose")
        original = next((entry for entry in self.doses if entry.dose_id == dose.dose_id), None)
        if original is not None:
            if original != dose:
                raise ValueError("This dose identifier has different provenance")
            return self
        if dose.day < max(self.last_night, self.last_discussion):
            raise ValueError("A new dose cannot start in a past discussion")
        store = self.experiences
        for stage in store.stages:
            if stage.closed_round is None:
                store = store.finish(stage.stage_id, dose.day)
        return replace(self, experiences=store.start(dose.stage(FeelingKind.INFATUATION, dose.day)),
                       doses=(*self.doses, dose), pending=dose)

    def begin_night(self, day: int) -> "LovePotionHistory":
        _round(day)
        if day < max(self.last_night, self.last_discussion):
            raise ValueError("A night cannot precede this history")
        if day == self.last_night:
            return self
        store = self.experiences
        for stage in store.stages:
            if stage.closed_round is None and stage.round_number < day:
                store = store.finish(stage.stage_id, day)
        return replace(self, experiences=store, last_night=day)

    def begin_discussion(self, day: int, *, alive: bool) -> "LovePotionHistory":
        _round(day)
        if day < max(self.last_night, self.last_discussion):
            raise ValueError("A discussion cannot precede this history")
        if day == self.last_discussion:
            return self
        store = self.experiences
        pending = self.pending
        if pending is not None and pending.day < day:
            if alive:
                store = store.start(pending.stage(FeelingKind.BITTERNESS, day))
            pending = None
        return replace(self, experiences=store, pending=pending, last_discussion=day)
