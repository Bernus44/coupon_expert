"""Enrich scraped Betclic markets with MLB context features."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Optional

from .mlb_client import MLBClient
from .teams import MLB_TEAMS, MLBTeam, format_matchup, parse_matchup

logger = logging.getLogger(__name__)


LINE_RE = re.compile(
    r"(?P<side>\+|\-|plus|moins|\+ de|\- de)\s*(?:de\s*)?(?P<line>\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)

# Game-level totals only (exclude team/inning props like +0.5 / +1.5 / +2.5 runs)
GAME_TOTAL_RUNS_RANGE = (5.5, 14.5)
GAME_TOTAL_HITS_RANGE = (11.5, 25.5)


def parse_market_option(option: str) -> Optional[dict[str, Any]]:
    """Parse '+ de 8,5' / '- de 7.5' into side + line."""
    text = (option or "").strip()
    m = LINE_RE.search(text)
    if not m:
        return None
    side_raw = m.group("side").lower().replace(" ", "")
    line = float(m.group("line").replace(",", "."))
    if side_raw in {"+", "+de", "plus", "plusde"}:
        side = "over"
    else:
        side = "under"
    return {"side": side, "line": line, "raw": option}


def is_game_total_market(market_type: str, line: float, option: str = "", title: str = "") -> bool:
    blob = f"{option} {title}".lower()
    if "&" in blob or " et " in f" {blob} ":
        return False
    if any(x in blob for x in ("manche", "inning", "équipe", "equipe", "joueur", "1ère", "1ere")):
        return False
    if market_type == "Total Runs":
        lo, hi = GAME_TOTAL_RUNS_RANGE
        return lo <= line <= hi
    if market_type == "Total Hits":
        lo, hi = GAME_TOTAL_HITS_RANGE
        return lo <= line <= hi
    return False


def stake_score(standing: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Quantify how much a team still has to play for (playoff race / division)."""
    if not standing:
        return {"score": 0.5, "label": "inconnu"}

    if standing.get("clinched"):
        return {"score": 0.25, "label": "déjà qualifié / peu d'enjeu"}

    elim = str(standing.get("elimination_number") or standing.get("wildcard_elim") or "")
    if elim.upper() == "E":
        return {"score": 0.15, "label": "éliminé"}

    rank = standing.get("division_rank", 99)
    gb = standing.get("games_back", 99.0)
    wcg = standing.get("wildcard_gb", 99.0)

    if rank == 1 and gb <= 3:
        return {"score": 0.9, "label": "lutte division / leader serré"}
    if wcg is not None and wcg <= 3:
        return {"score": 0.85, "label": "course wild-card serrée"}
    if gb <= 5 or (wcg is not None and wcg <= 5):
        return {"score": 0.7, "label": "encore en course"}
    if rank <= 3:
        return {"score": 0.55, "label": "enjeu moyen"}
    return {"score": 0.35, "label": "enjeu faible"}


