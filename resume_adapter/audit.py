"""Audit: grounding + leakage checks run on every document before it can be written.

Grounding -- every non-template line must cite at least one source span, each
span must match the resume text verbatim, and the line itself must be
derivable from those spans:

* ``verbatim``    -- the line text appears in the cited span text
* ``scrubbed`` / ``generalized`` -- every word/number is in the cited spans or in
  the closed generalization vocabulary (industry, size, region, degree labels...)
* ``computed``    -- the duration is recomputed from the cited date spans

Lines that fail are dropped from the document, listed in ``AuditReport.dropped``
and lower the confidence score.

Leakage -- the output must contain zero matches for the PII/protected-attribute
token list built from the extracted profile. Any hit fails the audit, which
blocks writing.
"""

from __future__ import annotations

import re

from .generalize import (
    CAREER_BREAK,
    _words,
    candidate_name_tokens,
    generalization_vocab,
    recompute_duration,
)
from .models import AuditReport, DroppedLine, LeakageHit, LeakageReport, RenderedDocument, RenderedLine, ResumeProfile
from .render import is_template_text
from .safety import (
    DEMOGRAPHIC_PATTERNS,
    INJECTION_RE,
    PRONOUN_DECL_RE,
    PRONOUN_RE,
    REDACTION_ARTIFACT_RE,
    ZIP_RE,
)

TEMPLATE_STYLES = {"title", "section", "footer"}
LINE_PREFIXES = ("Duration: ", "Region: ")
PII_CATEGORIES = ("name", "email", "phone", "link", "address", "location", "employer", "institution", "date", "zip")


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def build_leakage_tokens(profile: ResumeProfile) -> list[tuple[str, str]]:
    """``(token, category)`` pairs that must never appear in the blinded output."""
    c = profile.contact
    tokens: set[tuple[str, str]] = {(t, "name") for t in candidate_name_tokens(profile)}
    tokens |= {(e.value, "email") for e in c.emails}
    tokens |= {(e.value, "phone") for e in c.phones}
    for e in c.urls:
        tokens.add((e.value, "link"))
        tokens.add((re.sub(r"^(?:https?://)?(?:www\.)?", "", e.value).rstrip("/"), "link"))
    tokens |= {(e.value, "address") for e in c.addresses}
    for e in c.locations:
        tokens.add((e.value, "location"))
        tokens.add((e.value.split(",")[0].strip(), "location"))
        tokens |= {(z, "zip") for z in ZIP_RE.findall(e.value)}
    for r in profile.roles:
        if r.employer:
            tokens.add((r.employer.value, "employer"))
        tokens |= {(d.value, "date") for d in (r.dates.start, r.dates.end) if d and re.search(r"\d", d.value)}
    for ed in profile.education:
        if ed.institution:
            tokens.add((ed.institution.value, "institution"))
        tokens |= {(d.value, "date") for d in (ed.dates.start, ed.dates.end) if d and re.search(r"\d", d.value)}
    for sig in profile.demographic_signals:
        tokens.add((sig.span.text, f"protected:{sig.category}"))
    return sorted((t, cat) for t, cat in tokens if len(t.strip()) >= 2)


def leakage_check(lines: list[str], profile: ResumeProfile) -> LeakageReport:
    tokens = build_leakage_tokens(profile)
    hits: list[LeakageHit] = []
    phone_digits = [re.sub(r"\D", "", e.value)[-10:] for e in profile.contact.phones]
    for line in lines:
        for token, category in tokens:
            if re.search(r"(?<![\w])" + re.escape(token) + r"(?![\w])", line, re.I):
                hits.append(LeakageHit(token=token, category=category, line=line))
        digits = re.sub(r"\D", "", line)
        hits += [LeakageHit(token=d, category="phone", line=line) for d in phone_digits if len(d) >= 7 and d in digits]
        for category, rx in DEMOGRAPHIC_PATTERNS.items():
            if m := rx.search(line):
                hits.append(LeakageHit(token=m.group(0), category=f"protected:{category}", line=line))
        for rx, category in ((PRONOUN_RE, "pronoun"), (PRONOUN_DECL_RE, "pronoun"), (INJECTION_RE, "injection")):
            if m := rx.search(line):
                hits.append(LeakageHit(token=m.group(0), category=category, line=line))
    return LeakageReport(tokens_checked=len(tokens), hits=hits)


