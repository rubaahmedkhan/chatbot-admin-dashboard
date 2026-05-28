import requests
from bs4 import BeautifulSoup
import json
import os
import hashlib
from urllib.parse import urljoin, urlparse
from dotenv import load_dotenv

load_dotenv()

MAX_PAGES = 50
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SchoolChatbot/1.0)'}

CLOUDFLARE_SIGNATURES = [
    'Just a moment', 'Performing security verification',
    'security service to protect', 'Enable JavaScript and cookies to continue',
    'Verification successful', 'Ray ID:'
]

def _is_cloudflare_blocked(text):
    return sum(1 for sig in CLOUDFLARE_SIGNATURES if sig in text) >= 2


def _get_homepage_hash(url):
    """Sirf homepage fetch karo aur uska hash nikalo"""
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
            data = json.load(f)
        return data.get('homepage_hash')
    except Exception:
        return None


def has_website_changed(base_url, school_id):
    """True return karo agar website badli hai ya pehli baar scrape ho rahi hai"""
    current_hash = _get_homepage_hash(base_url)
    if current_hash is None:
        return False  # Website accessible nahi — skip karo
    saved_hash = _load_saved_hash(school_id)
    if saved_hash is None:
        return True   # Pehli baar — zaroor scrape karo
    return current_hash != saved_hash


def _scrape_with_requests(base_url, domain, pages_to_visit, visited, all_text):
    """Plain HTML scraper — static websites ke liye"""
    while pages_to_visit and len(visited) < MAX_PAGES:
        url = pages_to_visit.pop(0)
        if url in visited:
            continue
        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            response.raise_for_status()
            visited.add(url)

            soup = BeautifulSoup(response.text, 'lxml')
            for tag in soup(['script', 'style', 'nav', 'footer']):
                tag.decompose()

            text = soup.get_text(separator=' ', strip=True)
            text = ' '.join(text.split())

            page_name = urlparse(url).path or 'home'
            if text.strip() and not _is_cloudflare_blocked(text):
                all_text[page_name] = text
                print(f"[Scraper] Done: {url}")
            elif _is_cloudflare_blocked(text):
                print(f"[Scraper] Cloudflare blocked — skip: {url}")

            for link in soup.find_all('a', href=True):
                full_url = urljoin(base_url, link['href'])
                parsed = urlparse(full_url)
                if parsed.netloc == domain and full_url not in visited:
                    if not any(ext in full_url for ext in ['.pdf', '.jpg', '.png', '.css', '.js']):
                        pages_to_visit.append(full_url)

        except Exception as e:
            print(f"[Scraper] Error {url}: {e}")
            continue