class MatchEnricher:
    def __init__(self, client: Optional[MLBClient] = None):
        self.client = client or MLBClient()
        self._standings_index: Optional[dict[int, dict[str, Any]]] = None
        self._rates_cache: dict[int, dict[str, Any]] = {}
        self._form_cache: dict[int, dict[str, Any]] = {}

    def _standings(self) -> dict[int, dict[str, Any]]:
        if self._standings_index is None:
            rows = self.client.standings()
            self._standings_index = {r["team_id"]: r for r in rows}
        return self._standings_index

    def _rates(self, team_id: int) -> dict[str, Any]:
        if team_id not in self._rates_cache:
            self._rates_cache[team_id] = self.client.team_season_rates(team_id)
        return self._rates_cache[team_id]

    def _form(self, team_id: int) -> dict[str, Any]:
        if team_id not in self._form_cache:
            self._form_cache[team_id] = self.client.recent_form(team_id, n_games=10)
        return self._form_cache[team_id]

    def _resolve_from_schedule(
        self,
        known: Optional[MLBTeam],
        around: date,
    ) -> tuple[Optional[MLBTeam], Optional[MLBTeam]]:
        """If only one club is known, recover the opponent from the MLB schedule."""
        if not known:
            return None, None
        by_id = {t.id: t for t in MLB_TEAMS}
        start = around - timedelta(days=2)
        end = around + timedelta(days=2)
        data = self.client.get(
            "/schedule",
            {
                "sportId": 1,
                "teamId": known.id,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
            },
        )
        game = None
        # Prefer exact date, else first available
        for day in data.get("dates", []):
            if day.get("date") == around.isoformat() and day.get("games"):
                game = day["games"][0]
                break
        if not game:
            for day in data.get("dates", []):
                if day.get("games"):
                    game = day["games"][0]
                    break
        if not game:
            return known, None
        away_id = game["teams"]["away"]["team"]["id"]
        home_id = game["teams"]["home"]["team"]["id"]
        return by_id.get(away_id), by_id.get(home_id)

    def enrich_match(self, scraped: dict[str, Any]) -> Optional[dict[str, Any]]:
        match_label = scraped.get("match") or ""
        around = _parse_date(scraped.get("date_heure"))
        away, home = parse_matchup(match_label, url=scraped.get("url"))
        if not away or not home:
            away, home = self._resolve_from_schedule(away or home, around)
        if not away or not home:
            logger.warning("Équipes non résolues pour %s", match_label)
            return None
        match_label = format_matchup(away, home)

        # Detect orientation against MLB schedule if possible
        scheduled = self.client.find_scheduled_game(away.id, home.id, around=around)
        orientation_flipped = False
        if scheduled:
            sched_away = scheduled["teams"]["away"]["team"]["id"]
            sched_home = scheduled["teams"]["home"]["team"]["id"]
            if sched_away == home.id and sched_home == away.id:
                away, home = home, away
                orientation_flipped = True
            elif sched_away == away.id and sched_home == home.id:
                orientation_flipped = False
            else:
                # keep Betclic order as away/home assumption
                pass

        standings = self._standings()
        away_st = standings.get(away.id)
        home_st = standings.get(home.id)
        away_rates = self._rates(away.id)
        home_rates = self._rates(home.id)
        away_form = self._form(away.id)
        home_form = self._form(home.id)
        h2h = self.client.head_to_head(away.id, home.id)
        away_inj = self.client.injuries_for_team(away.name)
        home_inj = self.client.injuries_for_team(home.name)
        away_il = self.client.roster_il_count(away.id)
        home_il = self.client.roster_il_count(home.id)

        league_avg_runs = _league_avg_runs(standings)
        league_avg_hits = 8.3  # MLB typical hits/game per team approx; refined below if possible

        expected_runs = _expected_total(
            away_offense=away_rates["away"]["runs_scored_pg"],
            away_defense=away_rates["away"]["runs_allowed_pg"],
            home_offense=home_rates["home"]["runs_scored_pg"],
            home_defense=home_rates["home"]["runs_allowed_pg"],
            league_avg=league_avg_runs,
            away_form_off=away_form["runs_scored_pg"],
            away_form_def=away_form["runs_allowed_pg"],
            home_form_off=home_form["runs_scored_pg"],
            home_form_def=home_form["runs_allowed_pg"],
            h2h_avg=h2h.get("avg_total_runs"),
        )
        expected_hits = _expected_total(
            away_offense=away_rates["away"]["hits_pg"],
            away_defense=away_rates["away"]["hits_allowed_pg"],
            home_offense=home_rates["home"]["hits_pg"],
            home_defense=home_rates["home"]["hits_allowed_pg"],
            league_avg=league_avg_hits * 2,
            away_form_off=away_form["hits_pg"],
            away_form_def=away_form["hits_allowed_pg"],
            home_form_off=home_form["hits_pg"],
            home_form_def=home_form["hits_allowed_pg"],
            h2h_avg=h2h.get("avg_total_hits"),
        )

        away_stake = stake_score(away_st)
        home_stake = stake_score(home_st)

        markets = []
        for paris in scraped.get("paris") or []:
            parsed = parse_market_option(paris.get("option") or "")
            if not parsed:
                continue
            mtype = paris.get("type") or ""
            if not is_game_total_market(
                mtype,
                parsed["line"],
                option=paris.get("option") or "",
                title=paris.get("title") or "",
            ):
                continue
            markets.append(
                {
                    "type": mtype,
                    "option": paris.get("option"),
                    "cote": float(paris.get("cote") or 0),
                    "side": parsed["side"],
                    "line": parsed["line"],
                }
            )

        return {
            "match": match_label,
            "date_heure": scraped.get("date_heure"),
            "away": _team_payload(away, away_st, away_rates, away_form, away_inj, away_il, away_stake),
            "home": _team_payload(home, home_st, home_rates, home_form, home_inj, home_il, home_stake),
            "posture": {
                "away_is_visitor": True,
                "orientation_flipped_from_betclic": orientation_flipped,
                "venue": ((scheduled or {}).get("venue") or {}).get("name"),
                "day_night": (scheduled or {}).get("dayNight"),
                "probable_pitchers": {
                    "away": _probable(scheduled, "away") if scheduled else None,
                    "home": _probable(scheduled, "home") if scheduled else None,
                },
            },
            "h2h": h2h,
            "enjeu": {
                "away": away_stake,
                "home": home_stake,
                "combined": round((away_stake["score"] + home_stake["score"]) / 2, 3),
            },
            "model_priors": {
                "expected_total_runs": round(expected_runs, 3),
                "expected_total_hits": round(expected_hits, 3),
                "league_avg_runs_game": round(league_avg_runs, 3),
            },
            "markets": markets,
            "scheduled_game_pk": (scheduled or {}).get("gamePk"),
        }


