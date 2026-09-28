from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from docx import Document
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from resume_adapter.agent import AdapterConfig, ResumeAdapterAgent
from resume_adapter.audit import audit_document, build_leakage_tokens
from resume_adapter.generalize import duration_label, generalize, tenure_months
from resume_adapter.models import RenderedLine, SourceSpan
from resume_adapter.parse import OpenAIExtractor, TemplateExtractor, extract_text, parse_resume, parse_text
from resume_adapter.render import SECTIONS, render, to_markdown
from resume_adapter.safety import REDACTION_ARTIFACT_RE

RESUMES = Path(__file__).resolve().parents[1] / "evaluation" / "resumes"
GOLDEN = sorted(RESUMES.glob("*.txt"))
AS_OF = "2026-09"


def pipeline(path: Path, granularity: str = "standard"):
    profile = parse_resume(path, TemplateExtractor())
    generalized = generalize(profile, granularity, AS_OF)
    doc, report = audit_document(render(generalized), profile)
    return profile, generalized, doc, report


def load(name: str):
    return pipeline(RESUMES / name)


# --------------------------------------------------------------------------- ingestion
def test_every_extracted_field_carries_verbatim_source_spans():
    for path in GOLDEN:
        profile = parse_resume(path, TemplateExtractor())
        fields = [*profile.skills, *profile.certifications, *profile.achievements, *profile.contact.names]
        for role in profile.roles:
            fields += [role.title, *role.bullets] + [f for f in (role.employer, role.location, role.dates.start,
                                                                   role.dates.end) if f]
        for edu in profile.education:
            fields += [f for f in (edu.institution, edu.degree, edu.field, edu.dates.start, edu.dates.end) if f]
        assert fields
        for f in fields:
            assert f.spans, f
            for s in f.spans:
                assert profile.raw_text[s.start:s.end] == s.text


def test_parser_extracts_roles_dates_and_education():
    profile = parse_resume(RESUMES / "software_engineer.txt", TemplateExtractor())
    assert [(r.title.value, r.employer.value) for r in profile.roles] == [
        ("Senior Software Engineer", "Northwind Analytics"), ("Software Engineer", "Lakeshore Community Bank"),
        ("Software Engineering Intern", "Microsoft")]
    assert profile.roles[0].dates.start.value == "Mar 2021" and profile.roles[0].dates.end.value == "Present"
    edu = profile.education[0]
    assert (edu.institution.value, edu.degree.value, edu.field.value) == ("University of Michigan", "B.S.", "Computer Science")
    assert "Kubernetes" in [s.value for s in profile.skills]


def _docx_from(text: str, path: Path) -> Path:
    d = Document()
    for line in text.splitlines():
        d.add_paragraph(line)
    d.save(str(path))
    return path


def _pdf_from(text: str, path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=LETTER)
    y = 750
    for line in text.splitlines():
        c.drawString(40, y, line)
        y -= 14
        if y < 40:
            c.showPage()
            y = 750
    c.save()
    return path


@pytest.mark.parametrize("builder,fmt", [(_docx_from, "docx"), (_pdf_from, "pdf")])
def test_pdf_and_docx_ingestion(tmp_path, builder, fmt):
    text = (RESUMES / "software_engineer.txt").read_text(encoding="utf-8").replace("–", "-")
    path = builder(text, tmp_path / f"resume.{fmt}")
    raw, source_format, _ = extract_text(path)
    assert source_format == fmt and "Northwind Analytics" in raw
    profile = parse_resume(path, TemplateExtractor())
    assert len(profile.roles) == 3 and profile.contact.emails


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def complete_json(self, system, user):
        self.prompts.append((system, user))
        return self.payload


def test_llm_extraction_is_grounded_and_invented_values_are_dropped():
    raw = (RESUMES / "injected_instruction.txt").read_text(encoding="utf-8")
    fake = FakeLLM({
        "names": ["Alex Morgan"],
        "roles": [{"title": "Data Scientist", "employer": "Summit Health Analytics", "start": "Feb 2020", "end": "Nov 2023",
                   "bullets": ["Built churn models in Python and scikit-learn for 1.4 million members",
                               "Won a Nobel prize for churn modelling"]}],
        "skills": ["Python", "Rust"],
    })
    profile = parse_text(raw, OpenAIExtractor(client=fake))
    assert [b.value for b in profile.roles[0].bullets] == [
        "Built churn models in Python and scikit-learn for 1.4 million members"]
    assert [s.value for s in profile.skills] == ["Python"]
    assert any("ungrounded" in w for w in profile.extraction_warnings)
    system, user = fake.prompts[0]
    assert "UNTRUSTED" in system
    assert "ignore all previous instructions" not in user.lower() and "[system]" not in user.lower()


