from __future__ import annotations
import http.cookiejar
import os
import aiohttp
from dataclasses import dataclass, field
from urllib.parse import urljoin
import trafilatura

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

_COOKIES: dict | None = None
_COOKIES_LOADED: bool = False

_NEXT_PAGE_TEXTS = [
    "次のページ", "次へ", "次ページ", "続きを読む",
    "Next page", "Next Page", "NEXT", "next »", "»",
]


@dataclass
class ScrapeResult:
    url: str
    title: str
    text: str
    is_paywall: bool = False
    page_count: int = 1


def _get_cookies() -> dict | None:
    global _COOKIES, _COOKIES_LOADED
    if _COOKIES_LOADED:
        return _COOKIES
    _COOKIES_LOADED = True

    cookie_file = os.getenv("COOKIES_FILE", "")
    if not cookie_file or not os.path.exists(cookie_file):
        return None

    try:
        jar = http.cookiejar.MozillaCookieJar(cookie_file)
        jar.load(ignore_discard=True, ignore_expires=True)
        _COOKIES = {c.name: c.value for c in jar}
        print(f"[INFO] Loaded {len(_COOKIES)} cookies from {cookie_file}")
        return _COOKIES
    except Exception as e:
        print(f"[WARN] Cookie load failed: {e}")
        return None


def _find_next_page_url(html: str, current_url: str) -> str | None:
    """Detect a 'next page' link via rel=next or common text patterns."""
    try:
        from lxml import html as lxml_html
        tree = lxml_html.fromstring(html.encode("utf-8"))

        # rel="next" on <a> or <link>
        for elem in tree.xpath(
            '//*[contains(concat(" ", normalize-space(@rel), " "), " next ")][@href]'
        ):
            href = elem.get("href", "").strip()
            if href and not href.startswith("#"):
                return urljoin(current_url, href)

        # Text-based patterns
        for a in tree.xpath("//a[@href]"):
            text = (a.text_content() or "").strip()
            href = a.get("href", "").strip()
            if href and not href.startswith("#") and any(p in text for p in _NEXT_PAGE_TEXTS):
                return urljoin(current_url, href)
    except Exception:
        pass
    return None


async def fetch_article(url: str) -> ScrapeResult:
    cookies = _get_cookies()

    pages_html: list[str] = []
    current_url = url
    visited: set[str] = set()

    for _ in range(5):  # max 5 pages
        if current_url in visited:
            break
        visited.add(current_url)

        html = await _download_html(current_url, cookies=cookies)
        if html is None:
            break
        pages_html.append(html)

        next_url = _find_next_page_url(html, current_url)
        if next_url is None or next_url in visited:
            break
        current_url = next_url

    if not pages_html:
        return ScrapeResult(url=url, title=url, text="", is_paywall=True)

    title = _extract_title(pages_html[0])
    texts: list[str] = []

    for i, html in enumerate(pages_html):
        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        if text and len(text) >= 200:
            texts.append(text)
        elif i == 0:
            fallback_text, fallback_title = _newspaper_fallback(url, html)
            if not title:
                title = fallback_title
            if fallback_text and len(fallback_text) >= 200:
                texts.append(fallback_text)

    combined = "\n\n---\n\n".join(texts)

    if not combined or len(combined) < 200:
        return ScrapeResult(url=url, title=title or url, text="", is_paywall=True)

    return ScrapeResult(
        url=url,
        title=title or url,
        text=combined,
        is_paywall=False,
        page_count=len(pages_html),
    )


async def _download_html(url: str, cookies: dict | None = None) -> str | None:
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(
                url,
                cookies=cookies or {},
                timeout=aiohttp.ClientTimeout(total=30),
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.text()
    except Exception:
        return None


def _extract_title(html: str) -> str:
    meta = trafilatura.extract_metadata(html)
    if meta and meta.title:
        return meta.title
    return ""


def _newspaper_fallback(url: str, html: str) -> tuple[str, str]:
    try:
        from newspaper import Article
        article = Article(url)
        article.set_html(html)
        article.parse()
        return article.text, article.title
    except Exception:
        return "", ""
