from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response
from flask_cors import CORS
import json
import os
import uuid
import re
import hashlib
import threading
import base64
from openai import OpenAI
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('ADMIN_PASSWORD', 'changeme') + '_flask_secret'
CORS(app)

openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')
SERVER_URL = os.getenv('SERVER_URL', 'http://localhost:5000')

# ─── Response Cache ──────────────────────────────────────────────────────────
# {school_id: {question_hash: answer}} — server restart pe reset hota hai
_cache = {}

def _cache_key(text):
    return hashlib.md5(text.lower().strip().encode()).hexdigest()

def get_cached(school_id, question):
    return _cache.get(school_id, {}).get(_cache_key(question))

def set_cached(school_id, question, answer):
    if school_id not in _cache:
        _cache[school_id] = {}
    # Har school ka cache max 500 questions tak
    if len(_cache[school_id]) >= 500:
        oldest = next(iter(_cache[school_id]))
        del _cache[school_id][oldest]
    _cache[school_id][_cache_key(question)] = answer

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


# ─── School Data Helpers ─────────────────────────────────────────────────────

def get_all_schools():
    schools = []
    if not os.path.exists(DATA_DIR):
        return schools
    for name in os.listdir(DATA_DIR):
        config_path = os.path.join(DATA_DIR, name, 'config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                schools.append(json.load(f))
    return schools


def load_school_config(school_id):
    path = os.path.join(DATA_DIR, school_id, 'config.json')
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_school_config(school_id, config):
    path = os.path.join(DATA_DIR, school_id, 'config.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_school_data(school_id):
    path = os.path.join(DATA_DIR, school_id, 'school_data.json')
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_context(school_id):
    data = load_school_data(school_id)
    scraped = data.pop('scraped_pages', {})

    for key in ('homepage_hash', 'last_scraped', 'page_links'):
        data.pop(key, None)

    config = load_school_config(school_id)
    school_name = config.get('school_name', school_id) if config else school_id
    custom_info = (config or {}).get('custom_info', '').strip() if config else ''
    custom_info_enabled = (config or {}).get('custom_info_enabled', True) if config else True

    if not scraped:
        context = f"=== {school_name} - Information ===\n"
        context += json.dumps(data, ensure_ascii=False, indent=2)
        if custom_info and custom_info_enabled:
            context += f"\n\n=== Additional Information (use if answer not found above) ===\n{custom_info}\n"
        return context

    data.pop('phone', None)
    data.pop('note', None)

    PRIORITY_KEYWORDS = [
        'about', 'founder', 'team', 'contact', 'service', 'product',
        'fee', 'admission', 'price', 'plan', 'faculty', 'staff',
        'mission', 'vision', 'history', 'overview', 'index',
        'portfolio', 'project', 'work', 'case', 'client', 'solution'
    ]

    priority_pages = {}
    other_pages = {}

    for page, text in scraped.items():
        page_lower = page.lower().strip('/')
        is_homepage = page_lower in ('', 'home', 'index', '/')
        if is_homepage or any(kw in page_lower for kw in PRIORITY_KEYWORDS):
            priority_pages[page] = text
        else:
            other_pages[page] = text

    context = f"=== {school_name} — Website Data (PRIMARY SOURCE) ===\n"
    context += "\n[IMPORTANT PAGES — Check here first]\n"
    for page, text in priority_pages.items():
        page_lower = page.lower().strip('/')
        is_homepage = page_lower in ('', 'home', 'index', '/')
        limit = 8000 if is_homepage else 4000
        context += f"\n--- {page} ---\n{text[:limit]}\n"

    if other_pages:
        context += "\n[OTHER PAGES]\n"
        for page, text in other_pages.items():
            context += f"\n--- {page} ---\n{text[:1500]}\n"

    if custom_info and custom_info_enabled:
        context += f"\n\n=== Additional Information (use ONLY if answer is NOT found in website data above) ===\n{custom_info}\n"

    return context


def slugify(name):
    slug = name.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug[:40]


# ─── Admin Auth ──────────────────────────────────────────────────────────────

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated


# ─── Admin Routes ────────────────────────────────────────────────────────────

@app.route('/admin', methods=['GET'])
def admin_login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_panel'))
    return render_template('admin.html', page='login', error=None)


@app.route('/admin/login', methods=['POST'])
def admin_do_login():
    password = request.form.get('password', '')
    if password == ADMIN_PASSWORD:
        session['admin_logged_in'] = True
        return redirect(url_for('admin_panel'))
    return render_template('admin.html', page='login', error='Incorrect password!')


@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))