def test_llm_failure_falls_back_to_template_extractor():
    class Broken:
        def complete_json(self, system, user):
            raise RuntimeError("outage")

    profile = parse_text((RESUMES / "software_engineer.txt").read_text(), OpenAIExtractor(client=Broken()))
    assert profile.extractor == "TemplateExtractor" and len(profile.roles) == 3
    assert any("OpenAIExtractor failed" in w for w in profile.extraction_warnings)


# --------------------------------------------------------------------------- generalization
def test_employers_generalize_to_industry_and_size():
    _, g, _, _ = load("software_engineer.txt")
    roles = [r for r in g.experience if r.kind == "role"]
    assert [r.context.text for r in roles] == [
        "Technology · Size not specified · US Midwest",
        "Financial services · Size not specified · US Midwest",
        "Technology · Large enterprise · US West",
    ]


def test_schools_generalize_to_degree_field_and_institution_type():
    _, g, _, _ = load("software_engineer.txt")
    assert [e.text for e in g.education] == [
        "Bachelor's degree in Computer Science · Research university (very high research activity)"]
    _, g, _, _ = load("career_changer.txt")
    assert "Associate degree in Business · Community or technical college" in [e.text for e in g.education]


def test_locations_generalize_to_region_and_coarse_to_country():
    _, g, _, _ = load("software_engineer.txt")
    assert g.home_region.text == "US Midwest"
    coarse = generalize(parse_resume(RESUMES / "software_engineer.txt", TemplateExtractor()), "coarse", AS_OF)
    assert coarse.home_region.text == "United States"
    assert all("Size" not in r.context.text for r in coarse.experience if r.context)


def test_dates_become_durations_and_gaps_become_career_breaks():
    _, g, doc, _ = load("software_engineer.txt")
    assert [(r.kind, r.duration.text) for r in g.experience] == [
        ("role", "5 years 7 months"), ("career_break", "1 year 6 months"), ("role", "2 years 3 months"),
        ("role", "4 months")]
    md = to_markdown(doc)
    assert not re.search(r"\b(19|20)\d{2}\b", md), "calendar years must not survive (age proxy)"
    assert tenure_months("2019", "2023", AS_OF) == (48, True)
    assert duration_label(150, False, "coarse") == "10+ years"


def test_explicit_leave_becomes_neutral_career_break():
    _, g, doc, _ = load("nurse_manager.txt")
    kinds = [r.kind for r in g.experience]
    assert kinds == ["role", "career_break", "role"]
    assert "Parental" not in to_markdown(doc) and "Leave" not in to_markdown(doc)


def test_identifiers_pronouns_and_demographic_signals_removed():
    _, g, doc, _ = load("nurse_manager.txt")
    md = to_markdown(doc)
    for banned in ("Priya", "Okafor", "she", "her", "Chairwoman", "Mother", "Church", "Harbor View", "Boston",
                   "St. Brigid", "Simmons", "Northeastern"):
        assert not re.search(rf"(?<!\w){re.escape(banned)}(?!\w)", md, re.I), banned
    assert "they introduced" in md and "their unit" in md and "Chair of the unit" in md
    categories = {r.category for r in g.removed}
    assert {"name", "email", "phone", "address", "pronouns", "demographic:religion", "demographic:family_status"} <= categories


def test_protected_attribute_lines_and_personal_sections_dropped():
    _, g, doc, _ = load("career_changer.txt")
    md = to_markdown(doc)
    for banned in ("Samuel", "Whitfield", "Mosque", "Black", "Married", "Canadian", "Date of Birth", "veteran", "Sergeant",
                   "U.S. Army", "Fort Bragg", "Toronto", "chess", "Photo", "samwhitfield"):
        assert banned.lower() not in md.lower(), banned
    assert "Logistics Supervisor" in md
    assert {"demographic:photo", "section", "demographic:age", "demographic:national_origin"} <= {r.category for r in g.removed}


