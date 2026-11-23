"""Immutable personal experiences with explicitly controlled active stages."""

from dataclasses import dataclass, replace

from ai_context import CurrentFeeling, MemoryRecord


def _identifier(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("An experience requires nonempty identifiers and text")


def _round(value):
    if type(value) is not int or value < 1:
        raise ValueError("An experience requires a positive round")


@dataclass(frozen=True)
class PrivateExperienceStage:
    stage_id: str
    round_number: int
    current_feeling: CurrentFeeling
    past_text: str
    subject_id: str
    source: str = "self"
    closed_round: int | None = None

    def __post_init__(self):
        for value in (self.stage_id, self.past_text, self.subject_id, self.source):
            _identifier(value)
        _round(self.round_number)
        if not isinstance(self.current_feeling, CurrentFeeling):
            raise ValueError("An active experience requires a typed feeling")
        if self.closed_round is not None:
            _round(self.closed_round)
            if self.closed_round < self.round_number:
                raise ValueError("An experience cannot end before it began")


@dataclass(frozen=True)
class PrivateExperienceProjection:
    experiences: tuple[MemoryRecord, ...]
    current_feelings: tuple[CurrentFeeling, ...]


@dataclass(frozen=True)
class PrivateExperienceStore:
    """Owned by one experiencer's private data, independently of their body."""

    stages: tuple[PrivateExperienceStage, ...] = ()
    forgotten_stage_ids: tuple[str, ...] = ()
    recall_round: int = 1

    def __post_init__(self):
        object.__setattr__(self, "stages", tuple(self.stages))
        object.__setattr__(self, "forgotten_stage_ids", tuple(self.forgotten_stage_ids))
        _round(self.recall_round)
        if (any(not isinstance(stage, PrivateExperienceStage) for stage in self.stages)
                or len({stage.stage_id for stage in self.stages}) != len(self.stages)):
            raise ValueError("An experience store requires unique typed stages")
        if not set(self.forgotten_stage_ids) <= {stage.stage_id for stage in self.stages}:
            raise ValueError("Forgotten experiences must identify existing stages")

    def start(self, stage: PrivateExperienceStage) -> "PrivateExperienceStore":
        if not isinstance(stage, PrivateExperienceStage) or stage.closed_round is not None:
            raise ValueError("Only an open experience can start")
        original = next((entry for entry in self.stages if entry.stage_id == stage.stage_id), None)
        if original is not None:
            if replace(original, closed_round=None) != stage:
                raise ValueError("This experience identifier has different provenance")
            return self
        return replace(self, stages=(*self.stages, stage))

    def finish(self, stage_id: str, closed_round: int) -> "PrivateExperienceStore":
        _identifier(stage_id)
        _round(closed_round)
        original = next((stage for stage in self.stages if stage.stage_id == stage_id), None)
        if original is None:
            raise ValueError("This experience has not started")
        if original.closed_round is not None:
            if original.closed_round != closed_round:
                raise ValueError("This experience already ended at another time")
            return self
        completed = replace(original, closed_round=closed_round)
        return replace(self, stages=tuple(completed if stage.stage_id == stage_id else stage
                                         for stage in self.stages))

    def forget_recollections(self, day: int) -> "PrivateExperienceStore":
        """Keep immutable stages and active state while starting a new recall boundary."""
        _round(day)
        if day < self.recall_round:
            raise ValueError("A recall boundary cannot move backwards")
        return replace(self, recall_round=day, forgotten_stage_ids=tuple(
            stage.stage_id for stage in self.stages
            if stage.closed_round is not None or stage.stage_id in self.forgotten_stage_ids))

    def project(self) -> PrivateExperienceProjection:
        return PrivateExperienceProjection(
            tuple(MemoryRecord(max(stage.round_number, self.recall_round), "experience", stage.past_text, stage.source)
                  for stage in self.stages
                  if stage.closed_round is not None and stage.stage_id not in self.forgotten_stage_ids),
            tuple(stage.current_feeling for stage in self.stages if stage.closed_round is None),
        )