def _team_payload(
    team: MLBTeam,
    standing: Optional[dict[str, Any]],
    rates: dict[str, Any],
    form: dict[str, Any],
    injuries: list[dict[str, Any]],
    il: dict[str, int],
    stake: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": team.id,
        "name": team.name,
        "abbreviation": team.abbreviation,
        "standings": standing,
        "season_rates": rates,
        "form_last10": form,
        "injuries": injuries[:12],
        "injury_count": len(injuries),
        "il_roster": il,
        "stake": stake,
    }


def _probable(game: dict[str, Any], side: str) -> Optional[str]:
    try:
        p = game["teams"][side].get("probablePitcher") or {}
        return p.get("fullName")
    except (KeyError, TypeError):
        return None


def _parse_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return date.today()


def _league_avg_runs(standings: dict[int, dict[str, Any]]) -> float:
    if not standings:
        return 8.6
    totals = []
    for row in standings.values():
        gp = max((row.get("wins", 0) + row.get("losses", 0)), 1)
        # runs scored + allowed ≈ 2 * team games contribution; use scored only then *2/gp later
        totals.append(row.get("runs_scored", 0) / gp)
    # average runs scored per team per game * 2 ≈ total runs in a game
    return (sum(totals) / len(totals)) * 2 if totals else 8.6


def _expected_total(
    away_offense: float,
    away_defense: float,
    home_offense: float,
    home_defense: float,
    league_avg: float,
    away_form_off: float,
    away_form_def: float,
    home_form_off: float,
    home_form_def: float,
    h2h_avg: Optional[float],
) -> float:
    """Blend season, form and H2H into an expected game total."""
    half_league = max(league_avg / 2.0, 0.1)
    # Pythagorean-ish matchup: offense vs opponent defense scaled to league
    away_exp = (away_offense * home_defense) / half_league
    home_exp = (home_offense * away_defense) / half_league
    season_total = away_exp + home_exp

    form_total = (away_form_off + home_form_off + away_form_def + home_form_def) / 2.0
    # weight: season 50%, recent form 30%, H2H 20%
    if h2h_avg and h2h_avg > 0:
        return 0.5 * season_total + 0.3 * form_total + 0.2 * h2h_avg
    return 0.65 * season_total + 0.35 * form_total
