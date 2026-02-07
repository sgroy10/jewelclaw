"""
Playwright-based Headless Browser Scraper for Trend Scout.

Scrapes jewelry designs from:
- BlueStone
- CaratLane
- Tanishq
- Pinterest

Uses real browser rendering to handle JavaScript-heavy sites.
"""

import logging
import asyncio
import re
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Try to import playwright - will fail if not installed
try:
    from playwright.async_api import async_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = None
    Page = None
    async_playwright = None
    logger.warning("Playwright not installed. Run: playwright install chromium")


@dataclass
class ScrapedDesign:
    """Scraped design data."""
    title: str
    price: Optional[float]
    image_url: str
    source_url: str
    source: str  # bluestone, caratlane, tanishq, pinterest
    category: str
    metal_type: str = "gold"


# Site configurations
SITE_CONFIGS = {
    "bluestone": {
        "base_url": "https://www.bluestone.com",
        "search_url": "https://www.bluestone.com/jewellery.html?q={query}",
        "category_urls": {
            "necklaces": "https://www.bluestone.com/jewellery/gold-necklaces.html",
            "earrings": "https://www.bluestone.com/jewellery/gold-earrings.html",
            "rings": "https://www.bluestone.com/jewellery/gold-rings.html",
            "bangles": "https://www.bluestone.com/jewellery/gold-bangles.html",
            "bracelets": "https://www.bluestone.com/jewellery/gold-bracelets.html",
            "pendants": "https://www.bluestone.com/jewellery/gold-pendants.html",
        },
        "selectors": {
            "product_card": "[data-product-id], .product-card, .plp-card",
            "title": ".product-title, .plp-prod-name, h3",
            "price": ".product-price, .plp-price, .final-price",
            "image": "img[src*='bluestone'], img[data-src*='bluestone']",
            "link": "a[href*='/jewellery/']",
        }
    },
    "caratlane": {
        "base_url": "https://www.caratlane.com",
        "search_url": "https://www.caratlane.com/search?q={query}",
        "category_urls": {
            "necklaces": "https://www.caratlane.com/jewellery/necklaces.html",
            "earrings": "https://www.caratlane.com/jewellery/earrings.html",
            "rings": "https://www.caratlane.com/jewellery/rings.html",
            "bangles": "https://www.caratlane.com/jewellery/bangles-bracelets.html",
            "pendants": "https://www.caratlane.com/jewellery/pendants.html",
        },
        "selectors": {
            "product_card": "[data-product-id], .product-tile, .plp-product",
            "title": ".product-name, .product-title, h2, h3",
            "price": ".product-price, .price, .final-price",
            "image": "img[src*='caratlane'], img[data-src]",
            "link": "a[href*='/jewellery/']",
        }
    },
    "tanishq": {
        "base_url": "https://www.tanishq.co.in",
        "search_url": "https://www.tanishq.co.in/search?q={query}",
        "category_urls": {
            "necklaces": "https://www.tanishq.co.in/jewellery/gold-jewellery/necklaces.html",
            "earrings": "https://www.tanishq.co.in/jewellery/gold-jewellery/earrings.html",
            "rings": "https://www.tanishq.co.in/jewellery/gold-jewellery/rings.html",
            "bangles": "https://www.tanishq.co.in/jewellery/gold-jewellery/bangles.html",
        },
        "selectors": {
            "product_card": ".product-tile, .product-card, [data-pid]",
            "title": ".product-name, .pdp-link, h3",
            "price": ".product-price, .price, .sales",
            "image": "img.tile-image, img[data-src]",
            "link": "a.pdp-link, a[href*='/product/']",
        }
    }
}

# Category keyword mappings
CATEGORY_KEYWORDS = {
    "bridal": ["bridal", "wedding", "engagement", "mangalsutra", "choker", "kundan", "polki", "heavy"],
    "dailywear": ["dailywear", "daily wear", "lightweight", "office", "casual", "simple", "minimal", "everyday"],
    "temple": ["temple", "traditional", "antique", "south indian", "kemp", "lakshmi", "coin"],
    "contemporary": ["contemporary", "modern", "fusion", "western", "geometric", "abstract"],
    "mens": ["mens", "men's", "gents", "male", "kada", "bracelet for men", "chain for men"],
    "kids": ["kids", "children", "baby", "infant", "tiny"],
}


def detect_category(text: str) -> str:
    """Detect category from title/description."""
    text_lower = text.lower() if text else ""
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text_lower:
                return category
    return "general"


