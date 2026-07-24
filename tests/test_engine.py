"""Unit tests for the MLB value engine (no network required for core math)."""

from __future__ import annotations

from mlb_engine.combiner import build_combines
from mlb_engine.enricher import parse_market_option
from mlb_engine.probability import (
    ScoredEvent,
    implied_prob,
    pick_best_per_match,
    poisson_over_under,
    score_markets,
)
from mlb_engine.teams import parse_matchup, resolve_team


def test_resolve_team_aliases():
    assert resolve_team("Yankees").id == 147
    assert resolve_team("New York Yankees").abbreviation == "NYY"
    assert resolve_team("Guardians").id == 114
    assert resolve_team("Athletics").id == 133


def test_parse_matchup():
    a, b = parse_matchup("Texas Rangers vs Cleveland Guardians")
    assert a and b
    assert a.abbreviation == "TEX"
    assert b.abbreviation == "CLE"


def test_parse_broken_betclic_names():
    a, b = parse_matchup("Toronto Blue vs Jays Baltimore Orioles")
    assert a and b
    assert {a.abbreviation, b.abbreviation} == {"TOR", "BAL"}

    a, b = parse_matchup("Detroit vs Tigers Seattle")
    assert a and b
    assert {a.abbreviation, b.abbreviation} == {"DET", "SEA"}

    a, b = parse_matchup(
        "padres mets",
        url="https://www.betclic.fr/baseball-sbaseball/major-league-c473/san-diego-padres-new-york-mets-m123",
    )
    assert a and b
    assert a.abbreviation == "SD"
    assert b.abbreviation == "NYM"


def test_parse_market_option():
    assert parse_market_option("+ de 8,5") == {"side": "over", "line": 8.5, "raw": "+ de 8,5"}
    assert parse_market_option("- de 7.5")["side"] == "under"
    assert parse_market_option("Plus de 16,5")["side"] == "over"


def test_poisson_over_under_symmetry():
    lam = 8.5
    over = poisson_over_under(lam, 8.5, "over")
    under = poisson_over_under(lam, 8.5, "under")
    assert 0.45 < over < 0.55
    assert abs((over + under) - 1.0) < 1e-6


def test_implied_prob():
    assert abs(implied_prob(2.0) - 0.5) < 1e-9


def test_american_to_decimal():
    from mlb_engine.odds_providers import american_to_decimal

    assert abs(american_to_decimal(-110) - 1.909) < 0.01
    assert abs(american_to_decimal(150) - 2.5) < 1e-9


def _enriched_high_scoring():
    return {
        "match": "Chicago Cubs vs Pittsburgh Pirates",
        "date_heure": "2026-07-24T22:40:00Z",
        "away": {
            "name": "Chicago Cubs",
            "injury_count": 2,
            "il_roster": {"il": 1},
            "form_last10": {
                "games": 10,
                "runs_scored_pg": 6.0,
                "runs_allowed_pg": 5.5,
                "hits_pg": 10.0,
                "hits_allowed_pg": 9.5,
            },
            "season_rates": {"home": {"era": 4.5}, "away": {"era": 4.8}},
            "standings": {"division_rank": 2},
        },
        "home": {
            "name": "Pittsburgh Pirates",
            "injury_count": 1,
            "il_roster": {"il": 1},
            "form_last10": {
                "games": 10,
                "runs_scored_pg": 5.5,
                "runs_allowed_pg": 5.8,
                "hits_pg": 9.5,
                "hits_allowed_pg": 10.0,
            },
            "season_rates": {"home": {"era": 4.6}, "away": {"era": 4.4}},
            "standings": {"division_rank": 3},
        },
        "enjeu": {"combined": 0.4},
        "h2h": {"meetings": 6, "avg_total_runs": 10.5, "avg_total_hits": 18.0},
        "posture": {"venue": "PNC Park"},
        "model_priors": {"expected_total_runs": 10.2, "expected_total_hits": 17.5},
        "markets": [
            {"type": "Total Runs", "option": "+ de 8,0", "cote": 1.90, "side": "over", "line": 8.0},
            {"type": "Total Runs", "option": "- de 8,0", "cote": 1.90, "side": "under", "line": 8.0},
        ],
    }


def test_score_markets_prefers_over_when_lambda_high():
    events = score_markets(_enriched_high_scoring(), min_prob=0.55)
    assert events
    assert any(e.side == "over" for e in events)
    best = pick_best_per_match(events)[0]
    assert best.side == "over"
    assert best.blended_prob >= 0.55


def test_score_markets_prefers_under_when_lambda_low():
    enriched = _enriched_high_scoring()
    enriched["model_priors"]["expected_total_runs"] = 6.2
    enriched["h2h"]["avg_total_runs"] = 6.0
    enriched["away"]["form_last10"]["runs_scored_pg"] = 2.5
    enriched["home"]["form_last10"]["runs_scored_pg"] = 2.8
    enriched["markets"] = [
        {"type": "Total Runs", "option": "+ de 8,5", "cote": 1.90, "side": "over", "line": 8.5},
        {"type": "Total Runs", "option": "- de 8,5", "cote": 1.90, "side": "under", "line": 8.5},
    ]
    events = score_markets(enriched, min_prob=0.55)
    assert events
    best = pick_best_per_match(events)[0]
    assert best.side == "under"


def test_build_combines_joint_threshold():
    def ev(match, p, cote, side="under", option="x"):
        return ScoredEvent(
            match=match,
            date_heure=None,
            market_type="Total Runs",
            option=option,
            side=side,
            line=8.5,
            cote=cote,
            model_prob=p,
            market_prob=1 / cote,
            blended_prob=p,
            realization_score=p,
            edge=0.05,
            expected_value=p * cote - 1,
            confidence=0.7,
            reasons=[],
            criteria={},
            context={},
        )

    events = [
        ev("A vs B", 0.72, 1.40, side="over", option="+ de 7,5"),
        ev("C vs D", 0.70, 1.45, side="under", option="- de 8,5"),
        ev("E vs F", 0.68, 1.50, side="over", option="+ de 8,5"),
        ev("G vs H", 0.55, 1.90),
    ]
    tickets = build_combines(events, min_joint_prob=0.60, max_legs=3, top_n_tickets=10)
    assert tickets
    assert all(t.joint_probability >= 0.60 - 1e-9 for t in tickets)
    # Card should mix sides when best picks differ
    card = next(t for t in tickets if t.strategy == "singles_card")
    sides = {leg.side for leg in card.legs}
    assert "over" in sides
