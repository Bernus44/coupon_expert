# Betclic MLB Scraper

A Python + Playwright scraper that extracts MLB "Total Runs" / "Total Hits" betting odds from
`betclic.fr` and writes them to `data/betclic_mlb.json`.

- `betclic_mlb_scraper.py` — main scraper (run: `python3 betclic_mlb_scraper.py`).
- `check_captcha.py` — connectivity/anti-bot probe; navigates to the MLB page and saves `captcha_check.png`.

## Cursor Cloud specific instructions

- **Langue de communication :** répondre toujours en français (réflexions, résultats, messages,
  descriptions de PR, etc.). Seul le code peut rester dans la langue technique requise
  (identifiants, commentaires déjà en anglais, etc.).
- Dependencies: `playwright`, `playwright-stealth` (see `requirements.txt`). The startup update script
  installs these plus the Chromium browser (`playwright install --with-deps chromium`). Browsers live in
  `~/.cache/ms-playwright`.
- There is no lint config and no automated test suite in this repo. A basic syntax check is
  `python3 -m py_compile betclic_mlb_scraper.py check_captcha.py`.
- The scraper targets a live external site, `betclic.fr`, which is geo-restricted to France and blocks
  datacenter IPs with an anti-bot layer. From the Cloud VM's datacenter IP the site returns
  **HTTP 403 Forbidden** (a "Betclic / Error 403" page). This is expected here and is NOT an environment
  problem: the scraper launches Chromium, navigates, detects the 403, logs
  `ALERTE : Erreur 403 (Accès refusé)`, and writes an empty `[]` to `data/betclic_mlb.json`.
  To scrape real odds you must run from a French residential IP / proxy.
- `data/betclic_mlb.json` is a committed sample of real output. Running the scraper here overwrites it
  with `[]`; restore it with `git checkout -- data/betclic_mlb.json` if you don't intend to commit that.
