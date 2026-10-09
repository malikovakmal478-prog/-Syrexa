import os, json, time, hmac, hashlib, urllib.parse, sqlite3, threading, logging
from functools import wraps
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string, abort, session, redirect
import requests

APP_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv('DB_PATH', str(APP_DIR / 'syrexa.db'))
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip().isdigit()}
BOT_USERNAME = os.getenv('BOT_USERNAME', '').strip().lstrip('@')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '').strip()
SESSION_SECRET = os.getenv('SESSION_SECRET', '').strip() or (BOT_TOKEN + ':syrexa-session' if BOT_TOKEN else 'change-this-session-secret-before-deploy')
WEBAPP_URL = os.getenv('WEBAPP_URL', '').strip().rstrip('/')
PUBLIC_URL = os.getenv('RENDER_EXTERNAL_URL', '').strip().rstrip('/')
CHANNELS = [x.strip() for x in os.getenv('REQUIRED_CHANNELS', '').split(',') if x.strip()]
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
log = logging.getLogger('syrexa')
app = Flask(__name__)
app.config.update(SECRET_KEY=SESSION_SECRET, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=bool(os.getenv('RENDER')))
app.config['JSON_AS_ASCII'] = False

# --- database ---
def db():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con

def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT DEFAULT '', name TEXT DEFAULT '', lang TEXT DEFAULT 'uz', balance INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, slug TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT 'game', icon TEXT NOT NULL DEFAULT '', banner TEXT NOT NULL DEFAULT '', player_field TEXT NOT NULL DEFAULT 'Player ID', description TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE, name TEXT NOT NULL, price INTEGER NOT NULL DEFAULT 0, image TEXT NOT NULL DEFAULT '', badge TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, sort INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS cards(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL DEFAULT '', number TEXT NOT NULL, holder TEXT NOT NULL DEFAULT '', bank TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), game_id INTEGER, product_id INTEGER, game_name TEXT NOT NULL, product_name TEXT NOT NULL, price INTEGER NOT NULL, player_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'new', created INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS topups(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), amount INTEGER NOT NULL, card_id INTEGER, status TEXT NOT NULL DEFAULT 'new', created INTEGER NOT NULL, proof TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS promos(code TEXT PRIMARY KEY, amount INTEGER NOT NULL, uses_left INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS promo_uses(code TEXT NOT NULL, user_id INTEGER NOT NULL, PRIMARY KEY(code,user_id));
        CREATE TABLE IF NOT EXISTS transactions(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, kind TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', admin_id INTEGER, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS admins(user_id INTEGER PRIMARY KEY, added_by INTEGER, created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS broadcasts(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, created INTEGER NOT NULL, sent INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0);
        ''')
        defaults = {
          'brand':'SYREXA','hero_title':'O‘yinlaringiz uchun tezkor to‘ldirish','hero_subtitle':'PUBG UC • Free Fire Diamonds • Mobile Legends va boshqalar',
          'hero_image':'','support_url':'https://t.me/','channel_url':'','maintenance':'0','uzcard':'','currency':'so‘m',
          'order_notice':'Buyurtma qabul qilindi. Admin tekshirib, bajaradi.','topup_notice':'To‘lov cheki tekshirilgach balans to‘ldiriladi.','required_channels':''
        }
        for k,v in defaults.items(): c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',(k,v))
        if c.execute('SELECT COUNT(*) FROM games').fetchone()[0] == 0:
            seed = [
              ('PUBG Mobile','pubg','game','https://images.unsplash.com/photo-1542751371-adc38448a05e?w=500','https://images.unsplash.com/photo-1542751371-adc38448a05e?w=1200','Player ID','PUBG Mobile UC',1,1),
              ('Free Fire','free-fire','game','https://images.unsplash.com/photo-1511512578047-dfb367046420?w=500','https://images.unsplash.com/photo-1511512578047-dfb367046420?w=1200','Player ID','Free Fire olmoslari',1,2),
              ('Mobile Legends','mobile-legends','game','https://images.unsplash.com/photo-1511512578047-dfb367046420?w=500','https://images.unsplash.com/photo-1511512578047-dfb367046420?w=1200','User ID + Zone ID','Mobile Legends Diamonds',1,3),
              ('Standoff 2','standoff-2','game','https://images.unsplash.com/photo-1542751371-adc38448a05e?w=500','https://images.unsplash.com/photo-1542751371-adc38448a05e?w=1200','Player ID','Gold va paketlar',1,4),
              ('Roblox','roblox','game','https://images.unsplash.com/photo-1614294149010-950b698f72c0?w=500','https://images.unsplash.com/photo-1614294149010-950b698f72c0?w=1200','Username / ID','Robux',1,5)]
            c.executemany('INSERT INTO games(name,slug,category,icon,banner,player_field,description,active,sort) VALUES(?,?,?,?,?,?,?,?,?)',seed)
            packs = {'PUBG Mobile':[('60 UC',14000),('325 UC',69000),('660 UC',135000),('1800 UC',345000),('3850 UC',690000),('8100 UC',1380000)], 'Free Fire':[('100 Diamonds',14000),('310 Diamonds',39000),('520 Diamonds',65000),('1060 Diamonds',129000),('2180 Diamonds',259000)], 'Mobile Legends':[('86 Diamonds',16000),('172 Diamonds',32000),('257 Diamonds',48000),('706 Diamonds',125000)], 'Standoff 2':[('Gold paket S',15000),('Gold paket M',45000),('Gold paket L',120000)], 'Roblox':[('80 Robux',19000),('400 Robux',79000),('800 Robux',149000)]}
            for gn, items in packs.items():
                gid = c.execute('SELECT id FROM games WHERE name=?',(gn,)).fetchone()[0]
                c.executemany('INSERT INTO products(game_id,name,price,sort) VALUES(?,?,?,?)',[(gid,n,p,i) for i,(n,p) in enumerate(items,1)])
        for aid in ADMIN_IDS: c.execute('INSERT OR IGNORE INTO admins(user_id,added_by,created) VALUES(?,?,?)',(aid,aid,int(time.time())))

init_db()

def setting(key, default=''):
    with db() as c:
        r = c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    return r['value'] if r else default

def set_setting(key, value):
    with db() as c: c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))

def is_admin(uid):
    if not uid: return False
    with db() as c: r=c.execute('SELECT 1 FROM admins WHERE user_id=?',(int(uid),)).fetchone()
    return int(uid) in ADMIN_IDS or bool(r)

def telegram_user(init_data):
    if not BOT_TOKEN or not init_data: return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received = parsed.pop('hash', None)
        if not received: return None
        check = '\n'.join(f'{k}={v}' for k,v in sorted(parsed.items()))
        secret = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received): return None
        auth_date = int(parsed.get('auth_date','0'))
        if abs(int(time.time()) - auth_date) > 86400: return None
        return json.loads(parsed.get('user','{}'))
    except Exception:
        return None

def current_user():
    raw = request.headers.get('X-Telegram-Init-Data') or (request.get_json(silent=True) or {}).get('_initData') or request.args.get('_initData') or ''
    u = telegram_user(raw)
    if not u: return None
    uid = int(u.get('id',0))
    if not uid: return None
    name = (' '.join([u.get('first_name',''),u.get('last_name','')])).strip()
    with db() as c:
        c.execute('INSERT INTO users(id,username,name,created) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,name=excluded.name',(uid,u.get('username',''),name,int(time.time())))
        row = c.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
    return dict(row)

def check_subscription(uid):
    missing=[]
    dynamic = [x.strip() for x in setting('required_channels','').split(',') if x.strip()]
    channels = list(dict.fromkeys(CHANNELS + dynamic))
    for channel in channels:
        res=bot_api('getChatMember', {'chat_id': channel, 'user_id': int(uid)})
        member=(res.get('result') or {}) if res.get('ok') else {}
        status=member.get('status','left')
        if status not in ('creator','administrator','member') and not (status=='restricted' and member.get('is_member')):
            link=('https://t.me/'+channel.lstrip('@')) if channel.startswith('@') else setting('channel_url','')
            missing.append({'channel':channel,'url':link})
    return missing

def need_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u=current_user()
        if not u: return jsonify(error='telegram_auth_required', message='Mini Appni Telegram ichida oching.'),401
        if u['banned']: return jsonify(error='banned'),403
        if setting('maintenance','0')=='1' and not is_admin(u['id']): return jsonify(error='maintenance'),503
        if CHANNELS and not is_admin(u['id']):
            missing=check_subscription(u['id'])
            if missing: return jsonify(error='required_subscription',channels=missing),403
        return fn(u,*args,**kwargs)
    return wrapper

def need_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # Supports both Telegram Mini App admins and the password-protected browser dashboard.
        if session.get('browser_admin') is True:
            return fn({'id': int(session.get('admin_id', -1)), 'name': 'Browser admin'}, *args, **kwargs)
        u=current_user()
        if not u: return jsonify(error='telegram_auth_required', message='Admin panelni Telegram ichida oching yoki /admin orqali kiring.'),401
        if not is_admin(u['id']): return jsonify(error='forbidden'),403
        return fn(u,*args,**kwargs)
    return wrapper

def rows(sql, args=()):
    with db() as c: return [dict(x) for x in c.execute(sql,args).fetchall()]
def row(sql,args=()):
    with db() as c:
        x=c.execute(sql,args).fetchone(); return dict(x) if x else None

def money(v): return f"{int(v):,}".replace(',',' ')

def bot_api(method, data=None):
    if not BOT_TOKEN: return {'ok':False,'description':'BOT_TOKEN sozlanmagan'}
    try:
        r=requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/{method}',json=data or {},timeout=15)
        return r.json()
    except Exception as e:
        log.warning('Telegram API %s failed: %s',method,e); return {'ok':False,'description':str(e)}

def notify_admins(text, markup=None):
    for aid in set(ADMIN_IDS)|{x['user_id'] for x in rows('SELECT user_id FROM admins')}:
        payload={'chat_id':aid,'text':text,'parse_mode':'HTML'}
        if markup: payload['reply_markup']=markup
        bot_api('sendMessage',payload)

INDEX_HTML = r"""<!doctype html><html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no"><meta name="theme-color" content="#090d18"><title>SYREXA — Game Store</title><script src="https://telegram.org/js/telegram-web-app.js"></script><style>
:root{--bg:#090d18;--panel:#11182a;--panel2:#18223a;--text:#f4f7ff;--muted:#94a3bd;--line:#28334d;--accent:#8b5cf6;--accent2:#00d4ff;--green:#24d18b;--red:#ff647c}*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 50% -20%,#282050 0,transparent 48%),var(--bg);color:var(--text);font:15px/1.4 system-ui,-apple-system,Segoe UI,sans-serif}button,input,select,textarea{font:inherit}button{cursor:pointer;border:0;color:white}.wrap{max-width:780px;margin:auto;padding:16px 14px 92px}.top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:18px}.brand{display:flex;gap:10px;align-items:center;font-weight:900;letter-spacing:1px;font-size:19px}.logo{width:42px;height:42px;border-radius:14px;background:linear-gradient(135deg,#8b5cf6,#00d4ff);display:grid;place-items:center;font-weight:1000;font-size:20px;box-shadow:0 5px 22px #784bff66}.pill{border:1px solid var(--line);background:#121a2b;border-radius:30px;padding:9px 12px;color:var(--text)}.hero{position:relative;overflow:hidden;border-radius:24px;padding:23px 20px;min-height:168px;background:linear-gradient(120deg,#29164d,#152b54 65%,#0c5262);border:1px solid #51417b;margin-bottom:20px}.hero img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.23}.hero>div{position:relative;z-index:1;max-width:85%}.eyebrow{color:#9beaff;text-transform:uppercase;font-size:11px;letter-spacing:2px;font-weight:800}.hero h1{font-size:25px;line-height:1.13;margin:9px 0}.hero p{margin:0;color:#d4d9ef;font-size:13px}.sectionhead{display:flex;justify-content:space-between;align-items:center;margin:18px 0 12px}.sectionhead h2{font-size:18px;margin:0}.muted{color:var(--muted);font-size:12px}.games{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.game{background:var(--panel);border:1px solid var(--line);border-radius:17px;overflow:hidden;text-align:left;padding:0;transition:.15s}.game:active{transform:scale(.98)}.game img{display:block;width:100%;height:103px;object-fit:cover;background:#202b45}.game .gbody{padding:11px}.game b{display:block;font-size:14px}.game small{color:var(--muted);font-size:11px}.products{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.product{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:12px;display:flex;flex-direction:column;gap:8px;min-width:0}.pimg{width:48px;height:48px;border-radius:13px;object-fit:cover;background:#26304a}.product b{font-size:14px}.price{font-weight:900;color:#9b8cff}.btn{border-radius:12px;padding:11px 12px;background:linear-gradient(100deg,#7c3aed,#536dfe);font-weight:800;width:100%}.btn.alt{background:#202b42;border:1px solid var(--line)}.btn.good{background:linear-gradient(100deg,#0e9f6e,#12b981)}.btn.danger{background:#622436}.btn:disabled{opacity:.45;cursor:not-allowed}.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:15px;margin:12px 0}.balance{display:flex;align-items:center;justify-content:space-between;background:linear-gradient(120deg,#1b2140,#132b3b);border:1px solid #343d64;padding:15px;border-radius:18px;margin-bottom:16px}.balance strong{font-size:22px}.tabs{position:fixed;z-index:5;bottom:0;left:0;right:0;background:#0d1322f2;border-top:1px solid var(--line);display:flex;justify-content:center;backdrop-filter:blur(18px)}.tabrow{width:100%;max-width:780px;display:grid;grid-template-columns:repeat(4,1fr);padding:8px 5px max(8px,env(safe-area-inset-bottom))}.tab{background:transparent;color:var(--muted);font-size:11px;padding:7px 2px;border-radius:10px}.tab.active{color:#c6b5ff;background:#262044}.tab span{display:block;font-size:19px;margin-bottom:2px}.hidden{display:none!important}.row{display:flex;gap:8px;align-items:center}.row>*{min-width:0;flex:1}input,select,textarea{width:100%;background:#0b1120;color:var(--text);border:1px solid var(--line);border-radius:11px;padding:12px;outline:none;margin:5px 0 10px}label{font-size:12px;color:var(--muted)}.modal{position:fixed;z-index:10;inset:0;background:#02040bd9;display:flex;align-items:flex-end;justify-content:center;padding:12px}.sheet{width:100%;max-width:620px;max-height:88vh;overflow:auto;background:#11182a;border:1px solid var(--line);border-radius:24px 24px 16px 16px;padding:18px}.sheet h2{margin:0 0 12px}.close{float:right;background:#273149;border-radius:10px;padding:7px 10px}.listitem{padding:11px 0;border-bottom:1px solid var(--line)}.badge{font-size:10px;border-radius:8px;background:#3b285f;color:#d9c7ff;padding:4px 7px;display:inline-block}.adminnav{display:flex;gap:7px;overflow:auto;margin-bottom:12px}.adminnav button{white-space:nowrap;background:#1b2540;padding:9px 12px;border-radius:10px}.adminnav button.on{background:#6844ce}.statgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}.stat{background:#172138;border:1px solid var(--line);border-radius:14px;padding:13px}.stat small{display:block;color:var(--muted)}.stat strong{font-size:20px}.notice{padding:12px;background:#28213e;border:1px solid #44356a;border-radius:12px;color:#ded4ff;margin:10px 0}.empty{text-align:center;padding:35px 12px;color:var(--muted)}.lang{font-size:12px}.thumb{width:38px;height:38px;object-fit:cover;border-radius:9px;vertical-align:middle;margin-right:7px}@media(min-width:600px){.games{grid-template-columns:repeat(3,1fr)}.products{grid-template-columns:repeat(3,1fr)}.hero h1{font-size:30px}}

/* SYREXA premium refresh */
.wrap{max-width:1120px;padding:22px 18px 110px}.tabrow{max-width:1120px}.hero{min-height:205px;padding:30px 28px;border-radius:27px;box-shadow:0 18px 55px #0003}.hero h1{font-size:clamp(25px,4vw,38px);max-width:720px}.hero p{font-size:14px;max-width:720px}.games{grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}.game{box-shadow:0 8px 26px #0002}.game img{height:140px}.game:hover{border-color:#8065ef;transform:translateY(-2px)}.products{grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px}.product{padding:15px;box-shadow:0 8px 24px #0002}.sectionhead{margin-top:24px}.adminnav{padding:4px 0 10px;scrollbar-width:thin}.adminnav button{border:1px solid #303b59;transition:.15s}.adminnav button:hover,.adminnav button.on{border-color:#9a83ff;box-shadow:0 4px 18px #7957f533}.statgrid{grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px}.stat{min-height:104px;background:linear-gradient(145deg,#151f36,#101726);border:1px solid #303b59;border-radius:18px;padding:16px}.stat strong{display:block;margin-top:8px;font-size:clamp(18px,2vw,25px);overflow-wrap:anywhere}.card{box-shadow:0 8px 25px #0002}.btn{transition:filter .15s,transform .15s}.btn:hover{filter:brightness(1.12)}.btn:active{transform:scale(.99)}input:focus,select:focus,textarea:focus{border-color:#8b5cf6;box-shadow:0 0 0 3px #8b5cf622}.thumb{width:62px;height:62px;object-fit:cover;border-radius:14px;background:#202b45}.admin-logout{box-shadow:0 6px 22px #0005}@media(min-width:900px){.wrap{padding-top:30px}.balance{padding:20px 24px}.game img{height:155px}}@media(max-width:520px){.wrap{padding:12px 11px 95px}.hero{padding:22px 18px;min-height:175px}.game img{height:108px}.games{grid-template-columns:repeat(2,minmax(0,1fr))}.products{grid-template-columns:repeat(2,minmax(0,1fr))}.statgrid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style></head><body><div class="wrap"><header class="top"><div class="brand"><div class="logo">S</div><div><div>SYREXA</div><div class="muted" style="letter-spacing:0;font-weight:500">GAME STORE</div></div></div><button class="pill lang" onclick="toggleLang()">UZ / RU</button></header><main id="app"><div class="empty">SYREXA yuklanmoqda…</div></main></div><nav class="tabs"><div class="tabrow"><button class="tab active" data-tab="home" onclick="go('home')"><span>⌂</span><i data-i="home">Bosh sahifa</i></button><button class="tab" data-tab="history" onclick="go('history')"><span>▤</span><i data-i="history">Buyurtmalar</i></button><button class="tab" data-tab="topup" onclick="go('topup')"><span>＋</span><i data-i="topup">Balans</i></button><button class="tab" data-tab="profile" onclick="go('profile')"><span>◉</span><i data-i="profile">Profil</i></button></div></nav><div id="modal" class="modal hidden" onclick="if(event.target.id==='modal')closeModal()"><div class="sheet" id="sheet"></div></div><script>
const browserAdmin={{ browser_admin|tojson }};
const tg=window.Telegram?.WebApp; if(tg){tg.ready();tg.expand();try{tg.setHeaderColor('#090d18');tg.setBackgroundColor('#090d18')}catch(e){}}
const initData=tg?.initData||'';let D=null,tab='home',lang='uz',selectedGame=null,adminTab='overview',adminData=null;const $=s=>document.querySelector(s);const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const money=n=>Number(n||0).toLocaleString('ru-RU')+' so‘m';const tr=(uz,ru)=>lang==='uz'?uz:ru;
async function api(path,body,method){const opt={method:method||(body?'POST':'GET'),headers:{'X-Telegram-Init-Data':initData,'Content-Type':'application/json'}};if(body)opt.body=JSON.stringify(body);const r=await fetch(path,opt);let d={};try{d=await r.json()}catch(e){}if(!r.ok)throw Object.assign(new Error(d.message||d.error||'Xatolik'),{data:d,status:r.status});return d}
function toast(s){if(tg?.showAlert)tg.showAlert(s);else alert(s)}function openModal(html){$('#sheet').innerHTML=html;$('#modal').classList.remove('hidden')}function closeModal(){$('#modal').classList.add('hidden')}
function nav(){document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab));}
async function boot(){if(!initData && browserAdmin){D={admin:true,user:{id:'browser-admin',name:'Admin',username:'admin',balance:0,lang:'uz'},settings:{},games:[]};lang='uz';tab='admin';await loadAdmin();return}if(!initData){$('#app').innerHTML='<div class="notice">Mini Appni Telegram ichidan, botdagi “Do‘konni ochish” tugmasi orqali oching. Telegram autentifikatsiyasi xavfsizlik uchun majburiy.</div>';return}try{D=await api('/api/init');lang=D.user.lang||'uz';render()}catch(e){if(e.data?.error==='required_subscription'){const cs=e.data.channels||[];$('#app').innerHTML='<div class="card" style="text-align:center;padding:24px"><div style="font-size:42px">🔒</div><h2>Majburiy obuna / Подписка</h2><p class="muted">Do‘kondan foydalanish uchun kanallarga obuna bo‘ling.</p>'+cs.map(c=>c.url?'<a class="btn" style="display:block;text-decoration:none;margin:8px 0" href="'+esc(c.url)+'" target="_blank">📢 '+esc(c.channel)+' ga obuna bo‘lish</a>':'<div class="notice">'+esc(c.channel)+' kanaliga obuna bo‘ling</div>').join('')+'<button class="btn" onclick="boot()">Tekshirish / Проверить</button></div>';return}$('#app').innerHTML='<div class="empty">'+esc(e.message)+'<br><small>Bot tokeni va Web App URL sozlamalarini tekshiring.</small></div>'}}
function render(){nav();const root=$('#app');if(tab==='home')root.innerHTML=homeHTML();else if(tab==='game')root.innerHTML=gameHTML();else if(tab==='history')loadHistory();else if(tab==='topup')root.innerHTML=topupHTML();else if(tab==='profile')root.innerHTML=profileHTML();else if(tab.startsWith('admin'))loadAdmin();}
function homeHTML(){const s=D.settings||{};return `<div class="balance"><div><small class="muted">${tr('SIZNING BALANSINGIZ','ВАШ БАЛАНС')}</small><br><strong>${money(D.user.balance)}</strong></div><button class="btn" style="width:auto" onclick="go('topup')">＋ ${tr('To‘ldirish','Пополнить')}</button></div><section class="hero">${s.hero_image?`<img src="${esc(s.hero_image)}" onerror="this.remove()">`:''}<div><div class="eyebrow">⚡ SYREXA • FAST TOP-UP</div><h1>${esc(s.hero_title||tr('O‘yinlaringiz uchun tezkor to‘ldirish','Быстрое пополнение игр'))}</h1><p>${esc(s.hero_subtitle||'PUBG UC • Free Fire Diamonds • Mobile Legends')}</p></div></section><div class="sectionhead"><h2>${tr('🎮 O‘yinlar','🎮 Игры')}</h2><span class="muted">${D.games.length} ${tr('ta','шт.')}</span></div><div class="games">${D.games.map(g=>`<button class="game" onclick="openGame(${g.id})"><img src="${esc(g.icon||'')}" onerror="this.style.opacity='.2'"><div class="gbody"><b>${esc(g.name)}</b><small>${esc(g.description||g.player_field||'Top up')}</small></div></button>`).join('')}</div><div class="card"><b>🎟 ${tr('Promokod','Промокод')}</b><div class="row"><input id="promo" placeholder="SYREXA2026" style="margin:8px 0 0"><button class="btn" style="flex:0 0 105px" onclick="usePromo()">${tr('Faollashtirish','Активировать')}</button></div></div>${D.admin?`<button class="btn alt" onclick="go('admin')">🛠 ${tr('Admin panel','Админ-панель')}</button>`:''}`}
function openGame(id){selectedGame=D.games.find(x=>x.id===id);tab='game';render();}
function gameHTML(){const g=selectedGame||D.games[0];if(!g){tab='home';return homeHTML()}return `<button class="pill" onclick="go('home')">← ${tr('Orqaga','Назад')}</button><section class="hero" style="margin-top:13px">${g.banner?`<img src="${esc(g.banner)}" onerror="this.remove()">`:''}<div><div class="eyebrow">SYREXA GAME STORE</div><h1>${esc(g.name)}</h1><p>${esc(g.description||'')}</p></div></section><div class="sectionhead"><h2>${tr('Paketni tanlang','Выберите пакет')}</h2></div><div class="products">${(g.products||[]).map(p=>`<article class="product">${p.image?`<img class="pimg" src="${esc(p.image)}" onerror="this.style.visibility='hidden'">`:`<div class="pimg" style="display:grid;place-items:center">💎</div>`}${p.badge?`<span class="badge">${esc(p.badge)}</span>`:''}<b>${esc(p.name)}</b><div class="price">${money(p.price)}</div><button class="btn" onclick="buy(${p.id})">${tr('Sotib olish','Купить')}</button></article>`).join('')||'<div class="empty">Hozircha paket yo‘q</div>'}</div>`}
function buy(id){const g=selectedGame;const p=g.products.find(x=>x.id===id);openModal(`<button class="close" onclick="closeModal()">✕</button><h2>🛒 ${tr('Buyurtma','Заказ')}</h2><p><b>${esc(g.name)} — ${esc(p.name)}</b><br><span class="price">${money(p.price)}</span></p><label>${esc(g.player_field||'Player ID')}</label><input id="playerid" placeholder="${esc(g.player_field||'Player ID')}" maxlength="100"><p class="muted">${tr('ID raqamini diqqat bilan tekshiring. Noto‘g‘ri ID uchun o‘yin ichidagi yetkazish kafolatlanmaydi.','Проверьте ID внимательно. Доставка на неверный ID не гарантируется.')}</p><button class="btn" onclick="submitOrder(${p.id})">${tr('Tasdiqlash','Подтвердить')}</button>`)}
async function submitOrder(id){try{const r=await api('/api/order',{product_id:id,player_id:$('#playerid').value.trim()});D.user.balance=r.balance;closeModal();toast(tr('Buyurtma #'+r.order_id+' qabul qilindi.','Заказ #'+r.order_id+' принят.'));go('history')}catch(e){if(e.data?.error==='insufficient_balance'){closeModal();toast(tr('Balans yetarli emas. Avval balansni to‘ldiring.','Недостаточно средств. Пополните баланс.'));go('topup')}else toast(e.message)}}
function topupHTML(){return `<div class="sectionhead"><h2>💳 ${tr('Balansni to‘ldirish','Пополнить баланс')}</h2></div><div class="card"><p class="muted">${tr('To‘ldirish summasini kiriting. Karta ma’lumoti keyingi oynada ko‘rsatiladi. To‘lov admin tasdiqlagandan keyin balansga tushadi.','Введите сумму. Реквизиты карты будут показаны после создания запроса. Баланс пополнится после подтверждения администратором.')}</p><label>${tr('Summa (so‘m)','Сумма (сум)')}</label><input id="topamount" type="number" min="5000" max="100000000" placeholder="50000"><button class="btn" onclick="makeTopup()">${tr('To‘ldirish so‘rovi yaratish','Создать запрос')}</button></div><div class="card"><b>${tr('Eslatma','Важно')}</b><p class="muted">${tr('Faqat admin panelida faol qilib qo‘yilgan karta ishlatiladi. To‘lovni o‘zingiz yuborgach, tasdiqlashni kuting.','Используется только активная карта из админ-панели. После оплаты дождитесь подтверждения.')}</p></div>`}
async function makeTopup(){try{const amount=Number($('#topamount').value);const r=await api('/api/topup',{amount});const c=r.card;openModal(`<button class="close" onclick="closeModal()">✕</button><h2>💳 ${tr('To‘lov ma’lumotlari','Реквизиты')}</h2><div class="notice">${tr('So‘rov','Запрос')} #${r.topup_id} — ${money(amount)}</div><div class="card"><label>${esc(c.bank||'BANK')}</label><h2>${esc(c.number)}</h2><p>${esc(c.holder)} · ${esc(c.title)}</p><button class="btn alt" onclick="navigator.clipboard?.writeText('${esc(c.number)}');toast('Karta raqami nusxalandi')">${tr('Karta raqamini nusxalash','Скопировать карту')}</button></div><p class="muted">${tr('To‘lovni yuboring va admin tasdiqlashini kuting. Bu avtomatik bank tekshiruvi emas.','Оплатите и дождитесь подтверждения администратора. Автоматической проверки банка нет.')}</p><button class="btn" onclick="closeModal();go('history')">${tr('Tushunarli','Понятно')}</button>`)}catch(e){toast(e.message)}}
async function usePromo(){try{const r=await api('/api/promo',{code:$('#promo').value});D.user.balance=r.balance;toast(tr('Promokod qabul qilindi: +','Промокод применён: +')+money(r.amount));render()}catch(e){toast(e.message)}}
function profileHTML(){return `<div class="card" style="text-align:center;padding:25px"><div class="logo" style="margin:auto;width:64px;height:64px;border-radius:22px">${esc((D.user.name||'S').slice(0,1).toUpperCase())}</div><h2>${esc(D.user.name||'SYREXA user')}</h2><div class="muted">@${esc(D.user.username||'—')} · ID ${D.user.id}</div><div class="balance" style="margin-top:18px"><div><small class="muted">${tr('Balans','Баланс')}</small><br><strong>${money(D.user.balance)}</strong></div><button class="btn" style="width:auto" onclick="go('topup')">＋</button></div></div><div class="card"><b>🌐 ${tr('Til','Язык')}</b><div class="row" style="margin-top:10px"><button class="btn ${lang==='uz'?'':'alt'}" onclick="setLang('uz')">O‘zbekcha</button><button class="btn ${lang==='ru'?'':'alt'}" onclick="setLang('ru')">Русский</button></div></div><div class="card"><b>📞 ${tr('Yordam','Поддержка')}</b><p><a style="color:#a99aff" href="${esc(D.settings.support_url||'https://t.me/')}" target="_blank">${tr('Yordam xizmatiga yozish','Написать в поддержку')}</a></p></div>${D.admin?`<button class="btn" onclick="go('admin')">🛠 Admin panel</button>`:''}`}
async function setLang(l){try{await api('/api/lang',{lang:l});lang=l;D.user.lang=l;render()}catch(e){toast(e.message)}}function toggleLang(){setLang(lang==='uz'?'ru':'uz')}
async function loadHistory(){try{const h=await api('/api/history');$('#app').innerHTML=`<div class="sectionhead"><h2>▤ ${tr('Tarix','История')}</h2></div><div class="card"><b>${tr('Buyurtmalar','Заказы')}</b>${h.orders.map(o=>`<div class="listitem"><div class="row"><div><b>#${o.id} ${esc(o.game_name)}</b><div class="muted">${esc(o.product_name)} · ID: ${esc(o.player_id)}</div><div class="muted">${new Date(o.created*1000).toLocaleString()}</div></div><div style="text-align:right"><b>${money(o.price)}</b><div class="badge">${esc(o.status)}</div></div></div></div>`).join('')||'<p class="muted">Hali buyurtma yo‘q</p>'}</div><div class="card"><b>${tr('Balans so‘rovlari','Пополнения')}</b>${h.topups.map(t=>`<div class="listitem"><b>#${t.id} · ${money(t.amount)}</b><span class="badge" style="float:right">${esc(t.status)}</span></div>`).join('')||'<p class="muted">Hali so‘rov yo‘q</p>'}</div>`}catch(e){$('#app').innerHTML=`<div class="notice">${esc(e.message)}</div>`}}
function go(t){tab=t;render();if(t==='history')loadHistory();if(t==='admin'&&!D.admin){toast('Admin ruxsati kerak');go('profile')}}
// Admin panel UI and CRUD
async function loadAdmin(){if(!D.admin){tab='profile';render();return}try{adminData=await api('/api/admin/overview');renderAdmin()}catch(e){$('#app').innerHTML=`<div class="notice">Admin panel: ${esc(e.message)}</div>`}}
function renderAdmin(){const s=adminData.stats||{};$('#app').innerHTML=`<div class="sectionhead"><h2>🛠 ${tr('Boshqaruv paneli','Панель управления')}</h2><button class="pill" onclick="go('profile')">←</button></div><div class="adminnav">${[['overview','📊 Statistika'],['games','🎮 O‘yinlar'],['products','💎 Paketlar'],['orders','🛒 Buyurtma'],['topups','💳 To‘lovlar'],['users','👥 Userlar'],['cards','🏦 Kartalar'],['promos','🎟 Promo'],['settings','⚙️ Sozlama'],['admins','🛡 Admin'],['transactions','📒 Harakatlar'],['broadcasts','📣 Xabarlar']].map(([k,v])=>`<button class="${adminTab===k?'on':''}" onclick="adminTab='${k}';renderAdmin()">${v}</button>`).join('')}</div><div id="adminbody">${adminBody()}</div>`}
function adminBody(){const A=adminData;if(adminTab==='overview')return `<div class="statgrid">${[['Foydalanuvchilar',A.stats.users],['Buyurtmalar',A.stats.orders],['Kutilayotgan buyurtma',A.stats.pending_orders],['Bajarilgan buyurtma summasi',money(A.stats.revenue)],['Kutilayotgan to‘lovlar',A.stats.pending_topups],['Jami balans',money(A.stats.balance_total)],['24 soat tushum',money(A.stats.revenue_today)]].map(([k,v])=>`<div class="stat"><small>${k}</small><strong>${v}</strong></div>`).join('')}</div><div class="card"><b>⚡ Tezkor amallar</b><div class="row" style="margin-top:10px"><button class="btn" onclick="adminTab='games';renderAdmin()">O‘yin qo‘shish</button><button class="btn alt" onclick="adminTab='products';renderAdmin()">Paket qo‘shish</button></div></div>`;
if(adminTab==='games')return `<button class="btn" onclick="formGame()">＋ O‘yin qo‘shish</button>${A.games.map(g=>`<div class="card"><div class="row"><img class="thumb" src="${esc(g.icon)}"><div><b>${esc(g.name)}</b><div class="muted">ID ${g.id} · ${g.active?'Faol':'Yopiq'}</div></div><button class="pill" style="flex:0 0 auto" onclick="formGame(${g.id})">✎</button></div><div class="row"><button class="btn alt" onclick="editGameQuick(${g.id},'icon')">Icon URL</button><button class="btn alt" onclick="editGameQuick(${g.id},'banner')">Banner URL</button><button class="btn danger" onclick="delItem('game',${g.id})">O‘chirish</button></div></div>`).join('')}`;
if(adminTab==='products')return `<button class="btn" onclick="formProduct()">＋ Paket qo‘shish</button>${A.products.map(p=>`<div class="card"><div class="row"><div><b>${esc(p.name)}</b><div class="muted">O‘yin ID ${p.game_id} · ${money(p.price)} · #${p.id}</div></div><button class="pill" onclick="formProduct(${p.id})" style="flex:0 0 auto">✎</button></div><div class="row"><button class="btn alt" onclick="editProductQuick(${p.id},'price')">Narx</button><button class="btn alt" onclick="editProductQuick(${p.id},'image')">Rasm URL</button><button class="btn danger" onclick="delItem('product',${p.id})">O‘chirish</button></div></div>`).join('')}`;
if(adminTab==='orders')return A.orders.map(o=>`<div class="card"><b>#${o.id} ${esc(o.game_name)} — ${esc(o.product_name)}</b><div class="muted">User ${o.user_id} · Player ID ${esc(o.player_id)} · ${money(o.price)}</div><p><span class="badge">${esc(o.status)}</span></p>${o.status==='new'?`<div class="row"><button class="btn good" onclick="processOrder(${o.id},'done')">Bajarildi</button><button class="btn danger" onclick="processOrder(${o.id},'refund')">Bekor + qaytarish</button></div>`:''}</div>`).join('')||'<div class="empty">Buyurtma yo‘q</div>';
if(adminTab==='topups')return A.topups.map(t=>`<div class="card"><b>#${t.id} · ${money(t.amount)}</b><div class="muted">User ${t.user_id} · ${new Date(t.created*1000).toLocaleString()}</div><p><span class="badge">${esc(t.status)}</span></p>${t.status==='new'?`<div class="row"><button class="btn good" onclick="processTopup(${t.id},'paid')">Tasdiqlash</button><button class="btn danger" onclick="processTopup(${t.id},'reject')">Rad etish</button></div>`:''}</div>`).join('')||'<div class="empty">To‘lov so‘rovi yo‘q</div>';
if(adminTab==='users')return `<div class="card"><label>Foydalanuvchi ID</label><input id="uid" type="number" placeholder="Telegram ID"><label>Summa</label><input id="balamount" type="number" placeholder="5000"><label>Amal</label><select id="balmode"><option value="add">Balansga qo‘shish</option><option value="subtract">Balansdan ayirish</option><option value="set">Balansni aynan shunga tenglash</option></select><input id="balreason" placeholder="Izoh"><button class="btn" onclick="balanceChange()">Balansni o‘zgartirish</button><hr style="border-color:var(--line);margin:18px 0"><label>Ban / Unban uchun User ID</label><input id="banuid" type="number"><div class="row"><button class="btn danger" onclick="userAction('ban')">Ban</button><button class="btn alt" onclick="userAction('unban')">Unban</button></div></div><h3>So‘nggi foydalanuvchilar</h3>${A.users.map(u=>`<div class="listitem"><b>${esc(u.name||u.username||u.id)}</b><div class="muted">ID ${u.id} · @${esc(u.username)} · ${money(u.balance)} · ${u.banned?'BAN':'Faol'}</div></div>`).join('')}`;
if(adminTab==='cards')return `<button class="btn" onclick="formCard()">＋ Bank kartasi qo‘shish</button>${A.cards.map(c=>`<div class="card"><b>${esc(c.title||c.bank)} · ${esc(c.number)}</b><div class="muted">${esc(c.holder)} · ${c.active?'Faol':'Yopiq'}</div><div class="row"><button class="btn alt" onclick="formCard(${c.id})">Tahrirlash</button><button class="btn danger" onclick="delItem('card',${c.id})">O‘chirish</button></div></div>`).join('')}`;
if(adminTab==='promos')return `<button class="btn" onclick="formPromo()">＋ Promokod yaratish</button>${A.promos.map(p=>`<div class="card"><b>${esc(p.code)}</b><div class="muted">${money(p.amount)} · qolgan ishlatish: ${p.uses_left} · ${p.active?'Faol':'Yopiq'}</div><button class="btn danger" onclick="delItem('promo','${esc(p.code)}')">O‘chirish</button></div>`).join('')}`;
if(adminTab==='settings')return `<div class="card"><b>Sayt ko‘rinishi va sozlamalari</b>${[['brand','Brend nomi'],['hero_title','Bosh banner sarlavhasi'],['hero_subtitle','Banner izohi'],['hero_image','Katta banner rasmi URL'],['support_url','Yordam Telegram URL'],['channel_url','Majburiy obuna kanal URL'],['maintenance','Texnik ishlar (1=yoqilgan, 0=o‘chiq)'],['order_notice','Buyurtma matni'],['topup_notice','To‘lov matni'],['required_channels','Majburiy obuna kanallari (vergul bilan: @kanal1,@kanal2)']].map(([k,l])=>`<label>${l}</label><input id="set_${k}" value="${esc(A.settings[k]||'')}"><button class="btn alt" onclick="saveSetting('${k}')">Saqlash</button>`).join('<div style="height:7px"></div>')}</div><div class="card"><b>📣 Ommaviy xabar</b><textarea id="broadcast" rows="4" placeholder="Foydalanuvchilarga yuboriladigan xabar"></textarea><button class="btn" onclick="broadcast()">Yuborish</button><p class="muted">Xabar haqiqiy foydalanuvchilarga yuboriladi. Bot bilan avval chat boshlamaganlar yoki botni bloklaganlar olmasligi mumkin.</p></div>`;
if(adminTab==='admins')return `<div class="card"><label>Admin Telegram ID</label><input id="newadmin" type="number"><button class="btn" onclick="adminChange('add')">Admin qo‘shish</button></div>${A.admins.map(a=>`<div class="listitem"><b>${a.user_id}</b> <button class="pill" style="float:right" onclick="removeAdmin(${a.user_id})">O‘chirish</button></div>`).join('')}`;if(adminTab==='transactions')return `<div class="card"><b>Balans va pul harakatlari</b><p class="muted">Oxirgi 100 ta yozuv. Musbat summa balansga qo‘shilgan, manfiy summa yechilgan.</p>${A.transactions.map(t=>`<div class="listitem"><div class="row"><div><b>#${t.id} · User ${t.user_id}</b><div class="muted">${esc(t.kind)} · ${esc(t.reason)} · ${new Date(t.created*1000).toLocaleString()}</div></div><b style="color:${t.amount<0?'#ff647c':'#24d18b'}">${t.amount>0?'+':''}${money(t.amount)}</b></div></div>`).join('')||'<p class="muted">Harakatlar yo‘q</p>'}</div>`;if(adminTab==='broadcasts')return `<div class="card"><b>📣 Barcha foydalanuvchilarga xabar</b><textarea id="broadcast" rows="5" placeholder="Xabar matni"></textarea><button class="btn" onclick="broadcast()">Yuborish</button><p class="muted">Faqat bot bilan chatni boshlagan foydalanuvchilarga yetib borishi mumkin.</p></div><div class="card"><b>Yuborish tarixi</b>${A.broadcasts.map(b=>`<div class="listitem"><b>#${b.id} · ${new Date(b.created*1000).toLocaleString()}</b><div class="muted">${esc(b.text)}</div><div class="muted">Yuborildi: ${b.sent} · Xato: ${b.failed}</div></div>`).join('')||'<p class="muted">Hali xabar yuborilmagan</p>'}</div>`;return ''}
function formGame(id){const g=id?adminData.games.find(x=>x.id===id):{};openModal(`<button class="close" onclick="closeModal()">✕</button><h2>${id?'O‘yinni tahrirlash':'Yangi o‘yin'}</h2>${[['name','Nomi'],['slug','Slug'],['icon','Kichik rasm URL'],['banner','Katta banner URL'],['player_field','ID maydoni nomi'],['description','Izoh'],['category','Kategoriya'],['sort','Tartib']].map(([k,l])=>`<label>${l}</label><input id="fg_${k}" value="${esc(g[k]??'')}" >`).join('')}<label><input id="fg_active" type="checkbox" style="width:auto" ${g.active!==0?'checked':''}> Faol</label><button class="btn" onclick="saveGame(${id||0})">Saqlash</button>`)}
async function saveGame(id){const d={};['name','slug','icon','banner','player_field','description','category','sort'].forEach(k=>d[k]=$('#fg_'+k).value);d.active=$('#fg_active').checked;try{if(id)await api('/api/admin/game/'+id,d,'PATCH');else await api('/api/admin/game',d);closeModal();await loadAdmin()}catch(e){toast(e.message)}}
function formProduct(id){const p=id?adminData.products.find(x=>x.id===id):{};openModal(`<button class="close" onclick="closeModal()">✕</button><h2>${id?'Paketni tahrirlash':'Yangi paket'}</h2><label>O‘yin</label><select id="fp_game">${adminData.games.map(g=>`<option value="${g.id}" ${p.game_id===g.id?'selected':''}>${esc(g.name)}</option>`).join('')}</select>${[['name','Nomi'],['price','Narxi so‘m'],['image','Mahsulot rasmi URL'],['badge','Badge'],['sort','Tartib']].map(([k,l])=>`<label>${l}</label><input id="fp_${k}" value="${esc(p[k]??'')}" ${k==='price'||k==='sort'?'type="number"':''}>`).join('')}<label><input id="fp_active" type="checkbox" style="width:auto" ${p.active!==0?'checked':''}> Faol</label><button class="btn" onclick="saveProduct(${id||0})">Saqlash</button>`)}
async function saveProduct(id){const d={game_id:Number($('#fp_game').value),name:$('#fp_name').value,price:Number($('#fp_price').value),image:$('#fp_image').value,badge:$('#fp_badge').value,sort:Number($('#fp_sort').value||0),active:$('#fp_active').checked};try{if(id)await api('/api/admin/product/'+id,d,'PATCH');else await api('/api/admin/product',d);closeModal();await loadAdmin()}catch(e){toast(e.message)}}
function formCard(id){const c=id?adminData.cards.find(x=>x.id===id):{};openModal(`<button class="close" onclick="closeModal()">✕</button><h2>Bank kartasi</h2>${[['title','Karta turi'],['number','Karta raqami'],['holder','Karta egasi'],['bank','Bank']].map(([k,l])=>`<label>${l}</label><input id="fc_${k}" value="${esc(c[k]||'')}">`).join('')}<label><input id="fc_active" type="checkbox" style="width:auto" ${c.active!==0?'checked':''}> Faol</label><button class="btn" onclick="saveCard(${id||0})">Saqlash</button>`)}
async function saveCard(id){const d={title:$('#fc_title').value,number:$('#fc_number').value,holder:$('#fc_holder').value,bank:$('#fc_bank').value,active:$('#fc_active').checked};try{if(id)await api('/api/admin/card/'+id,d,'PATCH');else await api('/api/admin/card',d);closeModal();await loadAdmin()}catch(e){toast(e.message)}}
function formPromo(){openModal(`<button class="close" onclick="closeModal()">✕</button><h2>Promokod</h2><label>Kod</label><input id="pr_code"><label>Bonus (so‘m)</label><input id="pr_amount" type="number"><label>Necha marta ishlatiladi</label><input id="pr_uses" type="number" value="1"><button class="btn" onclick="savePromo()">Yaratish</button>`)}async function savePromo(){try{await api('/api/admin/promo',{code:$('#pr_code').value,amount:Number($('#pr_amount').value),uses_left:Number($('#pr_uses').value)});closeModal();await loadAdmin()}catch(e){toast(e.message)}}

async function editGameQuick(id,key){const g=adminData.games.find(x=>x.id===id);const val=prompt(key==='icon'?'Icon rasm URL':'Banner rasm URL',g[key]||'');if(val===null)return;try{await api('/api/admin/game/'+id,{[key]:val},'PATCH');await loadAdmin()}catch(e){toast(e.message)}}async function editProductQuick(id,key){const p=adminData.products.find(x=>x.id===id);const val=prompt(key==='price'?'Yangi narx (so‘m)':'Mahsulot rasmi URL',p[key]||'');if(val===null)return;try{await api('/api/admin/product/'+id,{[key]:key==='price'?Number(val):val},'PATCH');await loadAdmin()}catch(e){toast(e.message)}}
async function delItem(kind,id){if(!confirm('Haqiqatan o‘chirasizmi?'))return;try{await api('/api/admin/'+kind+'/'+encodeURIComponent(id),null,'DELETE');await loadAdmin()}catch(e){toast(e.message)}}async function processOrder(id,action){try{await api('/api/admin/order/'+id,{action});await loadAdmin()}catch(e){toast(e.message)}}async function processTopup(id,action){try{await api('/api/admin/topup/'+id,{action});await loadAdmin()}catch(e){toast(e.message)}}async function balanceChange(){try{const r=await api('/api/admin/balance',{user_id:Number($('#uid').value),amount:Number($('#balamount').value),mode:$('#balmode').value,reason:$('#balreason').value});toast('Yangi balans: '+money(r.balance));await loadAdmin()}catch(e){toast(e.message)}}async function userAction(action){try{await api('/api/admin/user',{user_id:Number($('#banuid').value),action});toast('Bajarildi');await loadAdmin()}catch(e){toast(e.message)}}async function saveSetting(key){try{await api('/api/admin/setting',{key,value:$('#set_'+key).value});toast('Saqlandi');await loadAdmin()}catch(e){toast(e.message)}}async function broadcast(){const text=$('#broadcast').value;if(!text.trim())return; if(!confirm('Xabar barcha foydalanuvchilarga yuborilsinmi?'))return;try{const r=await api('/api/admin/broadcast',{text});toast(`Yuborildi: ${r.sent}; xato: ${r.failed}`)}catch(e){toast(e.message)}}async function adminChange(action){try{await api('/api/admin/admin',{user_id:Number($('#newadmin').value),action});await loadAdmin()}catch(e){toast(e.message)}}async function removeAdmin(id){if(!confirm('Admin huquqi olib tashlansinmi?'))return;try{await api('/api/admin/admin',{user_id:id,action:'remove'});await loadAdmin()}catch(e){toast(e.message)}}
// avoid any external dependency beyond Telegram WebApp and Flask
boot();
</script></body></html>
"""

@app.get('/')
def home(): return render_template_string(INDEX_HTML, bot_username=BOT_USERNAME, token_configured=bool(BOT_TOKEN), browser_admin=False)

ADMIN_LOGIN_HTML = r'''<!doctype html><html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#090d18"><title>SYREXA Admin</title><style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:radial-gradient(ellipse at 50% 0,#33235c 0,transparent 45%),#080c16;color:#f4f7ff;font:15px system-ui}.box{width:min(100%,430px);padding:28px;border:1px solid #303a57;border-radius:26px;background:#11182a;box-shadow:0 25px 80px #0008}.logo{display:grid;place-items:center;width:58px;height:58px;border-radius:18px;background:linear-gradient(135deg,#8b5cf6,#00d4ff);font-size:27px;font-weight:900}.muted{color:#9aa8c2;font-size:13px}label{display:block;margin:18px 0 6px;color:#aab6ce;font-size:13px}input{width:100%;padding:14px;border:1px solid #303a57;border-radius:12px;background:#090f1d;color:white;font:inherit;outline:none}button{width:100%;margin-top:15px;padding:14px;border:0;border-radius:12px;background:linear-gradient(100deg,#7c3aed,#536dfe);color:white;font-weight:800;font:inherit;cursor:pointer}.error{padding:10px;border-radius:10px;background:#552338;color:#ffc4d2;margin:12px 0}.hint{margin-top:18px;line-height:1.5}</style></head><body><form class="box" method="post"><div class="logo">S</div><h1>SYREXA <span style="color:#a78bfa">ADMIN</span></h1><p class="muted">Xavfsiz boshqaruv paneli</p>{% if error %}<div class="error">{{ error }}</div>{% endif %}<label>Admin paroli</label><input type="password" name="password" required autofocus autocomplete="current-password" placeholder="ADMIN_PASSWORD qiymati"><button type="submit">Panelga kirish →</button><p class="muted hint">Parol Render → Environment bo‘limidagi ADMIN_PASSWORD qiymatidir. Uni hech kimga yubormang.</p></form></body></html>'''

@app.route('/admin', methods=['GET', 'POST'])
def browser_admin():
    if not ADMIN_PASSWORD:
        return 'Admin browser login yopiq: Render Environment ichida ADMIN_PASSWORD sozlang.', 503
    if request.method == 'POST':
        supplied = request.form.get('password', '')
        if hmac.compare_digest(supplied.encode(), ADMIN_PASSWORD.encode()):
            session.clear()
            session['browser_admin'] = True
            session['admin_id'] = min(ADMIN_IDS) if ADMIN_IDS else -1
            session.permanent = False
            return redirect('/admin')
        return render_template_string(ADMIN_LOGIN_HTML, error='Parol noto‘g‘ri. Qayta urinib ko‘ring.'), 401
    if session.get('browser_admin') is True:
        page = render_template_string(INDEX_HTML, bot_username=BOT_USERNAME, token_configured=bool(BOT_TOKEN), browser_admin=True)
        return page.replace('</head>', '<style>.admin-logout{position:fixed;right:12px;top:12px;z-index:30;background:#68253b;color:white;border:0;border-radius:10px;padding:9px 12px;font-weight:800}</style></head>').replace('<body>', '<body><form action="/admin/logout" method="post" style="position:fixed;right:12px;top:12px;z-index:30"><button class="admin-logout" type="submit">Chiqish ↗</button></form>')
    return render_template_string(ADMIN_LOGIN_HTML, error='')

@app.post('/admin/logout')
def browser_admin_logout():
    session.clear()
    return redirect('/admin')
@app.get('/health')
def health(): return jsonify(ok=True, service='SYREXA', database=os.path.exists(DB_PATH), bot_configured=bool(BOT_TOKEN))
@app.get('/api/init')
@need_user
def api_init(u):
    games=rows('SELECT * FROM games WHERE active=1 ORDER BY sort,id')
    for g in games: g['products']=rows('SELECT * FROM products WHERE game_id=? AND active=1 ORDER BY sort,id',(g['id'],))
    return jsonify(user={k:u[k] for k in ('id','username','name','lang','balance','banned')},admin=is_admin(u['id']),games=games,settings={k:setting(k) for k in ('brand','hero_title','hero_subtitle','hero_image','support_url','channel_url','maintenance','required_channels')},cards=rows('SELECT id,title,number,holder,bank FROM cards WHERE active=1 ORDER BY id'))

@app.post('/api/lang')
@need_user
def api_lang(u):
    lang=(request.get_json(silent=True) or {}).get('lang')
    if lang not in ('uz','ru'): return jsonify(error='invalid_language'),400
    with db() as c: c.execute('UPDATE users SET lang=? WHERE id=?',(lang,u['id']))
    return jsonify(ok=True)

@app.post('/api/order')
@need_user
def api_order(u):
    d=request.get_json(silent=True) or {}; pid=d.get('product_id'); player=str(d.get('player_id','')).strip()
    try: pid=int(pid)
    except: return jsonify(error='product_required'),400
    p=row('SELECT p.*,g.name AS game_name,g.id AS gid,g.player_field FROM products p JOIN games g ON g.id=p.game_id WHERE p.id=? AND p.active=1 AND g.active=1',(pid,))
    if not p: return jsonify(error='product_not_found'),404
    if not player or len(player)>100: return jsonify(error='player_id_required'),400
    oid=None
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        user=c.execute('SELECT balance,banned FROM users WHERE id=?',(u['id'],)).fetchone()
        if user['banned']: return jsonify(error='banned'),403
        if user['balance'] < p['price']: return jsonify(error='insufficient_balance',balance=user['balance'],price=p['price']),400
        c.execute('UPDATE users SET balance=balance-? WHERE id=?',(p['price'],u['id']))
        cur=c.execute('INSERT INTO orders(user_id,game_id,product_id,game_name,product_name,price,player_id,status,created) VALUES(?,?,?,?,?,?,? ,?,?)',(u['id'],p['gid'],pid,p['game_name'],p['name'],p['price'],player,'new',int(time.time())))
        oid=cur.lastrowid
        c.execute('INSERT INTO transactions(user_id,amount,kind,reason,created) VALUES(?,?,?,?,?)',(u['id'],-p['price'],'order',f'Buyurtma #{oid}',int(time.time())))
    notify_admins(f'🛒 <b>Yangi buyurtma #{oid}</b>\n👤 <code>{u["id"]}</code> @{u["username"] or "-"}\n🎮 {p["game_name"]} — {p["name"]}\n🆔 {player}\n💰 {money(p["price"])} so‘m', {'inline_keyboard':[[{'text':'✅ Bajarildi','callback_data':f'order:done:{oid}'},{'text':'❌ Bekor / qaytarish','callback_data':f'order:refund:{oid}'}]]})
    return jsonify(ok=True,order_id=oid,balance=row('SELECT balance FROM users WHERE id=?',(u['id'],))['balance'])

@app.post('/api/topup')
@need_user
def api_topup(u):
    d=request.get_json(silent=True) or {}
    try: amount=int(d.get('amount',0))
    except: amount=0
    if amount<5000 or amount>100000000: return jsonify(error='amount_range',message='Summa 5 000 dan 100 000 000 so‘mgacha.'),400
    card=row('SELECT * FROM cards WHERE active=1 ORDER BY id LIMIT 1')
    if not card: return jsonify(error='no_active_card',message='Admin hali karta qo‘shmagan.'),400
    with db() as c:
        cur=c.execute('INSERT INTO topups(user_id,amount,card_id,status,created) VALUES(?,?,? ,?,?)',(u['id'],amount,card['id'],'new',int(time.time())))
        tid=cur.lastrowid
    notify_admins(f'💳 <b>Balans to‘ldirish #{tid}</b>\n👤 <code>{u["id"]}</code> @{u["username"] or "-"}\n💰 {money(amount)} so‘m\n🏦 {card["number"]} ({card["holder"]})',{'inline_keyboard':[[{'text':'✅ To‘lov tasdiqlandi','callback_data':f'topup:paid:{tid}'},{'text':'❌ Rad etish','callback_data':f'topup:reject:{tid}'}]]})
    return jsonify(ok=True,topup_id=tid,card=card)

@app.post('/api/promo')
@need_user
def api_promo(u):
    code=str((request.get_json(silent=True) or {}).get('code','')).strip().upper()
    with db() as c:
        c.execute('BEGIN IMMEDIATE'); p=c.execute('SELECT * FROM promos WHERE code=? AND active=1',(code,)).fetchone()
        if not p or p['uses_left']<1: return jsonify(error='promo_invalid'),400
        if c.execute('SELECT 1 FROM promo_uses WHERE code=? AND user_id=?',(code,u['id'])).fetchone(): return jsonify(error='promo_used'),400
        c.execute('INSERT INTO promo_uses(code,user_id) VALUES(?,?)',(code,u['id']))
        c.execute('UPDATE promos SET uses_left=uses_left-1 WHERE code=?',(code,)); c.execute('UPDATE users SET balance=balance+? WHERE id=?',(p['amount'],u['id']))
        c.execute('INSERT INTO transactions(user_id,amount,kind,reason,created) VALUES(?,?,?,?,?)',(u['id'],p['amount'],'promo',f'Promokod {code}',int(time.time())))
        bal=c.execute('SELECT balance FROM users WHERE id=?',(u['id'],)).fetchone()['balance']
    return jsonify(ok=True,amount=p['amount'],balance=bal)

@app.get('/api/history')
@need_user
def api_history(u):
    return jsonify(orders=rows('SELECT id,game_name,product_name,price,player_id,status,created,note FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 100',(u['id'],)),topups=rows('SELECT id,amount,status,created FROM topups WHERE user_id=? ORDER BY id DESC LIMIT 100',(u['id'],)),transactions=rows('SELECT id,amount,kind,reason,created FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 100',(u['id'],)))

# Admin APIs: all writes require a validated Telegram WebApp signature AND admin ID.
@app.get('/api/admin/overview')
@need_admin
def admin_overview(u):
    return jsonify(stats={'users':row('SELECT COUNT(*) n FROM users')['n'],'orders':row('SELECT COUNT(*) n FROM orders')['n'],'pending_orders':row("SELECT COUNT(*) n FROM orders WHERE status='new'")['n'],'revenue':row("SELECT COALESCE(SUM(price),0) n FROM orders WHERE status='done'")['n'],'pending_topups':row("SELECT COUNT(*) n FROM topups WHERE status='new'")['n'],'balance_total':row('SELECT COALESCE(SUM(balance),0) n FROM users')['n'],'revenue_today':row("SELECT COALESCE(SUM(price),0) n FROM orders WHERE status='done' AND created>=?",(int(time.time())-86400,))['n']},users=rows('SELECT id,username,name,lang,balance,banned,created FROM users ORDER BY created DESC LIMIT 100'),orders=rows('SELECT * FROM orders ORDER BY id DESC LIMIT 100'),topups=rows('SELECT * FROM topups ORDER BY id DESC LIMIT 100'),games=rows('SELECT * FROM games ORDER BY sort,id'),products=rows('SELECT * FROM products ORDER BY game_id,sort,id'),cards=rows('SELECT * FROM cards ORDER BY id'),promos=rows('SELECT * FROM promos ORDER BY code'),settings={r['key']:r['value'] for r in rows('SELECT * FROM settings')},admins=rows('SELECT user_id,added_by,created FROM admins ORDER BY user_id'),transactions=rows('SELECT * FROM transactions ORDER BY id DESC LIMIT 100'),broadcasts=rows('SELECT * FROM broadcasts ORDER BY id DESC LIMIT 50'))

@app.post('/api/admin/<kind>')
@need_admin
def admin_create(u,kind):
    d=request.get_json(silent=True) or {}
    if kind=='game':
        name=str(d.get('name','')).strip()
        if not name: return jsonify(error='name_required'),400
        with db() as c: cur=c.execute('INSERT INTO games(name,slug,category,icon,banner,player_field,description,active,sort) VALUES(?,?,?,?,?,?,?,?,?)',(name,str(d.get('slug','')).strip(),str(d.get('category','game')),str(d.get('icon','')),str(d.get('banner','')),str(d.get('player_field','Player ID')),str(d.get('description','')),int(bool(d.get('active',True))),int(d.get('sort',0))))
        return jsonify(ok=True,id=cur.lastrowid)
    if kind=='product':
        try: gid=int(d.get('game_id')); price=int(d.get('price',0))
        except: return jsonify(error='invalid_game_or_price'),400
        if price<0 or not row('SELECT id FROM games WHERE id=?',(gid,)): return jsonify(error='invalid_game_or_price'),400
        with db() as c: cur=c.execute('INSERT INTO products(game_id,name,price,image,badge,active,sort) VALUES(?,?,?,?,?,?,?)',(gid,str(d.get('name','Yangi mahsulot')).strip(),price,str(d.get('image','')),str(d.get('badge','')),int(bool(d.get('active',True))),int(d.get('sort',0))))
        return jsonify(ok=True,id=cur.lastrowid)
    if kind=='card':
        number=str(d.get('number','')).strip()
        if not number: return jsonify(error='number_required'),400
        with db() as c: cur=c.execute('INSERT INTO cards(title,number,holder,bank,active) VALUES(?,?,?,?,?)',(str(d.get('title','')),number,str(d.get('holder','')),str(d.get('bank','')),int(bool(d.get('active',True)))))
        return jsonify(ok=True,id=cur.lastrowid)
    if kind=='promo':
        code=str(d.get('code','')).strip().upper()
        try: amount=int(d.get('amount',0)); uses=int(d.get('uses_left',1))
        except: return jsonify(error='invalid_amount'),400
        if not code or amount<1 or uses<1: return jsonify(error='invalid_promo'),400
        with db() as c: c.execute('INSERT INTO promos(code,amount,uses_left,active) VALUES(?,?,?,1)',(code,amount,uses))
        return jsonify(ok=True)
    if kind=='setting':
        key=str(d.get('key','')).strip(); value=str(d.get('value',''))
        allowed={'brand','hero_title','hero_subtitle','hero_image','support_url','channel_url','maintenance','uzcard','order_notice','topup_notice','channel_url','required_channels'}
        if key not in allowed: return jsonify(error='setting_not_allowed'),400
        set_setting(key,value); return jsonify(ok=True)
    if kind=='balance':
        try: target=int(d.get('user_id')); amount=int(d.get('amount')); mode=d.get('mode','add')
        except: return jsonify(error='invalid_input'),400
        if amount<0 or amount>1000000000 or mode not in ('add','subtract','set'): return jsonify(error='invalid_amount'),400
        with db() as c:
            c.execute('BEGIN IMMEDIATE'); exists=c.execute('SELECT id,balance FROM users WHERE id=?',(target,)).fetchone()
            if not exists: return jsonify(error='user_not_found'),404
            new=amount if mode=='set' else exists['balance']+amount if mode=='add' else exists['balance']-amount
            if new<0: return jsonify(error='balance_would_be_negative'),400
            delta=new-exists['balance']; c.execute('UPDATE users SET balance=? WHERE id=?',(new,target)); c.execute('INSERT INTO transactions(user_id,amount,kind,reason,admin_id,created) VALUES(?,?,?,?,?,?)',(target,delta,'admin_balance',str(d.get('reason','Admin tomonidan')),u['id'],int(time.time())))
        return jsonify(ok=True,balance=new)
    if kind=='user':
        try: target=int(d.get('user_id'))
        except: return jsonify(error='invalid_user'),400
        action=d.get('action')
        if action not in ('ban','unban'): return jsonify(error='invalid_action'),400
        with db() as c: cur=c.execute('UPDATE users SET banned=? WHERE id=?',(1 if action=='ban' else 0,target))
        return jsonify(ok=cur.rowcount>0)
    if kind=='admin':
        try: target=int(d.get('user_id'))
        except: return jsonify(error='invalid_user'),400
        if target in ADMIN_IDS and d.get('action')=='remove': return jsonify(error='root_admin_cannot_be_removed'),400
        with db() as c:
            if d.get('action')=='add': c.execute('INSERT OR IGNORE INTO admins(user_id,added_by,created) VALUES(?,?,?)',(target,u['id'],int(time.time())))
            elif d.get('action')=='remove': c.execute('DELETE FROM admins WHERE user_id=?',(target,))
            else: return jsonify(error='invalid_action'),400
        return jsonify(ok=True)
    return jsonify(error='unknown_resource'),404

@app.patch('/api/admin/<kind>/<int:item_id>')
@need_admin
def admin_update(u,kind,item_id):
    d=request.get_json(silent=True) or {}
    maps={'game':('games',{'name','slug','category','icon','banner','player_field','description','active','sort'}),'product':('products',{'game_id','name','price','image','badge','active','sort'}),'card':('cards',{'title','number','holder','bank','active'}),'promo':('promos',{'amount','uses_left','active'})}
    if kind not in maps: return jsonify(error='unknown_resource'),404
    table,allowed=maps[kind]; changes={k:v for k,v in d.items() if k in allowed}
    if not changes: return jsonify(error='no_changes'),400
    if 'price' in changes and int(changes['price'])<0: return jsonify(error='invalid_price'),400
    sql='UPDATE '+table+' SET '+','.join(k+'=?' for k in changes)+' WHERE id=?' if kind!='promo' else 'UPDATE promos SET '+','.join(k+'=?' for k in changes)+' WHERE code=?'
    with db() as c: cur=c.execute(sql,[*changes.values(),str(item_id) if kind=='promo' else item_id])
    return jsonify(ok=cur.rowcount>0)

@app.delete('/api/admin/<kind>/<item_id>')
@need_admin
def admin_delete(u,kind,item_id):
    config={'game':('games','id'),'product':('products','id'),'card':('cards','id'),'promo':('promos','code')}
    if kind not in config: return jsonify(error='unknown_resource'),404
    table,col=config[kind]
    with db() as c: cur=c.execute(f'DELETE FROM {table} WHERE {col}=?',(int(item_id) if col=='id' else item_id,))
    return jsonify(ok=cur.rowcount>0)

@app.post('/api/admin/order/<int:oid>')
@need_admin
def admin_order(u,oid):
    action=(request.get_json(silent=True) or {}).get('action')
    if action not in ('done','cancel','refund'): return jsonify(error='invalid_action'),400
    o=row('SELECT * FROM orders WHERE id=?',(oid,))
    if not o: return jsonify(error='not_found'),404
    with db() as c:
        c.execute('BEGIN IMMEDIATE'); o=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
        if o['status']!='new': return jsonify(error='already_processed'),400
        if action=='done': c.execute("UPDATE orders SET status='done',note=? WHERE id=?",(str((request.get_json(silent=True) or {}).get('note','')),oid))
        else:
            c.execute("UPDATE orders SET status='cancelled',note=? WHERE id=?",(str((request.get_json(silent=True) or {}).get('note','Admin bekor qildi')),oid))
            c.execute('UPDATE users SET balance=balance+? WHERE id=?',(o['price'],o['user_id']))
            c.execute('INSERT INTO transactions(user_id,amount,kind,reason,admin_id,created) VALUES(?,?,?,?,?,?)',(o['user_id'],o['price'],'refund',f'Buyurtma #{oid} qaytarildi',u['id'],int(time.time())))
    return jsonify(ok=True)

@app.post('/api/admin/topup/<int:tid>')
@need_admin
def admin_topup(u,tid):
    action=(request.get_json(silent=True) or {}).get('action')
    if action not in ('paid','reject'): return jsonify(error='invalid_action'),400
    with db() as c:
        c.execute('BEGIN IMMEDIATE'); t=c.execute('SELECT * FROM topups WHERE id=?',(tid,)).fetchone()
        if not t: return jsonify(error='not_found'),404
        if t['status']!='new': return jsonify(error='already_processed'),400
        if action=='paid':
            c.execute("UPDATE topups SET status='paid' WHERE id=?",(tid,)); c.execute('UPDATE users SET balance=balance+? WHERE id=?',(t['amount'],t['user_id']))
            c.execute('INSERT INTO transactions(user_id,amount,kind,reason,admin_id,created) VALUES(?,?,?,?,?,?)',(t['user_id'],t['amount'],'topup',f'Balans to‘ldirish #{tid}',u['id'],int(time.time())))
        else: c.execute("UPDATE topups SET status='rejected' WHERE id=?",(tid,))
    return jsonify(ok=True)

@app.post('/api/admin/broadcast')
@need_admin
def admin_broadcast(u):
    d=request.get_json(silent=True) or {}; message=str(d.get('text','')).strip()
    if not message or len(message)>3500: return jsonify(error='message_length'),400
    with db() as c: cur=c.execute('INSERT INTO broadcasts(text,created) VALUES(?,?)',(message,int(time.time()))); bid=cur.lastrowid
    # run synchronously to avoid losing the job on restart; report real success/failure counts.
    sent=failed=0
    for user in rows('SELECT id FROM users WHERE banned=0'):
        res=bot_api('sendMessage',{'chat_id':user['id'],'text':message,'parse_mode':'HTML'})
        if res.get('ok'): sent+=1
        else: failed+=1
        time.sleep(.04)
    with db() as c: c.execute('UPDATE broadcasts SET sent=?,failed=? WHERE id=?',(sent,failed,bid))
    return jsonify(ok=True,sent=sent,failed=failed)

# Telegram bot: long polling is optional but enabled when BOT_TOKEN exists.
def handle_callback(q):
    data=q.get('data',''); uid=q.get('from',{}).get('id'); msg=q.get('message',{}); chat=msg.get('chat',{}).get('id')
    if not is_admin(uid): bot_api('answerCallbackQuery',{'callback_query_id':q['id'],'text':'Admin ruxsati kerak','show_alert':True}); return
    parts=data.split(':')
    if len(parts)!=3: bot_api('answerCallbackQuery',{'callback_query_id':q['id']}); return
    kind,action,sid=parts
    try: sid=int(sid)
    except: return
    if kind=='order':
        endpoint=f'/api/admin/order/{sid}'
        # share same DB transitions as web panel
        o=row('SELECT * FROM orders WHERE id=?',(sid,))
        if o and o['status']=='new':
            with db() as c:
                if action=='done': c.execute("UPDATE orders SET status='done' WHERE id=?",(sid,))
                elif action=='refund':
                    c.execute("UPDATE orders SET status='cancelled',note='Admin qaytardi' WHERE id=?",(sid,)); c.execute('UPDATE users SET balance=balance+? WHERE id=?',(o['price'],o['user_id'])); c.execute('INSERT INTO transactions(user_id,amount,kind,reason,admin_id,created) VALUES(?,?,?,?,?,?)',(o['user_id'],o['price'],'refund',f'Buyurtma #{sid}',uid,int(time.time())))
            bot_api('editMessageReplyMarkup',{'chat_id':chat,'message_id':msg.get('message_id'),'reply_markup':{'inline_keyboard':[]}})
            bot_api('answerCallbackQuery',{'callback_query_id':q['id'],'text':'Buyurtma yangilandi'})
        else: bot_api('answerCallbackQuery',{'callback_query_id':q['id'],'text':'Buyurtma allaqachon ko‘rib chiqilgan'})
    elif kind=='topup':
        t=row('SELECT * FROM topups WHERE id=?',(sid,))
        if t and t['status']=='new':
            with db() as c:
                if action=='paid':
                    c.execute("UPDATE topups SET status='paid' WHERE id=?",(sid,)); c.execute('UPDATE users SET balance=balance+? WHERE id=?',(t['amount'],t['user_id'])); c.execute('INSERT INTO transactions(user_id,amount,kind,reason,admin_id,created) VALUES(?,?,?,?,?,?)',(t['user_id'],t['amount'],'topup',f'Topup #{sid}',uid,int(time.time())))
                else: c.execute("UPDATE topups SET status='rejected' WHERE id=?",(sid,))
            bot_api('editMessageReplyMarkup',{'chat_id':chat,'message_id':msg.get('message_id'),'reply_markup':{'inline_keyboard':[]}})
            bot_api('answerCallbackQuery',{'callback_query_id':q['id'],'text':'To‘lov holati yangilandi'})
        else: bot_api('answerCallbackQuery',{'callback_query_id':q['id'],'text':'So‘rov allaqachon ko‘rib chiqilgan'})

def bot_loop():
    offset=0
    while True:
        try:
            res=bot_api('getUpdates',{'timeout':25,'offset':offset,'allowed_updates':['message','callback_query']})
            if not res.get('ok'): time.sleep(4); continue
            for upd in res.get('result',[]):
                offset=upd['update_id']+1
                if 'callback_query' in upd: handle_callback(upd['callback_query']); continue
                m=upd.get('message',{}); text=m.get('text',''); user=m.get('from',{}); chat=m.get('chat',{}).get('id')
                if not text or not chat: continue
                if text.startswith('/start'):
                    url=WEBAPP_URL or PUBLIC_URL
                    if not url: bot_api('sendMessage',{'chat_id':chat,'text':'SYREXA ishga tushdi, lekin WEBAPP_URL sozlanmagan.'}); continue
                    bot_api('sendMessage',{'chat_id':chat,'text':'🎮 <b>SYREXA</b> — o‘yinlar do‘koniga xush kelibsiz!','parse_mode':'HTML','reply_markup':{'inline_keyboard':[[{'text':'🛍 Do‘konni ochish','web_app':{'url':url}}],[{'text':'📞 Yordam','url':setting('support_url','https://t.me/')}]]}})
                elif text.startswith('/admin') and is_admin(user.get('id')):
                    url=WEBAPP_URL or PUBLIC_URL
                    bot_api('sendMessage',{'chat_id':chat,'text':'🛠 SYREXA admin paneli','reply_markup':{'inline_keyboard':[[{'text':'Admin panelni ochish','web_app':{'url':url+'?admin=1'}}]]}})
                elif text.startswith('/id'):
                    bot_api('sendMessage',{'chat_id':chat,'text':f'Telegram ID: <code>{user.get("id")}</code>','parse_mode':'HTML'})
        except Exception:
            log.exception('Polling error'); time.sleep(4)

if BOT_TOKEN and os.getenv('DISABLE_BOT_POLLING','0')!='1':
    threading.Thread(target=bot_loop,daemon=True,name='telegram-polling').start()

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