def _scrape_with_playwright(base_url, domain, pages_to_visit, visited, all_text):
    """Playwright scraper — React/Angular/JS websites ke liye"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers({'User-Agent': 'Mozilla/5.0 (compatible; SchoolChatbot/1.0)'})

        while pages_to_visit and len(visited) < MAX_PAGES:
            url = pages_to_visit.pop(0)
            if url in visited:
                continue
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=15000)
                page.wait_for_timeout(1500)
                visited.add(url)

                # JS render hone ke baad HTML lo
                html = page.content()
                soup = BeautifulSoup(html, 'lxml')
                for tag in soup(['script', 'style', 'nav', 'footer']):
                    tag.decompose()

                text = soup.get_text(separator=' ', strip=True)
                text = ' '.join(text.split())

                page_name = urlparse(url).path or 'home'
                if text.strip() and not _is_cloudflare_blocked(text):
                    all_text[page_name] = text
                    print(f"[Playwright] Done: {url}")
                elif _is_cloudflare_blocked(text):
                    print(f"[Playwright] Cloudflare blocked — skip: {url}")

                # Saare links dhundo
                links = page.eval_on_selector_all('a[href]', 'els => els.map(e => e.href)')
                for full_url in links:
                    parsed = urlparse(full_url)
                    if parsed.netloc == domain and full_url not in visited:
                        if not any(ext in full_url for ext in ['.pdf', '.jpg', '.png', '.css', '.js']):
                            pages_to_visit.append(full_url)

            except Exception as e:
                print(f"[Playwright] Error {url}: {e}")
                continue

        browser.close()


def _is_js_heavy(base_url):
    """Check karo ke website JS-heavy hai ya nahi"""
    try:
        response = requests.get(base_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, 'lxml')

        # Body mein actual text kitna hai
        body_text = soup.body.get_text(strip=True) if soup.body else ''

        # React/Angular/Vue signatures
        js_signatures = [
            'id="root"', 'id="app"', 'ng-version', 'data-reactroot',
            '__NEXT_DATA__', 'nuxt', 'ng-app'
        ]
        raw_html = response.text

        is_js = any(sig in raw_html for sig in js_signatures)

        # Ya body text bohot kam ho (JS ne content render karna hai)
        if len(body_text) < 200:
            is_js = True

        return is_js
    except Exception:
        return False


def scrape_website(base_url, school_id):
    print(f"[Scraper] Shuru: {base_url} — school: {school_id}")

    # ── Change detection: sirf tab scrape karo jab website badli ho ──
    current_hash = _get_homepage_hash(base_url)
    saved_hash = _load_saved_hash(school_id)

    if current_hash and saved_hash and current_hash == saved_hash:
        print(f"[Scraper] {school_id}: Website nahi badli — scrape skip kiya. Computing bachaya!")
        return None  # Koi kaam nahi kiya

    print(f"[Scraper] {school_id}: Website badli hai (ya pehli baar) — poora scrape shuru")

    visited = set()
    all_text = {}
    page_links = {}   # category → full URL mapping
    domain = urlparse(base_url).netloc
    pages_to_visit = [base_url]

    # Keyword → category mapping for auto page detection
    PAGE_CATEGORIES = {
        'fee_url':       ['fee', 'fees', 'tuition', 'charges', 'payment', 'cost'],
        'admission_url': ['admission', 'admissions', 'enroll', 'apply', 'registration'],
        'contact_url':   ['contact', 'reach', 'location', 'address'],
        'about_url':     ['about', 'about-us', 'history', 'vision', 'mission'],
        'academics_url': ['academic', 'academics', 'curriculum', 'courses', 'syllabus', 'classes'],
        'result_url':    ['result', 'results', 'grades', 'marks'],
        'gallery_url':   ['gallery', 'photos', 'media'],
    }

    # Decide: plain requests ya Playwright?
    use_playwright = False
    try:
        from playwright.sync_api import sync_playwright  # noqa
        use_playwright = _is_js_heavy(base_url)
        if use_playwright:
            print(f"[Scraper] JS-heavy website detect hua — Playwright use hoga")
        else:
            print(f"[Scraper] Static website — requests use hoga")
    except ImportError:
        print(f"[Scraper] Playwright install nahi — requests use hoga")

    if use_playwright:
        _scrape_with_playwright(base_url, domain, pages_to_visit, visited, all_text)
    else:
        _scrape_with_requests(base_url, domain, pages_to_visit, visited, all_text)

    # Auto-detect page categories from scraped URLs
    for url in visited:
        path = urlparse(url).path.lower().strip('/')
        for category, keywords in PAGE_CATEGORIES.items():
            if category not in page_links:
                if any(kw in path for kw in keywords):
                    page_links[category] = url
                    print(f"[Scraper] Page detected — {category}: {url}")
                    break

    # Data + naya hash save karo
    data_path = os.path.join(os.path.dirname(__file__), 'data', school_id, 'school_data.json')
    try:
        with open(data_path, 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
    except Exception:
        existing_data = {}

    existing_data['scraped_pages'] = all_text
    existing_data['page_links']    = page_links
    existing_data['last_scraped']  = __import__('datetime').datetime.now().isoformat()
    if current_hash:
        existing_data['homepage_hash'] = current_hash

    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)

    print(f"[Scraper] Mukammal! {len(visited)} pages, {len(page_links)} page links detected for {school_id}.")
    return all_text


if __name__ == '__main__':
    import sys
    sid = sys.argv[1] if len(sys.argv) > 1 else 'young-scholars'
    scrape_website(os.getenv('SCHOOL_URL', 'http://localhost:5000'), sid)
