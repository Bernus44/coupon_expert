import asyncio
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

async def check():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800}
        )
        page = await context.new_page()
        stealth = Stealth()
        await stealth.apply_stealth_async(page)

        url = "https://www.betclic.fr/baseball-sbaseball/major-league-c473"
        await page.goto(url, wait_until="load")
        content = await page.content()
        print(f"Length of content: {len(content)}")
        if "captcha" in content.lower():
            print("CAPTCHA word found in content")

        await page.screenshot(path="captcha_check.png")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(check())
