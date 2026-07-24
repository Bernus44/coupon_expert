#!/usr/bin/env python3
"""CLI — Coupon Expert MLB: scrape Betclic, analyse, propose combinés ≥ 60%."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from mlb_engine.pipeline import DEFAULT_ODDS_PATH, DEFAULT_OUT_DIR, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Scrape les cotes MLB Betclic, enrichit avec stats MLB "
            "(forme, H2H, domicile/extérieur, classement, blessures, enjeu) "
            "et propose des combinés à probabilité estimée ≥ 60% orientés profit (EV+)."
        )
    )
    p.add_argument(
        "--scrape",
        action="store_true",
        help="Force un nouveau scrape Betclic avant l'analyse",
    )
    p.add_argument(
        "--odds",
        type=Path,
        default=DEFAULT_ODDS_PATH,
        help="Chemin JSON des cotes scrapées",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Dossier de sortie des rapports",
    )
    p.add_argument(
        "--min-prob",
        type=float,
        default=0.60,
        help="Seuil de probabilité par événement (défaut 0.60)",
    )
    p.add_argument(
        "--min-joint-prob",
        type=float,
        default=0.60,
        help="Seuil de probabilité jointe du combiné (défaut 0.60)",
    )
    p.add_argument(
        "--max-legs",
        type=int,
        default=4,
        help="Nombre max de sélections dans un combiné",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Logs détaillés",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(message)s",
    )
    try:
        asyncio.run(
            run_pipeline(
                scrape=args.scrape,
                odds_path=args.odds,
                out_dir=args.out,
                min_prob=args.min_prob,
                min_joint_prob=args.min_joint_prob,
                max_legs=args.max_legs,
            )
        )
    except Exception as exc:
        logging.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