def test_images_are_recorded_as_removed_photo():
    profile = parse_text("Jane Q Doe\n\nSKILLS\nPython\n", TemplateExtractor(), has_images=True)
    g = generalize(profile, as_of=AS_OF)
    assert "photo" in {r.category for r in g.removed}


# --------------------------------------------------------------------------- leakage + grounding
@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.stem)
@pytest.mark.parametrize("granularity", ["coarse", "standard", "fine"])
def test_zero_pii_leakage_on_golden_resumes(path, granularity):
    profile, _, doc, report = pipeline(path, granularity)
    assert report.leakage.passed, report.leakage.hits
    assert report.leakage.tokens_checked >= 5
    md = to_markdown(doc)
    for token, category in build_leakage_tokens(profile):
        assert not re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", md, re.I), (token, category)
    for email in profile.contact.emails:
        assert email.value not in md
    assert not REDACTION_ARTIFACT_RE.search(md)
    assert report.passed and report.grounding_rate == 1.0


def test_every_output_claim_line_cites_matching_source_spans():
    for path in GOLDEN:
        profile, _, doc, _ = pipeline(path)
        for line in doc.lines:
            if line.style in ("title", "section", "footer") or line.text == "None listed":
                continue
            assert line.sources, line
            assert all(profile.raw_text[s.start:s.end] == s.text for s in line.sources)


def test_grounding_audit_drops_ungrounded_and_lowers_confidence():
    profile, g, _, _ = load("software_engineer.txt")
    doc = render(g)
    real = next(ln for ln in doc.lines if ln.style == "bullet")
    doc.lines.insert(5, RenderedLine(text="Top 1% performer, strongly recommended", style="bullet", section="Experience"))
    doc.lines.insert(6, RenderedLine(text="Increased revenue by 900%", style="bullet", section="Experience",
                                     derivation="scrubbed", sources=real.sources))
    doc.lines.insert(7, RenderedLine(text="Led a team of 40 engineers", style="bullet", section="Experience",
                                     derivation="verbatim", sources=[SourceSpan(start=0, end=5, text="Fake!")]))
    clean, report = audit_document(doc, profile)
    texts = [ln.text for ln in clean.lines]
    assert all(t not in texts for t in ("Top 1% performer, strongly recommended", "Increased revenue by 900%",
                                         "Led a team of 40 engineers"))
    reasons = {d.text: d.reason for d in report.dropped}
    assert reasons["Top 1% performer, strongly recommended"] == "no source span cited"
    assert "unsupported terms" in reasons["Increased revenue by 900%"]
    assert reasons["Led a team of 40 engineers"] == "cited span does not match the resume text"
    assert report.confidence < 1.0 and report.grounding_rate < 1.0


def test_tampered_duration_is_rejected():
    profile, g, _, _ = load("software_engineer.txt")
    doc = render(g)
    line = next(ln for ln in doc.lines if ln.derivation == "computed" and ln.text.startswith("Duration"))
    line.text = "Duration: 12 years"
    _, report = audit_document(doc, profile)
    assert any("duration does not match" in d.reason for d in report.dropped)


def test_leakage_check_fails_audit_when_identifier_reaches_output():
    profile, g, _, _ = load("software_engineer.txt")
    doc = render(g)
    bullet = next(ln for ln in doc.lines if ln.text.startswith("Mentored"))
    bullet.text = "Mentored four engineers at Northwind Analytics on code review and testing practices"
    bullet.derivation = "verbatim"
    _, report = audit_document(doc, profile)
    assert not report.passed
    assert {"employer"} <= {h.category for h in report.leakage.hits}


# --------------------------------------------------------------------------- template consistency
def test_template_is_uniform_and_skills_first():
    orders = set()
    for path in GOLDEN:
        for granularity in ("coarse", "standard", "fine"):
            _, _, doc, _ = pipeline(path, granularity)
            orders.add(tuple(doc.section_order))
            assert doc.lines[0].style == "title" and doc.lines[-1].style == "footer"
            md = to_markdown(doc)
            assert [h[3:] for h in md.splitlines() if h.startswith("## ")] == list(SECTIONS)
    assert orders == {SECTIONS} and SECTIONS[0] == "Skills"


