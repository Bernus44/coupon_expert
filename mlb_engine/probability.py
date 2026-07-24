"""Probability model for Total Runs / Total Hits markets + EV."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Optional


def poisson_pmf(k: int, lam: float) -> float:
    if k < 0:
        return 0.0
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
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
    Probability for totals lines.
    over 8.5 => P(X >= 9); under 8.5 => P(X <= 8)
    For whole lines, exclude the push: over => P(X > line), under => P(X < line)
    """
    lam = max(float(lam), 0.05)
    if abs(line - round(line)) < 1e-9:
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
    realization_score: float
    edge: float
    expected_value: float
    confidence: float
    reasons: list[str]
    criteria: dict[str, float]
    context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _injury_offense_factor(team_payload: dict[str, Any]) -> float:
    """
    1.0 = full strength; lower = weakened offense.

    Prefer IL roster counts (real absences). ESPN injury blurbs are noisy
    (day-to-day / probable) so they only apply a light penalty.
    """
    il = int((team_payload.get("il_roster") or {}).get("il", 0) or 0)
    espn_n = int(team_payload.get("injury_count") or 0)
    drag = min(0.10, 0.012 * il) + min(0.03, 0.002 * espn_n)
    return max(0.90, 1.0 - drag)


def _expand_alternate_lines(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Add nearby alternate totals so PLUS/MOINS can be compared beyond the main close.
    Synthetic alts use ~ -110 (1.91) as placeholder book price.
    """
    expanded = list(markets)
    seen = {(m["type"], float(m["line"]), m["side"]) for m in markets}
    bases = {(m["type"], float(m["line"])) for m in markets if m.get("type") == "Total Runs"}
    for mtype, line in bases:
        for delta in (-1.0, -0.5, 0.5, 1.0):
            alt = line + delta
            if not (5.5 <= alt <= 14.5):
                continue
            for side, prefix in (("over", "+ de"), ("under", "- de")):
                key = (mtype, alt, side)
                if key in seen:
                    continue
                seen.add(key)
                line_fr = str(alt).replace(".", ",")
                expanded.append(
                    {
                        "type": mtype,
                        "option": f"{prefix} {line_fr}",
                        "cote": 1.91,
                        "side": side,
                        "line": alt,
                        "title": "Total Runs",
                        "synthetic": True,
                    }
                )
    return expanded


def _criteria_adjustments(
    *,
    side: str,
    mtype: str,
    line: float,
    lam: float,
    away: dict[str, Any],
    home: dict[str, Any],
    h2h: dict[str, Any],
    enjeu: float,
) -> tuple[float, dict[str, float], list[str]]:
    """
    Symmetric criteria that can favor OVER or UNDER.
    Returns (delta_prob, criteria_breakdown, reasons).
    """
    reasons: list[str] = []
    crit: dict[str, float] = {
        "model_vs_line": 0.0,
        "forme": 0.0,
        "h2h": 0.0,
        "pitching_posture": 0.0,
        "enjeu": 0.0,
        "blessures": 0.0,
    }

    # 1) Model vs line gap (primary signal, symmetric)
    gap = lam - line
    # ~3% per run of gap, capped
    model_vs_line = max(-0.08, min(0.08, gap * 0.035))
    if side == "under":
        model_vs_line = -model_vs_line
    crit["model_vs_line"] = round(model_vs_line, 4)
    if abs(model_vs_line) >= 0.015:
        direction = "au-dessus" if gap > 0 else "sous"
        reasons.append(
            f"λ={lam:.2f} {direction} de la ligne {line} → favorise les "
            f"{'PLUS' if (gap > 0 and side == 'over') or (gap < 0 and side == 'under') else 'MOINS'} de"
        )

    form_away = away.get("form_last10") or {}
    form_home = home.get("form_last10") or {}

    # 2) Recent form (attack + defense allowed)
    if mtype == "Total Runs":
        scored = form_away.get("runs_scored_pg", 0) + form_home.get("runs_scored_pg", 0)
        allowed = form_away.get("runs_allowed_pg", 0) + form_home.get("runs_allowed_pg", 0)
        # expected recent total ≈ (scored+allowed)/2 is wrong; use scored as offensive firepower
        # and allowed as openness of pitching
        recent_total_proxy = (scored + allowed) / 2.0
        baseline = 8.6
        form_gap = recent_total_proxy - baseline
        form_adj = max(-0.04, min(0.04, form_gap * 0.025))
        if side == "under":
            form_adj = -form_adj
        crit["forme"] = round(form_adj, 4)
        if abs(form_adj) >= 0.01:
            reasons.append(
                f"forme récente proxy_total≈{recent_total_proxy:.1f} "
                f"({'PLUS' if form_gap > 0 else 'MOINS'} de favorisé pour ce côté)"
            )
    else:
        scored = form_away.get("hits_pg", 0) + form_home.get("hits_pg", 0)
        allowed = form_away.get("hits_allowed_pg", 0) + form_home.get("hits_allowed_pg", 0)
        recent_total_proxy = (scored + allowed) / 2.0
        baseline = 16.5
        form_gap = recent_total_proxy - baseline
        form_adj = max(-0.04, min(0.04, form_gap * 0.02))
        if side == "under":
            form_adj = -form_adj
        crit["forme"] = round(form_adj, 4)

    # 3) H2H historical totals
    avg_key = "avg_total_runs" if mtype == "Total Runs" else "avg_total_hits"
    h2h_avg = h2h.get(avg_key)
    if h2h_avg:
        h2h_gap = float(h2h_avg) - line
        h2h_adj = max(-0.035, min(0.035, h2h_gap * 0.02))
        if side == "under":
            h2h_adj = -h2h_adj
        crit["h2h"] = round(h2h_adj, 4)
        if abs(h2h_adj) >= 0.01:
            reasons.append(f"H2H avg={float(h2h_avg):.1f} vs ligne {line}")

    # 4) Pitching posture (home/away ERA) — weak pitching → over, strong → under
    home_era = ((home.get("season_rates") or {}).get("home") or {}).get("era") or 0.0
    away_era = ((away.get("season_rates") or {}).get("away") or {}).get("era") or 0.0
    eras = [e for e in (home_era, away_era) if e]
    if eras:
        avg_era = sum(eras) / len(eras)
        # MLB ~4.00 ERA; higher ERA → more runs
        era_gap = avg_era - 4.0
        pitch_adj = max(-0.03, min(0.03, era_gap * 0.02))
        if side == "under":
            pitch_adj = -pitch_adj
        crit["pitching_posture"] = round(pitch_adj, 4)
        if abs(pitch_adj) >= 0.01:
            reasons.append(f"ERA combiné≈{avg_era:.2f}")

    # 5) Stakes — high stakes slight lean under; low stakes slight lean over
    # enjeu in [0,1]; center at 0.5
    stake_gap = 0.5 - enjeu  # positive => low stakes => over
    stake_adj = max(-0.02, min(0.02, stake_gap * 0.04))
    if side == "under":
        stake_adj = -stake_adj
    crit["enjeu"] = round(stake_adj, 4)
    if abs(stake_adj) >= 0.008:
        reasons.append(f"enjeu={enjeu:.2f} ({'ouvert' if stake_gap > 0 else 'fermé'})")

    # 6) Injuries — depleted offenses lean under; healthy lean over (mild)
    offense_factor = (_injury_offense_factor(away) + _injury_offense_factor(home)) / 2.0
    # factor 1.0 → 0; factor 0.85 → favor under
    inj_gap = offense_factor - 0.95
    inj_adj = max(-0.03, min(0.03, inj_gap * 0.4))
    if side == "under":
        inj_adj = -inj_adj
    crit["blessures"] = round(inj_adj, 4)
    inj_n = int(away.get("injury_count") or 0) + int(home.get("injury_count") or 0)
    if abs(inj_adj) >= 0.008:
        reasons.append(f"blessures/IL (n={inj_n}, facteur offense={offense_factor:.2f})")

    delta = sum(crit.values())
    return delta, crit, reasons


def score_markets(enriched: dict[str, Any], min_prob: float = 0.60) -> list[ScoredEvent]:
    """
    Score every scraped market (PLUS de / MOINS de) for one enriched match.

    Both sides are evaluated with the same criteria; the caller/combiner then
    keeps the most realizable option per match.
    """
    priors = enriched.get("model_priors") or {}
    base_runs = float(priors.get("expected_total_runs") or 8.5)
    base_hits = float(priors.get("expected_total_hits") or 16.0)

    away = enriched["away"]
    home = enriched["home"]
    enjeu = float((enriched.get("enjeu") or {}).get("combined") or 0.5)
    h2h = enriched.get("h2h") or {}

    # Mild injury drag on expected totals (symmetric impact on both sides via λ)
    offense_factor = (_injury_offense_factor(away) + _injury_offense_factor(home)) / 2.0
    runs_lam = base_runs * offense_factor
    hits_lam = base_hits * offense_factor

    markets = _expand_alternate_lines(list(enriched.get("markets") or []))

    by_key: dict[tuple[str, float], dict[str, dict]] = {}
    for m in markets:
        key = (m["type"], float(m["line"]))
        by_key.setdefault(key, {})[m["side"]] = m

    fair_cache: dict[tuple[str, float, str], float] = {}
    for key, sides in by_key.items():
        if "over" in sides and "under" in sides:
            if not sides["over"].get("synthetic") and not sides["under"].get("synthetic"):
                po, pu = remove_vig_two_way(
                    implied_prob(sides["over"]["cote"]),
                    implied_prob(sides["under"]["cote"]),
                )
                fair_cache[(key[0], key[1], "over")] = po
                fair_cache[(key[0], key[1], "under")] = pu

    events: list[ScoredEvent] = []
    for m in markets:
        mtype = m["type"]
        lam = runs_lam if mtype == "Total Runs" else hits_lam if mtype == "Total Hits" else None
        if lam is None or m["cote"] < 1.01:
            continue

        model_p = poisson_over_under(lam, m["line"], m["side"])
        market_p = fair_cache.get(
            (mtype, float(m["line"]), m["side"]),
            implied_prob(m["cote"]),
        )

        # Adaptive blend: trust the model more when λ is far from the line
        gap = abs(lam - float(m["line"]))
        model_w = 0.55 + min(0.30, gap * 0.12)  # up to 85% model
        market_w = 1.0 - model_w
        blended = model_w * model_p + market_w * market_p

        delta, criteria, reasons = _criteria_adjustments(
            side=m["side"],
            mtype=mtype,
            line=float(m["line"]),
            lam=lam,
            away=away,
            home=home,
            h2h=h2h,
            enjeu=enjeu,
        )
        blended = min(0.95, max(0.05, blended + delta))

        max_edge = 0.22 if gap >= 1.0 else 0.15
        edge_raw = blended - market_p
        if abs(edge_raw) > max_edge and not m.get("synthetic"):
            blended = market_p + (max_edge if edge_raw > 0 else -max_edge)
            blended = min(0.95, max(0.05, blended))
            reasons.append(f"edge calibré (plafond ±{int(max_edge * 100)} pts vs marché)")

        edge = blended - market_p
        ev = blended * m["cote"] - 1.0

        form_away = away.get("form_last10") or {}
        form_home = home.get("form_last10") or {}
        agree = 1.0 - min(1.0, abs(model_p - market_p) * 2)
        meetings = int(h2h.get("meetings") or 0)
        sample = min(
            1.0,
            (form_away.get("games", 0) + form_home.get("games", 0)) / 20 + meetings / 20,
        )
        confidence = round(0.55 * agree + 0.45 * sample, 3)

        criteria_strength = sum(abs(v) for v in criteria.values())
        book_bonus = 0.0 if m.get("synthetic") else 0.02
        realization_score = round(
            0.75 * blended
            + 0.13 * confidence
            + 0.10 * min(1.0, criteria_strength / 0.15)
            + book_bonus,
            4,
        )

        side_label = "PLUS de" if m["side"] == "over" else "MOINS de"
        src = "alt" if m.get("synthetic") else "cote"
        reasons.insert(
            0,
            f"[{side_label}/{src}] λ={lam:.2f} line={m['line']} → "
            f"P_model={model_p:.1%} | P_marché={market_p:.1%} | "
            f"P_finale={blended:.1%} | score={realization_score:.3f}",
        )
        reasons.append(f"EV={ev:+.1%} | edge={edge:+.1%}")

        if blended < min_prob:
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
                realization_score=realization_score,
                edge=round(edge, 4),
                expected_value=round(ev, 4),
                confidence=confidence,
                reasons=reasons,
                criteria=criteria,
                context={
                    "expected_runs": priors.get("expected_total_runs"),
                    "expected_hits": priors.get("expected_total_hits"),
                    "lambda_used": round(lam, 3),
                    "enjeu": enriched.get("enjeu"),
                    "venue": (enriched.get("posture") or {}).get("venue"),
                    "away": away.get("name"),
                    "home": home.get("name"),
                    "standings": {
                        "away": (away.get("standings") or {}).get("division_rank"),
                        "home": (home.get("standings") or {}).get("division_rank"),
                    },
                    "side_label": side_label,
                    "synthetic_line": bool(m.get("synthetic")),
                },
            )
        )

    # Prefer most realizable events first
    events.sort(
        key=lambda e: (e.realization_score, e.blended_prob, e.expected_value),
        reverse=True,
    )
    return events



def _is_trivial_extreme(ev: ScoredEvent) -> bool:
    """Filter near-certain extremes far from λ (e.g. under 10.5 when λ≈8)."""
    lam = float((ev.context or {}).get("lambda_used") or ev.line)
    if ev.side == "under" and ev.line >= lam + 1.25:
        return True
    if ev.side == "over" and ev.line <= lam - 1.25:
        return True
    # Also drop absurd certainty from synthetic alts
    if (ev.context or {}).get("synthetic_line") and ev.blended_prob >= 0.90:
        return True
    return False


def pick_best_per_match(events: list[ScoredEvent]) -> list[ScoredEvent]:
    """
    For each match, keep the single most realizable option among PLUS/MOINS.

    Prefer real book lines; ignore trivial extreme alts.
    """
    usable = [e for e in events if not _is_trivial_extreme(e)]
    if not usable:
        usable = list(events)

    book = [e for e in usable if not (e.context or {}).get("synthetic_line")]
    pool = book if book else usable

    best: dict[str, ScoredEvent] = {}
    for ev in pool:
        cur = best.get(ev.match)
        if cur is None or (ev.realization_score, ev.blended_prob, ev.expected_value) > (
            cur.realization_score,
            cur.blended_prob,
            cur.expected_value,
        ):
            best[ev.match] = ev

    # If a match had only synthetic survivors in pool emptiness, fall back
    covered = set(best)
    for ev in usable:
        if ev.match in covered:
            continue
        best[ev.match] = ev
        covered.add(ev.match)

    return sorted(
        best.values(),
        key=lambda e: (e.realization_score, e.blended_prob, e.expected_value),
        reverse=True,
    )
