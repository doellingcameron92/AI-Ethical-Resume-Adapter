"""Shared detectors: prompt injection, scope, PII patterns and demographic signals.

Resume text is untrusted input. These patterns are deliberately conservative:
a false positive removes a line from the blinded document (and is logged), a
false negative could leak a protected attribute or let embedded text steer the
pipeline, so recall is preferred over precision.
"""

from __future__ import annotations

import re

INJECTION_RE = re.compile(
    r"(ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|prompts|rules)"
    r"|disregard\s+(?:all\s+|any\s+)?(?:your|the|previous|prior)\s+(?:rules|instructions|guidelines)"
    r"|you\s+are\s+now\b|system\s+prompt|developer\s+message|reveal\s+your"
    r"|(?:note|message|instruction)s?\s+(?:to|for)\s+(?:the\s+)?(?:ai|llm|gpt|chatgpt|language\s+model|screening|ats|recruit\w*\s+bot)"
    r"|(?:rank|rate|score|mark|classify)\s+(?:this|the)\s+(?:candidate|applicant|resume)"
    r"|(?:recommend|advance|shortlist)\s+(?:this|the)\s+(?:candidate|applicant)\s+(?:for|to)"
    r"|(?:this|the)\s+(?:candidate|applicant)\s+is\s+(?:the\s+)?(?:best|top|ideal|perfect)"
    r"|(?:top|best|ideal)\s+(?:candidate|applicant)\s+(?:for|in)"
    r"|\b(?:hire|interview)\s+(?:this|the)\s+(?:candidate|applicant|person)\b"
    r"|do\s+not\s+(?:blind|redact|anonymi[sz]e|remove)"
    r"|\[\s*(?:system|assistant|inst)\s*\]|<\s*/?\s*(?:system|assistant|instructions?)\s*>)",
    re.I,
)

SCOPE_PATTERNS: dict[str, re.Pattern[str]] = {
    "candidate_ranking": re.compile(
        r"\b(rank\w*|score\w*|rate|rating|grade|shortlist\w*|compare\s+(?:the\s+)?(?:candidates|applicants|resumes)|"
        r"(?:best|strongest|top|weakest)\s+(?:candidate|applicant|fit)|fit\s+score|match\s+score|"
        r"how\s+(?:good|strong|qualified)\s+is|"
        r"(?:is|are)\s+(?:this|the|these)\s+(?:candidate|applicant)s?\s+(?:a\s+)?(?:good|strong|qualified))\b",
        re.I),
    "hiring_decision": re.compile(
        r"\b(hire\s+or\s+(?:not|no)|no[- ]hire|hire/no|should\s+(?:we|i|they)\s+(?:hire|interview|reject|advance)|"
        r"(?:reject|accept|advance|screen\s+out)\s+(?:this|the)\s+(?:candidate|applicant)|hiring\s+(?:decision|recommendation)|"
        r"recommend\s+(?:whether|if)\b|worth\s+interviewing|pass\s+or\s+fail)",
        re.I),
    "demographic_inference": re.compile(
        r"\b(?:infer|guess|estimate|predict|determine|figure\s+out|tell\s+me|what\s+is|what's|work\s+out)\b.{0,40}\b"
        r"(age|how\s+old|gender|sex|race|ethnicity|ethnic|religion|religious|nationality|citizenship|national\s+origin|"
        r"sexual\s+orientation|disability|disabled|pregnan\w*|marital|married|veteran|native\s+language|birth\s*place|"
        r"year\s+of\s+birth|date\s+of\s+birth)\b|\bhow\s+old\s+is\b|\bis\s+(?:he|she|the\s+candidate)\s+(?:a\s+)?"
        r"(?:man|woman|male|female|married|pregnant|disabled|foreign|immigrant|citizen)\b",
        re.I),
}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)")
URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s,;)]+|\b(?:linkedin\.com|github\.com|gitlab\.com|behance\.net|dribbble\.com|twitter\.com|x\.com|"
    r"medium\.com|about\.me)/[^\s,;)]*|\b[\w-]+\.(?:com|io|dev|net|org|me)/[^\s,;)]+",
    re.I)
STREET_RE = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z][a-z]+\.?\s+){1,4}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way|Court|Ct|"
    r"Place|Pl|Terrace|Parkway|Pkwy|Circle|Cir|Highway|Hwy)\b\.?(?:,?\s+(?:Apt|Suite|Unit|#)\.?\s*[\w-]+)?")
ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")

PRONOUNS: dict[str, str] = {
    "he": "they", "she": "they", "him": "them", "his": "their", "her": "their", "hers": "theirs",
    "himself": "themselves", "herself": "themselves",
}
PRONOUN_RE = re.compile(r"\b(" + "|".join(PRONOUNS) + r")\b", re.I)
PRONOUN_DECL_RE = re.compile(r"\(?\b(?:she|he|they|ze|xe)\s*/\s*(?:her|him|them|hir|xem)(?:\s*/\s*\w+)?\b\)?", re.I)
HONORIFIC_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Mx|Sir|Madam)\.?(?=\s|$)")

