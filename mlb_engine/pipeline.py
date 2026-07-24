"""End-to-end pipeline: scrape → enrich → score → combine."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .combiner import CombineTicket, build_combines, summarize_for_display
from .enricher import MatchEnricher
from .mlb_client import MLBClient
from .probability import ScoredEvent, score_markets

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ODDS_PATH = ROOT / "data" / "betclic_mlb.json"
DEFAULT_OUT_DIR = ROOT / "data"


def load_odds(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Format inattendu dans {path}: liste attendue")
    return data


async def maybe_scrape(force: bool = False, odds_path: Path = DEFAULT_ODDS_PATH) -> Path:
    """Run Betclic scraper when forced or when odds file is missing."""
    if odds_path.exists() and not force:
        logger.info("Réutilisation des cotes existantes: %s", odds_path)
        return odds_path

    logger.info("Lancement du scraper Betclic MLB…")
    # Lazy import: Playwright may be heavy / optional for analyze-only runs
    import sys

    sys.path.insert(0, str(ROOT))
    from betclic_mlb_scraper import BetclicMLBScraper

    scraper = BetclicMLBScraper()
    await scraper.run()
    return odds_path


def analyze_odds(
    odds: list[dict[str, Any]],
    client: Optional[MLBClient] = None,
    min_prob: float = 0.60,
    min_joint_prob: float = 0.60,
    max_legs: int = 4,
) -> dict[str, Any]:
    enricher = MatchEnricher(client=client or MLBClient())
    enriched_matches: list[dict[str, Any]] = []
    all_events: list[ScoredEvent] = []

    for row in odds:
        try:
            enriched = enricher.enrich_match(row)
        except Exception as exc:
            logger.exception("Enrichissement échoué pour %s: %s", row.get("match"), exc)
            continue
        if not enriched:
            continue
        enriched_matches.append(enriched)
        scored = score_markets(enriched, min_prob=min_prob)
        all_events.extend(scored)
        logger.info(
            "%s → %d marchés retenus (≥ %.0f%% & EV≈+)",
            enriched["match"],
            len(scored),
            min_prob * 100,
        )

    all_events.sort(key=lambda e: (e.expected_value, e.blended_prob), reverse=True)
    tickets = build_combines(
        all_events,
        min_joint_prob=min_joint_prob,
        max_legs=max_legs,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "params": {
            "min_prob": min_prob,
            "min_joint_prob": min_joint_prob,
            "max_legs": max_legs,
            "matches_input": len(odds),
            "matches_enriched": len(enriched_matches),
            "events_qualified": len(all_events),
        },
        "qualified_events": [e.to_dict() for e in all_events],
        "combines": [t.to_dict() for t in tickets],
        "enriched_matches": enriched_matches,
        "display": summarize_for_display(tickets),
    }


def save_report(report: dict[str, Any], out_dir: Path = DEFAULT_OUT_DIR) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    full_path = out_dir / "analysis_report.json"
    combines_path = out_dir / "recommended_combines.json"
    events_path = out_dir / "qualified_events.json"
    text_path = out_dir / "recommended_combines.txt"

    # Full dump can be large (enriched); keep it
    with full_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    with combines_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "generated_at": report["generated_at"],
                "params": report["params"],
                "combines": report["combines"],
            },
            f,
            indent=2,
            ensure_ascii=False,
        )

    with events_path.open("w", encoding="utf-8") as f:
        json.dump(report["qualified_events"], f, indent=2, ensure_ascii=False)

    text_path.write_text(report["display"], encoding="utf-8")

    return {
        "report": full_path,
        "combines": combines_path,
        "events": events_path,
        "text": text_path,
    }


async def run_pipeline(
    scrape: bool = False,
    odds_path: Path = DEFAULT_ODDS_PATH,
    out_dir: Path = DEFAULT_OUT_DIR,
    min_prob: float = 0.60,
    min_joint_prob: float = 0.60,
    max_legs: int = 4,
) -> dict[str, Any]:
    path = await maybe_scrape(force=scrape, odds_path=odds_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Aucune cote trouvée ({path}). Lance avec --scrape ou fournis data/betclic_mlb.json"
        )
    odds = load_odds(path)
    report = analyze_odds(
        odds,
        min_prob=min_prob,
        min_joint_prob=min_joint_prob,
        max_legs=max_legs,
    )
    paths = save_report(report, out_dir=out_dir)
    report["output_paths"] = {k: str(v) for k, v in paths.items()}
    logger.info("Rapport écrit: %s", paths["combines"])
    print(report["display"])
    return report