def extract_price(text: str) -> Optional[float]:
    """Extract price from text like '₹45,000' or 'Rs. 45000'."""
    if not text:
        return None
    clean_text = text.replace(',', '').replace(' ', '')
    match = re.search(r'[\₹Rs\.]*(\d+)', clean_text)
    if match:
        try:
            return float(match.group(1))
        except:
            pass
    return None


def generate_design_id(source: str, title: str) -> str:
    """Generate unique ID for a design."""
    text = f"{source}:{title}".lower()
    return hashlib.md5(text.encode()).hexdigest()[:12]


class PlaywrightScraper:
    """Headless browser scraper using Playwright."""

    def __init__(self):
        self.browser: Optional[Browser] = None
        self.playwright = None

    async def start(self):
        """Start the browser."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not installed. Run: pip install playwright && playwright install chromium")

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--disable-gpu'
            ]
        )
        logger.info("Playwright browser started")

    async def stop(self):
        """Stop the browser."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("Playwright browser stopped")

    async def create_page(self) -> Page:
        """Create a new browser page with realistic settings."""
        context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='en-IN',
            timezone_id='Asia/Kolkata',
        )
        page = await context.new_page()

        # Block unnecessary resources for faster loading
        await page.route("**/*.{png,jpg,jpeg,gif,webp,svg}", lambda route: route.abort())
        await page.route("**/*google*", lambda route: route.abort())
        await page.route("**/*facebook*", lambda route: route.abort())
        await page.route("**/*analytics*", lambda route: route.abort())

        return page

    async def scrape_bluestone(self, query: str = None, category: str = None, limit: int = 20) -> List[ScrapedDesign]:
        """Scrape designs from BlueStone."""
        designs = []
        config = SITE_CONFIGS["bluestone"]

        try:
            page = await self.create_page()

            # Determine URL
            if query:
                url = config["search_url"].format(query=query.replace(' ', '+'))
            elif category and category in config["category_urls"]:
                url = config["category_urls"][category]
            else:
                url = config["category_urls"]["necklaces"]

            logger.info(f"BlueStone: Loading {url}")
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # Wait for products to load
            await page.wait_for_timeout(2000)

            # Scroll to load more products
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 1000)")
                await page.wait_for_timeout(500)

            # Extract products using JavaScript
            products = await page.evaluate("""
                () => {
                    const items = [];
                    const cards = document.querySelectorAll('[data-product-id], .product-card, .plp-card, .product-item');

                    cards.forEach((card, index) => {
                        if (index >= 20) return;

                        const titleEl = card.querySelector('.product-title, .plp-prod-name, h3, h4, [class*="name"]');
                        const priceEl = card.querySelector('.product-price, .plp-price, .final-price, [class*="price"]');
                        const imgEl = card.querySelector('img');
                        const linkEl = card.querySelector('a');

                        if (titleEl) {
                            items.push({
                                title: titleEl.innerText.trim(),
                                price: priceEl ? priceEl.innerText.trim() : null,
                                image: imgEl ? (imgEl.src || imgEl.dataset.src) : null,
                                link: linkEl ? linkEl.href : null
                            });
                        }
                    });

                    return items;
                }
            """)

            logger.info(f"BlueStone: Found {len(products)} products")

            for product in products[:limit]:
                if not product.get('title'):
                    continue

                designs.append(ScrapedDesign(
                    title=product['title'],
                    price=extract_price(product.get('price', '')),
                    image_url=product.get('image', ''),
                    source_url=product.get('link', url),
                    source='bluestone',
                    category=detect_category(product['title'])
                ))

            await page.context.close()

        except Exception as e:
            logger.error(f"BlueStone scrape error: {e}")

        return designs

    async def scrape_caratlane(self, query: str = None, category: str = None, limit: int = 20) -> List[ScrapedDesign]:
        """Scrape designs from CaratLane."""
        designs = []
        config = SITE_CONFIGS["caratlane"]

        try:
            page = await self.create_page()

            # Determine URL
            if query:
                url = config["search_url"].format(query=query.replace(' ', '+'))
            elif category and category in config["category_urls"]:
                url = config["category_urls"][category]
            else:
                url = config["category_urls"]["necklaces"]

            logger.info(f"CaratLane: Loading {url}")
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # Wait for products to load
            await page.wait_for_timeout(3000)

            # Scroll to load more products
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 1000)")
                await page.wait_for_timeout(500)

            # Extract products
            products = await page.evaluate("""
                () => {
                    const items = [];
                    const cards = document.querySelectorAll('[data-product-id], .product-tile, .product-card, [class*="product"]');

                    cards.forEach((card, index) => {
                        if (index >= 20) return;

                        const titleEl = card.querySelector('.product-name, .product-title, h2, h3, [class*="name"]');
                        const priceEl = card.querySelector('.product-price, .price, [class*="price"]');
                        const imgEl = card.querySelector('img');
                        const linkEl = card.querySelector('a');

                        if (titleEl && titleEl.innerText.trim()) {
                            items.push({
                                title: titleEl.innerText.trim(),
                                price: priceEl ? priceEl.innerText.trim() : null,
                                image: imgEl ? (imgEl.src || imgEl.dataset.src) : null,
                                link: linkEl ? linkEl.href : null
                            });
                        }
                    });

                    return items;
                }
            """)

            logger.info(f"CaratLane: Found {len(products)} products")

            for product in products[:limit]:
                if not product.get('title'):
                    continue

                designs.append(ScrapedDesign(
                    title=product['title'],
                    price=extract_price(product.get('price', '')),
                    image_url=product.get('image', ''),
                    source_url=product.get('link', url),
                    source='caratlane',
                    category=detect_category(product['title'])
                ))

            await page.context.close()

        except Exception as e:
            logger.error(f"CaratLane scrape error: {e}")

        return designs

    async def scrape_tanishq(self, query: str = None, category: str = None, limit: int = 20) -> List[ScrapedDesign]:
        """Scrape designs from Tanishq."""
        designs = []
        config = SITE_CONFIGS["tanishq"]

        try:
            page = await self.create_page()

            # Determine URL
            if query:
                url = config["search_url"].format(query=query.replace(' ', '+'))
            elif category and category in config["category_urls"]:
                url = config["category_urls"][category]
            else:
                url = config["category_urls"]["necklaces"]

            logger.info(f"Tanishq: Loading {url}")

            # Tanishq might block, so we add extra headers
            await page.set_extra_http_headers({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
            })

            await page.goto(url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)

            # Scroll
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 1000)")
                await page.wait_for_timeout(500)

            # Extract products
            products = await page.evaluate("""
                () => {
                    const items = [];
                    const cards = document.querySelectorAll('.product-tile, .product-card, [data-pid], .plp-card');

                    cards.forEach((card, index) => {
                        if (index >= 20) return;

                        const titleEl = card.querySelector('.product-name, .pdp-link, h3, [class*="name"]');
                        const priceEl = card.querySelector('.product-price, .price, .sales');
                        const imgEl = card.querySelector('img');
                        const linkEl = card.querySelector('a');

                        if (titleEl && titleEl.innerText.trim()) {
                            items.push({
                                title: titleEl.innerText.trim(),
                                price: priceEl ? priceEl.innerText.trim() : null,
                                image: imgEl ? (imgEl.src || imgEl.dataset.src) : null,
                                link: linkEl ? linkEl.href : null
                            });
                        }
                    });

                    return items;
                }
            """)

            logger.info(f"Tanishq: Found {len(products)} products")

            for product in products[:limit]:
                if not product.get('title'):
                    continue

                designs.append(ScrapedDesign(
                    title=product['title'],
                    price=extract_price(product.get('price', '')),
                    image_url=product.get('image', ''),
                    source_url=product.get('link', url),
                    source='tanishq',
                    category=detect_category(product['title'])
                ))

            await page.context.close()

        except Exception as e:
            logger.error(f"Tanishq scrape error: {e}")

        return designs

    async def search_all(self, query: str, limit_per_site: int = 10) -> List[ScrapedDesign]:
        """Search all sites for a query."""
        all_designs = []

        # Run scrapers concurrently
        results = await asyncio.gather(
            self.scrape_bluestone(query=query, limit=limit_per_site),
            self.scrape_caratlane(query=query, limit=limit_per_site),
            self.scrape_tanishq(query=query, limit=limit_per_site),
            return_exceptions=True
        )

        for result in results:
            if isinstance(result, list):
                all_designs.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"Scraper error: {result}")

        logger.info(f"Total designs found: {len(all_designs)}")
        return all_designs

    async def scrape_category(self, category: str, limit_per_site: int = 10) -> List[ScrapedDesign]:
        """Scrape a specific category from all sites."""
        all_designs = []

        results = await asyncio.gather(
            self.scrape_bluestone(category=category, limit=limit_per_site),
            self.scrape_caratlane(category=category, limit=limit_per_site),
            self.scrape_tanishq(category=category, limit=limit_per_site),
            return_exceptions=True
        )

        for result in results:
            if isinstance(result, list):
                all_designs.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"Scraper error: {result}")

        return all_designs


# Global scraper instance
playwright_scraper = PlaywrightScraper()
