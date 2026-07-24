"""Thin client over the public MLB Stats API + ESPN injuries."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

MLB_BASE = "https://statsapi.mlb.com/api/v1"
ESPN_INJURIES = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries"


class MLBClient:
    def __init__(self, session: Optional[requests.Session] = None, timeout: float = 20.0):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.session.headers.setdefault(
            "User-Agent",
            "coupon-expert/1.0 (+https://github.com/bernus44/coupon_expert)",
        )

    def get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{MLB_BASE}{path}"
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def current_season(self) -> int:
        return datetime.now(timezone.utc).year

    def standings(self, season: Optional[int] = None) -> list[dict[str, Any]]:
        season = season or self.current_season()
        data = self.get(
            "/standings",
            {"leagueId": "103,104", "season": season, "standingsTypes": "regularSeason"},
        )
        rows: list[dict[str, Any]] = []
        for block in data.get("records", []):
            for rec in block.get("teamRecords", []):
                rows.append(
                    {
                        "team_id": rec["team"]["id"],
                        "team_name": rec["team"]["name"],
                        "wins": rec.get("wins", 0),
                        "losses": rec.get("losses", 0),
                        "pct": float(rec.get("winningPercentage") or 0),
                        "division_rank": int(rec.get("divisionRank") or 99),
                        "league_rank": int(rec.get("leagueRank") or 99),
                        "games_back": _parse_gb(rec.get("gamesBack")),
                        "wildcard_gb": _parse_gb(rec.get("wildCardGamesBack")),
                        "runs_scored": rec.get("runsScored", 0),
                        "runs_allowed": rec.get("runsAllowed", 0),
                        "run_diff": rec.get("runDifferential", 0),
                        "streak": (rec.get("streak") or {}).get("streakCode"),
                        "division_leader": bool(rec.get("divisionLeader")),
                        "clinched": bool(rec.get("clinched")),
                        "elimination_number": rec.get("eliminationNumber"),
                        "wildcard_elim": rec.get("wildCardEliminationNumber"),
                    }
                )
        return rows

    def team_season_rates(self, team_id: int, season: Optional[int] = None) -> dict[str, Any]:
        season = season or self.current_season()
        hitting = self._team_stat_split(team_id, season, "hitting", "season")
        pitching = self._team_stat_split(team_id, season, "pitching", "season")
        home_hit = self._team_stat_split(team_id, season, "hitting", "statSplits", sit="h")
        away_hit = self._team_stat_split(team_id, season, "hitting", "statSplits", sit="a")
        home_pit = self._team_stat_split(team_id, season, "pitching", "statSplits", sit="h")
        away_pit = self._team_stat_split(team_id, season, "pitching", "statSplits", sit="a")

        def rates(hit: dict, pit: dict) -> dict[str, float]:
            gp_h = max(int(hit.get("gamesPlayed") or 0), 1)
            gp_p = max(int(pit.get("gamesPlayed") or 0), 1)
            return {
                "runs_scored_pg": float(hit.get("runs") or 0) / gp_h,
                "hits_pg": float(hit.get("hits") or 0) / gp_h,
                "runs_allowed_pg": float(pit.get("runs") or 0) / gp_p,
                "hits_allowed_pg": float(pit.get("hits") or 0) / gp_p,
                "ops": float(hit.get("ops") or 0) if hit.get("ops") not in (None, "") else 0.0,
                "era": float(pit.get("era") or 0) if pit.get("era") not in (None, "") else 0.0,
                "games": float(max(gp_h, gp_p)),
            }

        overall = rates(hitting, pitching)
        return {
            "overall": overall,
            "home": rates(home_hit or hitting, home_pit or pitching),
            "away": rates(away_hit or hitting, away_pit or pitching),
            "raw": {"hitting": hitting, "pitching": pitching},
        }

    def _team_stat_split(
        self,
        team_id: int,
        season: int,
        group: str,
        stats: str,
        sit: Optional[str] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"stats": stats, "group": group, "season": season}
        if sit:
            params["sitCodes"] = sit
        data = self.get(f"/teams/{team_id}/stats", params)
        try:
            splits = data["stats"][0]["splits"]
            if not splits:
                return {}
            if sit:
                for split in splits:
                    code = (split.get("split") or {}).get("code")
                    if code == sit:
                        return split.get("stat") or {}
            return splits[0].get("stat") or {}
        except (KeyError, IndexError, TypeError):
            return {}

    def recent_form(self, team_id: int, n_games: int = 10) -> dict[str, Any]:
        end = date.today()
        start = end - timedelta(days=45)
        data = self.get(
            "/schedule",
            {
                "sportId": 1,
                "teamId": team_id,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "hydrate": "linescore",
            },
        )
        finals: list[dict[str, Any]] = []
        for day in data.get("dates", []):
            for game in day.get("games", []):
                if (game.get("status") or {}).get("abstractGameState") != "Final":
                    continue
                away = game["teams"]["away"]
                home = game["teams"]["home"]
                is_home = home["team"]["id"] == team_id
                own = home if is_home else away
                opp = away if is_home else home
                own_score = int(own.get("score") or 0)
                opp_score = int(opp.get("score") or 0)
                hits_own = _linescore_hits(game, "home" if is_home else "away")
                hits_opp = _linescore_hits(game, "away" if is_home else "home")
                finals.append(
                    {
                        "date": game.get("officialDate"),
                        "is_home": is_home,
                        "opponent_id": opp["team"]["id"],
                        "opponent": opp["team"]["name"],
                        "runs_scored": own_score,
                        "runs_allowed": opp_score,
                        "hits": hits_own,
                        "hits_allowed": hits_opp,
                        "won": own_score > opp_score,
                    }
                )
        finals.sort(key=lambda g: g["date"] or "")
        last = finals[-n_games:]
        if not last:
            return {
                "games": 0,
                "wins": 0,
                "losses": 0,
                "runs_scored_pg": 0.0,
                "runs_allowed_pg": 0.0,
                "hits_pg": 0.0,
                "hits_allowed_pg": 0.0,
                "win_pct": 0.0,
                "results": [],
            }
        n = len(last)
        wins = sum(1 for g in last if g["won"])
        return {
            "games": n,
            "wins": wins,
            "losses": n - wins,
            "runs_scored_pg": sum(g["runs_scored"] for g in last) / n,
            "runs_allowed_pg": sum(g["runs_allowed"] for g in last) / n,
            "hits_pg": sum(g["hits"] for g in last) / n,
            "hits_allowed_pg": sum(g["hits_allowed"] for g in last) / n,
            "win_pct": wins / n,
            "results": last,
        }

    def head_to_head(
        self,
        team_a: int,
        team_b: int,
        lookback_days: int = 900,
        limit: int = 12,
    ) -> dict[str, Any]:
        end = date.today()
        start = end - timedelta(days=lookback_days)
        data = self.get(
            "/schedule",
            {
                "sportId": 1,
                "teamId": team_a,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "hydrate": "linescore",
            },
        )
        meetings: list[dict[str, Any]] = []
        for day in data.get("dates", []):
            for game in day.get("games", []):
                if (game.get("status") or {}).get("abstractGameState") != "Final":
                    continue
                away = game["teams"]["away"]
                home = game["teams"]["home"]
                ids = {away["team"]["id"], home["team"]["id"]}
                if team_b not in ids:
                    continue
                total_runs = int(away.get("score") or 0) + int(home.get("score") or 0)
                total_hits = _linescore_hits(game, "away") + _linescore_hits(game, "home")
                meetings.append(
                    {
                        "date": game.get("officialDate"),
                        "venue": (game.get("venue") or {}).get("name"),
                        "away_id": away["team"]["id"],
                        "home_id": home["team"]["id"],
                        "away_score": int(away.get("score") or 0),
                        "home_score": int(home.get("score") or 0),
                        "total_runs": total_runs,
                        "total_hits": total_hits,
                        "day_night": game.get("dayNight"),
                    }
                )
        meetings.sort(key=lambda g: g["date"] or "", reverse=True)
        meetings = meetings[:limit]
        if not meetings:
            return {
                "meetings": 0,
                "avg_total_runs": None,
                "avg_total_hits": None,
                "team_a_wins": 0,
                "team_b_wins": 0,
                "games": [],
            }
        a_wins = 0
        for m in meetings:
            if m["away_id"] == team_a:
                a_won = m["away_score"] > m["home_score"]
            else:
                a_won = m["home_score"] > m["away_score"]
            if a_won:
                a_wins += 1
        n = len(meetings)
        return {
            "meetings": n,
            "avg_total_runs": sum(m["total_runs"] for m in meetings) / n,
            "avg_total_hits": sum(m["total_hits"] for m in meetings) / n,
            "team_a_wins": a_wins,
            "team_b_wins": n - a_wins,
            "games": meetings,
        }

    def find_scheduled_game(
        self,
        away_id: int,
        home_id: int,
        around: Optional[date] = None,
        window_days: int = 2,
    ) -> Optional[dict[str, Any]]:
        around = around or date.today()
        start = around - timedelta(days=window_days)
        end = around + timedelta(days=window_days)
        data = self.get(
            "/schedule",
            {
                "sportId": 1,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "hydrate": "probablePitcher,team,venue",
                "teamId": home_id,
            },
        )
        for day in data.get("dates", []):
            for game in day.get("games", []):
                away = game["teams"]["away"]["team"]["id"]
                home = game["teams"]["home"]["team"]["id"]
                if away == away_id and home == home_id:
                    return game
                # Betclic sometimes flips order — accept either orientation
                if away == home_id and home == away_id:
                    return game
        return None

    @lru_cache(maxsize=1)
    def injuries_by_team_name(self) -> dict[str, list[dict[str, Any]]]:
        try:
            resp = self.session.get(
                ESPN_INJURIES,
                timeout=self.timeout,
                headers={"User-Agent": self.session.headers["User-Agent"]},
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            logger.warning("Impossible de charger les blessures ESPN: %s", exc)
            return {}

        out: dict[str, list[dict[str, Any]]] = {}
        for team_block in payload.get("injuries", []):
            team_name = team_block.get("displayName") or ""
            rows = []
            for inj in team_block.get("injuries", []):
                athlete = inj.get("athlete") or {}
                rows.append(
                    {
                        "player": athlete.get("displayName") or athlete.get("fullName"),
                        "position": (athlete.get("position") or {}).get("abbreviation"),
                        "status": inj.get("status"),
                        "date": inj.get("date"),
                        "comment": inj.get("shortComment") or inj.get("longComment"),
                    }
                )
            out[team_name] = rows
        return out

    def injuries_for_team(self, team_name: str) -> list[dict[str, Any]]:
        index = self.injuries_by_team_name()
        if team_name in index:
            return index[team_name]
        # fuzzy: last token match (e.g. Yankees)
        needle = team_name.split()[-1].lower()
        for name, rows in index.items():
            if needle and needle in name.lower():
                return rows
        return []

    def roster_il_count(self, team_id: int) -> dict[str, int]:
        data = self.get(f"/teams/{team_id}/roster", {"rosterType": "40Man"})
        counts = {"active": 0, "il": 0, "other": 0}
        for player in data.get("roster", []):
            status = ((player.get("status") or {}).get("description") or "").lower()
            if "injured" in status or status.startswith("il"):
                counts["il"] += 1
            elif status == "active":
                counts["active"] += 1
            else:
                counts["other"] += 1
        return counts


def _parse_gb(value: Any) -> float:
    if value in (None, "-", "", "0"):
        return 0.0
    try:
        return float(str(value).replace("+", ""))
    except ValueError:
        return 0.0


def _linescore_hits(game: dict[str, Any], side: str) -> int:
    try:
        return int(game["linescore"]["teams"][side].get("hits") or 0)
    except (KeyError, TypeError, ValueError):
        return 0
