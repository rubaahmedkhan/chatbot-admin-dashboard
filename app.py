from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response
from flask_cors import CORS
import json
import os
import uuid
import re
import hashlib
import threading
import base64
import shutil
from openai import OpenAI
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
try:
    from supabase import create_client as _sb_create
    _SB_AVAILABLE = True
except ImportError:
    _SB_AVAILABLE = False

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('ADMIN_PASSWORD', 'changeme') + '_flask_secret'
CORS(app)

openai_client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')
SERVER_URL = os.getenv('SERVER_URL', 'http://localhost:5000')

# ─── Supabase Init ───────────────────────────────────────────────────────────
_supabase = None
if _SB_AVAILABLE:
    _sb_url = os.getenv('SUPABASE_URL', '')
    _sb_key = os.getenv('SUPABASE_KEY', '')
    if _sb_url and _sb_key:
        try:
            _supabase = _sb_create(_sb_url, _sb_key)
            print("[Supabase] Connected ✓")
        except Exception as _e:
            print(f"[Supabase] Connect failed: {_e}")

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
    if os.path.exists(DATA_DIR):
        for name in os.listdir(DATA_DIR):
            config_path = os.path.join(DATA_DIR, name, 'config.json')
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    schools.append(json.load(f))
    if not schools and _supabase:
        try:
            res = _supabase.table('schools').select('config_json').execute()
            schools = [row['config_json'] for row in (res.data or [])]
        except Exception as e:
            print(f"[Supabase] get_all_schools: {e}")
    return schools