def check_line(line: RenderedLine, profile: ResumeProfile, doc: RenderedDocument) -> str | None:
    """Return ``None`` when the line is grounded, else the reason it is not."""
    if not line.sources:
        return "no source span cited"
    raw = profile.raw_text
    for span in line.sources:
        if span.end > len(raw) or raw[span.start:span.end] != span.text:
            return "cited span does not match the resume text"
        if any(f.span.start < span.end and span.start < f.span.end for f in profile.injection_flags):
            return "cites text flagged as an embedded instruction"
    body = line.text
    for prefix in LINE_PREFIXES:
        if body.startswith(prefix):
            body = body[len(prefix):]
    source_text = " ".join(s.text for s in line.sources)
    if line.derivation == "computed":
        if line.computed is None:
            return "computed line without a computation record"
        expected = recompute_duration(line.computed, doc.as_of, doc.granularity)
        if expected is None:
            return "duration cannot be recomputed from the cited dates"
        if body not in (expected, CAREER_BREAK):
            return f"duration does not match cited dates (expected {expected!r})"
        return None
    if line.derivation == "verbatim":
        return None if _collapse(body) in _collapse(source_text) else "text is not present in the cited span"
    if line.derivation in ("scrubbed", "generalized"):
        unsupported = sorted(_words(body) - _words(source_text) - generalization_vocab())
        return f"unsupported terms: {', '.join(unsupported)}" if unsupported else None
    return "template-derived text outside the fixed template"


def audit_document(doc: RenderedDocument, profile: ResumeProfile,
                   min_confidence: float = 0.6) -> tuple[RenderedDocument, AuditReport]:
    kept: list[RenderedLine] = []
    dropped: list[DroppedLine] = []
    claims = grounded = 0
    for line in doc.lines:
        if line.style in TEMPLATE_STYLES or (line.derivation == "template" and not line.sources):
            if is_template_text(line.text):
                kept.append(line)
            else:
                claims += 1
                dropped.append(DroppedLine(text=line.text, section=line.section, reason="no source span cited"))
            continue
        claims += 1
        reason = check_line(line, profile, doc)
        if reason is None:
            grounded += 1
            kept.append(line)
        else:
            dropped.append(DroppedLine(text=line.text, section=line.section, reason=reason))

    clean = doc.model_copy(update={"lines": kept})
    texts = [ln.text for ln in kept]
    leakage = leakage_check(texts, profile)
    artifacts = [t for t in texts if REDACTION_ARTIFACT_RE.search(t)]
    rate = grounded / claims if claims else 0.0

    caveats: list[str] = []
    confidence = rate
    if dropped:
        caveats.append(f"{len(dropped)} ungrounded line(s) were dropped from the document.")
    if profile.injection_flags:
        confidence -= min(0.2, 0.05 * len(profile.injection_flags))
        caveats.append(f"{len(profile.injection_flags)} embedded instruction(s) were flagged and withheld; "
                       "review the source resume manually.")
    if profile.extraction_warnings:
        confidence -= 0.1
        caveats += profile.extraction_warnings
    if grounded == 0:
        caveats.append("No grounded content was produced.")
    if leakage.hits:
        caveats.append(f"Leakage check found {len(leakage.hits)} identifying token(s); output is blocked.")
    if artifacts:
        caveats.append("Redaction artifacts detected; output is blocked.")
    confidence = round(max(0.0, min(1.0, confidence)), 3)
    if confidence < min_confidence:
        caveats.append(f"Confidence {confidence} is below the minimum {min_confidence}; output is blocked.")

    report = AuditReport(
        claim_lines=claims, grounded_lines=grounded, grounding_rate=round(rate, 3), dropped=dropped, leakage=leakage,
        redaction_artifacts=artifacts, injection_flags=len(profile.injection_flags), confidence=confidence,
        caveats=caveats, passed=bool(grounded) and leakage.passed and not artifacts and confidence >= min_confidence,
    )
    return clean, report
