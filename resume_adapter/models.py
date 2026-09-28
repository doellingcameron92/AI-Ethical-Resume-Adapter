"""Typed contracts shared by every stage of the pipeline.

Every extracted value carries the character spans of the source text it came
from, and every rendered line carries the spans it was derived from, so the
audit can trace each output claim back to the original resume.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Granularity = Literal["coarse", "standard", "fine"]
Derivation = Literal["template", "verbatim", "scrubbed", "generalized", "computed"]
LineStyle = Literal["title", "meta", "section", "entry", "detail", "item", "bullet", "footer"]


class SourceSpan(BaseModel):
    """Half-open character range ``[start, end)`` into ``ResumeProfile.raw_text``."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str

    @model_validator(mode="after")
    def _ordered(self) -> SourceSpan:
        if self.end < self.start:
            raise ValueError("span end must be >= start")
        return self


class Extracted(BaseModel):
    value: str
    spans: list[SourceSpan] = Field(default_factory=list)


class ContactInfo(BaseModel):
    names: list[Extracted] = Field(default_factory=list)
    emails: list[Extracted] = Field(default_factory=list)
    phones: list[Extracted] = Field(default_factory=list)
    urls: list[Extracted] = Field(default_factory=list)
    addresses: list[Extracted] = Field(default_factory=list)
    locations: list[Extracted] = Field(default_factory=list)


class DateRange(BaseModel):
    start: Extracted | None = None
    end: Extracted | None = None


class RoleEntry(BaseModel):
    title: Extracted
    employer: Extracted | None = None
    location: Extracted | None = None
    dates: DateRange = Field(default_factory=DateRange)
    bullets: list[Extracted] = Field(default_factory=list)


class EducationEntry(BaseModel):
    institution: Extracted | None = None
    degree: Extracted | None = None
    field: Extracted | None = None
    location: Extracted | None = None
    dates: DateRange = Field(default_factory=DateRange)


class InjectionFlag(BaseModel):
    span: SourceSpan
    pattern: str


class DemographicSignal(BaseModel):
    category: str
    span: SourceSpan


class ResumeProfile(BaseModel):
    raw_text: str
    source_format: Literal["pdf", "docx", "txt"] = "txt"
    extractor: str = "TemplateExtractor"
    contact: ContactInfo = Field(default_factory=ContactInfo)
    roles: list[RoleEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[Extracted] = Field(default_factory=list)
    certifications: list[Extracted] = Field(default_factory=list)
    achievements: list[Extracted] = Field(default_factory=list)
    summary: list[Extracted] = Field(default_factory=list)
    dropped_sections: list[str] = Field(default_factory=list)
    has_images: bool = False
    injection_flags: list[InjectionFlag] = Field(default_factory=list)
    demographic_signals: list[DemographicSignal] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)


class OutputItem(BaseModel):
    text: str
    sources: list[SourceSpan] = Field(default_factory=list)
    derivation: Derivation = "verbatim"


class ComputedDuration(BaseModel):
    """Lets the audit recompute a duration from its cited date spans."""

    kind: Literal["tenure", "gap"]
    start: SourceSpan
    end: SourceSpan | None = None
    text: str


class GeneralizedRole(BaseModel):
    kind: Literal["role", "career_break"] = "role"
    title: OutputItem
    context: OutputItem | None = None
    duration: ComputedDuration | None = None
    bullets: list[OutputItem] = Field(default_factory=list)


class RemovalRecord(BaseModel):
    category: str
    reason: str
    spans: list[SourceSpan] = Field(default_factory=list)


class GeneralizedProfile(BaseModel):
    candidate_ref: str
    granularity: Granularity
    as_of: str
    home_region: OutputItem | None = None
    skills: list[OutputItem] = Field(default_factory=list)
    certifications: list[OutputItem] = Field(default_factory=list)
    experience: list[GeneralizedRole] = Field(default_factory=list)
    education: list[OutputItem] = Field(default_factory=list)
    achievements: list[OutputItem] = Field(default_factory=list)
    removed: list[RemovalRecord] = Field(default_factory=list)


class RenderedLine(BaseModel):
    text: str
    style: LineStyle
    section: str = ""
    derivation: Derivation = "template"
    sources: list[SourceSpan] = Field(default_factory=list)
    computed: ComputedDuration | None = None


class RenderedDocument(BaseModel):
    candidate_ref: str
    as_of: str
    granularity: Granularity = "standard"
    lines: list[RenderedLine] = Field(default_factory=list)

    @property
    def section_order(self) -> list[str]:
        return [ln.text for ln in self.lines if ln.style == "section"]


class DroppedLine(BaseModel):
    text: str
    section: str
    reason: str


class LeakageHit(BaseModel):
    token: str
    category: str
    line: str


class LeakageReport(BaseModel):
    tokens_checked: int
    hits: list[LeakageHit] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.hits


class AuditReport(BaseModel):
    claim_lines: int
    grounded_lines: int
    grounding_rate: float
    dropped: list[DroppedLine] = Field(default_factory=list)
    leakage: LeakageReport
    redaction_artifacts: list[str] = Field(default_factory=list)
    injection_flags: int = 0
    confidence: float
    caveats: list[str] = Field(default_factory=list)
    passed: bool
