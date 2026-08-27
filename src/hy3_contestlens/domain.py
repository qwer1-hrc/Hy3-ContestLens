from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStatus(StrEnum):
    CREATED = "CREATED"
    DISCOVERING_RESOURCES = "DISCOVERING_RESOURCES"
    WAITING_FOR_RESOURCE_CONFIRMATION = "WAITING_FOR_RESOURCE_CONFIRMATION"
    ANALYZING = "ANALYZING"
    SOLVING = "SOLVING"
    REVIEWING = "REVIEWING"
    COMPILING = "COMPILING"
    JUDGING = "JUDGING"
    LOCALIZING = "LOCALIZING"
    REPAIRING = "REPAIRING"
    REJUDGING = "REJUDGING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Verdict(StrEnum):
    OK = "OK"
    AC = "AC"
    CE = "CE"
    WA = "WA"
    TLE = "TLE"
    MLE = "MLE"
    RE = "RE"
    OLE = "OLE"
    IO_CONFLICT = "IO_CONFLICT"
    SANDBOX_VIOLATION = "SANDBOX_VIOLATION"
    INVALID_PROBLEM_MANIFEST = "INVALID_PROBLEM_MANIFEST"
    SANDBOX_UNAVAILABLE = "SANDBOX_UNAVAILABLE"


class StepVerdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"


class ErrorType(StrEnum):
    STATEMENT_MISREAD = "STATEMENT_MISREAD"
    CONSTRAINT_OMISSION = "CONSTRAINT_OMISSION"
    WRONG_ALGORITHM = "WRONG_ALGORITHM"
    PROOF_GAP = "PROOF_GAP"
    CIRCULAR_REASONING = "CIRCULAR_REASONING"
    HALLUCINATED_CLAIM = "HALLUCINATED_CLAIM"
    COMPLEXITY_TLE = "COMPLEXITY_TLE"
    COMPLEXITY_MLE = "COMPLEXITY_MLE"
    BOUNDARY_ERROR = "BOUNDARY_ERROR"
    INTEGER_OVERFLOW = "INTEGER_OVERFLOW"
    STATE_TRANSITION_ERROR = "STATE_TRANSITION_ERROR"
    IMPLEMENTATION_MISMATCH = "IMPLEMENTATION_MISMATCH"
    IO_OR_FORMAT_ERROR = "IO_OR_FORMAT_ERROR"
    COMPILE_ERROR = "COMPILE_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    RESULT_CORRECT_PROCESS_INVALID = "RESULT_CORRECT_PROCESS_INVALID"
    UNRESOLVED = "UNRESOLVED"


class ResourceLimits(StrictModel):
    time_ms: int = Field(gt=0)
    memory_mb: Annotated[StrictInt, Field(gt=0)] | None = None
    output_bytes: int = Field(default=16 * 1024 * 1024, gt=0)

    @field_validator("memory_mb")
    @classmethod
    def valid_memory(cls, value: int | None) -> int | None:
        if value is not None and (isinstance(value, bool) or value <= 0):
            raise ValueError("memory_mb must be a positive integer or null")
        return value


class IOConfig(StrictModel):
    basename: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    input_mode: Literal["stdin_and_file"] = "stdin_and_file"
    output_mode: Literal["stdout_or_file"] = "stdout_or_file"


class JudgeConfig(StrictModel):
    comparator: Literal["noip_fulltext"] = "noip_fulltext"
    input_glob: str = "tests/*.in"
    expected_glob: str = "expected/*.out"
    score_per_test: float = Field(gt=0)


class ProblemManifest(StrictModel):
    schema_version: Literal[1]
    dataset_id: Literal["noip2018"]
    problem_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title_zh: str
    day: Literal[1, 2]
    luogu_difficulty: str | None = None
    resource_limits: ResourceLimits
    io: IOConfig
    judge: JudgeConfig


class ReasoningStep(StrictModel):
    step_id: str
    goal: str
    statement: str
    dependencies: list[str] = Field(default_factory=list)
    invariant: str | None = None
    justification: str


class Complexity(StrictModel):
    time: str
    space: str


class CodeStepMap(StrictModel):
    step_id: str
    code_location: str


class SolverOutput(StrictModel):
    problem_summary: str
    assumptions: list[str] = Field(default_factory=list)
    steps: list[ReasoningStep]
    proof_obligations: list[str] = Field(default_factory=list)
    complexity: Complexity
    boundary_cases: list[str] = Field(default_factory=list)
    cpp_source: str
    code_step_map: list[CodeStepMap] = Field(default_factory=list)


class StepAssessment(StrictModel):
    step_id: str
    verdict: StepVerdict
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class CriticReview(StrictModel):
    reviewer: Literal["algorithm_critic", "code_critic"]
    assessments: list[StepAssessment] = Field(default_factory=list)
    error_type: ErrorType = ErrorType.UNRESOLVED
    first_error_step_id: str | None = None
    code_location: str | None = None
    summary: str


class Diagnosis(StrictModel):
    error_type: ErrorType
    first_error_step_id: str | None = None
    code_location: str | None = None
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    final_result_correct: bool
    process_correct: bool
    repair_suggestion: str | None = None


class CompileResult(StrictModel):
    verdict: Verdict
    compile_artifact_id: str | None = None
    compiler: str | None = None
    duration_ms: int = 0
    diagnostics: list[str] = Field(default_factory=list)
    binary_sha256: str | None = None
    source_artifact_id: str


class TestResult(StrictModel):
    test_id: str
    verdict: Verdict
    cpu_ms: int = 0
    wall_ms: int = 0
    peak_rss_mb: float = 0
    memory_limit_mb: int
    memory_limit_source: Literal["problem_manifest", "default"]
    expected_sha256: str
    actual_sha256: str | None = None
    first_diff: dict[str, Any] | None = None
    exit_code: int | None = None


class CheckResult(StrictModel):
    check_id: str
    verdict: Verdict
    problem_id: str
    dataset_id: str
    passed: int
    total: int
    score: float
    tests: list[TestResult] = Field(default_factory=list)
