"""Versioned private extraction schema. Export with Extraction.model_json_schema()."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

Relevance = Literal["direct", "adjacent", "context", "uncertain", "excluded"]

class Claim(Strict):
    text: str = Field(min_length=1)
    attribution: Literal["author", "analyst"]
    evidence: list[str] = Field(min_length=1)

class Sections(Strict):
    research_questions: list[Claim] = []
    contributions: list[Claim] = []
    mechanism: list[Claim] = []
    assumptions: list[Claim] = []
    theory: list[Claim] = []
    limitations: list[Claim] = []
    future_work: list[Claim] = []
    hypotheses: list[Claim] = []

class Evidence(Strict):
    id: str
    source_url: str
    locator: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    block_id: str
    page: int | None = Field(default=None, ge=1)

class Experiment(Strict):
    id: str
    name: str
    dataset: str | None = None
    split: str | None = None
    label_budget: str | None = None
    method: str | None = None
    metric: str | None = None
    value: str | None = None
    numeric_value: float | None = None
    units: str | None = None
    direction: Literal["higher", "lower"] | None = None
    uncertainty: str | None = None
    protocol: str | None = None
    evidence: list[str] = Field(min_length=1)

class Finding(Strict):
    id: str
    name: str
    interpretation: Claim
    key_results: list[Experiment] = Field(default_factory=list, max_length=5)
    caveats: list[Claim] = []
    evidence: list[str] = Field(min_length=1)

class Artifact(Strict):
    kind: str
    url: str
    status: Literal["linked", "reachable", "downloaded", "inspected"]
    note: str

class Inventory(Strict):
    id: str
    label: str
    status: Literal["complete", "partial", "not_quantitative", "inaccessible", "unresolved"]
    note: str

class Dimension(Strict):
    name: str
    score: int | None = Field(default=None, ge=0, le=4)
    reason: str
    evidence: list[str] = []

class Quality(Strict):
    score: float | None = None
    coverage: float = Field(default=0, ge=0, le=1)
    rubric: Literal["empirical-v1", "theory-unrated"] = "empirical-v1"
    dimensions: list[Dimension] = []
    flags: list[str] = []

class Validation(Strict):
    schema_valid: bool = False
    location_resolved: bool = False
    source_support_checked: bool = False
    human_reviewed: bool = False

class Extraction(Strict):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = ""
    keywords: list[str] = []
    relevance: Relevance = "uncertain"
    relevance_reason: str = ""
    topics: list[str] = []
    sections: Sections = Field(default_factory=Sections)
    # Historical private rows; new extraction uses findings.
    experiments: list[Experiment] = []
    findings: list[Finding] = []
    artifacts: list[Artifact] = []
    evidence: list[Evidence] = []
    inventory: list[Inventory] = []
    quality: Quality = Field(default_factory=Quality)
    validation: Validation = Field(default_factory=Validation)
    processing: dict = {}
    completeness: Literal["partial", "complete"] = "partial"

class Reconciliation(Strict):
    findings: list[Finding] = []
    inventory: list[Inventory] | None = None
    revisions: list[Claim] = []
    summary: Claim
    relevance: Relevance
    relevance_reason: Claim
    paper_scope: Claim
    keywords: list[str]
    topics: list[str]
    quality: Quality
