"""Odds providers: Betclic (preferred) + ESPN/DraftKings fallback for live slate."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
ESPN_ODDS = (
    "https://sports.core.api.espn.com/v2/sports/baseball/leagues/mlb"
    "/events/{event_id}/competitions/{comp_id}/odds"
)


def american_to_decimal(american: float) -> float:
    """Convert American odds to European decimal."""
    try:
        a = float(american)
    except (TypeError, ValueError):
        return 0.0
    if a >= 100:
        return round(1.0 + a / 100.0, 3)
    if a <= -100:
        return round(1.0 + 100.0 / abs(a), 3)
    return 0.0


class ESPNOddsProvider:
    """Fetch today's/tomorrow's MLB totals (DraftKings via ESPN)."""

    def __init__(self, session: Optional[requests.Session] = None, timeout: float = 20.0):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.session.headers.setdefault(
            "User-Agent",
            "coupon-expert/1.0 (+https://github.com/Bernus44/coupon_expert)",
        )

    def _get(self, url: str, params: Optional[dict] = None) -> dict[str, Any]:
        resp = self.session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def fetch_slate(self, days: int = 2) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        today = date.today()

        for offset in range(days):
            day = today + timedelta(days=offset)
            board = self._get(ESPN_SCOREBOARD, {"dates": day.strftime("%Y%m%d")})
            for event in board.get("events") or []:
                status = ((event.get("status") or {}).get("type") or {}).get("description") or ""
                if status.lower() in {"final", "postponed", "canceled", "cancelled"}:
                    continue
                row = self._event_to_match(event, scraped_at)
                if row and row.get("paris"):
                    matches.append(row)

        # de-dupe by match+date
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for m in matches:
            key = f"{m.get('date_heure')}|{m.get('match')}"
            if key in seen:
                continue
            seen.add(key)
            unique.append(m)
        logger.info("ESPN fallback: %d matchs avec totaux récupérés", len(unique))
        return unique

    def _event_to_match(self, event: dict[str, Any], scraped_at: str) -> Optional[dict[str, Any]]:
        competitions = event.get("competitions") or []
        if not competitions:
            return None
        comp = competitions[0]
        event_id = str(event.get("id") or "")
        comp_id = str(comp.get("id") or event_id)
        competitors = {c.get("homeAway"): c for c in (comp.get("competitors") or [])}
        away = competitors.get("away") or {}
        home = competitors.get("home") or {}
        away_name = (away.get("team") or {}).get("displayName") or away.get("displayName")
        home_name = (home.get("team") or {}).get("displayName") or home.get("displayName")
        if not away_name or not home_name:
            return None

        # Normalize "Athletics Athletics"
        away_name = " ".join(dict.fromkeys(away_name.split()))
        home_name = " ".join(dict.fromkeys(home_name.split()))

        date_heure = event.get("date") or scraped_at
        if date_heure.endswith("Z") is False and "+" not in date_heure:
            date_heure = date_heure + "Z" if "T" in date_heure else date_heure

        paris: list[dict[str, Any]] = []
        try:
            odds_payload = self._get(ESPN_ODDS.format(event_id=event_id, comp_id=comp_id))
            items = odds_payload.get("items") or []
        except Exception as exc:
            logger.debug("Pas de cotes ESPN pour %s: %s", event.get("name"), exc)
            items = []

        for item in items:
            line = item.get("overUnder")
            if line is None:
                continue
            over_dec = american_to_decimal(item.get("overOdds"))
            under_dec = american_to_decimal(item.get("underOdds"))
            line_f = float(line)
            # Present like Betclic French options for downstream parsers
            line_fr = str(line_f).replace(".", ",")
            if over_dec >= 1.20:
                paris.append(
                    {
                        "type": "Total Runs",
                        "option": f"+ de {line_fr}",
                        "cote": over_dec,
                        "title": "Total Runs",
                        "provider": (item.get("provider") or {}).get("name") or "ESPN",
                    }
                )
            if under_dec >= 1.20:
                paris.append(
                    {
                        "type": "Total Runs",
                        "option": f"- de {line_fr}",
                        "cote": under_dec,
                        "title": "Total Runs",
                        "provider": (item.get("provider") or {}).get("name") or "ESPN",
                    }
                )
            # Nearby half-lines with same prices are NOT available — stop at main line

        if not paris:
            return None

        return {
            "match": f"{away_name} vs {home_name}",
            "date_heure": date_heure.replace(".000Z", "Z") if date_heure.endswith(".000Z") else date_heure,
            "paris": paris,
            "scraped_at": scraped_at,
            "source": "espn_draftkings",
            "espn_event_id": event_id,
        }

    def save(self, matches: list[dict[str, Any]], path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = {
            "scraped_at": scraped_at,
            "source": "espn_draftkings",
            "match_count": len(matches),
            "matches": matches,
            "note": (
                "Fallback automatique: Betclic inaccessible. "
                "Cotes Total Runs DraftKings via ESPN (pas Betclic)."
            ),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
