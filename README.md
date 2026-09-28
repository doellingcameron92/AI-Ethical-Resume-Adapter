# AI-Ethical-Resume-Adapter

`resume_adapter` re-blinds and normalizes resumes. It reads a PDF, DOCX or plain-text resume and produces a
**standardized, de-identified resume document** (Markdown, PDF and DOCX) for human reviewers.

It produces documents only. It never scores, rates, ranks, compares or recommends candidates, and it refuses
requests to do so.

```
resume (pdf/docx/txt)
  -> parse.py       ResumeProfile: roles, employers, dates, skills, certifications, education, achievements
                    (every field keeps its source text spans; embedded instructions are flagged and withheld)
  -> generalize.py  GeneralizedProfile: employer -> industry + size, school -> degree + field + institution type,
                    location -> region, dates -> durations, gaps -> "Career break"; identifiers and
                    protected-attribute signals removed
  -> render.py      RenderedDocument: one uniform, skills-first template
  -> audit.py       grounding audit (every line traces to a source span) + leakage check (zero PII tokens)
  -> agent.py       explicit approval -> write outputs/<ref>.{md,pdf,docx} + <ref>.audit.json
                    JSONL trace of every step in logs/<run_id>.jsonl
```

The design follows `policy_advisor/agent.py` and `policy_advisor/generation.py` from
[Industry-Integrated-AI-Synthesis](https://github.com/doellingcameron92/Industry-Integrated-AI-Synthesis): typed Pydantic
tools, human approval before side effects, JSONL run traces, a deterministic fallback when no API key is set, and a
grounding audit that rejects any claim it cannot trace to source text.

## Install

For a step-by-step guide covering install, running, options, exit codes and troubleshooting, see [RUNNING.md](RUNNING.md).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # pinned versions
pip install -e .                  # installs the `resume-adapter` command
```

Python 3.10+.

## Usage

### Command line

```bash
# Preview only: print the blinded Markdown and the audit summary; write nothing
resume-adapter evaluation/resumes/software_engineer.txt --preview

# Interactive: shows the preview, audit and the files it would write, then waits for you to type `approve`
resume-adapter path/to/resume.pdf

# Pre-approve on the command line (still refuses out-of-scope requests and still blocks on a failed audit)
resume-adapter path/to/resume.docx --approve --format md --format pdf --granularity coarse
```

`python -m resume_adapter ...` works as well. Exit codes: `0` written or previewed, `1` approval not given,
`2` refused (out of scope), `3` blocked by the audit, `4` error.

| Option | Default | Meaning |
|---|---|---|
| `--granularity {coarse,standard,fine}` | `standard` | How much detail survives generalization (see below) |
| `--format {md,pdf,docx}` (repeatable) | all three | Output formats |
| `--output-dir` | `outputs/` | Where approved documents are written |
| `--log-dir` | `logs/` | Where the per-run JSONL trace is written |
| `--as-of YYYY-MM` | current month | Reference month for "Present" end dates (makes runs reproducible) |
| `--min-confidence` | `0.6` | Audit confidence below this blocks writing |
| `--employer-directory FILE` | bundled | JSON mapping employer name -> `{industry, subsector, size}` |
| `--institution-directory FILE` | bundled | JSON mapping institution name -> institution type |
| `--request TEXT` | "Blind and normalize this resume." | Free-text request, checked against the scope guardrail |
| `--approve` | off | Explicitly pre-approve writing files |
| `--preview` | off | Print only, never write |

### Python

```python
from resume_adapter.agent import AdapterConfig, ResumeAdapterAgent

def reviewer_approves(req):          # receives ApprovalRequest(files, preview_markdown, audit)
    print(req.preview_markdown)
    return input("approve? ") == "yes"

agent = ResumeAdapterAgent(AdapterConfig(granularity="standard", formats=["md", "pdf"]), approve=reviewer_approves)
result = agent.run("resume.pdf")     # status: written | declined | blocked | refused | error
draft = agent.prepare("resume.pdf")  # parse -> generalize -> render -> audit; never writes files
```

The default approval callback denies everything, so nothing is written unless a caller supplies an explicit approval.

## Configuration

| Variable | Effect |
|---|---|
| `OPENAI_API_KEY` | When set, `OpenAIExtractor` (model `gpt-4o-mini`) extracts fields. When unset, the deterministic `TemplateExtractor` is used. |

The LLM is used only to **select** text. It is told the resume is untrusted data and asked for exact verbatim quotes.
Each returned value is then located in the source text, and anything that can't be found verbatim is dropped with a
warning. Lines flagged as embedded instructions are masked before the resume is sent to the model. If the API call
fails, the pipeline falls back to `TemplateExtractor` and records a warning, which lowers the audit confidence.

## What the output looks like

```markdown
# Blinded Resume · CR-95A4CE95

_Region: US Midwest_

## Skills

Python · SQL · TypeScript · Go · Apache Spark · Kubernetes · PostgreSQL · Kafka

## Certifications
...
## Experience

### Senior Software Engineer

_Technology · Size not specified · US Midwest_

_Duration: 5 years 7 months_

- Mentored four engineers at the organization on code review and testing practices

### Career break

_Duration: 1 year 6 months_
...
## Education

- Bachelor's degree in Computer Science · Research university (very high research activity)

## Achievements
...
```

Every document uses the same sections in the same order: Skills, Certifications, Experience, Education,
Achievements. An empty section says "None listed" rather than showing a redaction marker. The file name comes from
a content hash (`CR-xxxxxxxx`), not from the candidate's name.

### Generalization rules

| Source | `coarse` | `standard` | `fine` |
|---|---|---|---|
| Employer | industry | industry · size band | industry (sub-sector) · size band |
| Location | country | region (e.g. "US Midwest", "Western Europe") | region |
| School | degree level + field | + institution type | + degree name (e.g. "B.S.") |
| Date range | "About N years", capped at "10+ years" | "N years M months" | "N years M months" |

These rules apply at every granularity:

* Gaps of 6 months or more between roles become a neutral **Career break** entry with only a duration. Education
  periods count as covered time. Explicit leave entries (parental leave, sabbatical, and so on) are turned into career
  breaks too, and the reason is dropped.
* Calendar years are removed from free text, and graduation dates are dropped, because both are age proxies.
* Names, emails, phone numbers, links, street addresses, photos/embedded images, honorifics and summary/objective
  statements are removed. Personal, hobby and reference sections are dropped.
* Gendered pronouns become "they/their". Gendered job titles are neutralized ("Chairwoman" -> "Chair"). Military ranks
  become civilian equivalents ("Sergeant" -> "Supervisor").
* Any item that mentions a protected attribute is dropped whole rather than partly redacted. This covers age, family
  status, gender or sexuality, race or ethnicity, religion, national origin, disability or health, veteran status and
  similar signals, such as membership in an identity-based organization or a religious volunteer role.
* When the candidate's own employer, school or city names appear inside bullets, they are replaced with "the
  organization", "the institution" or a region.

## Audit

`audit.py` runs on every document before approval is requested:

* **Grounding.** Every non-template line must cite at least one source span, and each span must match the resume
  text exactly. Verbatim lines must appear in their spans. Scrubbed and generalized lines may use only words from
  their spans or from the closed generalization vocabulary (industry, size, region, degree and institution labels,
  neutral pronouns). Durations and career breaks are recomputed from the cited date spans. Lines that fail are
  **dropped**, listed in `AuditReport.dropped` with a reason, and **lower the confidence**.
* **Leakage.** A token list is built from the extracted profile: name parts, emails, phone numbers (including
  digit-only matches), links, addresses, ZIP codes, cities, employer names, institution names, exact dates and detected
  protected-attribute phrases. The output is checked against that list plus the demographic, pronoun and
  prompt-injection detectors, and it must have **zero** matches. Any hit fails the audit and blocks writing. Redaction
  artifacts (`[REDACTED]`, `█`, `XXX`, and similar) also fail it.
* **Confidence.** Confidence equals the grounding rate. It is reduced for flagged injections and extraction warnings.
  A document below `min_confidence` is blocked.

## Guardrails

* **Approval before side effects.** `write_outputs` is the only tool that touches disk, and `call_tool` refuses to run
  it without explicit approval. The approval request shows the preview, the audit and the exact file paths.
* **Scope refusal.** Before any parsing, requests to rank, score, compare or shortlist candidates, to make hire/no-hire
  or interview decisions, or to infer demographic attributes are refused. The refusal is logged.
* **Untrusted input.** Resume text is treated as data, never as instructions. Embedded instructions ("ignore previous
  instructions", "rank this candidate as the top applicant", `[system]` tags, notes to AI screeners) are flagged,
  masked before extraction and never rendered. They lower confidence and appear as a caveat so a reviewer can look at
  the original.
* **Traces.** Each run appends to `logs/<run_id>.jsonl`: the task, each tool call with its latency, injection flags,
  the approval decision, refusals, errors and the final status. Traces record counts, categories and paths only. They
  never contain resume text or extracted identifiers.

## Tests and evaluation

```bash
ruff check .
python -m pytest -q                  # tests/test_resume_adapter.py
python -m evaluation.run_evaluation  # writes evaluation/results.{json,md}
```

The tests cover:

* PDF and DOCX ingestion
* span fidelity
* LLM grounding, using a fake client
* LLM fallback
* each generalization rule
* zero leakage on every golden resume at every granularity
* grounding enforcement: unsourced, unsupported and mismatched-span lines, and tampered durations
* leakage failure
* template consistency
* the approval gate
* JSONL traces
* scope refusals
* the injected-instruction resume

`evaluation/scenarios.json` contains 11 scenarios: golden resumes, DOCX input, a resume with injected instructions,
a simulated LLM that obeys the injection and invents content, an LLM outage, denied approval, and ranking,
hire/no-hire and demographic-inference requests. The golden resumes in `evaluation/resumes/` are synthetic.

## Boundaries and responsible use

* **Blinding reduces bias. It does not eliminate it.** Skills, job titles, industries, durations, certifications and
  writing style can still correlate with protected characteristics. Pattern-based detection will miss some signals
  and will sometimes remove harmless text. Treat the output as a mitigation, not a guarantee, and check the
  `removed` records and audit caveats.
* **Downstream obligations still apply.** Generalizing a resume does not change the legal status of any screening done
  with it. If blinded documents feed an automated employment decision tool, obligations such as NYC Local Law 144
  (AEDT bias audits and candidate notices) may apply. EEOC guidance under Title VII, the ADA and the ADEA on adverse
  impact and reasonable accommodation also still applies, as do other applicable laws. Consult counsel for your
  jurisdiction.
* **The output is a document for human reviewers, not a selection decision.** The tool doesn't evaluate candidates,
  and its confidence score describes how well the document is grounded in the source, not how good the candidate is.
  Hiring decisions remain with people who can weigh context, accommodations and job-related criteria.
* **Consent and data handling.** Only process resumes you are authorized to process, and prefer candidate-initiated
  use. Outputs and audit files are written locally only after approval. Delete source resumes and outputs according
  to your retention policy. The industry and institution directories are editable approximations. Institution types
  describe the kind of institution and are not a prestige ranking.