@app.route('/admin/panel')
@admin_required
def admin_panel():
    schools     = get_all_schools()
    server_url  = SERVER_URL
    msg         = request.args.get('msg', '')
    active_sid  = request.args.get('sid', '')
    return render_template('admin.html', page='panel', schools=schools,
                           server_url=server_url, msg=msg, active_sid=active_sid,
                           scrape_status=_scrape_status,
                           open_info_sid=active_sid if 'info' in msg or request.args.get('openinfo') else '')


@app.route('/admin/add-school', methods=['POST'])
@admin_required
def admin_add_school():
    school_name = request.form.get('school_name', '').strip()
    school_url = request.form.get('school_url', '').strip()
    phone = request.form.get('phone', '').strip()

    if not school_name:
        schools = get_all_schools()
        return render_template('admin.html', page='panel', schools=schools,
                               server_url=SERVER_URL, error='Client name is required!')

    school_id = slugify(school_name)
    school_dir = os.path.join(DATA_DIR, school_id)

    if os.path.exists(school_dir):
        school_id = school_id + '-' + str(uuid.uuid4())[:6]
        school_dir = os.path.join(DATA_DIR, school_id)

    os.makedirs(school_dir, exist_ok=True)

    api_key = 'sk-' + uuid.uuid4().hex[:24]

    config = {
        'school_id': school_id,
        'school_name': school_name,
        'school_url': school_url,
        'phone': phone,
        'api_key': api_key,
        'active': True,
        'created_at': __import__('datetime').date.today().isoformat()
    }
    save_school_config(school_id, config)

    school_data_template = {
        'school_name': school_name,
        'note': 'Website not yet synced. Please click Sync Data to load school information.'
    }
    data_path = os.path.join(school_dir, 'school_data.json')
    with open(data_path, 'w', encoding='utf-8') as f:
        json.dump(school_data_template, f, ensure_ascii=False, indent=2)

    if school_url and 'localhost' not in school_url and '127.0.0.1' not in school_url:
        register_scraper(school_id, school_url)

    return redirect(url_for('admin_panel'))


@app.route('/admin/toggle/<school_id>', methods=['POST'])
@admin_required
def admin_toggle(school_id):
    config = load_school_config(school_id)
    if config:
        config['active'] = not config.get('active', True)
        save_school_config(school_id, config)
    return redirect(url_for('admin_panel'))


# school_id → 'running' | 'done' | 'error'
_scrape_status = {}

def _run_scrape(school_id, school_url):
    _scrape_status[school_id] = 'running'
    try:
        from scraper import scrape_website
        result = scrape_website(school_url, school_id)
        if result is not None:
            _cache.pop(school_id, None)
        _scrape_status[school_id] = 'done'
        print(f"[Scrape] {school_id} done")
    except Exception as e:
        _scrape_status[school_id] = 'error'
        print(f"[Scrape] {school_id} error: {e}")


@app.route('/admin/scrape/<school_id>', methods=['POST'])
@admin_required
def admin_scrape(school_id):
    config = load_school_config(school_id)
    if not config or not config.get('school_url'):
        return redirect(url_for('admin_panel'))

    if _scrape_status.get(school_id) == 'running':
        return redirect(url_for('admin_panel') + '?msg=already_running&sid=' + school_id)

    t = threading.Thread(target=_run_scrape, args=(school_id, config['school_url']), daemon=True)
    t.start()
    return redirect(url_for('admin_panel') + '?msg=scrape_started&sid=' + school_id)


@app.route('/admin/scrape-status/<school_id>')
@admin_required
def scrape_status(school_id):
    status = _scrape_status.get(school_id, 'idle')
    return jsonify({'status': status})


