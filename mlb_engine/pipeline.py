"""End-to-end pipeline: scrape → enrich → score → combine."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .combiner import build_combines, summarize_for_display
from .enricher import MatchEnricher
from .mlb_client import MLBClient
from .probability import ScoredEvent, score_markets

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ODDS_PATH = ROOT / "data" / "betclic_mlb.json"
DEFAULT_OUT_DIR = ROOT / "data"
DEFAULT_MAX_AGE_HOURS = 6.0


def load_odds(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "matches" in data:
        matches = data["matches"]
    else:
        matches = data
    if not isinstance(matches, list):
        raise ValueError(f"Format inattendu dans {path}: liste attendue")
    return matches


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def odds_freshness(
    path: Path,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> tuple[bool, str]:
    """
    Return (is_fresh, reason).
    Stale when missing, too old on disk, or all match kickoffs are before today (UTC).
    """
    if not path.exists():
        return False, "fichier de cotes absent"

    now = datetime.now(timezone.utc)
    age_h = (now.timestamp() - path.stat().st_mtime) / 3600.0
    if age_h > max_age_hours:
        return False, f"fichier âgé de {age_h:.1f}h (> {max_age_hours:.0f}h)"

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"JSON illisible ({exc})"

    scraped_at = None
    if isinstance(raw, dict):
        scraped_at = _parse_iso(raw.get("scraped_at"))
        matches = raw.get("matches") or []
    else:
        matches = raw if isinstance(raw, list) else []

    if scraped_at:
        age_scrape = (now - scraped_at).total_seconds() / 3600.0
        if age_scrape > max_age_hours:
            return False, f"dernier scrape il y a {age_scrape:.1f}h"

    kickoffs: list[datetime] = []
    for row in matches:
        dt = _parse_iso(row.get("date_heure")) or _parse_iso(row.get("scraped_at"))
        if dt:
            kickoffs.append(dt.astimezone(timezone.utc))

    if not matches:
        return False, "aucun match dans le fichier"

    if kickoffs:
        newest = max(kickoffs)
        if newest.date() < now.date():
            return (
                False,
                "matchs obsolètes (plus récent: "
                f"{newest.date().isoformat()}, aujourd'hui: {now.date().isoformat()})",
            )

    return True, f"cotes OK ({len(matches)} matchs, âge fichier {age_h:.1f}h)"


async def maybe_scrape(
    force: bool = False,
    odds_path: Path = DEFAULT_ODDS_PATH,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    cached_only: bool = False,
) -> Path:
    """Run Betclic scraper when forced/missing/stale; fallback ESPN if Betclic blocked."""
    fresh, reason = odds_freshness(odds_path, max_age_hours=max_age_hours)

    if cached_only:
        if not odds_path.exists():
            raise FileNotFoundError(
                f"Aucune cote en cache ({odds_path}). Relance sans --cached."
            )
        if not fresh:
            logger.warning("Mode --cached: utilisation de cotes NON à jour (%s)", reason)
        else:
            logger.info("Mode --cached: %s", reason)
        return odds_path

    need_scrape = force or not fresh
    if not need_scrape:
        logger.info("Réutilisation des cotes à jour: %s", reason)
        return odds_path

    if force:
        logger.info("Scrape forcé (--scrape).")
    else:
        logger.info("Cotes pas à jour (%s) → nouveau scrape…", reason)

    betclic_ok = await _try_betclic_scrape(odds_path)
    fresh_after, reason_after = odds_freshness(odds_path, max_age_hours=max_age_hours)
    if betclic_ok and fresh_after:
        logger.info("Scrape Betclic OK: %s", reason_after)
        return odds_path

    logger.warning(
        "Betclic indisponible ou obsolète (%s). Fallback ESPN/DraftKings pour la slate du jour…",
        reason_after if odds_path.exists() else "fichier absent",
    )
    from .odds_providers import ESPNOddsProvider

    provider = ESPNOddsProvider()
    matches = provider.fetch_slate(days=2)
    if not matches:
        raise RuntimeError(
            "Impossible de récupérer des matchs à jour (Betclic bloqué et ESPN vide)."
        )
    provider.save(matches, odds_path)
    fresh_espn, reason_espn = odds_freshness(odds_path, max_age_hours=max_age_hours)
    logger.info("Fallback ESPN enregistré: %s", reason_espn)
    if not fresh_espn:
        logger.warning("Les cotes ESPN semblent encore douteuses (%s)", reason_espn)
    return odds_path


async def _try_betclic_scrape(odds_path: Path) -> bool:
    import sys

    sys.path.insert(0, str(ROOT))
    try:
        from betclic_mlb_scraper import BetclicMLBScraper

        scraper = BetclicMLBScraper()
        await scraper.run()
    except Exception as exc:
        logger.error("Échec scrape Betclic: %s", exc)
        return False

    if not odds_path.exists():
        return False
    try:
        matches = load_odds(odds_path)
    except Exception:
        return False
    return bool(matches)

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

    all_events.sort(
        key=lambda e: (e.realization_score, e.blended_prob, e.expected_value),
        reverse=True,
    )
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


def save_procedure_report(report: dict[str, Any], out_dir: Path = DEFAULT_OUT_DIR) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    full_path = out_dir / "procedure_report.json"
    kept_path = out_dir / "kept_events.json"
    f5_path = out_dir / "f5_under_8_5.json"
    text_path = out_dir / "recommended_combines.txt"
    # Keep legacy filename for the human summary
    summary_path = out_dir / "procedure_summary.txt"

    with full_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    with kept_path.open("w", encoding="utf-8") as f:
        json.dump(report.get("kept_events") or [], f, indent=2, ensure_ascii=False)
    with f5_path.open("w", encoding="utf-8") as f:
        json.dump(report.get("f5_under_8_5_list") or [], f, indent=2, ensure_ascii=False)
    text_path.write_text(report["display"], encoding="utf-8")
    summary_path.write_text(report["display"], encoding="utf-8")

    return {
        "report": full_path,
        "kept": kept_path,
        "f5": f5_path,
        "text": text_path,
        "summary": summary_path,
    }


async def run_pipeline(
    scrape: bool = False,
    odds_path: Path = DEFAULT_ODDS_PATH,
    out_dir: Path = DEFAULT_OUT_DIR,
    min_prob: float = 0.60,
    min_joint_prob: float = 0.60,
    max_legs: int = 4,
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
    cached_only: bool = False,
    legacy_engine: bool = False,
) -> dict[str, Any]:
    path = await maybe_scrape(
        force=scrape,
        odds_path=odds_path,
        max_age_hours=max_age_hours,
        cached_only=cached_only,
    )
    if not path.exists():
        raise FileNotFoundError(
            f"Aucune cote trouvée ({path}). Lance avec --scrape ou fournis data/betclic_mlb.json"
        )
    odds = load_odds(path)
    logger.info("Analyse de %d matchs scrapés…", len(odds))
    for row in odds:
        logger.info("  · %s | %s", row.get("date_heure"), row.get("match"))

    if legacy_engine:
        report = analyze_odds(
            odds,
            min_prob=min_prob,
            min_joint_prob=min_joint_prob,
            max_legs=max_legs,
        )
        paths = save_report(report, out_dir=out_dir)
        report["output_paths"] = {k: str(v) for k, v in paths.items()}
        logger.info("Rapport legacy écrit: %s", paths["combines"])
        print(report["display"])
        return report

    from .simple_procedure import analyze_slate

    report = analyze_slate(odds)
    paths = save_procedure_report(report, out_dir=out_dir)
    report["output_paths"] = {k: str(v) for k, v in paths.items()}
    logger.info(
        "Procédure simple: %d événements retenus | %d bonus F5",
        len(report.get("kept_events") or []),
        len(report.get("f5_under_8_5_list") or []),
    )
    logger.info("Rapport écrit: %s", paths["text"])
    print(report["display"])
    return report