GENDERED_TITLES: dict[str, str] = {
    "salesman": "salesperson", "saleswoman": "salesperson", "chairman": "chair", "chairwoman": "chair",
    "foreman": "supervisor", "forewoman": "supervisor", "waitress": "server", "waiter": "server",
    "stewardess": "flight attendant", "steward": "flight attendant", "spokesman": "spokesperson",
    "spokeswoman": "spokesperson", "businessman": "professional", "businesswoman": "professional",
    "craftsman": "craftsperson", "hostess": "host", "actress": "actor", "policeman": "police officer",
    "policewoman": "police officer", "fireman": "firefighter", "mailman": "mail carrier", "cameraman": "camera operator",
    "repairman": "repair technician", "workman": "worker", "headmistress": "head of school", "headmaster": "head of school",
    "councilman": "council member", "councilwoman": "council member", "anchorman": "anchor", "anchorwoman": "anchor",
    "journeyman": "journey-level worker", "draftsman": "drafter", "middleman": "intermediary",
}
GENDERED_TITLE_RE = re.compile(r"\b(" + "|".join(sorted(GENDERED_TITLES, key=len, reverse=True)) + r")\b", re.I)

DEMOGRAPHIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "age": re.compile(
        r"\b(date\s+of\s+birth|d\.?o\.?b\.?|born\s+(?:in|on)|\d{2}\s+years?\s+old|age[d]?\s*:?\s*\d{2}|"
        r"recent\s+(?:college\s+)?graduate|digital\s+native|young\s+(?:professional|and\s+energetic)|retiree)\b", re.I),
    "family_status": re.compile(
        r"\b(married|single\s+(?:mother|father|parent)|divorced|widow(?:ed|er)?|spouse|wife|husband|mother\s+of|father\s+of|"
        r"mom\s+of|dad\s+of|parent\s+of|maternity|paternity|parental\s+leave|pregnan\w*|stay[- ]at[- ]home|homemaker|"
        r"full[- ]time\s+parent|caregiver\s+(?:for|to)\s+(?:my|a)\s+\w+|children|kids)\b", re.I),
    "gender_sexuality": re.compile(
        r"\b(female|male|woman|women|women's|womens|man\s+of|girls?\s+who\s+code|society\s+of\s+women\s+engineers|sorority|"
        r"fraternity|lgbtq\w*\+?|gay|lesbian|bisexual|transgender|queer|nonbinary|non-binary)\b", re.I),
    "race_ethnicity": re.compile(
        r"\b(african[- ]american|caucasian|hispanic|latin[aoxe]\b|asian[- ]american|native\s+american|indigenous|"
        r"pacific\s+islander|first\s+nations|ethnicity|black\s+(?:mba|engineers|professionals|students?|women|men|alumni|"
        r"student\s+union)|nsbe|shpe|national\s+society\s+of\s+black|society\s+of\s+hispanic|hbcu)\b", re.I),
    "religion": re.compile(
        r"\b(church|mosque|synagogue|temple|parish|christian|muslim|islamic|jewish|hindu|buddhist|sikh|catholic|baptist|"
        r"methodist|lutheran|evangelical|mormon|lds|religious|faith[- ]based|bible|quran|torah|hillel|youth\s+group)\b", re.I),
    "national_origin": re.compile(
        r"\b(citizenship|citizen\s+of|nationality|national\s+origin|visa\s+status|green\s+card|work\s+permit|h-?1b|"
        r"immigra\w*|native\s+(?:english|spanish|mandarin|hindi|arabic|french|german|\w+)\s+speaker|mother\s+tongue|"
        r"place\s+of\s+birth|birthplace)\b", re.I),
    "disability_health": re.compile(
        r"\b(disabilit\w*|disabled|wheelchair|deaf|hard\s+of\s+hearing|visually\s+impaired|autis\w*|adhd|neurodivergent|"
        r"chronic\s+illness|cancer\s+survivor|medical\s+leave|health\s+condition)\b", re.I),
    "veteran_status": re.compile(r"\b(veteran|military\s+spouse|gi\s+bill|honorabl[ey]\s+discharged?)\b", re.I),
    "photo": re.compile(r"\b(photo(?:graph)?\s+(?:attached|enclosed|included)|headshot)\b", re.I),
    "personal_details": re.compile(r"\b(marital\s+status|gender\s*:|sex\s*:|height\s*:|weight\s*:|religion\s*:|race\s*:)", re.I),
}

REDACTION_ARTIFACT_RE = re.compile(
    r"\[\s*(?:redacted|removed|pii|name|email|phone|address|withheld|anonymi[sz]ed)[^\]]*\]|█|\bX{3,}\b|<redacted>|\*{3,}", re.I)


def find_injections(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group(0)) for m in INJECTION_RE.finditer(text)]


def scope_violation(request: str) -> str | None:
    for category, pattern in SCOPE_PATTERNS.items():
        if pattern.search(request):
            return category
    return None


def demographic_category(text: str) -> str | None:
    for category, pattern in DEMOGRAPHIC_PATTERNS.items():
        if pattern.search(text):
            return category
    return None


def find_demographic_signals(text: str) -> list[tuple[str, int, int, str]]:
    hits = []
    for category, pattern in DEMOGRAPHIC_PATTERNS.items():
        for m in pattern.finditer(text):
            hits.append((category, m.start(), m.end(), m.group(0)))
    for m in PRONOUN_DECL_RE.finditer(text):
        hits.append(("pronouns", m.start(), m.end(), m.group(0)))
    return sorted(hits, key=lambda h: h[1])
