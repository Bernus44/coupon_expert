"""
Procédure d'analyse simplifiée (par match) :

1) 6 dernières confrontations H2H → totaux hits (et runs) → garder
   l'option PLUS de / MOINS de la plus fréquente historiquement.
2) Posture (domicile/extérieur) + momentum → ne garder l'option
   que si la condition historique favorable est réunie aujourd'hui.
Bonus) P(hits manches 1–5 < 8.5) ≥ 70%.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from .mlb_client import MLBClient, _linescore_hits
from .teams import MLBTeam, format_matchup, parse_matchup

logger = logging.getLogger(__name__)

DEFAULT_HIT_LINES = (14.5, 15.5, 16.5, 17.5, 18.5, 19.5)
DEFAULT_RUN_LINES = (6.5, 7.5, 8.5, 9.5, 10.5)
F5_HIT_LINE = 8.5
MIN_H2H = 4
F5_MIN_SAMPLES = 4


@dataclass
class SidePick:
    market_type: str  # Total Hits | Total Runs
    side: str  # over | under
    line: float
    option: str
    historical_prob: float
    sample_size: int
    over_count: int
    under_count: int
    cote: Optional[float] = None
    kept_after_posture: bool = False
    posture_reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MatchProcedureResult:
    match: str
    date_heure: Optional[str]
    away: str
    home: str
    h2h_games: list[dict[str, Any]]
    hits_pick: Optional[SidePick]
    runs_pick: Optional[SidePick]
    kept_picks: list[SidePick]
    f5_under_8_5: dict[str, Any]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "match": self.match,
            "date_heure": self.date_heure,
            "away": self.away,
            "home": self.home,
            "h2h_games": self.h2h_games,
            "hits_pick": self.hits_pick.to_dict() if self.hits_pick else None,
            "runs_pick": self.runs_pick.to_dict() if self.runs_pick else None,
            "kept_picks": [p.to_dict() for p in self.kept_picks],
            "f5_under_8_5": self.f5_under_8_5,
            "notes": self.notes,
        }


class SimpleProcedureAnalyzer:
    def __init__(self, client: Optional[MLBClient] = None):
        self.client = client or MLBClient()
        self._f5_cache: dict[int, Optional[int]] = {}

    def analyze_match(self, scraped: dict[str, Any]) -> Optional[MatchProcedureResult]:
        label = scraped.get("match") or ""
        away, home = parse_matchup(label, url=scraped.get("url"))
        if not away or not home:
            logger.warning("Équipes non résolues: %s", label)
            return None

        around = _parse_date(scraped.get("date_heure"))
        scheduled = self.client.find_scheduled_game(away.id, home.id, around=around)
        if scheduled:
            sched_away = scheduled["teams"]["away"]["team"]["id"]
            sched_home = scheduled["teams"]["home"]["team"]["id"]
            if sched_away == home.id and sched_home == away.id:
                away, home = home, away

        match_name = format_matchup(away, home)
        # Always pull H2H with gamePk + F5 hits for the procedure
        enriched_games = self._h2h_with_pks(away.id, home.id, limit=6)

        book_hit_lines = _lines_from_markets(scraped.get("paris") or [], "Total Hits")
        book_run_lines = _lines_from_markets(scraped.get("paris") or [], "Total Runs")
        hit_cotes = _cotes_by_line_side(scraped.get("paris") or [], "Total Hits")
        run_cotes = _cotes_by_line_side(scraped.get("paris") or [], "Total Runs")

        notes: list[str] = []
        hits_pick = self._pick_from_h2h(
            games=enriched_games,
            value_key="total_hits",
            market_type="Total Hits",
            book_lines=book_hit_lines or list(DEFAULT_HIT_LINES),
            cotes=hit_cotes,
        )
        runs_pick = self._pick_from_h2h(
            games=enriched_games,
            value_key="total_runs",
            market_type="Total Runs",
            book_lines=book_run_lines or list(DEFAULT_RUN_LINES),
            cotes=run_cotes,
        )

        if not enriched_games or len(enriched_games) < MIN_H2H:
            notes.append(
                f"H2H insuffisant ({len(enriched_games)} matchs, min {MIN_H2H})."
            )

        # Step 2: posture + momentum gate
        posture = self._current_posture(away, home)
        momentum = self._momentum(away, home)
        kept: list[SidePick] = []
        for pick in (hits_pick, runs_pick):
            if not pick:
                continue
            ok, reason = self._posture_momentum_ok(
                pick=pick,
                h2h_games=enriched_games,
                away=away,
                home=home,
                posture=posture,
                momentum=momentum,
            )
            pick.kept_after_posture = ok
            pick.posture_reason = reason
            pick.details["posture"] = posture
            pick.details["momentum"] = momentum
            if ok:
                kept.append(pick)
                notes.append(
                    f"OK {pick.market_type} {pick.option}: {reason} "
                    f"(P_hist={pick.historical_prob:.0%})"
                )
            else:
                notes.append(
                    f"REJETÉ {pick.market_type} {pick.option}: {reason}"
                )

        f5 = self._f5_under_probability(enriched_games, away, home)

        return MatchProcedureResult(
            match=match_name,
            date_heure=scraped.get("date_heure"),
            away=away.name,
            home=home.name,
            h2h_games=enriched_games,
            hits_pick=hits_pick,
            runs_pick=runs_pick,
            kept_picks=kept,
            f5_under_8_5=f5,
            notes=notes,
        )

    def _h2h_with_pks(self, team_a: int, team_b: int, limit: int = 6) -> list[dict[str, Any]]:
        end = date.today()
        start = end - timedelta(days=1200)
        data = self.client.get(
            "/schedule",
            {
                "sportId": 1,
                "teamId": team_a,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "hydrate": "linescore,venue",
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
                pk = game.get("gamePk")
                total_runs = int(away.get("score") or 0) + int(home.get("score") or 0)
                total_hits = _linescore_hits(game, "away") + _linescore_hits(game, "home")
                meetings.append(
                    {
                        "date": game.get("officialDate"),
                        "game_pk": pk,
                        "venue": (game.get("venue") or {}).get("name"),
                        "away_id": away["team"]["id"],
                        "home_id": home["team"]["id"],
                        "away_score": int(away.get("score") or 0),
                        "home_score": int(home.get("score") or 0),
                        "total_runs": total_runs,
                        "total_hits": total_hits,
                        "day_night": game.get("dayNight"),
                        "f5_hits": self._f5_hits(pk) if pk else None,
                    }
                )
        meetings.sort(key=lambda g: g["date"] or "", reverse=True)
        return meetings[:limit]

    def _f5_hits(self, game_pk: int) -> Optional[int]:
        if game_pk in self._f5_cache:
            return self._f5_cache[game_pk]
        try:
            # v1.1 live feed has per-inning hits
            url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
            data = self.client.get(url)
            inns = (
                data.get("liveData", {})
                .get("linescore", {})
                .get("innings", [])
            )
            total = 0
            found = False
            for inn in inns:
                num = int(inn.get("num") or 0)
                if 1 <= num <= 5:
                    found = True
                    home = inn.get("home") or {}
                    away = inn.get("away") or {}
                    total += int(home.get("hits") or 0) + int(away.get("hits") or 0)
            val = total if found else None
        except Exception as exc:
            logger.debug("F5 hits indisponible pour %s: %s", game_pk, exc)
            val = None
        self._f5_cache[game_pk] = val
        return val

    def _pick_from_h2h(
        self,
        games: list[dict[str, Any]],
        value_key: str,
        market_type: str,
        book_lines: list[float],
        cotes: dict[tuple[float, str], float],
    ) -> Optional[SidePick]:
        values = [float(g[value_key]) for g in games if g.get(value_key) is not None]
        if len(values) < MIN_H2H:
            return None

        avg = sum(values) / len(values)
        # Prefer book lines; otherwise center defaults around the H2H average
        lines = list(book_lines)
        if not cotes:
            # Keep only lines within ~2.5 of the historical average (avoid trivial 19.5 unders)
            lines = [ln for ln in lines if abs(ln - avg) <= 2.5] or lines

        best: Optional[SidePick] = None
        best_key = None
        for line in lines:
            over_n = sum(1 for v in values if v > line)
            under_n = sum(1 for v in values if v < line)
            decided = over_n + under_n
            if decided == 0:
                continue
            p_over = over_n / decided
            p_under = under_n / decided
            if p_over > p_under:
                side, prob, option = "over", p_over, f"+ de {str(line).replace('.', ',')}"
            elif p_under > p_over:
                side, prob, option = "under", p_under, f"- de {str(line).replace('.', ',')}"
            else:
                continue
            # Require a clear historical edge
            if prob < 0.60:
                continue
            cote = cotes.get((line, side))
            pick = SidePick(
                market_type=market_type,
                side=side,
                line=float(line),
                option=option,
                historical_prob=round(prob, 4),
                sample_size=decided,
                over_count=over_n,
                under_count=under_n,
                cote=cote,
                details={
                    "values": values,
                    "avg": round(avg, 3),
                },
            )
            # Rank: real cote > high prob > closeness to H2H average
            key = (
                1 if cote else 0,
                pick.historical_prob,
                -abs(float(line) - avg),
                pick.sample_size,
            )
            if best is None or key > best_key:
                best = pick
                best_key = key
        return best

    def _current_posture(self, away: MLBTeam, home: MLBTeam) -> dict[str, Any]:
        return {
            "away_id": away.id,
            "home_id": home.id,
            "away": away.name,
            "home": home.name,
            "label": f"{away.name} @ {home.name}",
        }

    def _momentum(self, away: MLBTeam, home: MLBTeam) -> dict[str, Any]:
        away_form = self.client.recent_form(away.id, n_games=6)
        home_form = self.client.recent_form(home.id, n_games=6)
        return {
            "away": {
                "hits_pg": away_form.get("hits_pg"),
                "hits_allowed_pg": away_form.get("hits_allowed_pg"),
                "runs_scored_pg": away_form.get("runs_scored_pg"),
                "runs_allowed_pg": away_form.get("runs_allowed_pg"),
                "win_pct": away_form.get("win_pct"),
                "games": away_form.get("games"),
            },
            "home": {
                "hits_pg": home_form.get("hits_pg"),
                "hits_allowed_pg": home_form.get("hits_allowed_pg"),
                "runs_scored_pg": home_form.get("runs_scored_pg"),
                "runs_allowed_pg": home_form.get("runs_allowed_pg"),
                "win_pct": home_form.get("win_pct"),
                "games": home_form.get("games"),
            },
            "combined_hits_pg": round(
                (away_form.get("hits_pg") or 0)
                + (home_form.get("hits_pg") or 0),
                3,
            ),
            "combined_runs_pg": round(
                (away_form.get("runs_scored_pg") or 0)
                + (home_form.get("runs_scored_pg") or 0),
                3,
            ),
        }

    def _posture_momentum_ok(
        self,
        pick: SidePick,
        h2h_games: list[dict[str, Any]],
        away: MLBTeam,
        home: MLBTeam,
        posture: dict[str, Any],
        momentum: dict[str, Any],
    ) -> tuple[bool, str]:
        """
        Condition: parmi les H2H où la même équipe était à domicile,
        l'option retenue doit encore être majoritaire. + momentum aligné.
        """
        same_posture = [
            g
            for g in h2h_games
            if g.get("home_id") == home.id and g.get("away_id") == away.id
        ]
        # If no same-orientation H2H, use all H2H but flag it
        sample = same_posture if len(same_posture) >= 2 else h2h_games
        value_key = "total_hits" if pick.market_type == "Total Hits" else "total_runs"
        values = [float(g[value_key]) for g in sample if g.get(value_key) is not None]
        if len(values) < 2:
            return False, "pas assez de H2H en même posture"

        if pick.side == "over":
            hits = sum(1 for v in values if v > pick.line)
        else:
            hits = sum(1 for v in values if v < pick.line)
        posture_p = hits / len(values)
        posture_ok = posture_p >= 0.5

        # Momentum alignment
        if pick.market_type == "Total Hits":
            mom = momentum.get("combined_hits_pg") or 0
            # league ~16-17 combined hits / game typical
            if pick.side == "over":
                mom_ok = mom >= pick.line * 0.95  # recent pace supports over
            else:
                mom_ok = mom <= pick.line * 1.05
            mom_label = f"hits_forme={mom:.1f}"
        else:
            mom = momentum.get("combined_runs_pg") or 0
            if pick.side == "over":
                mom_ok = mom >= pick.line * 0.90
            else:
                mom_ok = mom <= pick.line * 1.10
            mom_label = f"runs_forme={mom:.1f}"

        if posture_ok and mom_ok:
            orient = "même domicile" if same_posture else "H2H global"
            return True, (
                f"{orient}: option réalisée {hits}/{len(values)} "
                f"({posture_p:.0%}) + momentum OK ({mom_label})"
            )
        reasons = []
        if not posture_ok:
            reasons.append(f"posture {hits}/{len(values)}={posture_p:.0%} < 50%")
        if not mom_ok:
            reasons.append(f"momentum non aligné ({mom_label})")
        return False, " ; ".join(reasons)

    def _f5_under_probability(
        self,
        h2h_games: list[dict[str, Any]],
        away: MLBTeam,
        home: MLBTeam,
    ) -> dict[str, Any]:
        samples = [g["f5_hits"] for g in h2h_games if g.get("f5_hits") is not None]
        # If H2H F5 thin, supplement with each team's recent finals F5
        if len(samples) < F5_MIN_SAMPLES:
            samples = samples + self._recent_f5_samples(away.id, limit=6)
            samples = samples + self._recent_f5_samples(home.id, limit=6)

        if len(samples) < F5_MIN_SAMPLES:
            return {
                "line": F5_HIT_LINE,
                "prob": None,
                "sample_size": len(samples),
                "under_count": 0,
                "qualified": False,
                "samples": samples,
                "reason": "échantillons F5 insuffisants",
            }

        under_n = sum(1 for v in samples if v < F5_HIT_LINE)
        prob = under_n / len(samples)
        return {
            "line": F5_HIT_LINE,
            "prob": round(prob, 4),
            "sample_size": len(samples),
            "under_count": under_n,
            "qualified": prob >= 0.70,
            "samples": samples,
            "reason": (
                f"P(hits manches 1-5 < {F5_HIT_LINE}) = {prob:.0%} "
                f"({under_n}/{len(samples)})"
            ),
        }

    def _recent_f5_samples(self, team_id: int, limit: int = 6) -> list[int]:
        end = date.today()
        start = end - timedelta(days=30)
        data = self.client.get(
            "/schedule",
            {
                "sportId": 1,
                "teamId": team_id,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
            },
        )
        out: list[int] = []
        for day in data.get("dates", []):
            for game in day.get("games", []):
                if (game.get("status") or {}).get("abstractGameState") != "Final":
                    continue
                pk = game.get("gamePk")
                if not pk:
                    continue
                f5 = self._f5_hits(pk)
                if f5 is not None:
                    out.append(f5)
        return out[-limit:]


def analyze_slate(odds: list[dict[str, Any]], client: Optional[MLBClient] = None) -> dict[str, Any]:
    """Run the simple procedure on all scraped matches."""
    analyzer = SimpleProcedureAnalyzer(client=client or MLBClient())
    results: list[MatchProcedureResult] = []
    for row in odds:
        try:
            res = analyzer.analyze_match(row)
        except Exception as exc:
            logger.exception("Analyse échouée pour %s: %s", row.get("match"), exc)
            continue
        if res:
            results.append(res)

    kept_events = []
    for r in results:
        for p in r.kept_picks:
            kept_events.append(
                {
                    "match": r.match,
                    "date_heure": r.date_heure,
                    **p.to_dict(),
                }
            )

    f5_list = [
        {
            "match": r.match,
            "date_heure": r.date_heure,
            **r.f5_under_8_5,
        }
        for r in results
        if r.f5_under_8_5.get("qualified")
    ]
    f5_list.sort(key=lambda x: x.get("prob") or 0, reverse=True)

    display = _format_display(results, kept_events, f5_list)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "procedure": [
            "1) 6 H2H → option PLUS/MOINS hits (et runs) la plus fréquente",
            "2) garder seulement si posture domicile + momentum alignés",
            "Bonus) confrontations avec P(hits manches 1-5 < 8.5) ≥ 70%",
        ],
        "matches": [r.to_dict() for r in results],
        "kept_events": kept_events,
        "f5_under_8_5_list": f5_list,
        "display": display,
    }


def _format_display(
    results: list[MatchProcedureResult],
    kept_events: list[dict[str, Any]],
    f5_list: list[dict[str, Any]],
) -> str:
    lines = [
        "=== PROCÉDURE SIMPLE — ÉVÉNEMENTS RETENUS ===",
        "",
    ]
    if not kept_events:
        lines.append("Aucun événement retenu après H2H + posture/momentum.")
    else:
        for i, e in enumerate(kept_events, 1):
            side = "PLUS" if e["side"] == "over" else "MOINS"
            cote = f" @ {e['cote']:.2f}" if e.get("cote") else ""
            lines.append(
                f"{i}. {e['match']} | {e['market_type']} {e['option']} [{side}]{cote} "
                f"| P_H2H={e['historical_prob']:.0%} ({e['over_count']} over / {e['under_count']} under) "
                f"| {e.get('posture_reason')}"
            )
    lines.extend(["", "=== BONUS — MOINS DE 8,5 HITS (MANCHES 1 à 5) P≥70% ===", ""])
    if not f5_list:
        lines.append("Aucune confrontation qualifiée pour le bonus F5.")
    else:
        for i, e in enumerate(f5_list, 1):
            lines.append(
                f"{i}. {e['match']} | P={e['prob']:.0%} "
                f"({e['under_count']}/{e['sample_size']}) — {e.get('reason')}"
            )
    lines.append("")
    return "\n".join(lines)


def _lines_from_markets(paris: list[dict[str, Any]], market_type: str) -> list[float]:
    from .enricher import parse_market_option

    lines = []
    for p in paris:
        if p.get("type") != market_type:
            continue
        parsed = parse_market_option(p.get("option") or "")
        if not parsed:
            continue
        lines.append(parsed["line"])
    # unique preserve order
    out = []
    for x in lines:
        if x not in out:
            out.append(x)
    return out


def _cotes_by_line_side(
    paris: list[dict[str, Any]], market_type: str
) -> dict[tuple[float, str], float]:
    from .enricher import parse_market_option

    out: dict[tuple[float, str], float] = {}
    for p in paris:
        if p.get("type") != market_type:
            continue
        parsed = parse_market_option(p.get("option") or "")
        if not parsed:
            continue
        out[(parsed["line"], parsed["side"])] = float(p.get("cote") or 0)
    return out


def _parse_date(value: Optional[str]) -> date:
    if not value:
        return date.today()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return date.today()
