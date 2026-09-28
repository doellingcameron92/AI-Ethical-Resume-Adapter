"""Generalization: ``ResumeProfile`` -> ``GeneralizedProfile`` with identifiers removed.

* employer names -> industry + organisation size (+ sub-sector at ``fine``)
* school names -> degree level + field + institution type
* exact locations -> broad region (country at ``coarse``)
* date ranges -> durations; gaps between roles -> neutral "Career break" entries
* names, pronouns, photos, addresses, links and demographic signals are removed

Every output item keeps the source spans it was derived from so the audit can
trace it back to the resume. Free text is *scrubbed* (identifiers substituted
from a closed vocabulary) rather than rewritten, so no new claims are created.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

from .geo import COUNTRY_REGIONS, US_STATES, find_locations
from .models import (
    ComputedDuration,
    Extracted,
    GeneralizedProfile,
    GeneralizedRole,
    Granularity,
    OutputItem,
    RemovalRecord,
    ResumeProfile,
    SourceSpan,
)
from .safety import (
    EMAIL_RE,
    GENDERED_TITLE_RE,
    GENDERED_TITLES,
    HONORIFIC_RE,
    PHONE_RE,
    PRONOUN_DECL_RE,
    PRONOUN_RE,
    PRONOUNS,
    STREET_RE,
    URL_RE,
    demographic_category,
)

DATA = Path(__file__).parent / "data"

SIZE_LABELS = {
    "enterprise": "Large enterprise",
    "large": "Large organization",
    "mid": "Mid-size organization",
    "small": "Small organization",
    "startup": "Early-stage company",
}
UNKNOWN_SIZE = "Size not specified"
UNKNOWN_INDUSTRY = "Industry not specified"

INSTITUTION_TYPES = {
    "research_very_high": "Research university (very high research activity)",
    "research_high": "Research university (high research activity)",
    "masters": "Master's-level university",
    "liberal_arts": "Liberal arts college",
    "community_college": "Community or technical college",
    "online": "Online institution",
    "university": "University",
    "college": "College",
    "training_provider": "Training provider",
}
UNKNOWN_INSTITUTION = "Institution type not specified"

INDUSTRY_KEYWORDS: list[tuple[str, re.Pattern[str]]] = [
    ("Public sector", re.compile(r"\b(army|navy|air force|marine corps|coast guard|national guard|department of|ministry|"
                                 r"county|city of|state of|government|federal|municipal|agency|public schools?)\b", re.I)),
    ("Education", re.compile(r"\b(university|college|school|academy|institute of technology)\b", re.I)),
    ("Healthcare", re.compile(r"\b(health\w*|medical|hospital|clinic|care|pharmacy|dental|nursing|surgical)\b", re.I)),
    ("Financial services", re.compile(r"\b(bank\w*|credit union|capital|financial|finance|insurance|investments?|securities|"
                                      r"lending|mortgage|wealth)\b", re.I)),
    ("Logistics", re.compile(r"\b(logistics|freight|shipping|transport\w*|delivery|trucking|supply chain)\b", re.I)),
    ("Energy", re.compile(r"\b(energy|power|utilit(?:y|ies)|oil|gas|solar|wind|electric)\b", re.I)),
    ("Technology", re.compile(r"\b(software|analytics|tech\w*|labs?|systems|data|digital|cloud|cyber\w*|computing|ai|"
                              r"networks?|robotics)\b", re.I)),
    ("Professional services", re.compile(r"\b(consult\w*|advisory|partners|law|legal|accounting|llp)\b", re.I)),
    ("Retail", re.compile(r"\b(retail|stores?|market|grocer\w*|outlet)\b", re.I)),
    ("Manufacturing", re.compile(r"\b(manufactur\w*|industries|motors|fabrication|steel|chemicals?)\b", re.I)),
    ("Hospitality", re.compile(r"\b(hotels?|restaurants?|hospitality|resorts?|catering)\b", re.I)),
    ("Nonprofit", re.compile(r"\b(foundation|nonprofit|non-profit|charity|charitable)\b", re.I)),
]

INSTITUTION_KEYWORDS: list[tuple[str, re.Pattern[str]]] = [
    ("community_college", re.compile(
        r"\b(community college|technical college|technical community college|junior college)\b", re.I)),
    ("online", re.compile(r"\b(online|coursera|udemy|edx)\b", re.I)),
    ("training_provider", re.compile(r"\b(bootcamp|academy)\b", re.I)),
    ("university", re.compile(r"\b(university|universidad|universit[éeä]t?|institute of technology|polytechnic)\b", re.I)),
    ("college", re.compile(r"\b(college|school)\b", re.I)),
]

DEGREE_LEVELS: list[tuple[str, re.Pattern[str]]] = [
    ("Doctoral degree", re.compile(r"ph\.?\s?d|doctor|^d\.?b\.?a|^j\.?d|^m\.?d\.?$|dnp", re.I)),
    ("Master's degree", re.compile(r"master|^m\.?b\.?a|^m\.?s|^m\.?a\.?$|^m\.?eng|^m\.?p\.?h", re.I)),
    ("Bachelor's degree", re.compile(r"bachelor|^b\.?s|^b\.?a|^b\.?eng|^b\.?b\.?a", re.I)),
    ("Associate degree", re.compile(r"associate|^a\.?a|^a\.?s", re.I)),
    ("High school diploma", re.compile(r"high school|ged", re.I)),
    ("Certificate", re.compile(r"certificate|diploma", re.I)),
]

MILITARY_TITLES = {
    "staff sergeant": "supervisor", "sergeant": "supervisor", "corporal": "team lead", "lieutenant": "officer",
    "petty officer": "supervisor", "airman": "specialist", "seaman": "specialist", "private first class": "specialist",
}
MILITARY_TITLE_RE = re.compile(r"\b(" + "|".join(sorted(MILITARY_TITLES, key=len, reverse=True)) + r")\b", re.I)
BREAK_TITLE_RE = re.compile(
    r"\b(leave|sabbatical|career\s+break|gap\s+year|caregiv\w*|family\s+care|travel(?:ling|ing)?)\b", re.I)

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
SEASONS = {"spring": 3, "summer": 6, "fall": 9, "autumn": 9, "winter": 12}
ONGOING_RE = re.compile(r"^(present|current|now|today|ongoing)$", re.I)

YEAR_PHRASE_RE = re.compile(
    r"(?:,\s*|\s+(?:in|since|during|from|by|as\s+of|circa|through|until)\s+|\s*\(\s*(?=(?:[A-Z][a-z]+\s+)?(?:19|20)\d{2}\s*\)))?"
    r"\b(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+)?(?:19|20)\d{2}"
    r"(?:\s*(?:-|–|—|to)\s*(?:(?:19|20)\d{2}|present|current))?\b\s*\)?",
    re.I,
)

CAREER_BREAK = "Career break"
REPLACE_EMPLOYER = "the organization"
REPLACE_INSTITUTION = "the institution"
REPLACE_REGION = "the region"


def _norm_key(name: str) -> str:
    key = re.sub(r"[^\w&\s]", "", name.lower())
    key = re.sub(r"\b(the|inc|llc|ltd|corp|corporation|co|plc|gmbh|company|group)\b", " ", key)
    return re.sub(r"\s+", " ", key).strip()


@lru_cache(maxsize=4)
def load_directory(kind: str, path: str | None = None) -> dict:
    src = Path(path) if path else DATA / f"{kind}.json"
    raw = json.loads(src.read_text(encoding="utf-8"))
    return {_norm_key(k): v for k, v in raw.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Durations (shared with the audit so computed lines can be re-derived)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MonthPoint:
    index: int  # year * 12 + (month - 1)
    approximate: bool  # year-only precision


def parse_point(text: str, as_of: str, is_end: bool = False) -> MonthPoint | None:
    t = text.strip().strip(".").lower()
    if ONGOING_RE.match(t):
        y, m = (int(x) for x in as_of.split("-")[:2])
        return MonthPoint(y * 12 + m - 1, False)
    if m := re.fullmatch(r"(\d{1,2})/((?:19|20)\d{2})", t):
        return MonthPoint(int(m.group(2)) * 12 + int(m.group(1)) - 1, False)
    if m := re.fullmatch(r"([a-z]+)\.?\s+((?:19|20)\d{2})", t):
        word = m.group(1)
        month = MONTHS.get(word[:3]) if word[:3] in MONTHS else SEASONS.get(word)
        if month:
            return MonthPoint(int(m.group(2)) * 12 + month - 1, False)
    if m := re.fullmatch(r"((?:19|20)\d{2})", t):
        return MonthPoint(int(m.group(1)) * 12 + (11 if is_end else 0), True)
    return None


def tenure_months(start: str, end: str | None, as_of: str) -> tuple[int, bool] | None:
    a = parse_point(start, as_of)
    b = parse_point(end, as_of, is_end=True) if end else None
    if a is None or b is None or b.index < a.index:
        return None
    if a.approximate or b.approximate:
        return max(1, b.index // 12 - a.index // 12) * 12, True
    return b.index - a.index + 1, False


def gap_months(prev_end: str, next_start: str, as_of: str) -> tuple[int, bool] | None:
    a = parse_point(prev_end, as_of, is_end=True)
    b = parse_point(next_start, as_of)
    if a is None or b is None:
        return None
    return b.index - a.index - 1, a.approximate or b.approximate


def duration_label(months: int, approximate: bool, granularity: Granularity) -> str:
    years, rem = divmod(max(months, 0), 12)
    if granularity == "coarse" or approximate:
        if months < 12:
            return "Less than 1 year"
        if granularity == "coarse" and months >= 120:
            return "10+ years"
        rounded = round(months / 12)
        return f"About {rounded} year{'s' if rounded != 1 else ''}"
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if rem or not years:
        parts.append(f"{max(rem, 1)} month{'s' if max(rem, 1) != 1 else ''}")
    return " ".join(parts)


def recompute_duration(c: ComputedDuration, as_of: str, granularity: Granularity) -> str | None:
    result = (tenure_months(c.start.text, c.end.text if c.end else None, as_of) if c.kind == "tenure"
              else gap_months(c.start.text, c.end.text if c.end else "", as_of))
    return duration_label(*result, granularity) if result else None


# ---------------------------------------------------------------------------
# Closed vocabulary for substituted/generalized text (the audit allows these)
# ---------------------------------------------------------------------------

def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'+#&.-]*|\d[\d,.%+]*", text)}


@lru_cache(maxsize=1)
def generalization_vocab() -> frozenset[str]:
    labels: list[str] = [UNKNOWN_SIZE, UNKNOWN_INDUSTRY, UNKNOWN_INSTITUTION, CAREER_BREAK,
                         REPLACE_EMPLOYER, REPLACE_INSTITUTION, REPLACE_REGION, "Remote", "Location not specified",
                         "United States", "Canada", "United Kingdom", "United Arab Emirates", "in", "Duration",
                         "Region", "Business Administration", "Professional role", "About", "Less than", "10+"]
    labels += list(SIZE_LABELS.values()) + list(INSTITUTION_TYPES.values())
    labels += [lvl for lvl, _ in DEGREE_LEVELS] + [ind for ind, _ in INDUSTRY_KEYWORDS]
    labels += list(PRONOUNS.values()) + list(GENDERED_TITLES.values()) + list(MILITARY_TITLES.values())
    labels += [f"US {r}" for _, r in US_STATES.values()] + list(COUNTRY_REGIONS.values())
    labels += ["year", "years", "month", "months"]
    for entry in load_directory("employers").values():
        labels += [entry.get("industry", ""), entry.get("subsector", "")]
    vocab: set[str] = set()
    for label in labels:
        vocab |= _words(label)
    return frozenset(vocab | {"the", "a", "an", "and", "of", "for", "to", "with", "at", "on"})


# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------

def _case_like(src: str, repl: str) -> str:
    return repl[:1].upper() + repl[1:] if src[:1].isupper() else repl


def _sub_word(pattern: str, repl: str, text: str) -> tuple[str, int]:
    rx = re.compile(r"(?<![\w'])" + pattern + r"(?![\w])", re.I)

    def _repl(m: re.Match[str]) -> str:
        sentence_start = not text[:m.start()].strip() or text[:m.start()].rstrip().endswith((".", "!", "?", ":", ";"))
        return repl[:1].upper() + repl[1:] if sentence_start and repl else repl

    return rx.subn(_repl, text)


def _tidy(text: str) -> str:
    text = re.sub(r"\s+([,.;:)])", r"\1", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"([,;:])(?=[,;:.])", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ,;:-–—|")


class Scrubber:
    def __init__(self, profile: ResumeProfile, granularity: Granularity, removed: list[RemovalRecord]) -> None:
        self.profile = profile
        self.granularity = granularity
        self.removed = removed
        self.name_parts = candidate_name_tokens(profile)
        self.employers = sorted({r.employer.value for r in profile.roles if r.employer}, key=len, reverse=True)
        self.institutions = sorted({e.institution.value for e in profile.education if e.institution}, key=len, reverse=True)
        self.locations: list[tuple[str, str, str]] = []  # (matched, city, region label)
        for loc in profile.contact.locations:
            for _s, _e, matched, country, region in find_locations(loc.value):
                city = matched.split(",")[0].strip()
                label = country if granularity == "coarse" else region
                self.locations.append((matched, city, label))
        self.locations.sort(key=lambda t: len(t[0]), reverse=True)

    def _record(self, category: str, reason: str, spans: list[SourceSpan]) -> None:
        self.removed.append(RemovalRecord(category=category, reason=reason, spans=spans))

    def text(self, value: str, spans: list[SourceSpan], context: str, is_title: bool = False) -> str | None:
        """Scrub one extracted value; ``None`` means the whole item must be dropped."""
        category = demographic_category(value)
        if category:
            self._record(f"demographic:{category}", f"{context} referenced a protected attribute", spans)
            return None
        t = value
        for rx, cat in ((EMAIL_RE, "email"), (URL_RE, "link"), (PHONE_RE, "phone"), (STREET_RE, "address")):
            t, n = rx.subn("", t)
            if n:
                self._record(cat, f"{cat} removed from {context}", spans)
        for name in self.employers:
            t, n = _sub_word(re.escape(name), REPLACE_EMPLOYER, t)
            if n:
                self._record("employer_name", f"employer name generalized in {context}", spans)
        for name in self.institutions:
            t, n = _sub_word(re.escape(name), REPLACE_INSTITUTION, t)
            if n:
                self._record("institution_name", f"institution name generalized in {context}", spans)
        for matched, city, label in self.locations:
            for pat in (re.escape(matched), re.escape(city)):
                t, n = _sub_word(pat, label if label != "Location not specified" else REPLACE_REGION, t)
                if n:
                    self._record("location", f"exact location generalized in {context}", spans)
        for part in self.name_parts:
            t, n = _sub_word(re.escape(part), "", t)
            if n:
                self._record("name", f"name removed from {context}", spans)
        t, n = PRONOUN_DECL_RE.subn("", t)
        t2, n2 = PRONOUN_RE.subn(lambda m: _case_like(m.group(0), PRONOUNS[m.group(0).lower()]), t)
        if n or n2:
            self._record("pronouns", f"gendered pronouns neutralized in {context}", spans)
        t = t2
        t, n = GENDERED_TITLE_RE.subn(lambda m: _case_like(m.group(0), GENDERED_TITLES[m.group(0).lower()]), t)
        if n:
            self._record("gendered_title", f"gendered job title neutralized in {context}", spans)
        if is_title:
            t, n = MILITARY_TITLE_RE.subn(lambda m: _case_like(m.group(0), MILITARY_TITLES[m.group(0).lower()]), t)
            if n:
                self._record("demographic:veteran_status", f"military rank generalized in {context}", spans)
        t, n = HONORIFIC_RE.subn("", t)
        if n:
            self._record("honorific", f"honorific removed from {context}", spans)
        t, n = YEAR_PHRASE_RE.subn("", t)
        if n:
            self._record("dates", f"calendar years removed from {context} (age proxy)", spans)
        t = _tidy(t)
        return t or None

    def item(self, e: Extracted, context: str, is_title: bool = False) -> OutputItem | None:
        text = self.text(e.value, e.spans, context, is_title)
        if text is None:
            return None
        return OutputItem(text=text, sources=list(e.spans), derivation="verbatim" if text == e.value else "scrubbed")


def candidate_name_tokens(profile: ResumeProfile) -> list[str]:
    tokens: set[str] = set()
    for name in profile.contact.names:
        base = name.value.split(",")[0].strip()
        tokens.add(base)
        for word in re.split(r"\s+", base):
            word = word.strip(".")
            for piece in {word, *word.split("-")}:
                if len(piece) >= 3 and not piece.isupper():
                    tokens.add(piece)
    return sorted(tokens, key=len, reverse=True)


# ---------------------------------------------------------------------------
# Field generalizers
# ---------------------------------------------------------------------------

def classify_employer(name: str, directory_path: str | None = None) -> dict[str, str]:
    hit = load_directory("employers", directory_path).get(_norm_key(name))
    if hit:
        return dict(hit)
    for industry, rx in INDUSTRY_KEYWORDS:
        if rx.search(name):
            return {"industry": industry}
    return {}


def classify_institution(name: str, directory_path: str | None = None) -> str | None:
    hit = load_directory("institutions", directory_path).get(_norm_key(name))
    if hit:
        return hit
    for kind, rx in INSTITUTION_KEYWORDS:
        if rx.search(name):
            return kind
    return None


def degree_level(degree: str) -> str:
    d = degree.strip()
    for level, rx in DEGREE_LEVELS:
        if rx.search(d):
            return level
    return "Degree"


def _region_for(value: str, granularity: Granularity) -> str | None:
    found = find_locations(value)
    if not found:
        return None
    _s, _e, _m, country, region = found[0]
    return country if granularity == "coarse" else region


def generalize(profile: ResumeProfile, granularity: Granularity = "standard", as_of: str | None = None,
               gap_threshold_months: int = 6, employer_directory: str | None = None,
               institution_directory: str | None = None) -> GeneralizedProfile:
    as_of = as_of or date.today().strftime("%Y-%m")
    removed: list[RemovalRecord] = []
    scrub = Scrubber(profile, granularity, removed)
    ref = "CR-" + hashlib.sha256(profile.raw_text.encode("utf-8")).hexdigest()[:8].upper()
    out = GeneralizedProfile(candidate_ref=ref, granularity=granularity, as_of=as_of)

    _record_identity_removals(profile, removed)

    used_location_spans = {(r.location.spans[0].start) for r in profile.roles if r.location and r.location.spans}
    used_location_spans |= {(e.location.spans[0].start) for e in profile.education if e.location and e.location.spans}
    for loc in profile.contact.locations:
        if loc.spans and loc.spans[0].start not in used_location_spans:
            label = _region_for(loc.value, granularity)
            if label:
                out.home_region = OutputItem(text=label, sources=list(loc.spans), derivation="generalized")
            break

    out.skills = [i for s in profile.skills if (i := scrub.item(s, "skills"))]
    out.certifications = [i for c in profile.certifications if (i := scrub.item(c, "certifications"))]
    out.achievements = [i for a in profile.achievements if (i := scrub.item(a, "achievements"))]
    if profile.summary:
        removed.append(RemovalRecord(category="summary", reason="free-form self-description omitted from uniform template",
                                     spans=[s for e in profile.summary for s in e.spans]))
    out.experience = _experience(profile, scrub, granularity, as_of, gap_threshold_months, employer_directory)
    out.education = _education(profile, scrub, granularity, institution_directory)
    out.removed = removed
    return out


def _record_identity_removals(profile: ResumeProfile, removed: list[RemovalRecord]) -> None:
    c = profile.contact
    for cat, items in (("name", c.names), ("email", c.emails), ("phone", c.phones), ("link", c.urls),
                       ("address", c.addresses)):
        if items:
            removed.append(RemovalRecord(category=cat, reason=f"direct identifier ({cat}) removed",
                                         spans=[s for e in items for s in e.spans]))
    if profile.has_images:
        removed.append(RemovalRecord(category="photo", reason="embedded images/photos are never carried into the output"))
    for sig in profile.demographic_signals:
        removed.append(RemovalRecord(category=f"demographic:{sig.category}", reason="protected-attribute signal removed",
                                     spans=[sig.span]))
    for flag in profile.injection_flags:
        removed.append(RemovalRecord(category="injection", reason="embedded instruction withheld (untrusted input)",
                                     spans=[flag.span]))
    for header in profile.dropped_sections:
        removed.append(RemovalRecord(category="section", reason=f"personal section '{header}' omitted"))


@dataclass
class _Interval:
    start: MonthPoint
    end: MonthPoint
    start_span: SourceSpan
    end_span: SourceSpan


def _experience(profile: ResumeProfile, scrub: Scrubber, granularity: Granularity, as_of: str, threshold: int,
                directory: str | None) -> list[GeneralizedRole]:
    dated: list[tuple[_Interval, GeneralizedRole]] = []
    undated: list[GeneralizedRole] = []
    for role in profile.roles:
        date_spans = [s for e in (role.dates.start, role.dates.end) if e for s in e.spans]
        duration = None
        interval = None
        if role.dates.start and role.dates.start.spans:
            start_span = role.dates.start.spans[0]
            end_span = role.dates.end.spans[0] if role.dates.end and role.dates.end.spans else None
            result = tenure_months(start_span.text, end_span.text if end_span else None, as_of)
            if result and end_span:
                duration = ComputedDuration(kind="tenure", start=start_span, end=end_span,
                                            text=duration_label(*result, granularity))
                a, b = parse_point(start_span.text, as_of), parse_point(end_span.text, as_of, is_end=True)
                if a and b:
                    interval = _Interval(a, b, start_span, end_span)
        removed_dates = RemovalRecord(category="dates", reason="exact dates converted to a duration", spans=date_spans)
        if date_spans:
            scrub.removed.append(removed_dates)

        if BREAK_TITLE_RE.search(role.title.value) and not role.employer:
            entry = GeneralizedRole(kind="career_break",
                                    title=OutputItem(text=CAREER_BREAK, sources=date_spans or list(role.title.spans),
                                                     derivation="computed"),
                                    duration=duration)
            scrub.removed.append(RemovalRecord(category="career_break", reason="leave reason replaced by neutral career break",
                                               spans=list(role.title.spans)))
        else:
            title = scrub.item(role.title, "job title", is_title=True)
            if title is None:
                title = OutputItem(text="Professional role", sources=list(role.title.spans), derivation="generalized")
            entry = GeneralizedRole(title=title, context=_role_context(role, scrub, granularity, directory),
                                    duration=duration,
                                    bullets=[i for b in role.bullets if (i := scrub.item(b, "experience bullet"))])
        (dated.append((interval, entry)) if interval else undated.append(entry))

    edu_intervals = []
    for e in profile.education:
        if e.dates.start and e.dates.end and e.dates.start.spans and e.dates.end.spans:
            a = parse_point(e.dates.start.spans[0].text, as_of)
            b = parse_point(e.dates.end.spans[0].text, as_of, is_end=True)
            if a and b:
                edu_intervals.append(_Interval(a, b, e.dates.start.spans[0], e.dates.end.spans[0]))

    dated.sort(key=lambda t: t[0].start.index)
    timeline: list[GeneralizedRole] = []
    covered: _Interval | None = None
    for interval, entry in dated:
        if covered is not None:
            for edu in edu_intervals:
                if edu.start.index <= interval.start.index and edu.end.index > covered.end.index:
                    covered = edu
            gap = gap_months(covered.end_span.text, interval.start_span.text, as_of)
            if gap and gap[0] >= threshold:
                timeline.append(GeneralizedRole(
                    kind="career_break",
                    title=OutputItem(text=CAREER_BREAK, sources=[covered.end_span, interval.start_span], derivation="computed"),
                    duration=ComputedDuration(kind="gap", start=covered.end_span, end=interval.start_span,
                                              text=duration_label(*gap, granularity))))
        timeline.append(entry)
        if covered is None or interval.end.index > covered.end.index:
            covered = interval
    return list(reversed(timeline)) + undated


def _role_context(role, scrub: Scrubber, granularity: Granularity, directory: str | None) -> OutputItem | None:
    parts: list[str] = []
    sources: list[SourceSpan] = []
    if role.employer:
        info = classify_employer(role.employer.value, directory)
        industry = info.get("industry", UNKNOWN_INDUSTRY)
        if granularity == "fine" and info.get("subsector"):
            industry = f"{industry} ({info['subsector']})"
        parts.append(industry)
        if granularity != "coarse":
            parts.append(SIZE_LABELS.get(info.get("size", ""), UNKNOWN_SIZE))
        sources += role.employer.spans
        scrub.removed.append(RemovalRecord(category="employer_name", reason="employer name generalized to industry and size",
                                           spans=list(role.employer.spans)))
    if role.location:
        label = _region_for(role.location.value, granularity)
        if label:
            parts.append(label)
            sources += role.location.spans
            scrub.removed.append(RemovalRecord(category="location", reason="exact location generalized to region",
                                               spans=list(role.location.spans)))
    if not parts:
        return None
    return OutputItem(text=" · ".join(parts), sources=sources, derivation="generalized")


def _education(profile: ResumeProfile, scrub: Scrubber, granularity: Granularity, directory: str | None) -> list[OutputItem]:
    items = []
    for e in profile.education:
        sources: list[SourceSpan] = []
        level = None
        if e.degree:
            level = degree_level(e.degree.value)
            sources += e.degree.spans
        field_text = None
        if e.field:
            field_text = scrub.text(e.field.value, e.field.spans, "field of study")
            if field_text:
                sources += e.field.spans
        if level and level.startswith("Master") and not field_text and e.degree and re.search(r"m\.?b\.?a", e.degree.value, re.I):
            field_text = "Business Administration"
        text = level or "Degree"
        if granularity == "fine" and e.degree and level != "Certificate":
            degree_text = scrub.text(e.degree.value, e.degree.spans, "degree")
            if degree_text and degree_text.lower() != level.lower():
                text = f"{text} ({degree_text})"
        if field_text:
            text = f"{text} in {field_text}"
        if e.institution:
            sources += e.institution.spans
            scrub.removed.append(RemovalRecord(category="institution_name",
                                               reason="institution name generalized to institution type",
                                               spans=list(e.institution.spans)))
            if granularity != "coarse":
                kind = classify_institution(e.institution.value, directory)
                text = f"{text} · {INSTITUTION_TYPES.get(kind or '', UNKNOWN_INSTITUTION)}"
        if e.location:
            scrub.removed.append(RemovalRecord(category="location", reason="institution location removed",
                                               spans=list(e.location.spans)))
        edu_dates = [s for d in (e.dates.start, e.dates.end) if d for s in d.spans]
        if edu_dates:
            scrub.removed.append(RemovalRecord(category="dates", reason="graduation/attendance dates removed (age proxy)",
                                               spans=edu_dates))
        if sources:
            items.append(OutputItem(text=text, sources=sources, derivation="generalized"))
    return items
