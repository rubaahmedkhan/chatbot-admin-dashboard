import requests
from bs4 import BeautifulSoup
import json
import os
import hashlib
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv
from collections import Counter

load_dotenv()

MAX_PAGES = 60
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

SKIP_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png', '.gif', '.css', '.js',
                   '.svg', '.ico', '.woff', '.woff2', '.ttf', '.eot',
                   '.mp4', '.mp3', '.zip', '.rar', '.exe', '.dmg'}

CLOUDFLARE_SIGNATURES = [
    'Just a moment', 'Performing security verification',
    'security service to protect', 'Enable JavaScript and cookies to continue',
    'Verification successful', 'Ray ID:'
]

PAGE_CATEGORIES = {
    'fee_url':       ['fee', 'fees', 'tuition', 'charges', 'payment', 'cost', 'pricing'],
    'admission_url': ['admission', 'admissions', 'enroll', 'apply', 'registration'],
    'contact_url':   ['contact', 'reach', 'location', 'address', 'get-in-touch'],
    'about_url':     ['about', 'about-us', 'history', 'vision', 'mission', 'team'],
    'academics_url': ['academic', 'academics', 'curriculum', 'courses', 'syllabus', 'classes'],
    'result_url':    ['result', 'results', 'grades', 'marks'],
    'gallery_url':   ['gallery', 'photos', 'media'],
    'service_url':   ['service', 'services', 'solutions', 'portfolio', 'work', 'projects'],
}


# ─── URL Helpers ─────────────────────────────────────────────────────────────

def _clean_url(url):
    """Fragment aur query string hata do — duplicate pages avoid karne ke liye"""
    parsed = urlparse(url)
    return parsed._replace(fragment='', query='').geturl().rstrip('/')


def _is_skippable(url):
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in SKIP_EXTENSIONS)


def _is_cloudflare_blocked(text):
    return sum(1 for sig in CLOUDFLARE_SIGNATURES if sig in text) >= 2


# ─── Change Detection ─────────────────────────────────────────────────────────

