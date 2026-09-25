"""Small, serialization-friendly contracts for manual and meeting documents."""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class Evidence:
    frame_path: str
    timestamp: float
    frame_qa: str = "not_reviewed"
    semantic_match: bool = False
    ocr_match: bool = False
    qualification: str = "unconfirmed"


@dataclass
class SemanticCandidate:
    """Manual-only candidate with enriched wording and auditable provenance."""
    text: str
    # Explicit enriched fields are accepted by the compatibility constructor.
    title: Optional[str] = None
    description: Optional[str] = None
    source_segment_ids: List[str] = field(default_factory=list)
    frame_ids: List[str] = field(default_factory=list)
    curator_decision: str = "unknown"
    enriched_step_id: Optional[str] = None
    qualification: str = "unknown"
    evidence_status: str = "missing"
    semantic_intent: Optional[str] = None
    procedure_id: Optional[str] = None
    start: float = 0.0
    end: float = 0.0
    local_context: str = ""
    visible_ui: Any = None


@dataclass
class ManualStepContract:
    action: str
    ui_target: str
    value: str
    expected_result: str
    condition_warning: Optional[str]
    time_range: Tuple[float, float]
    evidence: List[Any] = field(default_factory=list)
    qualification: str = "unconfirmed"
    status: str = "DRAFT"
    source_segment_ids: List[str] = field(default_factory=list)
    frame_ids: List[str] = field(default_factory=list)
    curator_decision: str = "unknown"
    enriched_step_id: Optional[str] = None
    evidence_status: str = "missing"
    semantic_intent: Optional[str] = None
    procedure_id: Optional[str] = None


@dataclass
class ProcedureGroup:
    """A semantic procedure, not a fixed video interval."""
    title: str
    steps: List[Any] = field(default_factory=list)
    goal: str = "DO POTWIERDZENIA"


@dataclass
class ManualDocument:
    title: str
    steps: List[Any] = field(default_factory=list)
    procedure_groups: List[ProcedureGroup] = field(default_factory=list)
    goal: str = "DO POTWIERDZENIA"
    preconditions: List[str] = field(default_factory=list)
    status: str = "DRAFT"
    completeness: str = "UNKNOWN"
    evidence: List[Any] = field(default_factory=list)
    rejected_candidates: List[Any] = field(default_factory=list)
    source_candidate_count: int = 0

    def all_steps(self) -> List[Any]:
        if self.procedure_groups:
            return [step for group in self.procedure_groups for step in group.steps]
        return list(self.steps)

    def accounting(self) -> Dict[str, int]:
        """Return accounting over the canonical post-dedup candidate stream.

        Invariant: ``source_candidates == client_steps + rejected_steps``.
        ``rejected_internal`` is the audit projection of those same rejected
        steps, so it must equal ``rejected_steps``; it is not an extra source.
        """
        steps = self.all_steps()
        client_steps = sum(step.status in {"CONFIRMED", "PARTIAL"} for step in steps)
        rejected_steps = sum(step.status == "REJECTED" for step in steps)
        rejected_internal = len(self.rejected_candidates)
        if self.source_candidate_count != client_steps + rejected_steps:
            raise ValueError("Manual accounting invariant violated: source candidates do not match canonical steps")
        if rejected_internal != rejected_steps:
            raise ValueError("Manual accounting invariant violated: rejected audit projection diverges from rejected steps")
        return {
            "source_candidates": self.source_candidate_count,
            "client_steps": client_steps,
            "rejected_internal": rejected_internal,
            "rejected_steps": rejected_steps,
        }

    def __post_init__(self) -> None:
        if self.procedure_groups and not self.steps:
            self.steps = self.all_steps()
        if self.steps and self.completeness == "UNKNOWN":
            self.completeness = "PARTIAL"


@dataclass
class MeetingDocument:
    title: str = ""
    decisions: List[Any] = field(default_factory=list)
    agreements: List[Any] = field(default_factory=list)
    action_items: List[Any] = field(default_factory=list)
    open_questions: List[Any] = field(default_factory=list)
    risks: List[Any] = field(default_factory=list)
    participants: List[Any] = field(default_factory=list)
    evidence: List[Any] = field(default_factory=list)
    status: str = "DRAFT"


ExistsCallback = Callable[[str], bool]