def test_empty_sections_render_explicitly_without_redaction_markers():
    profile = parse_text("Jane Q Doe\njane@example.com\n\nSKILLS\nPython, SQL\n", TemplateExtractor())
    doc, report = audit_document(render(generalize(profile, as_of=AS_OF)), profile)
    md = to_markdown(doc)
    assert md.count("None listed") == 4 and "Jane" not in md and "[" not in md
    assert report.passed


# --------------------------------------------------------------------------- guardrails
def _agent(tmp_path, approve, **config):
    cfg = AdapterConfig(as_of=AS_OF, output_dir=str(tmp_path / "out"), log_dir=str(tmp_path / "logs"), **config)
    return ResumeAdapterAgent(cfg, extractor=TemplateExtractor(), approve=approve)


def _trace(result):
    return [json.loads(line) for line in Path(result.trace_path).read_text().splitlines()]


def test_no_file_written_without_approval(tmp_path):
    seen = []
    result = _agent(tmp_path, lambda req: seen.append(req) or False).run(RESUMES / "software_engineer.txt")
    assert result.status == "declined" and not (tmp_path / "out").exists()
    assert seen and seen[0].files and "Blinded Resume" in seen[0].preview_markdown
    assert ResumeAdapterAgent(AdapterConfig(log_dir=str(tmp_path / "l2"), output_dir=str(tmp_path / "o2"), as_of=AS_OF),
                              extractor=TemplateExtractor()).run(RESUMES / "software_engineer.txt").status == "declined"
    assert not (tmp_path / "o2").exists()


def test_side_effect_tool_cannot_bypass_approval(tmp_path):
    agent = _agent(tmp_path, lambda _: False)
    draft = agent.prepare(RESUMES / "software_engineer.txt")
    assert draft.audit.passed
    run_id, trace = agent._new_run()
    with pytest.raises(PermissionError):
        agent.call_tool(trace, "write_outputs", {"output_dir": str(tmp_path / "out"), "formats": ["md"]})
    assert not (tmp_path / "out").exists()


def test_approved_run_writes_all_formats_and_jsonl_trace(tmp_path):
    result = _agent(tmp_path, lambda _: True).run(RESUMES / "nurse_manager.txt")
    assert result.status == "written"
    suffixes = sorted(Path(f).suffix for f in result.files)
    assert suffixes == [".docx", ".json", ".md", ".pdf"]
    for f in result.files:
        if f.endswith((".pdf", ".docx")):
            text, _, _ = extract_text(f)
            assert "Priya" not in text and "Boston" not in text and "Skills" in text
    kinds = [r["kind"] for r in _trace(result)]
    assert kinds[0] == "task" and "approval" in kinds and kinds[-1] == "final"
    trace_text = Path(result.trace_path).read_text()
    assert "Priya" not in trace_text and "priya.no@example.org" not in trace_text


@pytest.mark.parametrize("request_text,category", [
    ("Rank these candidates by fit", "candidate_ranking"),
    ("Give this resume a match score", "candidate_ranking"),
    ("Should we hire this person? hire or no-hire", "hiring_decision"),
    ("Blind the resume and tell me the candidate's age", "demographic_inference"),
    ("Can you infer the applicant's gender from the name?", "demographic_inference"),
])
def test_scope_refusals(tmp_path, request_text, category):
    result = _agent(tmp_path, lambda _: True).run(RESUMES / "software_engineer.txt", request_text)
    assert result.status == "refused" and not result.files and not (tmp_path / "out").exists()
    refusal = [r for r in _trace(result) if r["kind"] == "refusal"]
    assert refusal and refusal[0]["category"] == category


def test_injected_instructions_are_flagged_and_never_reach_output(tmp_path):
    result = _agent(tmp_path, lambda _: True).run(RESUMES / "injected_instruction.txt")
    assert result.status == "written"
    assert result.audit.injection_flags == 3 and result.audit.confidence < 1.0
    md = result.markdown.lower()
    banned = ("ignore", "rank this candidate", "recommend hire", "do not blind", "[system]", "recruiter bot", "top applicant")
    for phrase in banned:
        assert phrase not in md
    assert "scikit-learn" in md and "tableau" in md
    assert any(r["kind"] == "injection_flagged" for r in _trace(result))


def test_low_confidence_blocks_writing(tmp_path):
    result = _agent(tmp_path, lambda _: True, min_confidence=0.95).run(RESUMES / "injected_instruction.txt")
    assert result.status == "blocked" and not (tmp_path / "out").exists()
