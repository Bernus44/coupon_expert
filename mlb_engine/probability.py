"""Probability model for Total Runs / Total Hits markets + EV."""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any, Optional


def poisson_pmf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    # Iterative product avoids factorial overflow for large k
    log_p = -lam + k * math.log(lam) - math.lgamma(k + 1)
    return math.exp(log_p)


def poisson_cdf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    total = 0.0
    for i in range(0, k + 1):
        total += poisson_pmf(i, lam)
    return min(1.0, total)


def poisson_over_under(lam: float, line: float, side: str) -> float:
    """
    Probability for classic .5 totals lines.
    over 8.5 => P(X >= 9); under 8.5 => P(X <= 8)
    """
    lam = max(float(lam), 0.05)
    # integer threshold just above/below the half-line
    if abs(line - round(line)) < 1e-9:
        # whole number lines: push possible; treat over as > line, under as < line
        if side == "over":
            return 1.0 - poisson_cdf(int(line), lam)
        return poisson_cdf(int(line) - 1, lam)

    floor_line = math.floor(line)
    if side == "over":
        return 1.0 - poisson_cdf(floor_line, lam)
    return poisson_cdf(floor_line, lam)


def implied_prob(odds: float) -> float:
    if odds <= 1.0:
        return 0.0
    return 1.0 / odds


def remove_vig_two_way(p_over_imp: float, p_under_imp: float) -> tuple[float, float]:
    s = p_over_imp + p_under_imp
    if s <= 0:
        return 0.5, 0.5
    return p_over_imp / s, p_under_imp / s


@dataclass
class ScoredEvent:
    match: str
    date_heure: Optional[str]
    market_type: str
    option: str
    side: str
    line: float
    cote: float
    model_prob: float
    market_prob: float
    blended_prob: float
    edge: float
    expected_value: float
    confidence: float
    reasons: list[str]
    context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _injury_drag(team_payload: dict[str, Any]) -> float:
    """Return a mild multiplicative drag on offense (1.0 = no impact)."""
    n = int(team_payload.get("injury_count") or 0)
    il = (team_payload.get("il_roster") or {}).get("il", 0)
    # each notable injury / IL slot softens offense slightly
    drag = 1.0 - min(0.12, 0.01 * n + 0.008 * il)
    return max(0.85, drag)


def _enjeu_volatility(enjeu_combined: float) -> float:
    """High stakes can slightly suppress totals (tighter play) or inflate — mild shrink."""
    # map [0,1] -> multiplier around 0.97..1.03
    return 0.97 + 0.06 * (1.0 - enjeu_combined)


