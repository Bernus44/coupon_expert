# Coupon Expert — MLB Betclic Value Combinés

Scraper + moteur d’analyse pour proposer des **combinés MLB** dont la probabilité estimée de réalisation est **≥ 60%**, avec un filtre de **valeur attendue (EV ≥ 0)** pour viser le profit.

## Pipeline

1. **Scrape Betclic** (`betclic_mlb_scraper.py`) — marchés *Total Runs* / *Total Hits* (cotes ≥ 1.20)
2. **Enrichissement MLB** (API publique `statsapi.mlb.com` + blessures ESPN)
   - historique des confrontations (H2H)
   - forme récente (attaque / défense, 10 derniers matchs)
   - posture domicile / extérieur (splits)
   - classement & enjeu (course division / wild-card)
   - état des joueurs (IL roster + feed blessures)
   - conditions des anciennes confrontations (totaux runs/hits, venue, day/night)
3. **Modèle de probabilité** — Poisson sur total attendu, blendé avec la cote marché (devig), ajustements forme / H2H / blessures / enjeu
4. **Combinés** — 1 jambe par match, P(événement) ≥ 60%, P(joint) ≥ 60%, EV ≥ 0

## Installation

Sur cet environnement cloud, la commande s’appelle `python3` (pas `python`), et les binaires installés via pip (`playwright`) sont souvent hors `PATH`. Utilise plutôt :

```bash
# Option A — script tout-en-un
bash scripts/setup.sh

# Option B — commandes manuelles (recommandées)
python3 -m pip install --user -r requirements.txt
python3 -m playwright install chromium
```

Si tu tiens à appeler `playwright` / `python` directement :

```bash
export PATH="$HOME/.local/bin:$PATH"
# (optionnel) alias python=python3
```

## Usage

```bash
# Analyse — rescrape AUTO si les cotes ne sont pas à jour (recommandé)
python3 main.py

# Force un nouveau scrape même si le cache est récent
python3 main.py --scrape -v

# Utiliser uniquement le cache local (même obsolète)
python3 main.py --cached
```

Sorties dans `data/` :

| Fichier | Contenu |
|---|---|
| `betclic_mlb.json` | Cotes scrapées |
| `qualified_events.json` | Événements ≥ 60% & EV+ |
| `recommended_combines.json` | Combinés proposés |
| `recommended_combines.txt` | Résumé lisible |
| `analysis_report.json` | Rapport complet (contexte enrichi) |

## Outils utilisés

- **Playwright + playwright-stealth** — scrape Betclic anti-bot
- **Fallback ESPN / DraftKings** — si Betclic renvoie 403, récupère automatiquement la slate MLB du jour + totaux Runs
- **MLB Stats API** (`statsapi.mlb.com`) — standings, stats, schedule, H2H, roster IL
- **ESPN site API** — blessures + scoreboard/odds
- **Python stdlib + requests** — orchestration

## Limites (importante)

Les probabilités sont des **estimations de modèle**, pas des certitudes. Un EV+ historique n’implique pas un gain garanti. Jouer responsablement ; ce projet est un outil d’aide à la décision, pas un conseil financier.
