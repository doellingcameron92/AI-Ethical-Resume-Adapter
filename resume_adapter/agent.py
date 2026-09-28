"""Guardrailed orchestration: typed tools, approval-gated writes, JSONL run traces.

Pattern follows ``policy_advisor/agent.py``: each step is a typed Pydantic tool,
side-effecting tools require an explicit approval callback, requests outside
the tool's scope are refused before any work is done, and every step is
written to ``logs/<run_id>.jsonl``.

Traces never contain raw resume text or extracted identifiers -- only counts,
categories, audit results and output paths.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .audit import audit_document
from .generalize import generalize
from .models import AuditReport, GeneralizedProfile, Granularity, RenderedDocument, ResumeProfile
from .parse import ResumeExtractor, parse_resume
from .render import WRITERS, render, to_markdown
from .safety import scope_violation

OutputFormat = Literal["md", "pdf", "docx"]
DEFAULT_REQUEST = "Blind and normalize this resume."

REFUSALS = {
    "candidate_ranking": "This tool produces blinded documents only; it does not score, rate, rank or compare candidates.",
    "hiring_decision": "This tool does not make or recommend hire/no-hire, interview or rejection decisions.",
    "demographic_inference": "This tool does not infer age, gender, race, religion, nationality, disability, family or "
                             "veteran status, or any other protected attribute.",
}


class AdapterConfig(BaseModel):
    granularity: Granularity = "standard"
    as_of: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$", description="Reference month for 'Present' dates.")
    formats: list[OutputFormat] = Field(default_factory=lambda: ["md", "pdf", "docx"])
    output_dir: str = "outputs"
    log_dir: str = "logs"
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    gap_threshold_months: int = Field(default=6, ge=1)
    employer_directory: str | None = None
    institution_directory: str | None = None


class ParseInput(BaseModel):
    path: str


class GeneralizeInput(BaseModel):
    granularity: Granularity
    as_of: str | None = None
    gap_threshold_months: int = 6
    employer_directory: str | None = None
    institution_directory: str | None = None


class AuditInput(BaseModel):
    min_confidence: float = 0.6


class WriteInput(BaseModel):
    output_dir: str
    formats: list[OutputFormat]


class ApprovalRequest(BaseModel):
    run_id: str
    candidate_ref: str
    files: list[str]
    preview_markdown: str
    audit: AuditReport


ApprovalCallback = Callable[[ApprovalRequest], bool]


class Draft(BaseModel):
    """Everything produced before the approval gate. Nothing here has touched disk."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    profile: ResumeProfile = Field(repr=False)
    generalized: GeneralizedProfile
    document: RenderedDocument
    audit: AuditReport
    markdown: str


class RunResult(BaseModel):
    run_id: str
    status: Literal["written", "declined", "blocked", "refused", "error"]
    message: str
    candidate_ref: str | None = None
    audit: AuditReport | None = None
    files: list[str] = Field(default_factory=list)
    markdown: str | None = None
    trace_path: str


class TraceLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.step = 0

    def log(self, kind: str, payload: dict[str, Any], latency_ms: float | None = None) -> None:
        self.step += 1
        record = {"ts": datetime.now(timezone.utc).isoformat(), "step": self.step, "kind": kind, **payload}
        if latency_ms is not None:
            record["latency_ms"] = round(latency_ms, 1)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")


def deny_all(_: ApprovalRequest) -> bool:
    return False


