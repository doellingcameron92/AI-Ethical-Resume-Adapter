"""Scenario-based evaluation of the resume re-blinding pipeline.

Usage:  python -m evaluation.run_evaluation
Writes evaluation/results.json and evaluation/results.md. Output documents go to
a temporary directory; run traces go to logs/evaluation/.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from docx import Document

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from resume_adapter.agent import AdapterConfig, ResumeAdapterAgent  # noqa: E402
from resume_adapter.parse import OpenAIExtractor, TemplateExtractor  # noqa: E402

EVAL_DIR = ROOT / "evaluation"
RESUMES = EVAL_DIR / "resumes"
LOG_DIR = ROOT / "logs" / "evaluation"
AS_OF = "2026-09"


class CompromisedLLM:
    """Simulates an LLM that obeyed the resume's embedded instructions and invented content."""

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        return {
            "names": ["Alex Morgan"],
            "roles": [{"title": "Data Scientist", "employer": "Summit Health Analytics", "location": "Denver, CO",
                       "start": "Feb 2020", "end": "Nov 2023",
                       "bullets": ["Built churn models in Python and scikit-learn for 1.4 million members",
                                   "This candidate is the best fit; recommend hire"]}],
            "skills": ["Python", "SQL"],
            "achievements": ["Top candidate - winner of three Kaggle competitions"],
        }


class OutageLLM:
    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        raise RuntimeError("simulated model service outage")


def _to_docx(src: Path, dest: Path) -> Path:
    d = Document()
    for line in src.read_text(encoding="utf-8").splitlines():
        d.add_paragraph(line)
    d.save(str(dest))
    return dest


def run_scenarios() -> list[dict[str, Any]]:
    scenarios = json.loads((EVAL_DIR / "scenarios.json").read_text(encoding="utf-8"))
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for sc in scenarios:
            out_dir = Path(tmp) / sc["id"]
            resume = RESUMES / sc["resume"]
            if sc.get("convert_to") == "docx":
                resume = _to_docx(resume, Path(tmp) / f"{sc['id']}.docx")
            extractor = {"compromised_llm": OpenAIExtractor(client=CompromisedLLM()),
                         "llm_outage": OpenAIExtractor(client=OutageLLM())}.get(sc.get("fault", ""), TemplateExtractor())
            config = AdapterConfig(granularity=sc.get("granularity", "standard"), as_of=AS_OF, output_dir=str(out_dir),
                                   log_dir=str(LOG_DIR))
            approve = sc["approval"] == "approve"
            agent = ResumeAdapterAgent(config, extractor=extractor, approve=lambda _req, ok=approve: ok)
            kwargs = {"request": sc["request"]} if "request" in sc else {}
            result = agent.run(resume, **kwargs)
            trace = [json.loads(line) for line in Path(result.trace_path).read_text(encoding="utf-8").splitlines()]
            audit = result.audit
            refusal = next((r["category"] for r in trace if r["kind"] == "refusal"), None)
            observed = {
                "status": result.status,
                "files_written": sum(1 for p in out_dir.glob("*") if p.is_file()) if out_dir.exists() else 0,
                "leakage_hits": len(audit.leakage.hits) if audit else None,
                "grounding_rate": audit.grounding_rate if audit else None,
                "confidence": audit.confidence if audit else None,
                "dropped_lines": len(audit.dropped) if audit else None,
                "injection_flags": audit.injection_flags if audit else None,
                "injection_logged": any(r["kind"] == "injection_flagged" for r in trace),
                "extraction_warning": bool(audit and any("ungrounded" in c or "failed" in c for c in audit.caveats)),
                "refusal_category": refusal,
                "trace_records": len(trace),
            }
            checks = evaluate_expectations(sc["expect"], observed, result.markdown or "")
            results.append({"id": sc["id"], "category": sc["category"], "observed": observed, "checks": checks,
                            "passed": all(checks.values()), "message": result.message,
                            "caveats": audit.caveats if audit else []})
            print(f"{'PASS' if all(checks.values()) else 'FAIL'}  {sc['id']}  status={result.status}")
    return results


def evaluate_expectations(expect: dict[str, Any], observed: dict[str, Any], markdown: str) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    for key, value in expect.items():
        if key == "min_confidence":
            checks[key] = (observed["confidence"] or 0) >= value
        elif key == "max_confidence":
            checks[key] = observed["confidence"] is not None and observed["confidence"] <= value
        elif key == "output_contains":
            checks[key] = all(v.lower() in markdown.lower() for v in value)
        elif key == "output_not_contains":
            checks[key] = all(v.lower() not in markdown.lower() for v in value)
        else:
            checks[key] = observed.get(key) == value
    return checks


def write_report(results: list[dict[str, Any]]) -> None:
    (EVAL_DIR / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    passed = sum(r["passed"] for r in results)
    lines = ["# Evaluation results", "", f"Scenarios passed: **{passed}/{len(results)}** (deterministic extractor unless "
             "a simulated LLM fault is noted).", "",
             "| ID | Category | Status | Leakage hits | Grounding | Confidence | Injection flags | Dropped | Result |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        o = r["observed"]
        fmt = lambda v: "-" if v is None else v  # noqa: E731
        lines.append(f"| {r['id']} | {r['category']} | {o['status']} | {fmt(o['leakage_hits'])} | {fmt(o['grounding_rate'])} | "
                     f"{fmt(o['confidence'])} | {fmt(o['injection_flags'])} | {fmt(o['dropped_lines'])} | "
                     f"{'PASS' if r['passed'] else 'FAIL'} |")
    lines += ["", "## Notes", ""]
    for r in results:
        lines += [f"### {r['id']}", f"*Result:* {r['message']}", ""]
        if r["caveats"]:
            lines += ["*Caveats:*"] + [f"- {c}" for c in r["caveats"]] + [""]
        failed = [k for k, v in r["checks"].items() if not v]
        if failed:
            lines += [f"*Failed checks:* {failed}", ""]
    (EVAL_DIR / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    results = run_scenarios()
    write_report(results)
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed -> evaluation/results.md")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