def _extract_pdf_text(file_bytes):
    try:
        from pypdf import PdfReader
        import io
        reader = PdfReader(io.BytesIO(file_bytes))
        texts = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                texts.append(t.strip())
        return '\n'.join(texts)
    except Exception as e:
        print(f"[PDF] Extract error: {e}")
        return ''


def _extract_image_text(file_bytes, mime_type):
    try:
        b64 = base64.standard_b64encode(file_bytes).decode()
        resp = openai_client.chat.completions.create(
            model='gpt-4o-mini',
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime_type};base64,{b64}'}},
                    {'type': 'text', 'text': 'Extract ALL text from this image exactly as written. Return only the extracted text, nothing else.'}
                ]
            }],
            max_tokens=2000
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[Image OCR] Error: {e}")
        return ''


@app.route('/admin/custom-info/<school_id>', methods=['POST'])
@admin_required
def admin_save_custom_info(school_id):
    config = load_school_config(school_id)
    if not config:
        return redirect(url_for('admin_panel'))

    action = request.form.get('action', 'save')

    if action == 'delete':
        config['custom_info'] = ''
        config['custom_info_enabled'] = True
        save_school_config(school_id, config)
        _cache.pop(school_id, None)
        return redirect(url_for('admin_panel') + f'?msg=info_deleted&sid={school_id}')

    if action == 'toggle':
        config['custom_info_enabled'] = not config.get('custom_info_enabled', True)
        save_school_config(school_id, config)
        _cache.pop(school_id, None)
        return redirect(url_for('admin_panel') + f'?sid={school_id}')

    # Save text
    existing = config.get('custom_info', '')
    new_text = request.form.get('custom_info', '').strip()

    # Handle file upload
    file = request.files.get('info_file')
    extracted = ''
    if file and file.filename:
        file_bytes = file.read()
        fname = file.filename.lower()
        if fname.endswith('.pdf'):
            extracted = _extract_pdf_text(file_bytes)
        elif any(fname.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif']):
            mime = 'image/png' if fname.endswith('.png') else \
                   'image/gif' if fname.endswith('.gif') else \
                   'image/webp' if fname.endswith('.webp') else 'image/jpeg'
            extracted = _extract_image_text(file_bytes, mime)

    combined = '\n\n'.join(filter(None, [existing, new_text, extracted]))
    config['custom_info'] = combined
    config['custom_info_enabled'] = config.get('custom_info_enabled', True)
    save_school_config(school_id, config)
    _cache.pop(school_id, None)
    return redirect(url_for('admin_panel') + f'?msg=info_saved&sid={school_id}')


# ─── Widget JS Endpoint ──────────────────────────────────────────────────────

@app.route('/widget/<school_id>.js')
def widget_js(school_id):
    config = load_school_config(school_id)
    if not config or not config.get('active', False):
        return Response('/* Chatbot inactive */', mimetype='application/javascript')

    school_name = config.get('school_name', 'School')
    phone       = config.get('phone', '')
    api_key     = config.get('api_key', '')

    # Page links from scraped data
    school_data = load_school_data(school_id)
    page_links  = school_data.get('page_links', {})
    fee_url        = page_links.get('fee_url', '')
    admission_url  = page_links.get('admission_url', '')
    contact_url    = page_links.get('contact_url', '')
    academics_url  = page_links.get('academics_url', '')
    result_url     = page_links.get('result_url', '')

    js = f"""(function() {{
  var SCHOOL_ID   = "{school_id}";
  var API_KEY     = "{api_key}";
  var SERVER      = "{SERVER_URL}";
  var SCHOOL_NAME = "{school_name}";
  var PHONE       = "{phone}";
  var PAGE_LINKS  = [
    {{ keywords:['fee','fees','tuition','charges','payment','cost'],      url:"{fee_url}",       label:'View Fee Structure' }},
    {{ keywords:['admission','admissions','apply','enroll','register'],   url:"{admission_url}", label:'View Admissions' }},
    {{ keywords:['contact','address','location','directions','reach'],    url:"{contact_url}",   label:'Contact Us' }},
    {{ keywords:['academic','academics','curriculum','courses','classes'], url:"{academics_url}", label:'View Academics' }},
    {{ keywords:['result','results','grades','marks'],                    url:"{result_url}",    label:'View Results' }},
  ];

  // ── State ───────────────────────────────────────────────
  var history   = [];
  var isBusy    = false;

  // ── CSS ─────────────────────────────────────────────────
  var style = document.createElement('style');
  style.innerHTML = `
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    #_cb_wrap * {{ box-sizing:border-box; margin:0; padding:0; font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif; }}

    #_cb_btn {{
      position:fixed; bottom:28px; right:28px; width:62px; height:62px;
      background:linear-gradient(135deg,#4F46E5,#7C3AED);
      border-radius:50%; display:flex; align-items:center; justify-content:center;
      cursor:pointer; box-shadow:0 8px 24px rgba(79,70,229,.45);
      z-index:2147483647; transition:transform .25s,box-shadow .25s;
      border:3px solid rgba(255,255,255,.25); outline:none;
    }}
    #_cb_btn:hover {{ transform:scale(1.1); box-shadow:0 12px 32px rgba(79,70,229,.6); }}
    #_cb_btn svg {{ width:27px; height:27px; fill:#fff; }}

    #_cb_dot {{
      position:absolute; top:-2px; right:-2px; width:18px; height:18px;
      background:#ef4444; border-radius:50%; border:2px solid #fff;
      display:flex; align-items:center; justify-content:center;
      font-size:9px; font-weight:700; color:#fff;
      animation:_cb_ping 2s ease-in-out infinite;
    }}
    @keyframes _cb_ping {{
      0%,100% {{ box-shadow:0 0 0 0 rgba(239,68,68,.5); }}
      50%      {{ box-shadow:0 0 0 6px rgba(239,68,68,0); }}
    }}

    #_cb_tip {{
      position:fixed; bottom:102px; right:28px;
      background:#1E293B; color:#fff;
      padding:8px 14px; border-radius:10px;
      font-size:12.5px; font-weight:500;
      white-space:nowrap; pointer-events:none;
      opacity:0; transform:translateY(6px);
      transition:opacity .2s, transform .2s;
      box-shadow:0 4px 14px rgba(0,0,0,.2);
      z-index:2147483647;
    }}
    #_cb_tip::after {{
      content:''; position:absolute; bottom:-6px; right:22px;
      border:6px solid transparent;
      border-top-color:#1E293B; border-bottom:none;
    }}
    #_cb_btn:hover ~ #_cb_tip {{ opacity:1; transform:translateY(0); }}

    #_cb_box {{
      position:fixed; bottom:104px; right:28px;
      width:380px; height:560px;
      background:#fff; border-radius:20px;
      box-shadow:0 20px 60px rgba(0,0,0,.18), 0 4px 16px rgba(0,0,0,.08);
      display:none; flex-direction:column; z-index:2147483646;
      overflow:hidden;
      animation:_cb_slide .25s ease;
    }}
    #_cb_box.open {{ display:flex; }}
    @keyframes _cb_slide {{ from {{ opacity:0; transform:translateY(16px); }} to {{ opacity:1; transform:translateY(0); }} }}

    #_cb_head {{
      background:linear-gradient(135deg,#1e1b4b,#4F46E5);
      padding:16px 18px;
      display:flex; align-items:center; gap:12px;
      flex-shrink:0;
    }}
    #_cb_avatar {{
      width:40px; height:40px; border-radius:12px;
      background:rgba(255,255,255,.15);
      display:flex; align-items:center; justify-content:center;
      font-size:18px; flex-shrink:0;
    }}
    #_cb_head_info {{ flex:1; min-width:0; }}
    #_cb_head_name {{ color:#fff; font-weight:700; font-size:14.5px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    #_cb_head_status {{ display:flex; align-items:center; gap:5px; margin-top:2px; }}
    #_cb_head_status span {{ font-size:11px; color:rgba(255,255,255,.65); }}
    #_cb_online {{ width:7px; height:7px; background:#4ade80; border-radius:50%; animation:_cb_pulse 2s infinite; }}
    @keyframes _cb_pulse {{ 0%,100% {{ opacity:1; }} 50% {{ opacity:.4; }} }}
    #_cb_x {{
      background:rgba(255,255,255,.12); border:none; color:#fff;
      width:32px; height:32px; border-radius:8px;
      display:flex; align-items:center; justify-content:center;
      cursor:pointer; font-size:16px; flex-shrink:0;
      transition:background .2s;
    }}
    #_cb_x:hover {{ background:rgba(255,255,255,.22); }}

    #_cb_msgs {{
      flex:1; overflow-y:auto; padding:16px 14px;
      display:flex; flex-direction:column; gap:10px;
      background:#F8FAFC;
      scroll-behavior:smooth;
    }}
    #_cb_msgs::-webkit-scrollbar {{ width:4px; }}
    #_cb_msgs::-webkit-scrollbar-track {{ background:transparent; }}
    #_cb_msgs::-webkit-scrollbar-thumb {{ background:#D1D5DB; border-radius:4px; }}

    ._cb_row {{ display:flex; align-items:flex-end; gap:8px; }}
    ._cb_row.user {{ justify-content:flex-end; }}

    ._cb_ico {{
      width:28px; height:28px; border-radius:8px;
      display:flex; align-items:center; justify-content:center;
      font-size:13px; flex-shrink:0;
    }}
    ._cb_row.bot  ._cb_ico {{ background:linear-gradient(135deg,#4F46E5,#7C3AED); color:#fff; }}
    ._cb_row.user ._cb_ico {{ background:#E2E8F0; color:#64748B; }}

    ._cb_wrap {{ min-width:0; max-width:78%; }}

    ._cb_bub {{
      padding:11px 14px; border-radius:16px;
      font-size:13.5px; line-height:1.55; word-break:break-word;
    }}
    ._cb_row.bot  ._cb_bub {{ background:#fff; color:#1E293B; border-bottom-left-radius:4px; box-shadow:0 1px 4px rgba(0,0,0,.07); }}
    ._cb_row.user ._cb_bub {{ background:linear-gradient(135deg,#4F46E5,#7C3AED); color:#fff; border-bottom-right-radius:4px; }}

    ._cb_time {{ font-size:10px; color:#94A3B8; margin-top:4px; text-align:right; }}
    ._cb_row.bot ._cb_time {{ text-align:left; }}

    ._cb_typing_wrap {{ display:flex; gap:4px; padding:4px 2px; }}
    ._cb_typing_wrap span {{
      width:7px; height:7px; background:#94A3B8; border-radius:50%;
      animation:_cb_bounce 1.3s infinite ease-in-out;
    }}
    ._cb_typing_wrap span:nth-child(2) {{ animation-delay:.15s; }}
    ._cb_typing_wrap span:nth-child(3) {{ animation-delay:.3s; }}
    @keyframes _cb_bounce {{ 0%,60%,100% {{ transform:translateY(0); }} 30% {{ transform:translateY(-7px); }} }}

    #_cb_foot {{
      padding:12px 14px; border-top:1px solid #E2E8F0;
      display:flex; gap:8px; align-items:center;
      background:#fff; flex-shrink:0;
    }}
    #_cb_in {{
      flex:1; border:1.5px solid #E2E8F0; border-radius:12px;
      padding:10px 14px; font-size:13.5px; outline:none;
      transition:border-color .2s, box-shadow .2s;
      font-family:inherit; color:#1E293B; resize:none;
      background:#FAFAFA; max-height:80px;
    }}
    #_cb_in:focus {{ border-color:#4F46E5; box-shadow:0 0 0 3px rgba(79,70,229,.1); background:#fff; }}
    #_cb_in::placeholder {{ color:#CBD5E1; }}
    #_cb_go {{
      width:40px; height:40px; flex-shrink:0;
      background:linear-gradient(135deg,#4F46E5,#7C3AED);
      border:none; border-radius:12px; cursor:pointer;
      display:flex; align-items:center; justify-content:center;
      transition:opacity .2s, transform .2s;
    }}
    #_cb_go:hover {{ opacity:.88; transform:scale(1.05); }}
    #_cb_go:disabled {{ opacity:.4; cursor:not-allowed; transform:none; }}
    #_cb_go svg {{ width:17px; height:17px; fill:#fff; }}

    #_cb_powered {{
      text-align:center; padding:6px; font-size:10px; color:#CBD5E1;
      background:#fff; flex-shrink:0;
    }}

    ._cb_page_btn {{
      display:inline-flex; align-items:center; gap:6px;
      margin-top:8px; padding:7px 12px;
      background:#EEF2FF; color:#4F46E5;
      border-radius:8px; font-size:12px; font-weight:600;
      text-decoration:none; border:1px solid #C7D2FE;
      transition:background .2s;
    }}
    ._cb_page_btn:hover {{ background:#E0E7FF; }}
    ._cb_page_btn svg {{ width:13px; height:13px; }}

    @media (max-width:440px) {{
      #_cb_box {{ width:calc(100vw - 24px); right:12px; bottom:96px; height:70vh; }}
    }}
  `;
  document.head.appendChild(style);

  // ── HTML ─────────────────────────────────────────────────
  var wrap = document.createElement('div');
  wrap.id  = '_cb_wrap';
  wrap.innerHTML = `
    <button id="_cb_btn" aria-label="Open chat">
      <svg viewBox="0 0 32 32">
        <circle cx="16" cy="13" r="4.5" fill="#fff" opacity=".95"/>
        <ellipse cx="16" cy="21" rx="7" ry="4" fill="#fff" opacity=".85"/>
        <circle cx="16" cy="16" r="14" fill="none" stroke="#fff" stroke-width="1.5" opacity=".3"/>
        <path d="M8 26 Q16 30 24 26" stroke="#fff" stroke-width="1.8" fill="none" stroke-linecap="round" opacity=".6"/>
      </svg>
      <div id="_cb_dot">1</div>
    </button>
    <div id="_cb_tip">&#128172; Chat with our AI Assistant</div>
    <div id="_cb_box" role="dialog" aria-label="Chat assistant">
      <div id="_cb_head">
        <div id="_cb_avatar">
          <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
            <circle cx="16" cy="12" r="5" fill="rgba(255,255,255,.9)"/>
            <ellipse cx="16" cy="22" rx="8" ry="4.5" fill="rgba(255,255,255,.75)"/>
          </svg>
        </div>
        <div id="_cb_head_info">
          <div id="_cb_head_name">` + SCHOOL_NAME + `</div>
          <div id="_cb_head_status">
            <div id="_cb_online"></div>
            <span>AI Assistant &bull; Online</span>
          </div>
        </div>
        <button id="_cb_x" aria-label="Close">&#10005;</button>
      </div>
      <div id="_cb_msgs"></div>
      <div id="_cb_foot">
        <input id="_cb_in" type="text" placeholder="Type your message..." autocomplete="off" />
        <button id="_cb_go" aria-label="Send">
          <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
        </button>
      </div>
      <div id="_cb_powered">Powered by ChatBot AI</div>
    </div>
  `;
  document.body.appendChild(wrap);

  // ── Elements ─────────────────────────────────────────────
  var btn  = document.getElementById('_cb_btn');
  var box  = document.getElementById('_cb_box');
  var xBtn = document.getElementById('_cb_x');
  var msgs = document.getElementById('_cb_msgs');
  var inp  = document.getElementById('_cb_in');
  var go   = document.getElementById('_cb_go');
  var dot  = document.getElementById('_cb_dot');

  // ── Helpers ──────────────────────────────────────────────
  function timeNow() {{
    var d = new Date();
    return d.toLocaleTimeString([], {{hour:'2-digit', minute:'2-digit'}});
  }}

  function addMsg(text, who) {{
    var row = document.createElement('div');
    row.className = '_cb_row ' + who;
    var ico = document.createElement('div');
    ico.className = '_cb_ico';
    ico.innerHTML = who === 'bot'
      ? '<svg width="14" height="14" viewBox="0 0 32 32" fill="none"><circle cx="16" cy="11" r="5" fill="#fff"/><ellipse cx="16" cy="23" rx="8" ry="4" fill="#fff"/></svg>'
      : '<svg width="14" height="14" viewBox="0 0 32 32" fill="none"><circle cx="16" cy="11" r="5" fill="#94A3B8"/><ellipse cx="16" cy="23" rx="8" ry="4" fill="#94A3B8"/></svg>';
    var bub = document.createElement('div');
    bub.className = '_cb_bub';
    bub.innerHTML = text.replace(/\\n/g, '<br>');
    var t = document.createElement('div');
    t.className = '_cb_time';
    t.textContent = timeNow();
    var inner = document.createElement('div');
    inner.className = '_cb_wrap';
    inner.appendChild(bub);
    inner.appendChild(t);
    if (who === 'bot') {{
      row.appendChild(ico);
      row.appendChild(inner);
    }} else {{
      row.appendChild(inner);
      row.appendChild(ico);
    }}
    msgs.appendChild(row);
    msgs.scrollTop = msgs.scrollHeight;
    return row;
  }}

  function showTyping() {{
    var row = document.createElement('div');
    row.className = '_cb_row bot';
    row.id = '_cb_typing';
    row.innerHTML = '<div class="_cb_ico" style="background:linear-gradient(135deg,#4F46E5,#7C3AED)"><svg width="14" height="14" viewBox="0 0 32 32" fill="none"><circle cx="16" cy="11" r="5" fill="#fff"/><ellipse cx="16" cy="23" rx="8" ry="4" fill="#fff"/></svg></div><div class="_cb_bub"><div class="_cb_typing_wrap"><span></span><span></span><span></span></div></div>';
    msgs.appendChild(row);
    msgs.scrollTop = msgs.scrollHeight;
    return row;
  }}

  // ── Open / Close ─────────────────────────────────────────
  function openChat() {{
    box.classList.add('open');
    btn.style.display = 'none';
    inp.focus();
    if (msgs.children.length === 0) {{
      addMsg("Hello! &#128075; I'm <strong>" + SCHOOL_NAME + "'s</strong> AI Assistant. How can I help you today?<br><br>Feel free to ask me anything about us!", 'bot');
    }}
  }}

  function closeChat() {{
    box.classList.remove('open');
    btn.style.display = 'flex';
    // Clear conversation when closed
    history = [];
    msgs.innerHTML = '';
  }}

  btn.addEventListener('click', openChat);
  xBtn.addEventListener('click', closeChat);

  // ── Send ─────────────────────────────────────────────────
  function send() {{
    var text = inp.value.trim();
    if (!text || isBusy) return;

    addMsg(text, 'user');
    inp.value = '';
    isBusy = true;
    go.disabled = true;
    inp.disabled = true;
    dot.style.display = 'none';

    var typingEl = showTyping();
    var prevHistory = history.slice();  // snapshot before adding current

    fetch(SERVER + '/chat/' + SCHOOL_ID, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json', 'X-API-Key': API_KEY }},
      body: JSON.stringify({{ message: text, history: prevHistory }})
    }})
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      typingEl.remove();
      var reply = data.response || 'Sorry, I could not generate a response. Please try again.';
      var msgEl = addMsg(reply, 'bot');

      // ── Page link detection ─────────────────────────────
      var lower = text.toLowerCase();
      for (var i = 0; i < PAGE_LINKS.length; i++) {{
        var pl = PAGE_LINKS[i];
        if (pl.url && pl.keywords.some(function(k) {{ return lower.indexOf(k) !== -1; }})) {{
          var bub = msgEl.querySelector('._cb_bub');
          if (bub) {{
            var a = document.createElement('a');
            a.className = '_cb_page_btn';
            a.href      = pl.url;
            a.target    = '_blank';
            a.rel       = 'noopener';
            a.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg> ' + pl.label + ' →';
            bub.appendChild(a);
          }}
          break;
        }}
      }}

      history.push({{role:'user', content:text}});
      history.push({{role:'assistant', content:reply}});
    }})
    .catch(function() {{
      typingEl.remove();
      var err = 'Connection error. Please call ' + (PHONE || 'the school office') + ' for assistance.';
      addMsg(err, 'bot');
    }})
    .finally(function() {{
      isBusy = false;
      go.disabled = false;
      inp.disabled = false;
      inp.focus();
    }});
  }}

  go.addEventListener('click', send);
  inp.addEventListener('keydown', function(e) {{
    if (e.key === 'Enter' && !e.shiftKey) {{ e.preventDefault(); send(); }}
  }});
}})();
"""
    resp = Response(js, mimetype='application/javascript')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    return resp


# ─── Chat Endpoint ───────────────────────────────────────────────────────────

@app.route('/chat/<school_id>', methods=['POST'])
def chat(school_id):
    try:
        config = load_school_config(school_id)

        if not config:
            return jsonify({'response': 'Client not found. Please check the embed code.'}), 404

        if not config.get('active', False):
            return jsonify({'response': 'This chatbot is currently inactive.'}), 200

        api_key = request.headers.get('X-API-Key', '')
        if api_key != config.get('api_key', ''):
            return jsonify({'error': 'Unauthorized'}), 401

        user_message = request.json.get('message', '').strip()
        history     = request.json.get('history', [])
        if not user_message:
            return jsonify({'error': 'Empty message'}), 400

        # Cache only for first message (no prior history)
        if not history:
            cached = get_cached(school_id, user_message)
            if cached:
                return jsonify({'response': cached})

        school_name    = config.get('school_name', school_id)
        phone          = config.get('phone', '')
        school_context = build_context(school_id)

        system_prompt = f"""You are the official AI assistant for {school_name}.

STRICT RULES:
1. PRIORITY ORDER: First search "Website Data (PRIMARY SOURCE)" — this is always most up-to-date. Only if the answer is truly not there, then check "Additional Information" section.
2. Before saying "I don't know", search CAREFULLY through ALL sections — the answer is usually there.
3. Only use information from the data provided — never fabricate or guess.
4. For contact details (phone, email, address), use ONLY what is in the data below.
5. If after carefully reading ALL sections the answer is truly not there, say: "I don't have this information. Please contact us directly."
6. Reply in the same language the user writes in (English, Roman Urdu, or Urdu).
7. Keep answers concise and helpful.
8. Never discuss unrelated topics or other organizations.
9. Always be friendly and professional.

{school_name} Data:
{school_context}
"""

        # Build messages with conversation history (last 10 turns max)
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history[-10:]:
            if msg.get('role') in ('user', 'assistant') and msg.get('content'):
                messages.append({'role': msg['role'], 'content': msg['content']})
        messages.append({"role": "user", "content": user_message})

        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=0.2,
            max_tokens=600
        )

        answer = response.choices[0].message.content

        # Cache only single-turn responses
        if not history:
            set_cached(school_id, user_message, answer)

        return jsonify({'response': answer})

    except Exception as e:
        print(f"Chat error: {e}")
        config = load_school_config(school_id) or {}
        phone  = config.get('phone', '')
        msg    = 'Sorry, something went wrong. Please try again'
        if phone:
            msg += f' or call us at {phone}.'
        return jsonify({'response': msg}), 200


