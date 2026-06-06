import asyncio
import json
import logging
import os
import random
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

class BetclicMLBScraper:
    def __init__(self):
        self.base_url = "https://www.betclic.fr"
        self.mlb_url = f"{self.base_url}/baseball-sbaseball/major-league-c473"
        self.data = []

    async def random_sleep(self, min_s=1.0, max_s=3.0):
        """Sleep for a random duration to mimic human behavior."""
        await asyncio.sleep(random.uniform(min_s, max_s))

    async def bypass_banners(self, page):
        """Bypass Didomi and TC Privacy banners."""
        try:
            # Didomi
            didomi_btn = page.locator("#didomi-notice-agree-button")
            if await didomi_btn.is_visible(timeout=5000):
                await didomi_btn.click()
                logger.info("Bannière Didomi acceptée.")
                await self.random_sleep(1, 2)
        except Exception:
            pass

        try:
            # TC Privacy - search for "Tout accepter" button specifically inside the popin
            tc_privacy_popin = page.locator("#popin_tc_privacy")
            if await tc_privacy_popin.is_visible(timeout=3000):
                logger.info("Bannière TC Privacy détectée, tentative de fermeture...")
                # Button 2 is usually "Tout accepter"
                btn = page.locator("#popin_tc_privacy_button_2")
                if await btn.is_visible():
                    await btn.click()
                else:
                    # Fallback to searching by text if ID fails
                    try:
                        await page.get_by_role("button", name="Tout accepter").click(timeout=2000)
                    except:
                        pass
                logger.info("Bannière TC Privacy acceptée.")
                await asyncio.sleep(1)
        except Exception as e:
            logger.debug(f"Erreur lors de la fermeture TC Privacy: {e}")

    def parse_french_odds(self, odds_str):
        """Convert French decimal string (e.g., '1,50') to float (1.5)."""
        try:
            return float(odds_str.replace(',', '.'))
        except (ValueError, AttributeError):
            return 0.0

    async def get_match_links(self, page):
        """Extract match URLs, names and times from the listing page."""
        logger.info("Extraction des matchs depuis la page de liste...")
        response = await page.goto(self.mlb_url, wait_until="load")

        if response and response.status == 403:
            logger.error("ALERTE : Erreur 403 (Accès refusé). Le site a bloqué le scraper.")
            return []

        # Vérification anti-bot (avec vérification de présence de contenu pour éviter les faux positifs)
        content = await page.content()
        match_links_count = await page.locator('a[href*="-m"]').count()

        if match_links_count == 0:
            if "captcha" in content.lower() or "distilnetworks" in content.lower() or "blocked" in content.lower():
                logger.error("ALERTE : Blocage ou CAPTCHA détecté. Le site a bloqué le scraper.")
                await page.screenshot(path="block_detected.png")
                return []

        await self.bypass_banners(page)
        await self.random_sleep(4, 6)

        matches = await page.evaluate("""() => {
            const results = [];

            // Helper to get date context
            const getDateContext = (el) => {
                let current = el;
                while (current) {
                    const header = current.previousElementSibling;
                    if (header && (header.innerText.includes("Aujourd'hui") || header.innerText.includes("Demain") || header.innerText.includes("Maintenant"))) {
                        return header.innerText.trim();
                    }
                    if (header && /[0-9]{2}\\/[0-9]{2}/.test(header.innerText)) {
                        return header.innerText.trim();
                    }
                    current = current.parentElement;
                }
                return "Aujourd'hui";
            };

            const links = Array.from(document.querySelectorAll('a[href*="-m"]'));
            const seen = new Set();

            links.forEach(a => {
                if (seen.has(a.href) || !a.href.includes('/baseball-sbaseball/major-league-c473/')) return;
                seen.add(a.href);

                let matchName = "Inconnu";
                let time = "Inconnu";
                let dateContext = "Aujourd'hui";

                try {
                    let container = a.closest('.availableEvents_event') || a.closest('sports-events-event') || a.parentElement;
                    dateContext = getDateContext(container);

                    const teamElements = Array.from(container.querySelectorAll('.scoreboard_unfolded_team_name, .scoreboard_team_name, b, .event_name'));
                    if (teamElements.length >= 2) {
                        matchName = `${teamElements[0].innerText.trim()} vs ${teamElements[1].innerText.trim()}`;
                    } else {
                         const slug = a.href.split('/').pop().split('-m')[0];
                         const words = slug.split('-');
                         if (words.length >= 2) {
                             const mid = Math.floor(words.length / 2);
                             const team1 = words.slice(0, mid).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
                             const team2 = words.slice(mid).map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
                             matchName = `${team1} vs ${team2}`;
                         } else {
                             matchName = words.map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
                         }
                    }

                    const text = container.innerText;
                    const timeMatch = text.match(/([0-2][0-9]:[0-5][0-9])/);
                    time = timeMatch ? timeMatch[1] : 'Inconnu';
                    if (text.includes("LIVE") || text.includes("En direct")) time = "LIVE";
                } catch(e) {}

                results.push({
                    url: a.href,
                    name: matchName,
                    time: time,
                    dateContext: dateContext
                });
            });
            return results;
        }""")

        logger.info(f"{len(matches)} matchs potentiels trouvés.")
        return matches

    async def extract_markets(self, page, match_info):
        """Extract Runs and Hits markets from a match detail page."""
        url = match_info['url']
        logger.info(f"Analyse du match : {match_info['name']} ({url})")

        try:
            response = await page.goto(url, wait_until="domcontentloaded", referer=self.mlb_url)
            await self.random_sleep(2, 4)

            if response and response.status == 403:
                logger.error(f"ALERTE : Erreur 403 sur {url}")
                return None

            content = await page.content()
            # On vérifie si un élément réel de match est présent pour éviter les faux positifs sur le mot "captcha"
            has_market = await page.locator('.marketBox').count() > 0
            if not has_market and ("captcha" in content.lower() or "distilnetworks" in content.lower()):
                logger.error(f"ALERTE : CAPTCHA détecté sur {url}")
                await page.screenshot(path=f"captcha_match_{random.randint(1,100)}.png")
                return None

            await self.bypass_banners(page)
            await self.random_sleep(2, 4)

            paris = []

            # Helper to extract from current view
            async def get_from_current_view():
                return await page.evaluate("""() => {
                    const markets = [];
                    const blocks = document.querySelectorAll('.marketBox');
                    blocks.forEach(block => {
                        const title = (block.querySelector('.marketBox_headTitle') || block.querySelector('.marketBox_title'))?.innerText.trim() || '';

                        // Option 1: Standard line with label and odds button
                        const lines = block.querySelectorAll('.marketBox_lineSelection, .marketBox_line');
                        lines.forEach(line => {
                            let label = line.querySelector('.marketBox_label')?.innerText.trim();
                            if (!label) {
                                label = line.querySelector('bcdk-bet-button-label')?.innerText.trim();
                            }
                            const odds = (line.querySelector('bcdk-bet-button-odds-animated') || line.querySelector('.marketBox_oddsValue'))?.innerText.trim() || '';
                            if (label || odds) {
                                markets.push({ title, option: label || '', cote_raw: odds });
                            }
                        });

                        // Option 2: Simple markets (buttons side by side)
                        if (lines.length === 0) {
                             const buttons = block.querySelectorAll('button[bcdkbetbutton]');
                             buttons.forEach(btn => {
                                 const label = btn.querySelector('bcdk-bet-button-label')?.innerText.trim() || '';
                                 const odds = btn.querySelector('bcdk-bet-button-odds-animated')?.innerText.trim() || '';
                                 markets.push({ title, option: label, cote_raw: odds });
                             });
                        }
                    });
                    return markets;
                }""")

            # Try common tabs if needed (Total Hits is often in 'Joueurs' or 'Plus')
            match_tabs = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('.tab_item, .tab_label'))
                    .map(el => el.innerText.trim())
                    .filter(t => ["Le Top", "Runs", "Joueurs", "Manches", "Plus"].includes(t));
            }""")

            tabs_to_check = []
            for t in ["Le Top", "Runs", "Joueurs", "Manches", "Plus"]:
                if t in match_tabs:
                    tabs_to_check.append(t)

            if not tabs_to_check:
                tabs_to_check = ["Le Top"]

            seen_market_hashes = set()

            for tab_name in tabs_to_check:
                try:
                    tab_el = page.locator(".tab_item, .tab_link").filter(has_text=tab_name).first
                    if await tab_el.is_visible():
                        await tab_el.click(force=True)
                        await self.random_sleep(1.5, 2.5)
                except:
                    continue

                current_markets = await get_from_current_view()
                for m in current_markets:
                    m_hash = f"{m['title']}|{m['option']}"
                    if m_hash in seen_market_hashes:
                        continue
                    seen_market_hashes.add(m_hash)

                    title_lower = m['title'].lower()
                    market_type = None
                    if "total runs" in title_lower or (title_lower == "runs" and not "manche" in title_lower and not "-" in title_lower):
                        if "plus de" in m['option'].lower() or "moins de" in m['option'].lower() or "+ de" in m['option'].lower() or "- de" in m['option'].lower():
                            market_type = "Total Runs"
                    elif ("hits" in title_lower or "nombre total de hits" in title_lower) and not "manche" in title_lower:
                        market_type = "Total Hits"

                    if market_type:
                        cote = self.parse_french_odds(m['cote_raw'])
                        if cote >= 1.20:
                            paris.append({
                                "type": market_type,
                                "option": m['option'],
                                "cote": cote
                            })

            if paris:
                logger.info(f"[INFO] {len(paris)} options sauvegardées pour {match_info['name']}")

                now = datetime.now()
                try:
                    date_context = match_info.get('dateContext', "Aujourd'hui")
                    if "Demain" in date_context:
                        base_date = now + timedelta(days=1)
                    elif "Aujourd'hui" in date_context or "Maintenant" in date_context:
                        base_date = now
                    elif "/" in date_context:
                        day, month = map(int, date_context.split('/'))
                        year = now.year
                        if month < now.month: year += 1
                        base_date = datetime(year, month, day)
                    else:
                        base_date = now

                    if match_info['time'] == "LIVE":
                        dt = now
                    else:
                        h, m = map(int, match_info['time'].split(':'))
                        dt = base_date.replace(hour=h, minute=m, second=0, microsecond=0)

                    date_heure = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                except:
                    date_heure = now.strftime("%Y-%m-%dT%H:%M:%SZ")

                return {
                    "match": match_info['name'],
                    "date_heure": date_heure,
                    "paris": paris
                }
        except Exception as e:
            logger.error(f"Erreur lors de l'extraction du match {url}: {e}")
        return None

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 800}
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

            try:
                matches_to_scrape = await self.get_match_links(page)
                for match_info in matches_to_scrape:
                    match_data = await self.extract_markets(page, match_info)
                    if match_data:
                        self.data.append(match_data)

                os.makedirs("data", exist_ok=True)
                with open("data/betclic_mlb.json", "w", encoding="utf-8") as f:
                    json.dump(self.data, f, indent=2, ensure_ascii=False)

                logger.info(f"Extraction terminée. {len(self.data)} matchs enregistrés dans data/betclic_mlb.json")

            except Exception as e:
                logger.error(f"Erreur globale : {e}")

            await browser.close()

if __name__ == "__main__":
    scraper = BetclicMLBScraper()
    asyncio.run(scraper.run())