def score_markets(enriched: dict[str, Any], min_prob: float = 0.60) -> list[ScoredEvent]:
    """Score every scraped market for one enriched match."""
    priors = enriched.get("model_priors") or {}
    base_runs = float(priors.get("expected_total_runs") or 8.5)
    base_hits = float(priors.get("expected_total_hits") or 16.0)

    away = enriched["away"]
    home = enriched["home"]
    enjeu = float((enriched.get("enjeu") or {}).get("combined") or 0.5)

    # Adjust priors with injuries + stakes
    runs_lam = base_runs * ((_injury_drag(away) + _injury_drag(home)) / 2.0) * _enjeu_volatility(enjeu)
    hits_lam = base_hits * ((_injury_drag(away) + _injury_drag(home)) / 2.0) * _enjeu_volatility(enjeu)

    # Pair markets for vig removal when both sides present
    by_key: dict[tuple[str, float], dict[str, dict]] = {}
    for m in enriched.get("markets") or []:
        key = (m["type"], float(m["line"]))
        by_key.setdefault(key, {})[m["side"]] = m

    fair_cache: dict[tuple[str, float, str], float] = {}
    for key, sides in by_key.items():
        if "over" in sides and "under" in sides:
            po, pu = remove_vig_two_way(
                implied_prob(sides["over"]["cote"]),
                implied_prob(sides["under"]["cote"]),
            )
            fair_cache[(key[0], key[1], "over")] = po
            fair_cache[(key[0], key[1], "under")] = pu

    events: list[ScoredEvent] = []
    for m in enriched.get("markets") or []:
        mtype = m["type"]
        lam = runs_lam if mtype == "Total Runs" else hits_lam if mtype == "Total Hits" else None
        if lam is None or m["cote"] < 1.01:
            continue

        model_p = poisson_over_under(lam, m["line"], m["side"])
        market_p = fair_cache.get((mtype, float(m["line"]), m["side"]), implied_prob(m["cote"]))

        # Blend model (65%) with de-vigged market (35%) — market keeps us calibrated
        blended = 0.65 * model_p + 0.35 * market_p

        # Form / posture / H2H soft adjustments
        reasons: list[str] = []
        adj = 0.0

        form_away = away.get("form_last10") or {}
        form_home = home.get("form_last10") or {}
        if mtype == "Total Runs":
            recent = (form_away.get("runs_scored_pg", 0) + form_home.get("runs_scored_pg", 0))
            if m["side"] == "over" and recent >= 10:
                adj += 0.03
                reasons.append("forme offensive récente élevée (runs)")
            if m["side"] == "under" and recent <= 7:
                adj += 0.03
                reasons.append("forme offensive récente faible (runs)")
        else:
            recent = (form_away.get("hits_pg", 0) + form_home.get("hits_pg", 0))
            if m["side"] == "over" and recent >= 17:
                adj += 0.03
                reasons.append("forme hits récente élevée")
            if m["side"] == "under" and recent <= 14:
                adj += 0.03
                reasons.append("forme hits récente faible")

        h2h = enriched.get("h2h") or {}
        avg_key = "avg_total_runs" if mtype == "Total Runs" else "avg_total_hits"
        h2h_avg = h2h.get(avg_key)
        if h2h_avg:
            if m["side"] == "over" and h2h_avg > m["line"] + 0.75:
                adj += 0.025
                reasons.append(f"H2H historique au-dessus de la ligne ({h2h_avg:.1f})")
            if m["side"] == "under" and h2h_avg < m["line"] - 0.75:
                adj += 0.025
                reasons.append(f"H2H historique sous la ligne ({h2h_avg:.1f})")

        # Home/away posture: strong home pitcher environments lean under slightly
        home_era = ((home.get("season_rates") or {}).get("home") or {}).get("era") or 0
        if mtype == "Total Runs" and m["side"] == "under" and home_era and home_era < 3.5:
            adj += 0.02
            reasons.append("défense domicile solide (ERA bas)")

        if enjeu >= 0.75 and m["side"] == "under":
            adj += 0.015
            reasons.append("enjeu élevé → match potentiellement fermé")

        inj_n = int(away.get("injury_count") or 0) + int(home.get("injury_count") or 0)
        if inj_n >= 8 and m["side"] == "under":
            adj += 0.02
            reasons.append("effectif amputé (blessures)")

        blended = min(0.95, max(0.05, blended + adj))

        # Shrink extreme model edges toward the market (anti-overfit / mis-tagged props)
        edge_raw = blended - market_p
        if abs(edge_raw) > 0.12:
            blended = market_p + (0.12 if edge_raw > 0 else -0.12)
            blended = min(0.95, max(0.05, blended))
            reasons.append("edge calibré (plafond ±12 pts vs marché)")

        edge = blended - market_p
        ev = blended * m["cote"] - 1.0

        # Confidence from agreement model vs market + sample richness
        agree = 1.0 - min(1.0, abs(model_p - market_p) * 2)
        meetings = int(h2h.get("meetings") or 0)
        sample = min(1.0, (form_away.get("games", 0) + form_home.get("games", 0)) / 20 + meetings / 20)
        confidence = round(0.6 * agree + 0.4 * sample, 3)

        reasons.insert(
            0,
            f"λ={lam:.2f} → P_model={model_p:.1%} | P_marché={market_p:.1%} | blend={blended:.1%}",
        )
        reasons.append(f"EV={ev:+.1%} | edge={edge:+.1%}")

        if blended < min_prob:
            continue
        # Profit filter: require non-negative EV (allow tiny tolerance)
        if ev < -0.01:
            continue

        events.append(
            ScoredEvent(
                match=enriched["match"],
                date_heure=enriched.get("date_heure"),
                market_type=mtype,
                option=m["option"],
                side=m["side"],
                line=float(m["line"]),
                cote=float(m["cote"]),
                model_prob=round(model_p, 4),
                market_prob=round(market_p, 4),
                blended_prob=round(blended, 4),
                edge=round(edge, 4),
                expected_value=round(ev, 4),
                confidence=confidence,
                reasons=reasons,
                context={
                    "expected_runs": priors.get("expected_total_runs"),
                    "expected_hits": priors.get("expected_total_hits"),
                    "enjeu": enriched.get("enjeu"),
                    "venue": (enriched.get("posture") or {}).get("venue"),
                    "away": away.get("name"),
                    "home": home.get("name"),
                    "standings": {
                        "away": (away.get("standings") or {}).get("division_rank"),
                        "home": (home.get("standings") or {}).get("division_rank"),
                    },
                },
            )
        )

    events.sort(key=lambda e: (e.expected_value, e.blended_prob, e.confidence), reverse=True)
    return events