@app.route('/')
def index():
    return redirect(url_for('admin_login'))


# ─── Auto Scraper ────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()

def register_scraper(school_id, school_url):
    if 'localhost' in school_url or '127.0.0.1' in school_url:
        return
    job_id = f'scrape_{school_id}'
    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()

    def job():
        from scraper import scrape_website
        result = scrape_website(school_url, school_id)
        if result is not None:
            # Sirf tab cache clear karo jab scrape actually hua ho
            _cache.pop(school_id, None)
            print(f"[Cache] {school_id} ki cache clear ho gayi (website badli thi)")
        else:
            print(f"[Cache] {school_id} ki cache intact rahi (website nahi badli)")

    scheduler.add_job(job, 'cron', hour=6, minute=0, id=job_id)
    print(f"[Scheduler] Registered daily scraper for {school_id}")


def register_all_scrapers():
    for school in get_all_schools():
        url = school.get('school_url', '')
        sid = school.get('school_id', '')
        if url and sid:
            register_scraper(sid, url)

scheduler.start()
register_all_scrapers()


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    print(f"\n{'='*50}")
    print(f"  Multi-Tenant School Chatbot")
    print(f"  Admin Panel: http://localhost:{port}/admin")
    print(f"{'='*50}\n")
    app.run(debug=debug, host='0.0.0.0', port=port, use_reloader=False)