def load_school_config(school_id):
    path = os.path.join(DATA_DIR, school_id, 'config.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    if _supabase:
        try:
            res = _supabase.table('schools').select('config_json').eq('school_id', school_id).execute()
            if res.data:
                config = res.data[0]['config_json']
                os.makedirs(os.path.join(DATA_DIR, school_id), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
                return config
        except Exception as e:
            print(f"[Supabase] load_config {school_id}: {e}")
    return None


def save_school_config(school_id, config):
    path = os.path.join(DATA_DIR, school_id, 'config.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    if _supabase:
        try:
            _supabase.table('schools').upsert(
                {'school_id': school_id, 'config_json': config},
                on_conflict='school_id'
            ).execute()
        except Exception as e:
            print(f"[Supabase] save_config {school_id}: {e}")


def load_school_data(school_id):
    path = os.path.join(DATA_DIR, school_id, 'school_data.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    if _supabase:
        try:
            res = _supabase.table('school_data').select('data_json').eq('school_id', school_id).execute()
            if res.data:
                data = res.data[0]['data_json']
                os.makedirs(os.path.join(DATA_DIR, school_id), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                return data
        except Exception as e:
            print(f"[Supabase] load_data {school_id}: {e}")
    return {}


def save_school_data(school_id, data):
    path = os.path.join(DATA_DIR, school_id, 'school_data.json')
    os.makedirs(os.path.join(DATA_DIR, school_id), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if _supabase:
        try:
            _supabase.table('school_data').upsert(
                {'school_id': school_id, 'data_json': data},
                on_conflict='school_id'
            ).execute()
        except Exception as e:
            print(f"[Supabase] save_data {school_id}: {e}")


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
    save_school_data(school_id, school_data_template)

    if school_url and 'localhost' not in school_url and '127.0.0.1' not in school_url:
        register_scraper(school_id, school_url)

    return redirect(url_for('admin_panel'))


@app.route('/admin/delete/<school_id>', methods=['POST'])
@admin_required
def admin_delete_school(school_id):
    school_dir = os.path.join(DATA_DIR, school_id)
    if os.path.exists(school_dir):
        shutil.rmtree(school_dir)
    _cache.pop(school_id, None)
    job = scheduler.get_job(f'scrape_{school_id}')
    if job:
        job.remove()
    if _supabase:
        try:
            _supabase.table('school_data').delete().eq('school_id', school_id).execute()
            _supabase.table('schools').delete().eq('school_id', school_id).execute()
        except Exception as e:
            print(f"[Supabase] delete {school_id}: {e}")
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

def _run_scrape(school_id, school_url, force=False):
    _scrape_status[school_id] = 'running'
    try:
        from scraper import scrape_website
        result = scrape_website(school_url, school_id, force=force)
        if result is not None:
            _cache.pop(school_id, None)
            # Scraper ne file update ki — Supabase mein bhi sync karo
            _fresh_data = load_school_data(school_id)
            save_school_data(school_id, _fresh_data)
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

    t = threading.Thread(target=_run_scrape, args=(school_id, config['school_url']), kwargs={'force': True}, daemon=True)
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

  var history = [];
  var isBusy  = false;

  // ── CSS ──────────────────────────────────────────────────
  var style = document.createElement('style');
  style.innerHTML = `
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    #_cb_wrap * {{ box-sizing:border-box; margin:0; padding:0; font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif; }}

    #_cb_btn {{
      position:fixed; bottom:24px; right:24px; width:60px; height:60px;
      background:linear-gradient(135deg,#4f46e5,#7c3aed);
      border-radius:50%; border:none; cursor:pointer;
      display:flex; align-items:center; justify-content:center;
      box-shadow:0 4px 20px rgba(79,70,229,.5);
      z-index:2147483647; transition:transform .2s;
      animation:_cb_ring 2.5s ease-in-out infinite;
    }}
    @keyframes _cb_ring {{
      0%,100% {{ box-shadow:0 4px 20px rgba(79,70,229,.5),0 0 0 0 rgba(79,70,229,.3); }}
      50%     {{ box-shadow:0 4px 20px rgba(79,70,229,.5),0 0 0 10px rgba(79,70,229,0); }}
    }}
    #_cb_btn:hover {{ transform:scale(1.08); }}
    #_cb_btn svg {{ width:26px; height:26px; fill:#fff; }}
    #_cb_notif {{
      position:absolute; top:-2px; right:-2px; width:18px; height:18px;
      background:#ef4444; border-radius:50%; border:2px solid #fff;
      font-size:9px; font-weight:700; color:#fff;
      display:flex; align-items:center; justify-content:center;
    }}

    #_cb_box {{
      position:fixed; bottom:88px; right:24px; width:370px;
      height:min(570px, calc(100vh - 110px));
      background:#fff; border-radius:20px;
      box-shadow:0 24px 60px rgba(0,0,0,.18),0 4px 16px rgba(0,0,0,.07);
      display:none; flex-direction:column;
      z-index:2147483646; overflow:hidden; transform-origin:bottom right;
    }}
    #_cb_box.open {{ display:flex; animation:_cb_open .3s cubic-bezier(.34,1.56,.64,1); }}
    @keyframes _cb_open {{
      from {{ opacity:0; transform:scale(.88) translateY(16px); }}
      to   {{ opacity:1; transform:scale(1) translateY(0); }}
    }}

    #_cb_head {{
      background:linear-gradient(150deg,#0f0c29,#302b63 60%,#24243e);
      flex-shrink:0; position:relative; overflow:hidden;
    }}
    #_cb_head::before {{
      content:''; position:absolute; top:-50px; right:-30px;
      width:130px; height:130px; background:rgba(255,255,255,.04); border-radius:50%;
    }}
    #_cb_head::after {{
      content:''; position:absolute; bottom:-40px; left:-20px;
      width:110px; height:110px; background:rgba(255,255,255,.03); border-radius:50%;
    }}
    #_cb_hm {{
      padding:16px 18px; display:flex; align-items:center; gap:13px;
      position:relative; z-index:1;
    }}
    #_cb_av {{
      width:46px; height:46px; border-radius:13px;
      background:linear-gradient(135deg,#6366f1,#8b5cf6);
      display:flex; align-items:center; justify-content:center;
      font-size:22px; flex-shrink:0; box-shadow:0 4px 14px rgba(99,102,241,.45);
    }}
    #_cb_hi {{ flex:1; min-width:0; }}
    #_cb_hn {{
      font-size:15px; font-weight:700; color:#fff;
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis; letter-spacing:-.2px;
    }}
    #_cb_hs {{ display:flex; align-items:center; gap:6px; margin-top:3px; }}
    #_cb_hd {{
      width:7px; height:7px; background:#4ade80; border-radius:50%;
      animation:_cb_blink 2s ease-in-out infinite;
    }}
    @keyframes _cb_blink {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}
    #_cb_ht {{ font-size:11px; color:rgba(255,255,255,.6); font-weight:500; }}
    #_cb_x {{
      background:rgba(255,255,255,.1); border:none; color:rgba(255,255,255,.85);
      width:32px; height:32px; border-radius:9px; cursor:pointer;
      display:flex; align-items:center; justify-content:center;
      font-size:13px; transition:background .15s; flex-shrink:0; position:relative; z-index:1;
    }}
    #_cb_x:hover {{ background:rgba(255,255,255,.2); }}

    #_cb_sts {{
      display:flex; border-top:1px solid rgba(255,255,255,.07);
      position:relative; z-index:1; flex-shrink:0;
    }}
    .cb_s {{
      flex:1; padding:9px 0; text-align:center;
      border-right:1px solid rgba(255,255,255,.07);
    }}
    .cb_s:last-child {{ border-right:none; }}
    .cb_sv {{ font-size:12px; font-weight:700; color:rgba(255,255,255,.9); }}
    .cb_sl {{ font-size:10px; color:rgba(255,255,255,.4); font-weight:500; margin-top:1px; }}

    #_cb_msgs {{
      flex:1; overflow-y:auto; padding:14px 0 10px;
      display:flex; flex-direction:column; gap:8px;
      background:#f8fafc; scroll-behavior:smooth;
    }}
    #_cb_msgs::-webkit-scrollbar {{ width:3px; }}
    #_cb_msgs::-webkit-scrollbar-thumb {{ background:#cbd5e1; border-radius:10px; }}
    #_cb_msgs::-webkit-scrollbar-track {{ background:transparent; }}

    ._cb_g {{
      display:flex; flex-direction:column; gap:3px;
      padding:0 16px;
    }}
    ._cb_g.user {{ align-items:flex-end; }}
    ._cb_g.bot  {{ align-items:flex-start; }}
    ._cb_br {{ display:flex; align-items:flex-end; gap:8px; }}
    ._cb_ai {{
      width:28px; height:28px; border-radius:8px; flex-shrink:0;
      background:linear-gradient(135deg,#6366f1,#8b5cf6);
      display:flex; align-items:center; justify-content:center;
    }}
    ._cb_b {{
      max-width:82%; padding:10px 14px; border-radius:16px;
      font-size:13.5px; line-height:1.6; word-break:break-word;
    }}
    ._cb_b.bot {{
      background:#fff; color:#1e293b; border-bottom-left-radius:4px;
      box-shadow:0 1px 4px rgba(0,0,0,.08),0 0 0 1px rgba(0,0,0,.04);
    }}
    ._cb_b.user {{
      background:linear-gradient(135deg,#4f46e5,#6d28d9);
      color:#fff; border-bottom-right-radius:4px;
    }}
    ._cb_t {{ font-size:10px; color:#94a3b8; padding:0 2px; margin-top:3px; }}
    ._cb_g.user ._cb_t {{ text-align:right; }}

    #_cb_tw {{ display:flex; align-items:flex-end; gap:8px; padding:0 16px; }}
    ._cb_tb {{
      background:#fff; border-radius:16px; border-bottom-left-radius:4px;
      padding:12px 16px;
      box-shadow:0 1px 4px rgba(0,0,0,.08),0 0 0 1px rgba(0,0,0,.04);
      display:flex; gap:5px; align-items:center;
    }}
    ._cb_d {{
      width:7px; height:7px; background:#94a3b8; border-radius:50%;
      animation:_cb_bounce 1.4s ease-in-out infinite;
    }}
    ._cb_d:nth-child(2) {{ animation-delay:.2s; }}
    ._cb_d:nth-child(3) {{ animation-delay:.4s; }}
    @keyframes _cb_bounce {{ 0%,60%,100%{{transform:translateY(0)}} 30%{{transform:translateY(-6px)}} }}

    #_cb_chips {{
      padding:8px 16px 0; display:flex; flex-wrap:wrap; gap:7px;
      background:#f8fafc; flex-shrink:0;
    }}
    ._cb_chip {{
      background:#fff; border:1.5px solid #e2e8f0; color:#4f46e5;
      border-radius:20px; padding:6px 13px; font-size:12px; font-weight:600;
      cursor:pointer; transition:all .15s; font-family:inherit; white-space:nowrap;
    }}
    ._cb_chip:hover {{ background:#eef2ff; border-color:#a5b4fc; transform:translateY(-1px); }}

    #_cb_foot {{
      padding:12px 18px; background:#fff; border-top:1px solid #f1f5f9;
      display:flex; gap:9px; align-items:center; flex-shrink:0;
    }}
    #_cb_iw {{
      flex:1; background:#f8fafc; border:1.5px solid #e2e8f0;
      border-radius:14px; display:flex; align-items:center;
      padding:0 12px; transition:border-color .2s, box-shadow .2s;
    }}
    #_cb_iw:focus-within {{
      border-color:#6366f1; background:#fff; box-shadow:0 0 0 3px rgba(99,102,241,.1);
    }}
    #_cb_in {{
      flex:1; border:none; background:transparent; padding:11px 0;
      font-size:13.5px; font-family:inherit; color:#1e293b; outline:none;
    }}
    #_cb_in::placeholder {{ color:#cbd5e1; }}
    #_cb_go {{
      width:40px; height:40px; flex-shrink:0;
      background:linear-gradient(135deg,#4f46e5,#7c3aed);
      border:none; border-radius:12px; cursor:pointer;
      display:flex; align-items:center; justify-content:center;
      box-shadow:0 4px 12px rgba(79,70,229,.35); transition:all .2s;
    }}
    #_cb_go:hover {{ transform:scale(1.06); box-shadow:0 6px 16px rgba(79,70,229,.45); }}
    #_cb_go:disabled {{ opacity:.4; cursor:not-allowed; transform:none; box-shadow:none; }}
    #_cb_go svg {{ width:17px; height:17px; fill:#fff; }}

    #_cb_brand {{ text-align:center; padding:6px; font-size:10.5px; color:#cbd5e1; background:#fff; }}

    ._cb_pl {{
      display:inline-flex; align-items:center; gap:5px;
      margin-top:8px; padding:7px 13px; background:#eef2ff; color:#4f46e5;
      border-radius:8px; font-size:12px; font-weight:600;
      text-decoration:none; border:1px solid #c7d2fe; transition:background .15s;
    }}
    ._cb_pl:hover {{ background:#e0e7ff; }}

    @media (max-width:430px) {{
      #_cb_box {{ width:calc(100vw - 16px); right:8px; bottom:88px; height:72vh; }}
    }}
  `;
  document.head.appendChild(style);

  // ── HTML ─────────────────────────────────────────────────
  var BOT_SVG = '<svg width="14" height="14" viewBox="0 0 32 32" fill="none"><circle cx="16" cy="12" r="5" fill="#fff"/><ellipse cx="16" cy="23" rx="8" ry="4.5" fill="#fff"/></svg>';

  var wrap = document.createElement('div');
  wrap.id  = '_cb_wrap';
  wrap.innerHTML =
    '<button id="_cb_btn" aria-label="Chat">' +
      '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>' +
      '<div id="_cb_notif">1</div>' +
    '</button>' +
    '<div id="_cb_box" role="dialog" aria-label="Chat assistant">' +
      '<div id="_cb_head">' +
        '<div id="_cb_hm">' +
          '<div id="_cb_av"><svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M12 2c.28 0 .52.18.6.45l1.81 5.74 5.74 1.81c.27.08.45.33.45.6s-.18.52-.45.6l-5.74 1.81-1.81 5.74c-.08.27-.33.45-.6.45s-.52-.18-.6-.45l-1.81-5.74L3.45 12.6C3.18 12.52 3 12.27 3 12s.18-.52.45-.6l5.74-1.81 1.81-5.74c.08-.27.32-.45.6-.45z" fill="white" opacity=".95"/><path d="M19 15c.18 0 .35.11.41.28l.77 2.27 2.27.77c.17.06.28.22.28.41s-.11.35-.28.41l-2.27.77-.77 2.27c-.06.17-.23.28-.41.28s-.35-.11-.41-.28l-.77-2.27-2.27-.77c-.17-.06-.28-.22-.28-.41s.11-.35.28-.41l2.27-.77.77-2.27c.06-.17.23-.28.41-.28z" fill="white" opacity=".75"/></svg></div>' +
          '<div id="_cb_hi">' +
            '<div id="_cb_hn">' + SCHOOL_NAME + '</div>' +
            '<div id="_cb_hs"><div id="_cb_hd"></div><span id="_cb_ht">AI Assistant &bull; Online</span></div>' +
          '</div>' +
          '<button id="_cb_x" aria-label="Close">&#10005;</button>' +
        '</div>' +
        '<div id="_cb_sts">' +
          '<div class="cb_s"><div class="cb_sv">~1s</div><div class="cb_sl">Response</div></div>' +
          '<div class="cb_s"><div class="cb_sv">24/7</div><div class="cb_sl">Available</div></div>' +
          '<div class="cb_s"><div class="cb_sv">AI</div><div class="cb_sl">Powered</div></div>' +
        '</div>' +
      '</div>' +
      '<div id="_cb_msgs"></div>' +
      '<div id="_cb_chips"></div>' +
      '<div id="_cb_foot">' +
        '<div id="_cb_iw">' +
          '<input id="_cb_in" type="text" placeholder="Ask me anything..." autocomplete="off">' +
        '</div>' +
        '<button id="_cb_go" aria-label="Send"><svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg></button>' +
      '</div>' +
      '<div id="_cb_brand">Powered by <strong>ChatBot AI</strong></div>' +
    '</div>';
  document.body.appendChild(wrap);

  // ── Elements ─────────────────────────────────────────────
  var btn   = document.getElementById('_cb_btn');
  var box   = document.getElementById('_cb_box');
  var xBtn  = document.getElementById('_cb_x');
  var msgs  = document.getElementById('_cb_msgs');
  var inp   = document.getElementById('_cb_in');
  var go    = document.getElementById('_cb_go');
  var chips = document.getElementById('_cb_chips');
  var notif = document.getElementById('_cb_notif');

  // ── Helpers ──────────────────────────────────────────────
  function timeNow() {{
    return new Date().toLocaleTimeString([], {{hour:'2-digit', minute:'2-digit'}});
  }}

  function addMsg(text, who) {{
    var grp = document.createElement('div');
    grp.className = '_cb_g ' + who;

    if (who === 'bot') {{
      var row = document.createElement('div');
      row.className = '_cb_br';
      var av = document.createElement('div');
      av.className = '_cb_ai';
      av.innerHTML = BOT_SVG;
      var bub = document.createElement('div');
      bub.className = '_cb_b bot';
      bub.innerHTML = text.replace(/\\n/g, '<br>');
      row.appendChild(av);
      row.appendChild(bub);
      grp.appendChild(row);
    }} else {{
      var bub = document.createElement('div');
      bub.className = '_cb_b user';
      bub.innerHTML = text.replace(/\\n/g, '<br>');
      grp.appendChild(bub);
    }}

    var ts = document.createElement('div');
    ts.className = '_cb_t';
    ts.textContent = timeNow();
    grp.appendChild(ts);

    msgs.appendChild(grp);
    msgs.scrollTop = msgs.scrollHeight;
    return grp;
  }}

  function showTyping() {{
    var tw = document.createElement('div');
    tw.id  = '_cb_tw';
    tw.innerHTML =
      '<div class="_cb_ai">' + BOT_SVG + '</div>' +
      '<div class="_cb_tb"><div class="_cb_d"></div><div class="_cb_d"></div><div class="_cb_d"></div></div>';
    msgs.appendChild(tw);
    msgs.scrollTop = msgs.scrollHeight;
    return tw;
  }}

  // ── Quick reply chips ────────────────────────────────────
  var CHIPS = ['What services do you offer?', 'How to contact you?', 'Fee information', 'Tell me about you'];

  function renderChips() {{
    chips.innerHTML = '';
    chips.style.display = 'flex';
    CHIPS.forEach(function(q) {{
      var c = document.createElement('button');
      c.className = '_cb_chip';
      c.textContent = q;
      c.onclick = function() {{
        chips.style.display = 'none';
        chips.innerHTML = '';
        inp.value = q;
        send();
      }};
      chips.appendChild(c);
    }});
  }}

  // ── Open / Close ─────────────────────────────────────────
  function openChat() {{
    box.classList.add('open');
    btn.style.display = 'none';
    inp.focus();
    if (msgs.children.length === 0) {{
      addMsg("Hello! &#128075; I'm the AI Assistant for <strong>" + SCHOOL_NAME + "</strong>.<br>How can I help you today?", 'bot');
      renderChips();
    }}
  }}

  function closeChat() {{
    box.classList.remove('open');
    btn.style.display = 'flex';
    history = [];
    msgs.innerHTML = '';
    chips.style.display = 'none';
    chips.innerHTML = '';
  }}

  btn.addEventListener('click', openChat);
  xBtn.addEventListener('click', closeChat);

  // ── Send ─────────────────────────────────────────────────
  function send() {{
    var text = inp.value.trim();
    if (!text || isBusy) return;

    chips.style.display = 'none';
    addMsg(text, 'user');
    inp.value = '';
    isBusy = true;
    go.disabled  = true;
    inp.disabled = true;
    if (notif) notif.style.display = 'none';

    var typingEl    = showTyping();
    var prevHistory = history.slice();

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

      var lower = text.toLowerCase();
      for (var i = 0; i < PAGE_LINKS.length; i++) {{
        var pl = PAGE_LINKS[i];
        if (pl.url && pl.keywords.some(function(k) {{ return lower.indexOf(k) !== -1; }})) {{
          var bub = msgEl.querySelector('._cb_b');
          if (bub) {{
            var a = document.createElement('a');
            a.className = '_cb_pl';
            a.href = pl.url; a.target = '_blank'; a.rel = 'noopener';
            a.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg> ' + pl.label + ' &rarr;';
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
      addMsg('Connection error. Please call ' + (PHONE || 'the office') + ' for assistance.', 'bot');
    }})
    .finally(function() {{
      isBusy       = false;
      go.disabled  = false;
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

def _startup_restore_from_supabase():
    """Hugging Face ya koi bhi restart pe: Supabase se local files restore karo"""
    if not _supabase:
        return
    try:
        print("[Supabase] Startup restore shuru...")
        configs = _supabase.table('schools').select('school_id, config_json').execute()
        for row in (configs.data or []):
            sid, cfg = row['school_id'], row['config_json']
            school_dir = os.path.join(DATA_DIR, sid)
            os.makedirs(school_dir, exist_ok=True)
            cfg_path = os.path.join(school_dir, 'config.json')
            if not os.path.exists(cfg_path):
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)

        datas = _supabase.table('school_data').select('school_id, data_json').execute()
        for row in (datas.data or []):
            sid, dat = row['school_id'], row['data_json']
            school_dir = os.path.join(DATA_DIR, sid)
            os.makedirs(school_dir, exist_ok=True)
            dat_path = os.path.join(school_dir, 'school_data.json')
            if not os.path.exists(dat_path):
                with open(dat_path, 'w', encoding='utf-8') as f:
                    json.dump(dat, f, ensure_ascii=False, indent=2)

        print(f"[Supabase] Restore complete — {len(configs.data or [])} schools")
    except Exception as e:
        print(f"[Supabase] Startup restore failed: {e}")


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

_startup_restore_from_supabase()
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