def _get_homepage_hash(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.raise_for_status()
        return hashlib.md5(r.text.encode()).hexdigest()
    except Exception:
        return None


def _load_saved_hash(school_id):
    path = os.path.join(os.path.dirname(__file__), 'data', school_id, 'school_data.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f).get('homepage_hash') or None
    except Exception:
        return None


# ─── Content Extraction ───────────────────────────────────────────────────────

def _extract_with_trafilatura(html, url=''):
    """
    Trafilatura: industry-best content extractor.
    Kisi bhi framework (React, Vue, WordPress, custom) pe kaam karta hai.
    Nav, ads, footer, popups automatically remove karta hai.
    """
    try:
        import trafilatura
        text = trafilatura.extract(
            html,
            url=url,
            include_links=False,
            include_tables=True,
            include_images=False,
            no_fallback=False,
            favor_recall=True,
        )
        if text and len(text.strip()) > 80:
            return text.strip()
    except ImportError:
        pass
    return None


def _extract_with_bs4(html):
    """
    Smart BS4 fallback.
    Pehle semantic elements (main, article) dhundho,
    phir common content selectors try karo, last resort full page.
    """
    soup = BeautifulSoup(html, 'lxml')

    # Hard noise remove
    for tag in soup(['script', 'style', 'noscript', 'iframe', 'svg',
                     'head', 'nav', 'footer', 'header', 'aside', 'form']):
        tag.decompose()

    # Class/ID based noise remove
    noise_patterns = [
        'menu', 'sidebar', 'cookie', 'popup', 'modal',
        'banner', 'overlay', 'advertisement', 'ads', 'widget',
        'breadcrumb', 'pagination', 'social', 'share', 'newsletter',
    ]
    for el in soup.find_all(True):
        el_id = (el.get('id') or '').lower()
        el_class = ' '.join(el.get('class') or []).lower()
        combined = el_id + ' ' + el_class
        if any(pattern in combined for pattern in noise_patterns):
            el.decompose()

    # Semantic selectors — priority order
    for selector in [
        'main', 'article', '[role="main"]',
        '#content', '.content', '#main-content', '.main-content',
        '#page-content', '.page-content', '.entry-content',
        '.post-content', '#primary', '.primary', 'section',
    ]:
        try:
            el = soup.select_one(selector)
            if el:
                text = ' '.join(el.get_text(separator=' ', strip=True).split())
                if len(text) > 150:
                    return text
        except Exception:
            continue

    # Full page fallback
    text = ' '.join(soup.get_text(separator=' ', strip=True).split())
    return text


def _extract_best(html, url=''):
    """Trafilatura try karo, fail hone pe BS4"""
    text = _extract_with_trafilatura(html, url)
    if text:
        return text
    return _extract_with_bs4(html)


# ─── Sitemap Discovery ────────────────────────────────────────────────────────

def _discover_sitemap_urls(base_url, domain, max_urls=80):
    """
    Sitemap se saari URLs lo — layout/structure change hone pe bhi kaam karta hai.
    robots.txt → sitemap_index.xml → sitemap.xml → common paths
    """
    candidate_sitemaps = []

    # robots.txt mein sitemap dhundho
    try:
        r = requests.get(base_url.rstrip('/') + '/robots.txt', headers=HEADERS, timeout=8)
        if r.status_code == 200:
            for line in r.text.splitlines():
                if line.lower().startswith('sitemap:'):
                    candidate_sitemaps.append(line.split(':', 1)[1].strip())
    except Exception:
        pass

    # Common sitemap locations
    for path in ['/sitemap.xml', '/sitemap_index.xml', '/wp-sitemap.xml',
                 '/sitemap/', '/page-sitemap.xml', '/post-sitemap.xml']:
        candidate_sitemaps.append(base_url.rstrip('/') + path)

    collected = []
    tried = set()

    def _parse_one(sitemap_url):
        if sitemap_url in tried:
            return
        tried.add(sitemap_url)
        try:
            r = requests.get(sitemap_url, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                return
            soup = BeautifulSoup(r.text, 'lxml-xml')

            # Sitemap index — nested sitemaps
            for sm in soup.find_all('sitemap')[:8]:
                loc = sm.find('loc')
                if loc:
                    _parse_one(loc.text.strip())

            # Regular sitemap URLs
            for loc in soup.find_all('loc'):
                u = loc.text.strip()
                parsed = urlparse(u)
                if parsed.netloc in (domain, '') and not _is_skippable(u):
                    collected.append(_clean_url(u))

        except Exception:
            pass

    for sm_url in candidate_sitemaps[:6]:
        if len(collected) >= max_urls:
            break
        _parse_one(sm_url)

    # Dedupe + limit
    seen = set()
    unique = []
    for u in collected:
        if u not in seen:
            seen.add(u)
            unique.append(u)
        if len(unique) >= max_urls:
            break

    if unique:
        print(f"[Sitemap] {len(unique)} URLs mili")
    return unique


# ─── Playwright Scraper ───────────────────────────────────────────────────────

def _playwright_load_page(page, url):
    """
    Multiple strategies try karo — koi bhi website ho load ho jaaye.
    Lazy loading ke liye scroll bhi karta hai.
    """
    strategies = [
        ('domcontentloaded', 20000, 2000),
        ('networkidle',      25000, 1000),
        ('load',             25000, 2000),
        ('commit',           15000, 3000),
    ]

    for wait_until, timeout, extra in strategies:
        try:
            page.goto(url, wait_until=wait_until, timeout=timeout)
            page.wait_for_timeout(extra)

            # Lazy-load content trigger karo (scroll down step by step)
            try:
                page.evaluate("""() => new Promise(resolve => {
                    let total = 0;
                    const step = 400;
                    const timer = setInterval(() => {
                        window.scrollBy(0, step);
                        total += step;
                        if (total >= document.body.scrollHeight) {
                            clearInterval(timer);
                            window.scrollTo(0, 0);
                            resolve();
                        }
                    }, 80);
                    setTimeout(() => { clearInterval(timer); resolve(); }, 5000);
                })""")
                page.wait_for_timeout(600)
            except Exception:
                pass

            # Cookie/consent banner dismiss karo
            try:
                consent_selectors = [
                    'button[id*="accept"]',    'button[class*="accept"]',
                    'button[id*="consent"]',   'button[class*="consent"]',
                    'button[id*="agree"]',     'button[class*="agree"]',
                    'button[id*="allow"]',     'button[class*="allow"]',
                    '.cookie-accept',          '#cookie-accept',
                    '[data-testid*="accept"]', '[aria-label*="Accept"]',
                ]
                for sel in consent_selectors:
                    try:
                        btn = page.query_selector(sel)
                        if btn and btn.is_visible():
                            btn.click()
                            page.wait_for_timeout(400)
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            return True
        except Exception:
            continue

    return False


def _scrape_with_playwright(base_url, domain, pages_to_visit, visited, all_text):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security',
            ]
        )
        context = browser.new_context(
            user_agent=HEADERS['User-Agent'],
            ignore_https_errors=True,
            java_script_enabled=True,
            viewport={'width': 1280, 'height': 900},
        )
        page = context.new_page()
        # Webdriver detection bypass
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page.set_default_timeout(25000)

        while pages_to_visit and len(visited) < MAX_PAGES:
            url = pages_to_visit.pop(0)
            url = _clean_url(url)
            if url in visited or _is_skippable(url):
                continue

            success = _playwright_load_page(page, url)
            visited.add(url)

            if not success:
                print(f"[Playwright] Skip (load fail): {url}")
                continue

            try:
                html = page.content()
                text = _extract_best(html, url)
                page_name = urlparse(url).path or '/'

                if text and len(text) > 100 and not _is_cloudflare_blocked(text):
                    all_text[page_name] = text
                    print(f"[Playwright] Done: {url} ({len(text)} chars)")
                elif _is_cloudflare_blocked(text):
                    print(f"[Playwright] Cloudflare block: {url}")

                # Nayi links collect karo
                try:
                    links = page.eval_on_selector_all('a[href]', 'els => els.map(e => e.href)')
                except Exception:
                    links = []

                for raw_link in links:
                    clean = _clean_url(raw_link)
                    if (urlparse(clean).netloc == domain
                            and clean not in visited
                            and clean not in pages_to_visit
                            and not _is_skippable(clean)):
                        pages_to_visit.append(clean)

            except Exception as e:
                print(f"[Playwright] Error processing {url}: {e}")

        context.close()
        browser.close()


# ─── Requests Fallback Scraper ────────────────────────────────────────────────

def _scrape_with_requests(base_url, domain, pages_to_visit, visited, all_text):
    while pages_to_visit and len(visited) < MAX_PAGES:
        url = pages_to_visit.pop(0)
        url = _clean_url(url)
        if url in visited or _is_skippable(url):
            continue

        try:
            response = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
            response.raise_for_status()
            visited.add(url)

            text = _extract_best(response.text, url)
            page_name = urlparse(url).path or '/'

            if text and len(text) > 100 and not _is_cloudflare_blocked(text):
                all_text[page_name] = text
                print(f"[Requests] Done: {url} ({len(text)} chars)")
            elif _is_cloudflare_blocked(text):
                print(f"[Requests] Cloudflare block: {url}")

            soup = BeautifulSoup(response.text, 'lxml')
            for link in soup.find_all('a', href=True):
                full_url = urljoin(base_url, link['href'])
                clean = _clean_url(full_url)
                if (urlparse(clean).netloc == domain
                        and clean not in visited
                        and clean not in pages_to_visit
                        and not _is_skippable(clean)):
                    pages_to_visit.append(clean)

        except Exception as e:
            print(f"[Requests] Error {url}: {e}")


# ─── Boilerplate Removal ──────────────────────────────────────────────────────

def _remove_boilerplate(all_text):
    """
    Nav, footer, aur jo bhi text zyada tar pages pe repeat hota hai — remove karo.
    40%+ pages pe mile toh boilerplate consider karo.
    """
    if len(all_text) < 3:
        return all_text

    # Har page ko 10-word chunks mein todho
    page_chunks = {}
    for page, text in all_text.items():
        words = text.split()
        chunks = set()
        for i in range(0, max(0, len(words) - 9), 7):
            chunk = ' '.join(words[i:i + 10])
            if len(chunk) > 45:
                chunks.add(chunk)
        page_chunks[page] = chunks

    # Frequency count
    counter = Counter()
    for chunks in page_chunks.values():
        counter.update(chunks)

    threshold = max(2, int(len(all_text) * 0.40))
    boilerplate = {chunk for chunk, count in counter.items() if count >= threshold}

    if not boilerplate:
        return all_text

    # Boilerplate remove karo har page se
    cleaned = {}
    for page, text in all_text.items():
        words = text.split()
        result_words = []
        i = 0
        while i < len(words):
            chunk = ' '.join(words[i:i + 10])
            if chunk in boilerplate:
                i += 7
            else:
                result_words.append(words[i])
                i += 1
        cleaned_text = ' '.join(result_words).strip()
        if len(cleaned_text) > 80:
            cleaned[page] = cleaned_text

    removed = sum(len(v) for v in all_text.values()) - sum(len(v) for v in cleaned.values())
    print(f"[Boilerplate] {removed:,} chars hataye, {len(cleaned)} pages bacha")
    return cleaned


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def scrape_website(base_url, school_id, force=False):
    print(f"\n[Scraper] === Shuru: {school_id} | {base_url} | force={force} ===")

    current_hash = _get_homepage_hash(base_url)
    saved_hash   = _load_saved_hash(school_id)

    if not force and current_hash and saved_hash and current_hash == saved_hash:
        print(f"[Scraper] {school_id}: Website nahi badli — skip.")
        return None

    print(f"[Scraper] {school_id}: Scraping shuru ({'force' if force else 'change detected'})")

    visited   = set()
    all_text  = {}
    page_links = {}
    domain    = urlparse(base_url).netloc

    # ── Step 1: Sitemap se URLs lo (reliable, layout-change proof) ────────────
    sitemap_urls = _discover_sitemap_urls(base_url, domain)
    pages_to_visit = sitemap_urls if sitemap_urls else [base_url]

    # ── Step 2: Playwright — JS/SPA/React/Vue/any framework ──────────────────
    playwright_ok = False
    try:
        from playwright.sync_api import sync_playwright  # noqa
        playwright_ok = True
    except ImportError:
        print("[Scraper] Playwright nahi mila — requests fallback hoga")

    if playwright_ok:
        try:
            print("[Scraper] Playwright se scraping...")
            _scrape_with_playwright(base_url, domain, pages_to_visit[:], visited, all_text)
        except Exception as e:
            print(f"[Scraper] Playwright error: {e}")

    # ── Step 3: Requests fallback (agar Playwright se kuch nahi mila) ─────────
    if not all_text:
        print("[Scraper] Requests fallback...")
        fallback_pages = sitemap_urls[:] if sitemap_urls else [base_url]
        visited_r = set()
        _scrape_with_requests(base_url, domain, fallback_pages, visited_r, all_text)
        visited.update(visited_r)

    # ── Step 4: Boilerplate remove karo ──────────────────────────────────────
    if len(all_text) >= 3:
        all_text = _remove_boilerplate(all_text)

    # ── Step 5: Page categories detect karo ──────────────────────────────────
    for url in visited:
        path = urlparse(url).path.lower().strip('/')
        for category, keywords in PAGE_CATEGORIES.items():
            if category not in page_links and any(kw in path for kw in keywords):
                page_links[category] = url
                print(f"[Scraper] Category: {category} → {url}")
                break

    # ── Save ──────────────────────────────────────────────────────────────────
    data_path = os.path.join(os.path.dirname(__file__), 'data', school_id, 'school_data.json')
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
    except Exception:
        existing_data = {}

    if all_text:
        existing_data['scraped_pages'] = all_text
        existing_data['page_links']    = page_links
        existing_data['last_scraped']  = __import__('datetime').datetime.now().isoformat()
        if current_hash:
            existing_data['homepage_hash'] = current_hash
        print(f"[Scraper] Mukammal! {len(all_text)} pages scraped, {len(page_links)} categories.")
    else:
        existing_data.pop('homepage_hash', None)
        existing_data['last_scraped'] = __import__('datetime').datetime.now().isoformat()
        print("[Scraper] Warning: 0 pages mili — purana data safe, hash reset.")

    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)

    return all_text if all_text else None


if __name__ == '__main__':
    import sys
    if len(sys.argv) >= 3:
        scrape_website(sys.argv[2], sys.argv[1])
    else:
        sid = sys.argv[1] if len(sys.argv) > 1 else 'test'
        scrape_website(os.getenv('SCHOOL_URL', 'http://localhost:5000'), sid)
