"""Mapping between Betclic match labels and MLB Stats API teams."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MLBTeam:
    id: int
    name: str
    team_name: str
    abbreviation: str
    aliases: tuple[str, ...] = ()
    slug_tokens: tuple[str, ...] = ()


# Canonical MLB clubs + French / Betclic aliases frequently seen on betclic.fr
MLB_TEAMS: list[MLBTeam] = [
    MLBTeam(108, "Los Angeles Angels", "Angels", "LAA", ("Angels", "LA Angels", "Los Angeles Angels"), ("los-angeles-angels", "la-angels", "angels")),
    MLBTeam(109, "Arizona Diamondbacks", "D-backs", "AZ", ("Diamondbacks", "Dbacks", "D-backs", "Arizona"), ("arizona-diamondbacks", "arizona", "diamondbacks")),
    MLBTeam(110, "Baltimore Orioles", "Orioles", "BAL", ("Orioles", "Baltimore"), ("baltimore-orioles", "baltimore", "orioles")),
    MLBTeam(111, "Boston Red Sox", "Red Sox", "BOS", ("Red Sox", "Boston"), ("boston-red-sox", "boston", "red-sox")),
    MLBTeam(112, "Chicago Cubs", "Cubs", "CHC", ("Cubs", "Chicago Cubs"), ("chicago-cubs", "cubs")),
    MLBTeam(113, "Cincinnati Reds", "Reds", "CIN", ("Reds", "Cincinnati"), ("cincinnati-reds", "cincinnati", "reds")),
    MLBTeam(114, "Cleveland Guardians", "Guardians", "CLE", ("Guardians", "Cleveland", "Indians"), ("cleveland-guardians", "cleveland", "guardians")),
    MLBTeam(115, "Colorado Rockies", "Rockies", "COL", ("Rockies", "Colorado"), ("colorado-rockies", "colorado", "rockies")),
    MLBTeam(116, "Detroit Tigers", "Tigers", "DET", ("Tigers", "Detroit"), ("detroit-tigers", "detroit", "tigers")),
    MLBTeam(117, "Houston Astros", "Astros", "HOU", ("Astros", "Houston"), ("houston-astros", "houston", "astros")),
    MLBTeam(118, "Kansas City Royals", "Royals", "KC", ("Royals", "Kansas City", "KC"), ("kansas-city-royals", "kansas-city", "royals")),
    MLBTeam(119, "Los Angeles Dodgers", "Dodgers", "LAD", ("Dodgers", "LA Dodgers", "Los Angeles Dodgers"), ("los-angeles-dodgers", "la-dodgers", "dodgers")),
    MLBTeam(120, "Washington Nationals", "Nationals", "WSH", ("Nationals", "Washington", "Nats"), ("washington-nationals", "washington", "nationals")),
    MLBTeam(121, "New York Mets", "Mets", "NYM", ("Mets", "NY Mets", "New York Mets"), ("new-york-mets", "ny-mets", "mets")),
    MLBTeam(133, "Athletics", "Athletics", "ATH", ("Athletics", "Oakland", "A's", "As", "Oakland Athletics"), ("athletics", "oakland-athletics", "oakland")),
    MLBTeam(134, "Pittsburgh Pirates", "Pirates", "PIT", ("Pirates", "Pittsburgh"), ("pittsburgh-pirates", "pittsburgh", "pirates")),
    MLBTeam(135, "San Diego Padres", "Padres", "SD", ("Padres", "San Diego"), ("san-diego-padres", "san-diego", "padres")),
    MLBTeam(136, "Seattle Mariners", "Mariners", "SEA", ("Mariners", "Seattle"), ("seattle-mariners", "seattle", "mariners")),
    MLBTeam(137, "San Francisco Giants", "Giants", "SF", ("Giants", "San Francisco", "SF Giants"), ("san-francisco-giants", "san-francisco", "giants")),
    MLBTeam(138, "St. Louis Cardinals", "Cardinals", "STL", ("Cardinals", "St Louis", "Saint Louis", "St. Louis"), ("st-louis-cardinals", "st-louis", "cardinals")),
    MLBTeam(139, "Tampa Bay Rays", "Rays", "TB", ("Rays", "Tampa Bay", "Tampa"), ("tampa-bay-rays", "tampa-bay", "rays")),
    MLBTeam(140, "Texas Rangers", "Rangers", "TEX", ("Rangers", "Texas"), ("texas-rangers", "texas", "rangers")),
    MLBTeam(141, "Toronto Blue Jays", "Blue Jays", "TOR", ("Blue Jays", "Toronto", "Jays"), ("toronto-blue-jays", "toronto", "blue-jays")),
    MLBTeam(142, "Minnesota Twins", "Twins", "MIN", ("Twins", "Minnesota"), ("minnesota-twins", "minnesota", "twins")),
    MLBTeam(143, "Philadelphia Phillies", "Phillies", "PHI", ("Phillies", "Philadelphia"), ("philadelphia-phillies", "philadelphia", "phillies")),
    MLBTeam(144, "Atlanta Braves", "Braves", "ATL", ("Braves", "Atlanta"), ("atlanta-braves", "atlanta", "braves")),
    MLBTeam(145, "Chicago White Sox", "White Sox", "CWS", ("White Sox", "Chicago White Sox"), ("chicago-white-sox", "white-sox")),
    MLBTeam(146, "Miami Marlins", "Marlins", "MIA", ("Marlins", "Miami", "Florida Marlins"), ("miami-marlins", "miami", "marlins")),
    MLBTeam(147, "New York Yankees", "Yankees", "NYY", ("Yankees", "NY Yankees", "New York Yankees"), ("new-york-yankees", "ny-yankees", "yankees")),
    MLBTeam(158, "Milwaukee Brewers", "Brewers", "MIL", ("Brewers", "Milwaukee"), ("milwaukee-brewers", "milwaukee", "brewers")),
]


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _alias_index() -> dict[str, MLBTeam]:
    index: dict[str, MLBTeam] = {}
    for team in MLB_TEAMS:
        candidates = {
            team.name,
            team.team_name,
            team.abbreviation,
            *team.aliases,
        }
        for alias in candidates:
            key = _normalize(alias)
            # Prefer longer / more specific keys already set only if absent or shorter
            if key not in index or len(key) >= len(_normalize(index[key].name)):
                index[key] = team
    return index


_ALIAS_INDEX = _alias_index()


def resolve_team(label: str) -> Optional[MLBTeam]:
    """Resolve a free-text team label to an MLB team."""
    key = _normalize(label)
    if not key:
        return None
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]

    best: Optional[MLBTeam] = None
    best_len = 0
    for alias, team in _ALIAS_INDEX.items():
        if alias and alias in key and len(alias) > best_len:
            best = team
            best_len = len(alias)
    return best


def _find_team_spans(norm_text: str) -> list[tuple[int, int, int, MLBTeam]]:
    """Return (start, end, alias_len, team) matches sorted by length desc."""
    hits: list[tuple[int, int, int, MLBTeam]] = []
    for alias, team in _ALIAS_INDEX.items():
        if not alias:
            continue
        start = 0
        while True:
            idx = norm_text.find(alias, start)
            if idx < 0:
                break
            # Require token boundaries (spaces or edges)
            left_ok = idx == 0 or norm_text[idx - 1] == " "
            right = idx + len(alias)
            right_ok = right == len(norm_text) or norm_text[right] == " "
            if left_ok and right_ok:
                hits.append((idx, right, len(alias), team))
            start = idx + 1
    hits.sort(key=lambda h: (-h[2], h[0]))
    return hits


def find_two_teams(text: str) -> tuple[Optional[MLBTeam], Optional[MLBTeam]]:
    """
    Extract two distinct MLB teams from a noisy label.
    Handles broken Betclic names like 'Toronto Blue vs Jays Baltimore Orioles'.
    """
    cleaned = re.sub(r"\s+vs\.?\s+|\s+v\s+|\s+@\s+", " ", text, flags=re.IGNORECASE)
    norm = _normalize(cleaned)
    hits = _find_team_spans(norm)
    if not hits:
        return None, None

    # Greedy: take longest match, then longest non-overlapping different team
    first = hits[0]
    second = None
    for hit in hits[1:]:
        if hit[3].id == first[3].id:
            continue
        # non-overlapping
        if hit[1] <= first[0] or hit[0] >= first[1]:
            second = hit
            break
    if not second:
        return first[3], None

    # Order by appearance in text
    ordered = sorted([first, second], key=lambda h: h[0])
    return ordered[0][3], ordered[1][3]


def teams_from_betclic_slug(slug_or_url: str) -> tuple[Optional[MLBTeam], Optional[MLBTeam]]:
    """
    Parse Betclic URL slug, e.g.
    'texas-rangers-cleveland-guardians-m123' or full URL.
    """
    raw = slug_or_url.rstrip("/").split("/")[-1]
    raw = re.sub(r"-m\d+.*$", "", raw)
    raw = raw.strip("-")
    if not raw:
        return None, None

    # Try all split points; score by matched slug token length
    best: tuple[int, Optional[MLBTeam], Optional[MLBTeam]] = (-1, None, None)
    parts = raw.split("-")
    for i in range(1, len(parts)):
        left = "-".join(parts[:i])
        right = "-".join(parts[i:])
        t1 = _resolve_slug(left)
        t2 = _resolve_slug(right)
        if t1 and t2 and t1.id != t2.id:
            score = len(left) + len(right)
            if score > best[0]:
                best = (score, t1, t2)
    if best[1] and best[2]:
        return best[1], best[2]
    # Fallback: free-text finder on slug words
    return find_two_teams(raw.replace("-", " "))


def _resolve_slug(slug: str) -> Optional[MLBTeam]:
    slug = slug.strip("-").lower()
    for team in MLB_TEAMS:
        for token in team.slug_tokens:
            if slug == token:
                return team
    # soft containment: longest token contained / equal after normalize
    return resolve_team(slug.replace("-", " "))


def parse_matchup(
    match_label: str,
    url: Optional[str] = None,
) -> tuple[Optional[MLBTeam], Optional[MLBTeam]]:
    """
    Parse a Betclic matchup. Prefer URL slug when the display name is broken.
    Convention: Away vs Home when orientation can be inferred later via schedule.
    """
    if url:
        a, b = teams_from_betclic_slug(url)
        if a and b:
            return a, b

    a, b = find_two_teams(match_label)
    if a and b:
        return a, b

    parts = re.split(r"\s+vs\.?\s+|\s+v\s+|\s+@\s+|\s+-\s+", match_label, flags=re.IGNORECASE)
    if len(parts) == 2:
        t1, t2 = resolve_team(parts[0].strip()), resolve_team(parts[1].strip())
        if t1 and t2 and t1.id != t2.id:
            return t1, t2
    return a, b


def format_matchup(away: MLBTeam, home: MLBTeam) -> str:
    return f"{away.name} vs {home.name}"
