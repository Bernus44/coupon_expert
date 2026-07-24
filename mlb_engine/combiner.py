"""Build profit-oriented combinés from high-probability events."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

from .probability import ScoredEvent


@dataclass
class CombineTicket:
    legs: list[ScoredEvent]
    joint_probability: float
    combined_odds: float
    expected_value: float
    strategy: str
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "joint_probability": round(self.joint_probability, 4),
            "combined_odds": round(self.combined_odds, 4),
            "expected_value": round(self.expected_value, 4),
            "notes": self.notes,
            "legs": [leg.to_dict() for leg in self.legs],
        }


def _joint(probs: list[float]) -> float:
    p = 1.0
    for x in probs:
        p *= x
    return p


def _odds_product(odds: list[float]) -> float:
    o = 1.0
    for x in odds:
        o *= x
    return o


def _ticket_key(ticket: CombineTicket) -> tuple:
    return tuple(sorted((leg.match, leg.market_type, leg.option) for leg in ticket.legs))


def _dedupe_per_match(events: list[ScoredEvent]) -> list[ScoredEvent]:
    """Keep the best EV event per match to avoid correlated same-game legs."""
    best: dict[str, ScoredEvent] = {}
    for ev in events:
        cur = best.get(ev.match)
        if cur is None or (ev.expected_value, ev.blended_prob) > (
            cur.expected_value,
            cur.blended_prob,
        ):
            best[ev.match] = ev
    return sorted(
        best.values(),
        key=lambda e: (e.expected_value, e.blended_prob),
        reverse=True,
    )


def build_combines(
    events: list[ScoredEvent],
    min_joint_prob: float = 0.60,
    max_legs: int = 4,
    top_n_tickets: int = 5,
) -> list[CombineTicket]:
    """
    Propose combinés whose estimated joint realization probability stays >= min_joint_prob,
    while maximizing expected value (profit objective).

    Constraints:
    - one leg per match (independence approximation)
    - each leg already filtered at >= 60% blended probability upstream
    """
    pool = _dedupe_per_match(
        [e for e in events if e.blended_prob >= 0.60 and e.expected_value >= -0.01]
    )
    if not pool:
        return []

    collected: list[CombineTicket] = []

    # Card of independent singles (mise séparée) — often better for long-term profit than parlays
    card = sorted(pool, key=lambda e: (e.expected_value, e.blended_prob), reverse=True)[: max_legs]
    if card:
        avg_p = sum(e.blended_prob for e in card) / len(card)
        avg_ev = sum(e.expected_value for e in card) / len(card)
        collected.append(
            CombineTicket(
                legs=card,
                joint_probability=avg_p,  # mean hit-rate of the card, NOT a parlay joint
                combined_odds=sum(e.cote for e in card) / len(card),
                expected_value=avg_ev,
                strategy="singles_card",
                notes=[
                    "Carte de paris SIMPLES (1 mise par événement) — recommandé pour le profit long terme.",
                    f"Chaque jambe a P≥60%. Taux de réussite moyen estimé={avg_p:.1%}, EV moyen={avg_ev:+.1%}.",
                    "Ce n'est PAS un combiné multiplicatif: les cotes ne se multiplient pas.",
                ],
            )
        )

    best_ev = max(pool, key=lambda e: (e.expected_value, e.blended_prob))
    if best_ev.blended_prob >= min_joint_prob:
        collected.append(
            CombineTicket(
                legs=[best_ev],
                joint_probability=best_ev.blended_prob,
                combined_odds=best_ev.cote,
                expected_value=best_ev.expected_value,
                strategy="single_value",
                notes=[
                    "Meilleur événement isolé (probabilité ≥ 60% et EV ≥ 0).",
                    "Utile si tu priorises la sureté avant le levier du combiné.",
                ],
            )
        )

    best_prob = max(pool, key=lambda e: (e.blended_prob, e.expected_value))
    if best_prob.match != best_ev.match or best_prob.option != best_ev.option:
        collected.append(
            CombineTicket(
                legs=[best_prob],
                joint_probability=best_prob.blended_prob,
                combined_odds=best_prob.cote,
                expected_value=best_prob.expected_value,
                strategy="single_highest_prob",
                notes=[
                    "Événement à plus haute probabilité estimée (≥ 60%).",
                ],
            )
        )

    upper = min(max_legs, len(pool))
    for k in range(2, upper + 1):
        for combo in itertools.combinations(pool, k):
            probs = [c.blended_prob for c in combo]
            odds = [c.cote for c in combo]
            joint = _joint(probs)
            if joint < min_joint_prob:
                continue
            c_odds = _odds_product(odds)
            ev = joint * c_odds - 1.0
            if ev < 0:
                continue
            notes = [
                f"COMBINÉ multiplicatif — {k} matchs (indépendance approx.)",
                f"P_jointe={joint:.1%} ≥ {min_joint_prob:.0%}",
                f"Cote combinée ≈ {c_odds:.2f} | EV ≈ {ev:+.1%}",
            ]
            if len({c.market_type for c in combo}) > 1:
                notes.append("Diversification Runs/Hits")
            collected.append(
                CombineTicket(
                    legs=list(combo),
                    joint_probability=joint,
                    combined_odds=c_odds,
                    expected_value=ev,
                    strategy=f"parlay_{k}",
                    notes=notes,
                )
            )

    # Greedy parlay: stack highest-prob legs while joint stays >= threshold
    greedy_legs: list[ScoredEvent] = []
    greedy_p = 1.0
    for ev in sorted(pool, key=lambda e: e.blended_prob, reverse=True):
        nxt = greedy_p * ev.blended_prob
        if nxt < min_joint_prob:
            break
        greedy_legs.append(ev)
        greedy_p = nxt
        if len(greedy_legs) >= max_legs:
            break
    if len(greedy_legs) >= 2:
        c_odds = _odds_product([e.cote for e in greedy_legs])
        ev = greedy_p * c_odds - 1.0
        collected.append(
            CombineTicket(
                legs=greedy_legs,
                joint_probability=greedy_p,
                combined_odds=c_odds,
                expected_value=ev,
                strategy="greedy_high_prob",
                notes=[
                    "Empilement combiné des plus hautes probabilités tant que P_jointe ≥ 60%.",
                    f"P_jointe={greedy_p:.1%} | cote≈{c_odds:.2f} | EV={ev:+.1%}",
                ],
            )
        )

    unique: list[CombineTicket] = []
    seen: set[tuple] = set()
    # Prefer singles_card and value singles before low-quality parlays
    priority = {
        "singles_card": 0,
        "single_value": 1,
        "single_highest_prob": 2,
        "greedy_high_prob": 3,
    }
    for ticket in sorted(
        collected,
        key=lambda x: (
            priority.get(x.strategy, 4),
            -x.expected_value,
            -x.joint_probability,
        ),
    ):
        key = _ticket_key(ticket) + (ticket.strategy,)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ticket)
        if len(unique) >= top_n_tickets:
            break
    return unique


def summarize_for_display(tickets: list[CombineTicket]) -> str:
    if not tickets:
        return (
            "Aucun combiné trouvé avec P(réalisation) ≥ 60% et EV ≥ 0.\n"
            "Assouplis le seuil, relance le scrape, ou élargis les marchés."
        )
    lines = ["=== PROPOSITIONS (objectif profit, P ≥ 60%) ===", ""]
    for i, t in enumerate(tickets, 1):
        if t.strategy == "singles_card":
            lines.append(
                f"#{i} [CARTE SINGLES] réussite moy.={t.joint_probability:.1%} | "
                f"cote moy.={t.combined_odds:.2f} | EV moy.={t.expected_value:+.1%}"
            )
        else:
            lines.append(
                f"#{i} [{t.strategy}] P={t.joint_probability:.1%} | "
                f"cote={t.combined_odds:.2f} | EV={t.expected_value:+.1%}"
            )
        for leg in t.legs:
            lines.append(
                f"  - {leg.match} | {leg.market_type} {leg.option} @ {leg.cote:.2f} "
                f"(p={leg.blended_prob:.1%}, EV={leg.expected_value:+.1%})"
            )
        for note in t.notes:
            lines.append(f"    · {note}")
        lines.append("")
    return "\n".join(lines)
