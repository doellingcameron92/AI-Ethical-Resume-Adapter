"""Command-line entry point: ``resume-adapter RESUME [options]``.

Files are written only after the reviewer (or candidate) types ``approve`` at the
prompt, or passes ``--approve`` to pre-approve on the command line.
"""

from __future__ import annotations

import argparse
import sys

from .agent import DEFAULT_REQUEST, AdapterConfig, ApprovalRequest, ResumeAdapterAgent

EXIT = {"written": 0, "declined": 1, "refused": 2, "blocked": 3, "error": 4}


def interactive_approval(req: ApprovalRequest) -> bool:
    print(req.preview_markdown)
    a = req.audit
    print(f"Audit: passed={a.passed} confidence={a.confidence} grounding={a.grounding_rate} "
          f"dropped={len(a.dropped)} leakage_hits={len(a.leakage.hits)} injection_flags={a.injection_flags}")
    for caveat in a.caveats:
        print(f"  caveat: {caveat}")
    print("Files to write:")
    for f in req.files:
        print(f"  {f}")
    if not sys.stdin.isatty():
        print("No interactive terminal; pass --approve to pre-approve writing.", file=sys.stderr)
        return False
    return input("Type 'approve' to write these files: ").strip().lower() == "approve"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="resume-adapter", description="Produce a standardized, blinded resume document "
                                "for human review. Never scores, ranks or recommends candidates.")
    p.add_argument("resume", help="Path to a .pdf, .docx or .txt resume")
    p.add_argument("--granularity", choices=["coarse", "standard", "fine"], default="standard")
    p.add_argument("--format", dest="formats", action="append", choices=["md", "pdf", "docx"],
                   help="Output format (repeatable). Default: md, pdf and docx.")
    p.add_argument("--output-dir", default="outputs")
    p.add_argument("--log-dir", default="logs")
    p.add_argument("--as-of", help="YYYY-MM used for 'Present' end dates (default: current month)")
    p.add_argument("--min-confidence", type=float, default=0.6)
    p.add_argument("--employer-directory", help="JSON file extending the employer -> industry/size directory")
    p.add_argument("--institution-directory", help="JSON file extending the institution -> type directory")
    p.add_argument("--request", default=DEFAULT_REQUEST, help="Free-text request; out-of-scope requests are refused")
    p.add_argument("--approve", action="store_true", help="Explicitly pre-approve writing the output files")
    p.add_argument("--preview", action="store_true", help="Print the blinded Markdown and audit only; write nothing")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AdapterConfig(granularity=args.granularity, formats=args.formats or ["md", "pdf", "docx"],
                           output_dir=args.output_dir, log_dir=args.log_dir, as_of=args.as_of,
                           min_confidence=args.min_confidence, employer_directory=args.employer_directory,
                           institution_directory=args.institution_directory)
    agent = ResumeAdapterAgent(config)
    if args.preview:
        refusal = agent.refusal(args.request)
        if refusal:
            print(refusal, file=sys.stderr)
            return EXIT["refused"]
        draft = agent.prepare(args.resume)
        print(draft.markdown)
        print(draft.audit.model_dump_json(indent=2, include={"passed", "confidence", "grounding_rate", "caveats"}))
        return 0 if draft.audit.passed else EXIT["blocked"]
    agent.approve = (lambda _req: True) if args.approve else interactive_approval
    result = agent.run(args.resume, args.request)
    print(result.message, file=sys.stderr if result.status != "written" else sys.stdout)
    for f in result.files:
        print(f)
    print(f"trace: {result.trace_path}", file=sys.stderr)
    return EXIT[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
