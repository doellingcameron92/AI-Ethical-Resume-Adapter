"""Geography reference data used to generalize exact locations to regions."""

from __future__ import annotations

import re

US_STATES: dict[str, tuple[str, str]] = {
    # code: (name, census region)
    "AL": ("Alabama", "South"), "AK": ("Alaska", "West"), "AZ": ("Arizona", "West"), "AR": ("Arkansas", "South"),
    "CA": ("California", "West"), "CO": ("Colorado", "West"), "CT": ("Connecticut", "Northeast"),
    "DE": ("Delaware", "South"), "DC": ("District of Columbia", "South"), "FL": ("Florida", "South"),
    "GA": ("Georgia", "South"), "HI": ("Hawaii", "West"), "ID": ("Idaho", "West"), "IL": ("Illinois", "Midwest"),
    "IN": ("Indiana", "Midwest"), "IA": ("Iowa", "Midwest"), "KS": ("Kansas", "Midwest"), "KY": ("Kentucky", "South"),
    "LA": ("Louisiana", "South"), "ME": ("Maine", "Northeast"), "MD": ("Maryland", "South"),
    "MA": ("Massachusetts", "Northeast"), "MI": ("Michigan", "Midwest"), "MN": ("Minnesota", "Midwest"),
    "MS": ("Mississippi", "South"), "MO": ("Missouri", "Midwest"), "MT": ("Montana", "West"), "NE": ("Nebraska", "Midwest"),
    "NV": ("Nevada", "West"), "NH": ("New Hampshire", "Northeast"), "NJ": ("New Jersey", "Northeast"),
    "NM": ("New Mexico", "West"), "NY": ("New York", "Northeast"), "NC": ("North Carolina", "South"),
    "ND": ("North Dakota", "Midwest"), "OH": ("Ohio", "Midwest"), "OK": ("Oklahoma", "South"), "OR": ("Oregon", "West"),
    "PA": ("Pennsylvania", "Northeast"), "RI": ("Rhode Island", "Northeast"), "SC": ("South Carolina", "South"),
    "SD": ("South Dakota", "Midwest"), "TN": ("Tennessee", "South"), "TX": ("Texas", "South"), "UT": ("Utah", "West"),
    "VT": ("Vermont", "Northeast"), "VA": ("Virginia", "South"), "WA": ("Washington", "West"),
    "WV": ("West Virginia", "South"), "WI": ("Wisconsin", "Midwest"), "WY": ("Wyoming", "West"),
}
US_STATE_NAMES = {name.lower(): code for code, (name, _) in US_STATES.items()}

CA_PROVINCES = {"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT", "Alberta", "British Columbia",
                "Manitoba", "New Brunswick", "Newfoundland", "Nova Scotia", "Ontario", "Quebec", "Saskatchewan"}

COUNTRY_REGIONS: dict[str, str] = {
    "united states": "North America", "usa": "North America", "us": "North America", "canada": "North America",
    "mexico": "Latin America", "brazil": "Latin America", "argentina": "Latin America", "chile": "Latin America",
    "colombia": "Latin America", "peru": "Latin America", "united kingdom": "Europe", "uk": "Europe", "england": "Europe",
    "scotland": "Europe", "ireland": "Europe", "france": "Europe", "germany": "Europe", "spain": "Europe", "italy": "Europe",
    "netherlands": "Europe", "belgium": "Europe", "sweden": "Europe", "norway": "Europe", "denmark": "Europe",
    "finland": "Europe", "poland": "Europe", "portugal": "Europe", "switzerland": "Europe", "austria": "Europe",
    "india": "South Asia", "pakistan": "South Asia", "bangladesh": "South Asia", "sri lanka": "South Asia",
    "china": "East Asia", "japan": "East Asia", "south korea": "East Asia", "korea": "East Asia", "taiwan": "East Asia",
    "singapore": "Southeast Asia", "vietnam": "Southeast Asia", "philippines": "Southeast Asia", "indonesia": "Southeast Asia",
    "malaysia": "Southeast Asia", "thailand": "Southeast Asia", "australia": "Oceania", "new zealand": "Oceania",
    "nigeria": "Sub-Saharan Africa", "kenya": "Sub-Saharan Africa", "south africa": "Sub-Saharan Africa",
    "ghana": "Sub-Saharan Africa", "ethiopia": "Sub-Saharan Africa", "egypt": "Middle East and North Africa",
    "israel": "Middle East and North Africa", "united arab emirates": "Middle East and North Africa",
    "uae": "Middle East and North Africa", "saudi arabia": "Middle East and North Africa",
    "turkey": "Middle East and North Africa",
}

_REGION_ALT = "|".join(
    [re.escape(c) for c in US_STATES] + [re.escape(n) for n, _ in US_STATES.values()]
    + [re.escape(p) for p in CA_PROVINCES] + [re.escape(c.title()) for c in COUNTRY_REGIONS]
    + ["USA", "UK", "UAE"]
)
LOCATION_RE = re.compile(
    rf"\b(?P<city>[A-Z][a-zA-Z.'-]+(?:[ ]+[A-Z][a-zA-Z.'-]+){{0,3}}),[ ]*(?P<region>{_REGION_ALT})\b(?![ ]+[A-Z][a-z])"
    r"(?:[ ]+\d{5}(?:-\d{4})?)?"
    r"|\b(?P<remote>Remote|Hybrid)\b"
)


def region_labels(city: str | None, region: str | None, remote: bool = False) -> tuple[str, str]:
    """Return ``(country_label, region_label)`` for a parsed location."""
    if remote:
        return "Remote", "Remote"
    r = (region or "").strip()
    code = r.upper() if r.upper() in US_STATES else US_STATE_NAMES.get(r.lower())
    if code:
        return "United States", f"US {US_STATES[code][1]}"
    if r in CA_PROVINCES:
        return "Canada", "Canada"
    world = COUNTRY_REGIONS.get(r.lower())
    if world:
        country = {"usa": "United States", "us": "United States", "uk": "United Kingdom", "uae": "United Arab Emirates"}.get(
            r.lower(), r.title())
        return country, world
    return "Location not specified", "Location not specified"


def find_locations(text: str) -> list[tuple[int, int, str, str, str]]:
    """``(start, end, matched_text, country_label, region_label)`` for each location in ``text``."""
    found = []
    for m in LOCATION_RE.finditer(text):
        if m.group("remote"):
            country, region = region_labels(None, None, remote=True)
        else:
            country, region = region_labels(m.group("city"), m.group("region"))
        found.append((m.start(), m.end(), m.group(0), country, region))
    return found
