# How to run AI-Ethical-Resume-Adapter

This guide walks through installing the tool, blinding a resume, and running the tests and evaluation.
See [README.md](README.md) for how the pipeline works and for its responsible-use boundaries.

## 1. Prerequisites

* Python 3.10 or newer (`python3 --version`)
* Git
* Optional: an OpenAI API key. Without one, the tool uses its built-in deterministic extractor.

## 2. Get the code and install

```bash
git clone https://github.com/doellingcameron92/AI-Ethical-Resume-Adapter.git
cd AI-Ethical-Resume-Adapter

python3 -m venv .venv
source .venv/bin/activate          # Windows (PowerShell): .venv\Scripts\Activate.ps1
                                   # Windows (cmd):        .venv\Scripts\activate.bat

pip install -r requirements.txt    # pinned dependencies
pip install -e .                   # installs the `resume-adapter` command
```

Check that it installed:

```bash
resume-adapter --help
```

Run all later commands from the repo root with the virtual environment active.

## 3. Optional: turn on LLM extraction

```bash
export OPENAI_API_KEY=sk-...       # Windows (PowerShell): $env:OPENAI_API_KEY="sk-..."
```

When the key is set, OpenAI (`gpt-4o-mini`) picks the fields out of the resume, and every value it returns is checked
against the source text. If the key is unset, or the API call fails, the tool uses the built-in deterministic
extractor. Both paths produce the same kind of output.

## 4. Blind a resume

There are sample resumes in `evaluation/resumes/`. You can also use your own `.pdf`, `.docx` or `.txt` file.

### Preview first (writes nothing)

```bash
resume-adapter evaluation/resumes/software_engineer.txt --preview
```

This prints the blinded resume as Markdown, followed by the audit summary (grounding rate, confidence, caveats). Read it
before you write any files.

### Write the documents (interactive approval)

```bash
resume-adapter evaluation/resumes/nurse_manager.txt
```

The tool shows the preview, the audit and the exact files it would create, then waits. Type `approve` to write them.
Anything else cancels, and nothing is written.

### Write the documents (pre-approved, for scripts)

```bash
resume-adapter path/to/resume.pdf --approve
```

### Where the output goes

| Location | Contents |
|---|---|
| `outputs/CR-xxxxxxxx.md` | Blinded resume in Markdown |
| `outputs/CR-xxxxxxxx.pdf` | Same document as a PDF |
| `outputs/CR-xxxxxxxx.docx` | Same document as a Word file |
| `outputs/CR-xxxxxxxx.audit.json` | Grounding and leakage audit, dropped lines, removal records |
| `logs/<run_id>.jsonl` | Step-by-step trace of the run (never contains resume text) |

`CR-xxxxxxxx` is an anonymous reference derived from the file contents, not from the candidate's name.

`python -m resume_adapter ...` works the same way as `resume-adapter ...`.

## 5. Common options

```bash
# Only Markdown and DOCX, into a custom folder
resume-adapter resume.docx --approve --format md --format docx --output-dir blinded/

# Less detail: country instead of region, industry only, rounded durations
resume-adapter resume.pdf --preview --granularity coarse

# More detail: adds industry sub-sector and degree name
resume-adapter resume.pdf --preview --granularity fine

# Reproducible durations: treat "Present" as September 2026
resume-adapter resume.pdf --preview --as-of 2026-09

# Block writing unless the audit confidence is at least 0.8
resume-adapter resume.pdf --approve --min-confidence 0.8

# Use your own employer and institution mappings
resume-adapter resume.pdf --preview --employer-directory my_employers.json --institution-directory my_schools.json
```

A custom directory file **replaces** the bundled one, so don't start from an empty file. Copy the bundled file and
edit the copy:

```bash
cp resume_adapter/data/employers.json my_employers.json
cp resume_adapter/data/institutions.json my_schools.json
```

Both files are JSON. The keys are names, matched case-insensitively and ignoring punctuation and suffixes like "Inc":

```json
{"Acme Robotics": {"industry": "Manufacturing", "subsector": "robotics", "size": "mid"}}
```

```json
{"Springfield State University": "research_high"}
```

The allowed `size` values are `startup`, `small`, `mid`, `large` and `enterprise`. The allowed institution types are:

* `research_very_high`
* `research_high`
* `masters`
* `liberal_arts`
* `community_college`
* `online`
* `university`
* `college`
* `training_provider`

## 6. Exit codes

| Code | Meaning |
|---|---|
| `0` | Files written, or preview shown with a passing audit |
| `1` | Approval not given, so nothing was written |
| `2` | Request refused as out of scope (ranking, hire/no-hire, demographic inference) |
| `3` | Blocked by the audit (leakage found or confidence too low) |
| `4` | Error, such as a missing file or an unsupported format |

Example of a refusal:

```bash
resume-adapter evaluation/resumes/software_engineer.txt --request "Rank this candidate"
echo $?   # 2
```

## 7. Use it from Python

```python
from resume_adapter.agent import AdapterConfig, ResumeAdapterAgent

def approve(request):                       # request.files, request.preview_markdown, request.audit
    print(request.preview_markdown)
    return input("Type approve to write: ").strip().lower() == "approve"

agent = ResumeAdapterAgent(AdapterConfig(granularity="standard", formats=["md", "pdf"]), approve=approve)
result = agent.run("evaluation/resumes/career_changer.txt")
print(result.status, result.files)          # written | declined | blocked | refused | error

draft = agent.prepare("evaluation/resumes/career_changer.txt")   # preview only, never writes
print(draft.markdown)
print(draft.audit.confidence, draft.audit.leakage.passed)
```

If you don't pass `approve=`, every write is denied.

## 8. Run the tests and evaluation

```bash
pip install -e ".[dev]"             # only needed if you skipped requirements.txt (pytest + ruff)

ruff check .                        # lint
python -m pytest -q                 # unit tests (tests/test_resume_adapter.py)
python -m evaluation.run_evaluation # scenario evaluation -> evaluation/results.md and results.json
```

The evaluation runs 11 scenarios: golden resumes, DOCX input, a resume with injected instructions, a simulated LLM that
follows the injection, an LLM outage, denied approval, and three out-of-scope requests. Each scenario prints `PASS` or
`FAIL`, and the command exits with a non-zero code if any scenario fails.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `resume-adapter: command not found` | Activate the virtual environment and run `pip install -e .` again, or use `python -m resume_adapter`. |
| `No interactive terminal; pass --approve to pre-approve writing.` | You're running without a terminal (script or CI). Add `--approve`, or use `--preview`. |
| Exit code `3` / "blocked" | Open the audit in the preview. Identifying text may have reached the output, or confidence may be below `--min-confidence`. Check the source resume. |
| A section shows "None listed" | The extractor didn't recognize that section's heading. Use conventional headings such as `EXPERIENCE`, `EDUCATION`, `SKILLS` and `CERTIFICATIONS`. |
| Scanned (image-only) PDF gives an empty result | Only PDFs with a text layer are supported. Run OCR first, or export the resume as DOCX. |
| "embedded instruction(s) were flagged" caveat | The resume contains text aimed at AI screeners. That text was withheld. Review the original resume manually. |
