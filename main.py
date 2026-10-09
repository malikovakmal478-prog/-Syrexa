import os, json, time, hmac, hashlib, urllib.parse, sqlite3, threading, logging, html, csv, io
from functools import wraps
from pathlib import Path
from flask import Flask, request, jsonify, render_template, abort
import requests

APP_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv('DB_PATH', str(APP_DIR / 'syrexa.db'))
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
ADMIN_IDS = {int(x.strip()) for x in os.getenv('ADMIN_IDS', '').split(',') if x.strip().isdigit()}
BOT_USERNAME = os.getenv('BOT_USERNAME', '').strip().lstrip('@')
WEBAPP_URL = os.getenv('WEBAPP_URL', '').strip().rstrip('/')
PUBLIC_URL = os.getenv('RENDER_EXTERNAL_URL', '').strip().rstrip('/')
CHANNELS = [x.strip() for x in os.getenv('REQUIRED_CHANNELS', '').split(',') if x.strip()]
logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
log = logging.getLogger('syrexa')
app = Flask(__name__)
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
        CREATE TABLE IF NOT EXISTS support_tickets(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id), subject TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', admin_reply TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL, updated INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '', created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE, created INTEGER NOT NULL, PRIMARY KEY(user_id,product_id));
        CREATE TABLE IF NOT EXISTS referrals(referrer_id INTEGER NOT NULL REFERENCES users(id), referred_id INTEGER NOT NULL UNIQUE REFERENCES users(id), reward INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
        ''')
        defaults = {
          'brand':'SYREXA','hero_title':'O‘yinlaringiz uchun tezkor to‘ldirish','hero_subtitle':'PUBG UC • Free Fire Diamonds • Mobile Legends va boshqalar',
          'hero_image':'','support_url':'https://t.me/','channel_url':'','maintenance':'0','uzcard':'','currency':'so‘m',
          'order_notice':'Buyurtma qabul qilindi. Admin tekshirib, bajaradi.','topup_notice':'To‘lov cheki tekshirilgach balans to‘ldiriladi.',
          'referral_reward':'1000','min_topup':'5000','max_topup':'100000000','store_notice':'Buyurtmalar admin tomonidan bajariladi.','maintenance_message':'Texnik ishlar olib borilmoqda. Tez orada qaytamiz.'
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

def audit(admin_id, action, target='', details=''):
    try:
        with db() as c: c.execute('INSERT INTO audit_log(admin_id,action,target,details,created) VALUES(?,?,?,?,?)',(int(admin_id),str(action)[:100],str(target)[:160],str(details)[:1000],int(time.time())))
    except Exception: log.exception('audit log write failed')

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
    for channel in CHANNELS:
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
        u=current_user()
        if not u: return jsonify(error='telegram_auth_required'),401
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

@app.get('/')
def home(): return render_template('index.html', bot_username=BOT_USERNAME, token_configured=bool(BOT_TOKEN))
@app.get('/health')
def health(): return jsonify(ok=True, service='SYREXA', database=os.path.exists(DB_PATH), bot_configured=bool(BOT_TOKEN))
@app.get('/api/init')
@need_user
def api_init(u):
    games=rows('SELECT * FROM games WHERE active=1 ORDER BY sort,id')
    for g in games: g['products']=rows('SELECT * FROM products WHERE game_id=? AND active=1 ORDER BY sort,id',(g['id'],))
    return jsonify(user={k:u[k] for k in ('id','username','name','lang','balance','banned')},admin=is_admin(u['id']),games=games,settings={k:setting(k) for k in ('brand','hero_title','hero_subtitle','hero_image','support_url','channel_url','maintenance')},cards=rows('SELECT id,title,number,holder,bank FROM cards WHERE active=1 ORDER BY id'))

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
    if amount<int(setting('min_topup','5000') or 5000) or amount>int(setting('max_topup','100000000') or 100000000): return jsonify(error='amount_range',message=f'Summa {money(setting("min_topup","5000"))} dan {money(setting("max_topup","100000000"))} so‘mgacha.'),400
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

@app.get('/api/favorites')
@need_user
def api_favorites(u):
    return jsonify(products=rows('SELECT p.*,g.name AS game_name FROM favorites f JOIN products p ON p.id=f.product_id JOIN games g ON g.id=p.game_id WHERE f.user_id=? ORDER BY f.created DESC',(u['id'],)))

@app.post('/api/favorites/<int:product_id>')
@need_user
def api_favorite_toggle(u, product_id):
    if not row('SELECT id FROM products WHERE id=? AND active=1',(product_id,)): return jsonify(error='product_not_found'),404
    with db() as c:
        exists=c.execute('SELECT 1 FROM favorites WHERE user_id=? AND product_id=?',(u['id'],product_id)).fetchone()
        if exists: c.execute('DELETE FROM favorites WHERE user_id=? AND product_id=?',(u['id'],product_id)); saved=False
        else: c.execute('INSERT INTO favorites(user_id,product_id,created) VALUES(?,?,?)',(u['id'],product_id,int(time.time()))); saved=True
    return jsonify(ok=True,saved=saved)

@app.post('/api/tickets')
@need_user
def api_ticket_create(u):
    d=request.get_json(silent=True) or {}; subject=str(d.get('subject','')).strip(); message=str(d.get('message','')).strip()
    if len(subject)<3 or len(subject)>120 or len(message)<5 or len(message)>3000: return jsonify(error='ticket_validation',message='Mavzu 3–120, xabar 5–3000 belgi bo‘lsin.'),400
    now=int(time.time())
    with db() as c: cur=c.execute('INSERT INTO support_tickets(user_id,subject,message,created,updated) VALUES(?,?,?,?,?)',(u['id'],subject,message,now,now)); tid=cur.lastrowid
    notify_admins(f'🎫 <b>Yangi yordam murojaati #{tid}</b>\n👤 <code>{u["id"]}</code>\n<b>{html.escape(subject)}</b>\n{html.escape(message[:1500])}')
    return jsonify(ok=True,id=tid)

@app.get('/api/tickets')
@need_user
def api_ticket_list(u):
    return jsonify(tickets=rows('SELECT id,subject,message,status,admin_reply,created,updated FROM support_tickets WHERE user_id=? ORDER BY id DESC LIMIT 50',(u['id'],)))

@app.get('/api/referrals')
@need_user
def api_referrals(u):
    items=rows('SELECT referred_id,reward,created FROM referrals WHERE referrer_id=? ORDER BY created DESC LIMIT 100',(u['id'],))
    reward=int(setting('referral_reward','1000') or 1000)
    return jsonify(referral_link=(f'https://t.me/{BOT_USERNAME}?start=ref_{u["id"]}' if BOT_USERNAME else ''),reward=reward,count=len(items),items=items)

@app.get('/api/history')
@need_user
def api_history(u):
    return jsonify(orders=rows('SELECT id,game_name,product_name,price,player_id,status,created,note FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 100',(u['id'],)),topups=rows('SELECT id,amount,status,created FROM topups WHERE user_id=? ORDER BY id DESC LIMIT 100',(u['id'],)),transactions=rows('SELECT id,amount,kind,reason,created FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 100',(u['id'],)))

# Admin APIs: all writes require a validated Telegram WebApp signature AND admin ID.
@app.get('/api/admin/overview')
@need_admin
def admin_overview(u):
    return jsonify(stats={'users':row('SELECT COUNT(*) n FROM users')['n'],'orders':row('SELECT COUNT(*) n FROM orders')['n'],'pending_orders':row("SELECT COUNT(*) n FROM orders WHERE status='new'")['n'],'revenue':row("SELECT COALESCE(SUM(price),0) n FROM orders WHERE status='done'")['n'],'pending_topups':row("SELECT COUNT(*) n FROM topups WHERE status='new'")['n'],'balance_total':row('SELECT COALESCE(SUM(balance),0) n FROM users')['n']},users=rows('SELECT id,username,name,lang,balance,banned,created FROM users ORDER BY created DESC LIMIT 100'),orders=rows('SELECT * FROM orders ORDER BY id DESC LIMIT 100'),topups=rows('SELECT * FROM topups ORDER BY id DESC LIMIT 100'),games=rows('SELECT * FROM games ORDER BY sort,id'),products=rows('SELECT * FROM products ORDER BY game_id,sort,id'),cards=rows('SELECT * FROM cards ORDER BY id'),promos=rows('SELECT * FROM promos ORDER BY code'),settings={r['key']:r['value'] for r in rows('SELECT * FROM settings')},admins=rows('SELECT user_id,added_by,created FROM admins ORDER BY user_id'),tickets=rows("SELECT * FROM support_tickets ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END,id DESC LIMIT 100"),transactions=rows('SELECT * FROM transactions ORDER BY id DESC LIMIT 100'),audit=rows('SELECT * FROM audit_log ORDER BY id DESC LIMIT 100'),broadcasts=rows('SELECT * FROM broadcasts ORDER BY id DESC LIMIT 30'),referrals=rows('SELECT * FROM referrals ORDER BY created DESC LIMIT 100'))

@app.post('/api/admin/<kind>')
@need_admin
def admin_create(u,kind):
    d=request.get_json(silent=True) or {}
    if kind=='game':
        name=str(d.get('name','')).strip()
        if not name: return jsonify(error='name_required'),400
        with db() as c: cur=c.execute('INSERT INTO games(name,slug,category,icon,banner,player_field,description,active,sort) VALUES(?,?,?,?,?,?,?,?,?)',(name,str(d.get('slug','')).strip(),str(d.get('category','game')),str(d.get('icon','')),str(d.get('banner','')),str(d.get('player_field','Player ID')),str(d.get('description','')),int(bool(d.get('active',True))),int(d.get('sort',0))))
        audit(u['id'],'create_game',cur.lastrowid,name)
        return jsonify(ok=True,id=cur.lastrowid)
    if kind=='product':
        try: gid=int(d.get('game_id')); price=int(d.get('price',0))
        except: return jsonify(error='invalid_game_or_price'),400
        if price<0 or not row('SELECT id FROM games WHERE id=?',(gid,)): return jsonify(error='invalid_game_or_price'),400
        with db() as c: cur=c.execute('INSERT INTO products(game_id,name,price,image,badge,active,sort) VALUES(?,?,?,?,?,?,?)',(gid,str(d.get('name','Yangi mahsulot')).strip(),price,str(d.get('image','')),str(d.get('badge','')),int(bool(d.get('active',True))),int(d.get('sort',0))))
        audit(u['id'],'create_product',cur.lastrowid,name if 'name' in locals() else str(d.get('name','')))
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
        allowed={'brand','hero_title','hero_subtitle','hero_image','support_url','channel_url','maintenance','uzcard','order_notice','topup_notice','channel_url','referral_reward','min_topup','max_topup','store_notice','maintenance_message'}
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
        audit(u['id'],'balance_change',target,f'delta={delta}; balance={new}; reason={d.get("reason","")}')
        return jsonify(ok=True,balance=new)
    if kind=='user':
        try: target=int(d.get('user_id'))
        except: return jsonify(error='invalid_user'),400
        action=d.get('action')
        if action not in ('ban','unban'): return jsonify(error='invalid_action'),400
        with db() as c: cur=c.execute('UPDATE users SET banned=? WHERE id=?',(1 if action=='ban' else 0,target))
        if cur.rowcount: audit(u['id'],'user_'+action,target,'')
        return jsonify(ok=cur.rowcount>0)
    if kind=='admin':
        try: target=int(d.get('user_id'))
        except: return jsonify(error='invalid_user'),400
        if target in ADMIN_IDS and d.get('action')=='remove': return jsonify(error='root_admin_cannot_be_removed'),400
        with db() as c:
            if d.get('action')=='add': c.execute('INSERT OR IGNORE INTO admins(user_id,added_by,created) VALUES(?,?,?)',(target,u['id'],int(time.time())))
            elif d.get('action')=='remove': c.execute('DELETE FROM admins WHERE user_id=?',(target,))
            else: return jsonify(error='invalid_action'),400
        audit(u['id'],'admin_'+d.get('action',''),target,'')
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
    if cur.rowcount: audit(u['id'],'update_'+kind,item_id,json.dumps(changes,ensure_ascii=False))
    return jsonify(ok=cur.rowcount>0)

@app.delete('/api/admin/<kind>/<item_id>')
@need_admin
def admin_delete(u,kind,item_id):
    config={'game':('games','id'),'product':('products','id'),'card':('cards','id'),'promo':('promos','code')}
    if kind not in config: return jsonify(error='unknown_resource'),404
    table,col=config[kind]
    with db() as c: cur=c.execute(f'DELETE FROM {table} WHERE {col}=?',(int(item_id) if col=='id' else item_id,))
    if cur.rowcount: audit(u['id'],'delete_'+kind,item_id,'')
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
    audit(u['id'],'order_'+action,oid,o['user_id'])
    status_text='bajarildi' if action=='done' else 'bekor qilindi va pul qaytarildi'
    bot_api('sendMessage',{'chat_id':o['user_id'],'text':f'🛒 SYREXA: #{oid} buyurtmangiz {status_text}.'})
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
    audit(u['id'],'topup_'+action,tid,'')
    bot_api('sendMessage',{'chat_id':t['user_id'],'text':f'💳 SYREXA: #{tid} balans to‘ldirish so‘rovi '+('tasdiqlandi.' if action=='paid' else 'rad etildi.')})
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
    audit(u['id'],'broadcast',bid,f'sent={sent}; failed={failed}')
    return jsonify(ok=True,sent=sent,failed=failed)

@app.post('/api/admin/ticket/<int:ticket_id>')
@need_admin
def admin_ticket_update(u, ticket_id):
    d=request.get_json(silent=True) or {}; action=d.get('action'); reply=str(d.get('reply','')).strip()
    t=row('SELECT * FROM support_tickets WHERE id=?',(ticket_id,))
    if not t: return jsonify(error='not_found'),404
    if action not in ('reply','close','reopen'): return jsonify(error='invalid_action'),400
    status='closed' if action=='close' else 'open' if action=='reopen' else t['status']
    with db() as c: c.execute('UPDATE support_tickets SET status=?,admin_reply=CASE WHEN length(?)>0 THEN ? ELSE admin_reply END,updated=? WHERE id=?',(status,reply,reply,int(time.time()),ticket_id))
    if reply: bot_api('sendMessage',{'chat_id':t['user_id'],'text':f'🎫 SYREXA yordam xizmati — #{ticket_id}\n\n{reply}'})
    audit(u['id'],'ticket_'+action,ticket_id,reply)
    return jsonify(ok=True,status=status)

@app.get('/api/admin/export/<kind>')
@need_admin
def admin_export(u, kind):
    allowed={'users':('SELECT id,username,name,lang,balance,banned,created FROM users ORDER BY id','users.csv'), 'orders':('SELECT * FROM orders ORDER BY id','orders.csv'), 'transactions':('SELECT * FROM transactions ORDER BY id','transactions.csv')}
    if kind not in allowed: return jsonify(error='invalid_export'),400
    data=rows(allowed[kind][0]); out=io.StringIO(); cols=list(data[0].keys()) if data else ['empty']; writer=csv.DictWriter(out,fieldnames=cols); writer.writeheader(); writer.writerows(data)
    from flask import Response
    audit(u['id'],'export_'+kind,'',f'rows={len(data)}')
    return Response('\ufeff'+out.getvalue(),mimetype='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename={allowed[kind][1]}'})

@app.get('/api/admin/report')
@need_admin
def admin_report(u):
    now=int(time.time()); days=[]
    for i in range(6,-1,-1):
        start=now-(i+1)*86400; end=now-i*86400
        sales=row("SELECT COUNT(*) n,COALESCE(SUM(price),0) total FROM orders WHERE status='done' AND created>=? AND created<?",(start,end))
        tops=row("SELECT COALESCE(SUM(amount),0) total FROM topups WHERE status='paid' AND created>=? AND created<?",(start,end))
        days.append({'date':time.strftime('%Y-%m-%d',time.localtime(end-1)),'orders':sales['n'],'sales':sales['total'],'topups':tops['total']})
    return jsonify(days=days)

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
                    parts=text.split(maxsplit=1); payload=parts[1] if len(parts)>1 else ''
                    if payload.startswith('ref_'):
                        try:
                            referrer=int(payload[4:]); referred=int(user.get('id',0)); reward=max(0,int(setting('referral_reward','1000') or 1000))
                            if referrer!=referred and row('SELECT id FROM users WHERE id=?',(referrer,)) and not row('SELECT referred_id FROM referrals WHERE referred_id=?',(referred,)):
                                with db() as c:
                                    c.execute('INSERT OR IGNORE INTO users(id,username,name,created) VALUES(?,?,?,?)',(referred,user.get('username',''),(' '.join([user.get('first_name',''),user.get('last_name','')])).strip(),int(time.time())))
                                    c.execute('INSERT OR IGNORE INTO referrals(referrer_id,referred_id,reward,created) VALUES(?,?,?,?)',(referrer,referred,reward,int(time.time())))
                                    c.execute('UPDATE users SET balance=balance+? WHERE id=?',(reward,referrer))
                                    c.execute('INSERT INTO transactions(user_id,amount,kind,reason,created) VALUES(?,?,?,?,?)',(referrer,reward,'referral',f'Taklif bonusi: {referred}',int(time.time())))
                        except Exception: log.exception('referral processing failed')
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