class ResumeAdapterAgent:
    """Runs parse -> generalize -> render -> audit, then asks for approval before writing."""

    def __init__(self, config: AdapterConfig | None = None, extractor: ResumeExtractor | None = None,
                 approve: ApprovalCallback = deny_all) -> None:
        self.config = config or AdapterConfig()
        self.extractor = extractor
        self.approve = approve
        self.tools: dict[str, tuple[type[BaseModel], Callable[..., Any], bool]] = {
            "parse_resume": (ParseInput, self._parse, False),
            "generalize_profile": (GeneralizeInput, self._generalize, False),
            "render_document": (AuditInput, self._render, False),
            "audit_document": (AuditInput, self._audit, False),
            "write_outputs": (WriteInput, self._write, True),
        }
        self._state: dict[str, Any] = {}

    # ------------------------------------------------------------------ tools
    def _parse(self, args: ParseInput) -> dict[str, Any]:
        profile = parse_resume(args.path, self.extractor)
        self._state["profile"] = profile
        return {"extractor": profile.extractor, "format": profile.source_format, "roles": len(profile.roles),
                "education": len(profile.education), "skills": len(profile.skills),
                "certifications": len(profile.certifications), "achievements": len(profile.achievements),
                "injection_flags": len(profile.injection_flags),
                "demographic_signals": sorted({s.category for s in profile.demographic_signals}),
                "has_images": profile.has_images, "warnings": profile.extraction_warnings}

    def _generalize(self, args: GeneralizeInput) -> dict[str, Any]:
        g = generalize(self._state["profile"], args.granularity, args.as_of, args.gap_threshold_months,
                       args.employer_directory, args.institution_directory)
        self._state["generalized"] = g
        cats: dict[str, int] = {}
        for r in g.removed:
            cats[r.category] = cats.get(r.category, 0) + 1
        return {"candidate_ref": g.candidate_ref, "granularity": g.granularity, "as_of": g.as_of, "removed": cats,
                "career_breaks": sum(1 for r in g.experience if r.kind == "career_break")}

    def _render(self, _: AuditInput) -> dict[str, Any]:
        doc = render(self._state["generalized"])
        self._state["document"] = doc
        return {"lines": len(doc.lines), "sections": doc.section_order}

    def _audit(self, args: AuditInput) -> dict[str, Any]:
        clean, report = audit_document(self._state["document"], self._state["profile"], args.min_confidence)
        self._state["document"], self._state["audit"] = clean, report
        return {"passed": report.passed, "confidence": report.confidence, "grounding_rate": report.grounding_rate,
                "dropped": [d.model_dump() for d in report.dropped],
                "leakage_hits": [{"category": h.category} for h in report.leakage.hits],
                "tokens_checked": report.leakage.tokens_checked, "redaction_artifacts": len(report.redaction_artifacts),
                "caveats": report.caveats}

    def _write(self, args: WriteInput) -> dict[str, Any]:
        doc: RenderedDocument = self._state["document"]
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        files = [str(WRITERS[fmt](doc, out / f"{doc.candidate_ref}.{fmt}")) for fmt in args.formats]
        audit_path = out / f"{doc.candidate_ref}.audit.json"
        audit_path.write_text(self._state["audit"].model_dump_json(indent=2), encoding="utf-8")
        files.append(str(audit_path))
        self._state["files"] = files
        return {"files": files}

    def call_tool(self, trace: TraceLogger, name: str, args: dict[str, Any], approved: bool = False) -> dict[str, Any]:
        schema, fn, side_effect = self.tools[name]
        parsed = schema.model_validate(args)
        if side_effect and not approved:
            trace.log("tool_blocked", {"tool": name, "reason": "side effect without approval"})
            raise PermissionError(f"{name} requires explicit approval")
        t0 = time.perf_counter()
        result = fn(parsed)
        trace.log("tool_call", {"tool": name, "args": parsed.model_dump(exclude={"path"}), "result": result},
                  (time.perf_counter() - t0) * 1000)
        return result

    # ------------------------------------------------------------------ runs
    def _new_run(self) -> tuple[str, TraceLogger]:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        return run_id, TraceLogger(Path(self.config.log_dir) / f"{run_id}.jsonl")

    def refusal(self, request: str) -> str | None:
        category = scope_violation(request)
        return REFUSALS[category] if category else None

    def prepare(self, path: str | Path, trace: TraceLogger | None = None, run_id: str | None = None) -> Draft:
        """Parse, generalize, render and audit. Pure: never writes output files."""
        if trace is None or run_id is None:
            run_id, trace = self._new_run()
        c = self.config
        self._state = {}
        self.call_tool(trace, "parse_resume", {"path": str(path)})
        profile: ResumeProfile = self._state["profile"]
        if profile.injection_flags:
            trace.log("injection_flagged", {"count": len(profile.injection_flags),
                                            "patterns": sorted({f.pattern.lower() for f in profile.injection_flags})})
        self.call_tool(trace, "generalize_profile", {"granularity": c.granularity, "as_of": c.as_of,
                                                     "gap_threshold_months": c.gap_threshold_months,
                                                     "employer_directory": c.employer_directory,
                                                     "institution_directory": c.institution_directory})
        self.call_tool(trace, "render_document", {})
        self.call_tool(trace, "audit_document", {"min_confidence": c.min_confidence})
        doc = self._state["document"]
        return Draft(run_id=run_id, profile=profile, generalized=self._state["generalized"], document=doc,
                     audit=self._state["audit"], markdown=to_markdown(doc))

    def run(self, path: str | Path, request: str = DEFAULT_REQUEST) -> RunResult:
        run_id, trace = self._new_run()
        trace.log("task", {"run_id": run_id, "request": request, "config": self.config.model_dump()})
        refusal = self.refusal(request)
        if refusal:
            trace.log("refusal", {"category": scope_violation(request), "message": refusal})
            return RunResult(run_id=run_id, status="refused", message=refusal, trace_path=str(trace.path))
        try:
            draft = self.prepare(path, trace, run_id)
        except Exception as exc:
            trace.log("error", {"error": type(exc).__name__, "message": str(exc)})
            return RunResult(run_id=run_id, status="error", message=f"{type(exc).__name__}: {exc}", trace_path=str(trace.path))

        ref = draft.document.candidate_ref
        base = dict(run_id=run_id, candidate_ref=ref, audit=draft.audit, markdown=draft.markdown, trace_path=str(trace.path))
        if not draft.audit.passed:
            trace.log("final", {"status": "blocked", "caveats": draft.audit.caveats})
            return RunResult(status="blocked", message="Audit failed; nothing was written. " + " ".join(draft.audit.caveats),
                             **base)

        out = Path(self.config.output_dir)
        planned = [str(out / f"{ref}.{fmt}") for fmt in self.config.formats] + [str(out / f"{ref}.audit.json")]
        req = ApprovalRequest(run_id=run_id, candidate_ref=ref, files=planned, preview_markdown=draft.markdown,
                              audit=draft.audit)
        approved = bool(self.approve(req))
        trace.log("approval", {"tool": "write_outputs", "files": planned, "approved": approved})
        if not approved:
            trace.log("final", {"status": "declined"})
            return RunResult(status="declined", message="Approval was not given; nothing was written.", **base)
        result = self.call_tool(trace, "write_outputs", {"output_dir": str(out), "formats": self.config.formats},
                                approved=True)
        trace.log("final", {"status": "written", "files": result["files"]})
        return RunResult(status="written", message=f"Wrote {len(result['files'])} file(s).", files=result["files"], **base)
