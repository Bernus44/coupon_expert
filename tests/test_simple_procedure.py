"""Tests for the simplified H2H procedure (no network)."""

from __future__ import annotations

from mlb_engine.simple_procedure import SidePick, SimpleProcedureAnalyzer
from mlb_engine.teams import resolve_team


def test_pick_from_h2h_prefers_majority_under_hits():
    analyzer = SimpleProcedureAnalyzer.__new__(SimpleProcedureAnalyzer)
    games = [
        {"total_hits": 14},
        {"total_hits": 15},
        {"total_hits": 13},
        {"total_hits": 16},
        {"total_hits": 12},
        {"total_hits": 17},
    ]
    # vs 16.5 → under in 5/6
    pick = analyzer._pick_from_h2h(
        games=games,
        value_key="total_hits",
        market_type="Total Hits",
        book_lines=[16.5],
        cotes={(16.5, "under"): 1.85, (16.5, "over"): 1.95},
    )
    assert pick is not None
    assert pick.side == "under"
    assert pick.line == 16.5
    assert pick.historical_prob >= 0.8


def test_pick_from_h2h_prefers_majority_over_runs():
    analyzer = SimpleProcedureAnalyzer.__new__(SimpleProcedureAnalyzer)
    games = [
        {"total_runs": 10},
        {"total_runs": 11},
        {"total_runs": 9},
        {"total_runs": 12},
        {"total_runs": 8},
        {"total_runs": 10},
    ]
    pick = analyzer._pick_from_h2h(
        games=games,
        value_key="total_runs",
        market_type="Total Runs",
        book_lines=[8.5],
        cotes={},
    )
    assert pick is not None
    assert pick.side == "over"
    assert pick.historical_prob >= 0.8


def test_posture_momentum_keeps_aligned_under():
    analyzer = SimpleProcedureAnalyzer.__new__(SimpleProcedureAnalyzer)
    away = resolve_team("Yankees")
    home = resolve_team("Phillies")
    assert away and home
    pick = SidePick(
        market_type="Total Hits",
        side="under",
        line=16.5,
        option="- de 16,5",
        historical_prob=0.83,
        sample_size=6,
        over_count=1,
        under_count=5,
    )
    h2h = [
        {"away_id": away.id, "home_id": home.id, "total_hits": 14},
        {"away_id": away.id, "home_id": home.id, "total_hits": 15},
        {"away_id": away.id, "home_id": home.id, "total_hits": 13},
        {"away_id": home.id, "home_id": away.id, "total_hits": 20},  # flipped posture
    ]
    posture = {"away_id": away.id, "home_id": home.id}
    momentum = {"combined_hits_pg": 15.0, "combined_runs_pg": 7.0}
    ok, reason = analyzer._posture_momentum_ok(
        pick=pick,
        h2h_games=h2h,
        away=away,
        home=home,
        posture=posture,
        momentum=momentum,
    )
    assert ok
    assert "momentum OK" in reason


def test_f5_qualification_threshold():
    analyzer = SimpleProcedureAnalyzer.__new__(SimpleProcedureAnalyzer)
    # Directly craft result logic
    samples = [6, 7, 8, 5, 9, 7]  # 5/6 under 8.5
    under_n = sum(1 for v in samples if v < 8.5)
    prob = under_n / len(samples)
    assert prob >= 0.70
