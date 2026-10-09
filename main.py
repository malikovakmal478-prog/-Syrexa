# -*- coding: utf-8 -*-
"""
SYREXA - o'yin to'ldirish do'koni (Telegram bot + Mini App + to'liq admin panel)
Bitta fayl. Render.com uchun tayyor.

ENV (Render > Environment):
  BOT_TOKEN   - BotFather tokeni
  ADMIN_IDS   - admin Telegram ID lari, vergul bilan: 123456789,987654321
  DB_PATH     - (ixtiyoriy) masalan /data/syrexa.db  (Render Disk ulangan bo'lsa)
requirements.txt:
  python-telegram-bot==21.6
  Flask==3.0.3
  requests==2.32.3
Start command:  python main.py
"""
import os, re, json, time, hmac, html, random, sqlite3, hashlib, asyncio, logging, threading, functools, urllib.parse
from datetime import datetime
import requests
from flask import Flask, request, jsonify, Response
from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, MenuButtonWebApp)
from telegram.error import BadRequest
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("syrexa")

TOKEN = os.getenv("BOT_TOKEN", "")
OWNERS = [int(x) for x in re.findall(r"\d+", os.getenv("ADMIN_IDS", ""))]
BASE_URL = (os.getenv("WEBAPP_URL") or os.getenv("RENDER_EXTERNAL_URL") or "").rstrip("/")
PORT = int(os.getenv("PORT", "10000"))
DB_PATH = os.getenv("DB_PATH", "syrexa.db")
E = html.escape

# ============================ DATABASE ============================
_lock = threading.RLock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

SCHEMA = """
create table if not exists users(id integer primary key,name text,username text,lang text default 'uz',balance integer default 0,banned integer default 0,joined integer);
create table if not exists settings(k text primary key,v text);
create table if not exists games(id integer primary key autoincrement,name text,cat text default 'game',img text default '',field text default 'Player ID',active integer default 1,sort integer default 0);
create table if not exists products(id integer primary key autoincrement,game_id integer,name text,price integer,active integer default 1);
create table if not exists banners(id integer primary key autoincrement,img text,link text default '');
create table if not exists cards(id integer primary key autoincrement,number text,holder text,bank text default 'UZCARD',active integer default 1);
create table if not exists topups(id integer primary key autoincrement,uid integer,amount integer,card_id integer,status text,created integer);
create table if not exists orders(id integer primary key autoincrement,uid integer,game text,product text,price integer,player text,status text,created integer);
create table if not exists promos(code text primary key,amount integer,left integer);
create table if not exists promo_uses(code text,uid integer,primary key(code,uid));
create table if not exists channels(id integer primary key autoincrement,chat_id text,title text,link text);
create table if not exists admins(id integer primary key);
"""
DEFAULTS = {
    "bot_name": "Syrexa",
    "welcome_uz": "Xush kelibsiz, {name}! 👋\n\nSyrexa — o'yinlarni tez, ishonchli va xavfsiz to'ldirish xizmati.",
    "welcome_ru": "Добро пожаловать, {name}! 👋\n\nSyrexa — быстрое, надёжное и безопасное пополнение игр.",
    "support_link": "", "channel_link": "", "min_topup": "1000", "card_ttl": "5",
    "welcome_img": "", "maintenance": "0",
}
SEED_GAMES = [("PUBG Mobile", "game"), ("Free Fire", "game"), ("Mobile Legends", "game"), ("Honor of Kings", "game"),
              ("Standoff 2", "game"), ("Steam Top Up", "game"), ("Telegram Stars", "game"), ("Telegram Premium", "game"),
              ("Bigo Live", "game"), ("Clash of Clans", "game"), ("Brawl Stars", "game"), ("Clash Royale", "game"),
              ("Roblox Robux", "promo"), ("Discord Nitro", "promo")]

def ex(sql, args=()):
    with _lock:
        c = db.execute(sql, args); db.commit(); return c
def qa(sql, args=()):
    with _lock:
        return [dict(r) for r in db.execute(sql, args).fetchall()]
def q1(sql, args=()):
    r = qa(sql, args); return r[0] if r else None

def init_db():
    with _lock:
        db.executescript(SCHEMA); db.commit()
    if not q1("select 1 x from games"):
        for i, (n, c) in enumerate(SEED_GAMES):
            ex("insert into games(name,cat,sort) values(?,?,?)", (n, c, i))

def gs(k):
    r = q1("select v from settings where k=?", (k,))
    return r["v"] if r else DEFAULTS.get(k, "")
def ss(k, v):
    ex("insert into settings(k,v) values(?,?) on conflict(k) do update set v=excluded.v", (k, str(v)))

def all_admins():
    return list(dict.fromkeys(OWNERS + [r["id"] for r in qa("select id from admins")]))
def is_admin(uid):
    return uid in all_admins()
def money(n):
    return f"{int(n):,}".replace(",", " ")
def ts(t):
    return datetime.utcfromtimestamp(int(t) + 18000).strftime("%d.%m %H:%M")
def link_ok(u):
    return bool(u) and u.startswith(("https://", "http://", "tg://"))

def tg(method, **kw):
    try:
        return requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=kw, timeout=20).json()
    except Exception as e:
        log.warning("tg %s: %s", method, e); return {}

def notify(uid, text):
    tg("sendMessage", chat_id=uid, text=text, parse_mode="HTML")

def upsert_user(uid, name, username):
    ex("insert or ignore into users(id,name,username,lang,balance,joined) values(?,?,?,?,0,?)",
       (uid, name, username or "", "uz", int(time.time())))
    ex("update users set name=?,username=? where id=?", (name, username or "", uid))
    return q1("select * from users where id=?", (uid,))

_subcache = {}
def not_subbed(uid):
    if is_admin(uid): return []
    out = []
    for ch in qa("select * from channels"):
        key = (uid, ch["chat_id"]); c = _subcache.get(key)
        if c and time.time() - c[0] < 45: ok = c[1]
        else:
            r = tg("getChatMember", chat_id=ch["chat_id"], user_id=uid)
            st = r.get("result", {}).get("status") if r.get("ok") else "member"
            ok = st in ("creator", "administrator", "member", "restricted")
            _subcache[key] = (time.time(), ok)
        if not ok: out.append(ch)
    return out

def order_kb(oid):
    return {"inline_keyboard": [[{"text": "✅ Bajarildi", "callback_data": f"o:done:{oid}"},
                                 {"text": "❌ Bekor (qaytarish)", "callback_data": f"o:no:{oid}"}]]}
def topup_kb(tid):
    return {"inline_keyboard": [[{"text": "✅ Tasdiqlash", "callback_data": f"t:ok:{tid}"},
                                 {"text": "❌ Rad etish", "callback_data": f"t:no:{tid}"}]]}
def ulink(u):
    return f'<a href="tg://user?id={u["id"]}">{E(u["name"] or str(u["id"]))}</a> (<code>{u["id"]}</code>)'

# ============================ WEB API ============================
web = Flask(__name__)

def auth():
    init = request.headers.get("X-Init", "")
    try:
        data = dict(urllib.parse.parse_qsl(init, keep_blank_values=True))
        h = data.pop("hash")
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), h): return None
        if time.time() - int(data.get("auth_date", 0)) > 86400 * 7: return None
        u = json.loads(data["user"])
    except Exception:
        return None
    name = (u.get("first_name", "") + " " + u.get("last_name", "")).strip()
    return upsert_user(int(u["id"]), name, u.get("username"))

def need_user(f):
    @functools.wraps(f)
    def w(*a, **k):
        u = auth()
        if not u: return jsonify(err="auth"), 401
        if u["banned"]: return jsonify(err="banned"), 403
        if gs("maintenance") == "1" and not is_admin(u["id"]): return jsonify(err="maintenance"), 503
        return f(u, *a, **k)
    return w

@web.route("/")
def index():
    r = Response(INDEX.replace("__BOT__", E(gs("bot_name"))), mimetype="text/html")
    r.headers["Cache-Control"] = "no-store"; return r

@web.route("/health")
def health():
    return "ok"

_imgdir = "/tmp/syrexa_img"; os.makedirs(_imgdir, exist_ok=True)
@web.route("/img/<fid>")
def img(fid):
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", fid): return "", 404
    p = os.path.join(_imgdir, fid)
    if not os.path.exists(p):
        r = tg("getFile", file_id=fid)
        fp = r.get("result", {}).get("file_path")
        if not fp: return "", 404
        try:
            d = requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{fp}", timeout=30).content
            open(p, "wb").write(d)
        except Exception:
            return "", 404
    data = open(p, "rb").read()
    mt = "image/png" if data[:4] == b"\x89PNG" else "image/webp" if data[:4] == b"RIFF" else "image/jpeg"
    return Response(data, mimetype=mt, headers={"Cache-Control": "public, max-age=86400"})

@web.route("/api/init")
@need_user
def api_init(u):
    subs = [{"title": c["title"] or c["chat_id"], "link": c["link"]} for c in not_subbed(u["id"])]
    return jsonify(
        user={"id": u["id"], "name": u["name"], "username": u["username"], "lang": u["lang"],
              "balance": u["balance"], "admin": is_admin(u["id"])},
        banners=qa("select id,img,link from banners order by id"),
        games=qa("select id,name,cat,img from games where active=1 order by sort,id"),
        cfg={"support": gs("support_link"), "channel": gs("channel_link"), "min": int(gs("min_topup") or 1000),
             "bot": gs("bot_name")}, sub=subs)

@web.route("/api/game/<int:gid>")
@need_user
def api_game(u, gid):
    g = q1("select id,name,img,field,cat from games where id=? and active=1", (gid,))
    if not g: return jsonify(err="nf"), 404
    g["products"] = qa("select id,name,price from products where game_id=? and active=1 order by price,id", (gid,))
    return jsonify(g)

@web.route("/api/order", methods=["POST"])
@need_user
def api_order(u):
    d = request.get_json(silent=True) or {}
    player = str(d.get("player", "")).strip()[:100]
    p = q1("select p.*,g.name gname from products p join games g on g.id=p.game_id where p.id=? and p.active=1 and g.active=1",
           (int(d.get("product_id", 0)),))
    if not p or len(player) < 2: return jsonify(err="bad"), 400
    with _lock:
        c = ex("update users set balance=balance-? where id=? and balance>=?", (p["price"], u["id"], p["price"]))
        if c.rowcount == 0: return jsonify(err="balance"), 400
        oid = ex("insert into orders(uid,game,product,price,player,status,created) values(?,?,?,?,?,'pending',?)",
                 (u["id"], p["gname"], p["name"], p["price"], player, int(time.time()))).lastrowid
    txt = (f"🛒 <b>Yangi buyurtma #{oid}</b>\n👤 {ulink(u)}\n🎮 {E(p['gname'])} — {E(p['name'])}\n"
           f"🆔 ID: <code>{E(player)}</code>\n💰 {money(p['price'])} so'm")
    for a in all_admins(): tg("sendMessage", chat_id=a, text=txt, parse_mode="HTML", reply_markup=order_kb(oid))
    return jsonify(ok=True, id=oid, balance=q1("select balance from users where id=?", (u["id"],))["balance"])

@web.route("/api/topup", methods=["POST"])
@need_user
def api_topup(u):
    d = request.get_json(silent=True) or {}
    try: amount = int(d.get("amount", 0))
    except Exception: amount = 0
    mn = int(gs("min_topup") or 1000)
    if amount < mn or amount > 100000000: return jsonify(err="min", min=mn), 400
    cards = qa("select * from cards where active=1")
    if not cards: return jsonify(err="nocard"), 400
    cd = random.choice(cards)
    tid = ex("insert into topups(uid,amount,card_id,status,created) values(?,?,?,'new',?)",
             (u["id"], amount, cd["id"], int(time.time()))).lastrowid
    return jsonify(id=tid, amount=amount, ttl=int(gs("card_ttl") or 5) * 60,
                   card={"number": cd["number"], "holder": cd["holder"], "bank": cd["bank"]})

@web.route("/api/topup/<int:tid>/paid", methods=["POST"])
@need_user
def api_paid(u, tid):
    c = ex("update topups set status='pending' where id=? and uid=? and status='new'", (tid, u["id"]))
    if c.rowcount == 0: return jsonify(err="state"), 400
    t = q1("select * from topups where id=?", (tid,))
    cd = q1("select * from cards where id=?", (t["card_id"],)) or {}
    txt = (f"💳 <b>To'ldirish so'rovi #{tid}</b>\n👤 {ulink(u)}\n💰 <b>{money(t['amount'])}</b> so'm\n"
           f"🏦 Karta: <code>{E(cd.get('number',''))}</code> ({E(cd.get('holder',''))})\n🕒 {ts(t['created'])}\n\n"
           f"Pul kartaga tushganini tekshiring, so'ng tasdiqlang.")
    for a in all_admins(): tg("sendMessage", chat_id=a, text=txt, parse_mode="HTML", reply_markup=topup_kb(tid))
    return jsonify(ok=True)

@web.route("/api/history")
@need_user
def api_history(u):
    return jsonify(
        orders=qa("select id,game,product,price,status,created from orders where uid=? order by id desc limit 50", (u["id"],)),
        tx=qa("select id,amount,status,created from topups where uid=? and status!='new' order by id desc limit 50", (u["id"],)))

@web.route("/api/promo", methods=["POST"])
@need_user
def api_promo(u):
    code = str((request.get_json(silent=True) or {}).get("code", "")).strip().upper()
    with _lock:
        p = q1("select * from promos where code=?", (code,))
        if not p or p["left"] <= 0: return jsonify(err="nf"), 400
        if q1("select 1 x from promo_uses where code=? and uid=?", (code, u["id"])): return jsonify(err="used"), 400
        ex("insert into promo_uses values(?,?)", (code, u["id"]))
        ex("update promos set left=left-1 where code=?", (code,))
        ex("update users set balance=balance+? where id=?", (p["amount"], u["id"]))
    return jsonify(ok=True, amount=p["amount"], balance=q1("select balance from users where id=?", (u["id"],))["balance"])

@web.route("/api/lang", methods=["POST"])
@need_user
def api_lang(u):
    l = (request.get_json(silent=True) or {}).get("lang")
    if l in ("uz", "ru"): ex("update users set lang=? where id=?", (l, u["id"]))
    return jsonify(ok=True)

# ============================ BOT: USER SIDE ============================
def webapp_url():
    return BASE_URL + "/"

async def send_welcome(update: Update, ctx):
    u = update.effective_user
    row = upsert_user(u.id, (u.first_name or "") + " " + (u.last_name or ""), u.username)
    if row["banned"]: return
    if gs("maintenance") == "1" and not is_admin(u.id):
        return await ctx.bot.send_message(u.id, "🛠 Texnik ishlar olib borilmoqda. Keyinroq urinib ko'ring.")
    subs = not_subbed(u.id)
    if subs:
        rows = [[InlineKeyboardButton(f"📢 {c['title'] or 'Kanal'}", url=c["link"])] for c in subs if link_ok(c["link"])]
        rows.append([InlineKeyboardButton("✅ Tekshirish", callback_data="chk")])
        return await ctx.bot.send_message(u.id, "Botdan foydalanish uchun kanallarga obuna bo'ling:",
                                          reply_markup=InlineKeyboardMarkup(rows))
    text = gs("welcome_ru" if row["lang"] == "ru" else "welcome_uz").replace("{name}", E(u.first_name or ""))
    rows = [[InlineKeyboardButton("📱 Ilovani ochish", web_app=WebAppInfo(url=webapp_url()))]]
    r2 = []
    if link_ok(gs("channel_link")): r2.append(InlineKeyboardButton("Bizning kanal", url=gs("channel_link")))
    if link_ok(gs("support_link")): r2.append(InlineKeyboardButton("Yordam", url=gs("support_link")))
    if r2: rows.append(r2)
    kb = InlineKeyboardMarkup(rows)
    img = gs("welcome_img")
    if img:
        try:
            return await ctx.bot.send_photo(u.id, img, caption=text, reply_markup=kb, parse_mode="HTML")
        except Exception: pass
    await ctx.bot.send_message(u.id, text, reply_markup=kb, parse_mode="HTML")

async def cmd_start(update, ctx):
    ctx.user_data.pop("st", None)
    await send_welcome(update, ctx)

async def cb_chk(update, ctx):
    q = update.callback_query
    _subcache.clear()
    if not_subbed(q.from_user.id):
        return await q.answer("❌ Hali obuna bo'lmagansiz", show_alert=True)
    await q.answer("✅")
    try: await q.message.delete()
    except Exception: pass
    await send_welcome(update, ctx)

# ============================ BOT: ADMIN ============================
def AK(rows):
    out = []
    for r in rows:
        out.append([InlineKeyboardButton(t, url=d) if d.startswith("http") else InlineKeyboardButton(t, callback_data=d) for t, d in r])
    return InlineKeyboardMarkup(out)

BACK = [("🔙 Admin menyu", "a:home")]

async def show(update, view):
    text, rows = view
    q = update.callback_query
    if q:
        try:
            return await q.edit_message_text(text, reply_markup=AK(rows), parse_mode="HTML", disable_web_page_preview=True)
        except BadRequest as e:
            if "not modified" in str(e).lower(): return
            return await q.message.reply_text(text, reply_markup=AK(rows), parse_mode="HTML", disable_web_page_preview=True)
    await update.message.reply_text(text, reply_markup=AK(rows), parse_mode="HTML", disable_web_page_preview=True)

def v_home():
    return ("🛠 <b>SYREXA — Admin panel</b>\nKerakli bo'limni tanlang:", [
        [("📊 Statistika", "a:stat"), ("👥 Foydalanuvchilar", "a:users")],
        [("🎮 O'yinlar / Narxlar", "a:games"), ("🖼 Bannerlar", "a:banners")],
        [("💳 Kartalar", "a:cards"), ("💰 To'ldirishlar", "a:tops")],
        [("📦 Buyurtmalar", "a:ords"), ("🎟 Promokodlar", "a:promos")],
        [("📢 Majburiy obuna", "a:chs"), ("📨 Xabar yuborish", "a:bc")],
        [("⚙️ Sozlamalar", "a:set"), ("👮 Adminlar", "a:adms")]])

def v_stat():
    d0 = (int(time.time()) + 18000) // 86400 * 86400 - 18000
    n = q1("select count(*) c, coalesce(sum(balance),0) b from users")
    new = q1("select count(*) c from users where joined>=?", (d0,))["c"]
    tp = q1("select count(*) c, coalesce(sum(amount),0) s from topups where status='approved'")
    tpt = q1("select coalesce(sum(amount),0) s from topups where status='approved' and created>=?", (d0,))["s"]
    od = q1("select count(*) c, coalesce(sum(price),0) s from orders where status='done'")
    odp = q1("select count(*) c from orders where status='pending'")["c"]
    tpp = q1("select count(*) c from topups where status='pending'")["c"]
    txt = (f"📊 <b>Statistika</b>\n\n👥 Foydalanuvchilar: <b>{n['c']}</b> (bugun +{new})\n💼 Umumiy balans: <b>{money(n['b'])}</b> so'm\n\n"
           f"💰 To'ldirilgan: <b>{money(tp['s'])}</b> so'm ({tp['c']} ta)\n📅 Bugun: <b>{money(tpt)}</b> so'm\n\n"
           f"📦 Bajarilgan buyurtmalar: <b>{od['c']}</b> ta — {money(od['s'])} so'm\n"
           f"⏳ Kutilayotgan buyurtma: <b>{odp}</b>\n⏳ Kutilayotgan to'ldirish: <b>{tpp}</b>")
    return txt, [[("🔄 Yangilash", "a:stat")], BACK]

def v_users():
    top = qa("select * from users order by id desc limit 8")
    rows = [[("🔎 Qidirish (ID / @username)", "a:find")]]
    for u in top: rows.append([(f"{'🚫 ' if u['banned'] else ''}{u['name'][:22]} · {money(u['balance'])}", f"a:u:{u['id']}")])
    rows.append(BACK)
    return "👥 <b>Foydalanuvchilar</b>\nSo'nggi qo'shilganlar:", rows

def v_user(uid):
    u = q1("select * from users where id=?", (uid,))
    if not u: return "Topilmadi", [BACK]
    oc = q1("select count(*) c from orders where uid=?", (uid,))["c"]
    txt = (f"👤 {ulink(u)}\n@{E(u['username'] or '-')}\n💰 Balans: <b>{money(u['balance'])}</b> so'm\n"
           f"📦 Buyurtmalar: {oc}\n🌐 Til: {u['lang']}\n📅 {ts(u['joined'] or 0)}\n{'🚫 BLOKLANGAN' if u['banned'] else ''}")
    return txt, [[("➕ Balans qo'shish", f"a:bal:{uid}:+"), ("➖ Balans ayirish", f"a:bal:{uid}:-")],
                 [("✅ Blokdan chiqarish" if u["banned"] else "🚫 Bloklash", f"a:ban:{uid}")],
                 [("🔙 Userlar", "a:users")]]

def v_games():
    rows = [[("➕ O'yin qo'shish", "a:gnew")]]
    for g in qa("select * from games order by sort,id"):
        rows.append([(f"{'🟢' if g['active'] else '🔴'} {'🎁' if g['cat']=='promo' else '🎮'} {g['name']}", f"a:g:{g['id']}")])
    rows.append(BACK)
    return "🎮 <b>O'yinlar</b>\nO'yinni tanlab, rasm/nom/mahsulot va narxlarni o'zgartiring:", rows

def v_game(gid):
    g = q1("select * from games where id=?", (gid,))
    if not g: return "Topilmadi", [[("🔙", "a:games")]]
    ps = qa("select * from products where game_id=? order by price,id", (gid,))
    txt = (f"🎮 <b>{E(g['name'])}</b>\nKategoriya: {'Promokodlar' if g['cat']=='promo' else 'O`yinlar'}\n"
           f"ID maydoni: <i>{E(g['field'])}</i>\nRasm: {'✅' if g['img'] else '❌'}\nHolat: {'faol' if g['active'] else 'o`chirilgan'}\n"
           f"Mahsulotlar: {len(ps)} ta")
    rows = [[("➕ Mahsulot qo'shish", f"a:padd:{gid}")]]
    for p in ps: rows.append([(f"{'🟢' if p['active'] else '🔴'} {p['name']} — {money(p['price'])}", f"a:p:{p['id']}")])
    rows += [[("✏️ Nom", f"a:gname:{gid}"), ("🖼 Rasm", f"a:gimg:{gid}")],
             [("🔤 ID maydoni nomi", f"a:gfield:{gid}"), ("📂 Kategoriya", f"a:gcat2:{gid}")],
             [("👁 Yoqish/O'chirish", f"a:gtog:{gid}"), ("🗑 O'yinni o'chirish", f"a:gdel:{gid}")],
             [("🔙 O'yinlar", "a:games")]]
    return txt, rows

def v_prod(pid):
    p = q1("select * from products where id=?", (pid,))
    if not p: return "Topilmadi", [[("🔙", "a:games")]]
    return (f"📦 <b>{E(p['name'])}</b>\n💰 {money(p['price'])} so'm\nHolat: {'faol' if p['active'] else 'o`chirilgan'}",
            [[("✏️ Nom", f"a:pname:{pid}"), ("💰 Narx", f"a:pprice:{pid}")],
             [("👁 Yoqish/O'chirish", f"a:ptog:{pid}"), ("🗑 O'chirish", f"a:pdel:{pid}")],
             [("🔙 O'yin", f"a:g:{p['game_id']}")]])

def v_banners():
    bs = qa("select * from banners order by id")
    rows = [[("➕ Banner qo'shish", "a:badd")]]
    for b in bs: rows.append([(f"🗑 Banner #{b['id']} {('→ '+b['link'][:25]) if b['link'] else ''}", f"a:bdel:{b['id']}")])
    rows.append(BACK)
    return f"🖼 <b>Bannerlar</b> ({len(bs)} ta)\nIlova bosh sahifasidagi reklama bannerlari. O'chirish uchun bosing.", rows

def v_cards():
    cs = qa("select * from cards order by id")
    rows = [[("➕ Karta qo'shish", "a:cadd")]]
    for c in cs: rows.append([(f"{'🟢' if c['active'] else '🔴'} {c['bank']} {c['number']} · {c['holder']}", f"a:c:{c['id']}")])
    rows.append(BACK)
    return ("💳 <b>Kartalar</b>\nTo'ldirishda faol kartalardan biri tasodifiy beriladi.\nKartasiz to'ldirish ishlamaydi!", rows)

def v_card(cid):
    c = q1("select * from cards where id=?", (cid,))
    if not c: return "Topilmadi", [[("🔙", "a:cards")]]
    return (f"💳 <code>{E(c['number'])}</code>\n👤 {E(c['holder'])}\n🏦 {E(c['bank'])}\nHolat: {'faol' if c['active'] else 'o`chirilgan'}",
            [[("✏️ Almashtirish (raqam | ism | bank)", f"a:cedit:{cid}")],
             [("👁 Yoqish/O'chirish", f"a:ctog:{cid}"), ("🗑 O'chirish", f"a:cdel:{cid}")], [("🔙 Kartalar", "a:cards")]])

def v_tops():
    ts_ = qa("select * from topups where status='pending' order by id desc limit 15")
    rows = [[(f"#{t['id']} · {money(t['amount'])} · {ts(t['created'])}", f"a:t:{t['id']}")] for t in ts_]
    rows.append(BACK)
    return f"💰 <b>Kutilayotgan to'ldirishlar</b> ({len(ts_)})", rows

def v_top(tid):
    t = q1("select * from topups where id=?", (tid,))
    if not t: return "Topilmadi", [[("🔙", "a:tops")]]
    u = q1("select * from users where id=?", (t["uid"],)) or {"id": t["uid"], "name": ""}
    rows = [[("✅ Tasdiqlash", f"t:ok:{tid}"), ("❌ Rad etish", f"t:no:{tid}")]] if t["status"] == "pending" else []
    rows.append([("🔙", "a:tops")])
    return f"💳 To'ldirish #{tid}\n👤 {ulink(u)}\n💰 {money(t['amount'])} so'm\nHolat: <b>{t['status']}</b>", rows

def v_ords():
    os_ = qa("select * from orders where status='pending' order by id desc limit 15")
    rows = [[(f"#{o['id']} {o['game']} · {o['product']} · {money(o['price'])}", f"a:o:{o['id']}")] for o in os_]
    rows.append(BACK)
    return f"📦 <b>Kutilayotgan buyurtmalar</b> ({len(os_)})", rows

def v_ord(oid):
    o = q1("select * from orders where id=?", (oid,))
    if not o: return "Topilmadi", [[("🔙", "a:ords")]]
    u = q1("select * from users where id=?", (o["uid"],)) or {"id": o["uid"], "name": ""}
    rows = [[("✅ Bajarildi", f"o:done:{oid}"), ("❌ Bekor (qaytarish)", f"o:no:{oid}")]] if o["status"] == "pending" else []
    rows.append([("🔙", "a:ords")])
    return (f"📦 Buyurtma #{oid}\n👤 {ulink(u)}\n🎮 {E(o['game'])} — {E(o['product'])}\n🆔 <code>{E(o['player'])}</code>\n"
            f"💰 {money(o['price'])} so'm\nHolat: <b>{o['status']}</b>"), rows

def v_promos():
    ps = qa("select * from promos")
    rows = [[("➕ Promokod qo'shish", "a:pradd")]]
    for p in ps: rows.append([(f"🗑 {p['code']} · {money(p['amount'])} · qolgan {p['left']}", f"a:prdel:{p['code']}")])
    rows.append(BACK)
    return "🎟 <b>Promokodlar</b>\nBalansga pul beradigan kodlar. O'chirish uchun bosing.", rows

def v_chs():
    cs = qa("select * from channels")
    rows = [[("➕ Kanal/guruh qo'shish", "a:chadd")]]
    for c in cs: rows.append([(f"🗑 {c['title'] or c['chat_id']}", f"a:chdel:{c['id']}")])
    rows.append(BACK)
    return "📢 <b>Majburiy obuna</b>\nBot kanalda ADMIN bo'lishi shart.", rows

SET_KEYS = [("bot_name", "Bot nomi"), ("welcome_uz", "Salomlashuv (UZ)"), ("welcome_ru", "Salomlashuv (RU)"),
            ("welcome_img", "Salomlashuv rasmi"), ("support_link", "Yordam havolasi"), ("channel_link", "Kanal havolasi"),
            ("min_topup", "Minimal to'ldirish (so'm)"), ("card_ttl", "Karta amal qilish vaqti (daqiqa)")]

def v_set():
    rows = [[(f"✏️ {lbl}", f"a:s:{k}")] for k, lbl in SET_KEYS]
    m = gs("maintenance") == "1"
    rows.append([(f"🛠 Texnik ishlar: {'YOQIQ' if m else 'o`chiq'}", "a:s:maintenance")])
    rows.append(BACK)
    txt = "⚙️ <b>Sozlamalar</b>\n\n" + "\n".join(
        f"• {lbl}: <code>{E((gs(k) or '-')[:40])}</code>" for k, lbl in SET_KEYS if k != "welcome_img")
    return txt + "\n\nSalomlashuv matnida <code>{name}</code> — foydalanuvchi ismi.", rows

def v_adms():
    rows = [[("➕ Admin qo'shish", "a:amadd")]]
    for r in qa("select id from admins"): rows.append([(f"🗑 {r['id']}", f"a:amdel:{r['id']}")])
    rows.append(BACK)
    return f"👮 <b>Adminlar</b>\nAsosiy (ENV): {', '.join(map(str, OWNERS)) or '-'}", rows

async def ask(update, ctx, st, text):
    ctx.user_data["st"] = st
    await show(update, (text + "\n\n<i>Bekor qilish: /cancel</i>", [[("🔙 Bekor", "a:home")]]))

async def cmd_admin(update, ctx):
    if not is_admin(update.effective_user.id): return
    ctx.user_data.pop("st", None)
    await show(update, v_home())

async def cmd_cancel(update, ctx):
    ctx.user_data.pop("st", None)
    if is_admin(update.effective_user.id): await show(update, v_home())

async def adm_cb(update, ctx):
    q = update.callback_query
    if not is_admin(q.from_user.id): return await q.answer("⛔", show_alert=True)
    await q.answer()
    d = q.data.split(":"); a = d[1]; x = d[2] if len(d) > 2 else None
    ctx.user_data.pop("st", None) if a in ("home", "stat", "users", "games", "banners", "cards", "tops", "ords", "promos", "chs", "set", "adms") else None
    S = lambda v: show(update, v)
    if a == "home": return await S(v_home())
    if a == "stat": return await S(v_stat())
    if a == "users": return await S(v_users())
    if a == "find": return await ask(update, ctx, ("find",), "🔎 Foydalanuvchi ID yoki @username yuboring:")
    if a == "u": return await S(v_user(int(x)))
    if a == "bal": return await ask(update, ctx, ("bal", int(x), d[3]), f"{'➕ Qo`shiladigan' if d[3]=='+' else '➖ Ayiriladigan'} summani yuboring (so'm):")
    if a == "ban":
        u = q1("select banned from users where id=?", (int(x),))
        ex("update users set banned=? where id=?", (0 if u["banned"] else 1, int(x)))
        return await S(v_user(int(x)))
    # games
    if a == "games": return await S(v_games())
    if a == "gnew": return await ask(update, ctx, ("gnew",), "🎮 Yangi o'yin nomini yuboring:")
    if a == "gcat":
        name = ctx.user_data.get("gname", "Yangi")
        gid = ex("insert into games(name,cat,sort) values(?,?,?)", (name, x, int(time.time()) % 100000)).lastrowid
        return await ask(update, ctx, ("gimg", gid), f"✅ «{E(name)}» qo'shildi.\n🖼 Endi o'yin rasmini yuboring (yoki /cancel):")
    if a == "g": return await S(v_game(int(x)))
    if a == "gname": return await ask(update, ctx, ("gname", int(x)), "✏️ Yangi nomni yuboring:")
    if a == "gimg": return await ask(update, ctx, ("gimg", int(x)), "🖼 Rasmni yuboring (rasm sifatida):")
    if a == "gfield": return await ask(update, ctx, ("gfield", int(x)), "🔤 Foydalanuvchi to'ldiradigan maydon nomi (masalan: Player ID, UID, Telegram username):")
    if a == "gcat2":
        g = q1("select cat from games where id=?", (int(x),))
        ex("update games set cat=? where id=?", ("promo" if g["cat"] == "game" else "game", int(x)))
        return await S(v_game(int(x)))
    if a == "gtog":
        ex("update games set active=1-active where id=?", (int(x),)); return await S(v_game(int(x)))
    if a == "gdel":
        return await S(("⚠️ O'yin va uning barcha mahsulotlari o'chiriladi. Ishonchingiz komilmi?",
                        [[("✅ Ha, o'chirish", f"a:gdel2:{x}"), ("❌ Yo'q", f"a:g:{x}")]]))
    if a == "gdel2":
        ex("delete from products where game_id=?", (int(x),)); ex("delete from games where id=?", (int(x),))
        return await S(v_games())
    if a == "padd": return await ask(update, ctx, ("padd", int(x)),
        "➕ Mahsulot(lar)ni yuboring. Har qatorda: <code>nom | narx</code>\nMasalan:\n<code>60 UC | 12000\n325 UC | 60000</code>")
    if a == "p": return await S(v_prod(int(x)))
    if a == "pname": return await ask(update, ctx, ("pname", int(x)), "✏️ Yangi mahsulot nomi:")
    if a == "pprice": return await ask(update, ctx, ("pprice", int(x)), "💰 Yangi narx (so'm):")
    if a == "ptog":
        ex("update products set active=1-active where id=?", (int(x),)); return await S(v_prod(int(x)))
    if a == "pdel":
        p = q1("select game_id from products where id=?", (int(x),)); ex("delete from products where id=?", (int(x),))
        return await S(v_game(p["game_id"]) if p else v_games())
    # banners
    if a == "banners": return await S(v_banners())
    if a == "badd": return await ask(update, ctx, ("badd",), "🖼 Banner rasmini yuboring. Ixtiyoriy: izohga havola (https://...) yozsangiz, bosilganda ochiladi.")
    if a == "bdel": ex("delete from banners where id=?", (int(x),)); return await S(v_banners())
    # cards
    if a == "cards": return await S(v_cards())
    if a == "cadd": return await ask(update, ctx, ("cadd",), "💳 Format: <code>karta raqami | Ism Familiya | Bank</code>\nMasalan: <code>8600123412341234 | Ali Valiyev | UZCARD</code>")
    if a == "c": return await S(v_card(int(x)))
    if a == "cedit": return await ask(update, ctx, ("cedit", int(x)), "✏️ Yangi ma'lumot: <code>karta raqami | Ism Familiya | Bank</code>")
    if a == "ctog": ex("update cards set active=1-active where id=?", (int(x),)); return await S(v_card(int(x)))
    if a == "cdel": ex("delete from cards where id=?", (int(x),)); return await S(v_cards())
    # topups / orders
    if a == "tops": return await S(v_tops())
    if a == "t": return await S(v_top(int(x)))
    if a == "ords": return await S(v_ords())
    if a == "o": return await S(v_ord(int(x)))
    # promos
    if a == "promos": return await S(v_promos())
    if a == "pradd": return await ask(update, ctx, ("pradd",), "🎟 Format: <code>KOD | summa | necha kishi ishlata oladi</code>\nMasalan: <code>FREE5000 | 5000 | 100</code>")
    if a == "prdel": ex("delete from promos where code=?", (x,)); return await S(v_promos())
    # channels
    if a == "chs": return await S(v_chs())
    if a == "chadd": return await ask(update, ctx, ("chadd",), "📢 Kanal @username yoki ID yuboring (bot u yerda admin bo'lsin).\nMasalan: <code>@mychannel</code>")
    if a == "chdel": ex("delete from channels where id=?", (int(x),)); return await S(v_chs())
    # broadcast
    if a == "bc": return await ask(update, ctx, ("bc",), "📨 Barcha foydalanuvchilarga yuboriladigan xabarni yuboring (matn, rasm, video — istalgan):")
    if a == "bcgo":
        st = ctx.user_data.pop("bcmsg", None)
        if not st: return await S(v_home())
        await q.edit_message_text("⏳ Yuborilmoqda...")
        ok = bad = 0
        for u in qa("select id from users where banned=0"):
            try:
                await ctx.bot.copy_message(u["id"], st[0], st[1]); ok += 1
            except Exception: bad += 1
            await asyncio.sleep(0.05)
        return await q.message.reply_text(f"✅ Yuborildi: {ok}\n❌ Xato: {bad}", reply_markup=AK([BACK]))
    # settings
    if a == "set": return await S(v_set())
    if a == "s":
        if x == "maintenance":
            ss("maintenance", "0" if gs("maintenance") == "1" else "1"); return await S(v_set())
        lbl = dict(SET_KEYS).get(x, x)
        extra = "\n(rasm yuboring)" if x == "welcome_img" else ""
        return await ask(update, ctx, ("set", x), f"✏️ <b>{lbl}</b>\nHozirgi: <code>{E((gs(x) or '-')[:300])}</code>{extra}\nYangi qiymatni yuboring:")
    # admins
    if a == "adms": return await S(v_adms())
    if a == "amadd": return await ask(update, ctx, ("amadd",), "👮 Yangi admin Telegram ID sini yuboring:")
    if a == "amdel":
        if int(x) not in OWNERS: ex("delete from admins where id=?", (int(x),))
        return await S(v_adms())

async def on_msg(update, ctx):
    u = update.effective_user; m = update.message
    if not u or not m or not is_admin(u.id): return
    st = ctx.user_data.get("st")
    if not st: return
    k = st[0]; txt = (m.text or m.caption or "").strip()
    photo = m.photo[-1].file_id if m.photo else None
    done = lambda: ctx.user_data.pop("st", None)
    num = lambda s: int(re.sub(r"\D", "", s) or 0)
    try:
        if k == "find":
            s = txt.lstrip("@")
            r = q1("select id from users where id=?", (int(s),)) if s.isdigit() else q1("select id from users where lower(username)=?", (s.lower(),))
            if not r: return await m.reply_text("❌ Topilmadi. Qayta yuboring yoki /cancel")
            done(); return await show(update, v_user(r["id"]))
        if k == "bal":
            amt = num(txt)
            if amt <= 0: return await m.reply_text("Musbat son yuboring")
            _, uid, sign = st
            ex("update users set balance=max(0,balance+?) where id=?", (amt if sign == "+" else -amt, uid))
            notify(uid, f"{'➕' if sign=='+' else '➖'} Balansingiz o'zgardi: <b>{money(amt)}</b> so'm")
            done(); return await show(update, v_user(uid))
        if k == "gnew":
            if not txt: return await m.reply_text("Nom yuboring")
            ctx.user_data["gname"] = txt; done()
            return await show(update, (f"«{E(txt)}» uchun kategoriya:", [[("🎮 O'yinlar", "a:gcat:game"), ("🎁 Promokodlar bo'limi", "a:gcat:promo")]]))
        if k == "gname":
            ex("update games set name=? where id=?", (txt, st[1])); done(); return await show(update, v_game(st[1]))
        if k == "gfield":
            ex("update games set field=? where id=?", (txt, st[1])); done(); return await show(update, v_game(st[1]))
        if k == "gimg":
            if not photo: return await m.reply_text("Rasm yuboring (fayl emas, rasm sifatida)")
            ex("update games set img=? where id=?", (photo, st[1])); done(); return await show(update, v_game(st[1]))
        if k == "padd":
            n = 0
            for line in txt.splitlines():
                if "|" in line:
                    nm, pr = line.rsplit("|", 1)
                    if nm.strip() and num(pr) > 0:
                        ex("insert into products(game_id,name,price) values(?,?,?)", (st[1], nm.strip(), num(pr))); n += 1
            if not n: return await m.reply_text("Format xato. <code>nom | narx</code>", parse_mode="HTML")
            done(); return await show(update, v_game(st[1]))
        if k == "pname":
            ex("update products set name=? where id=?", (txt, st[1])); done(); return await show(update, v_prod(st[1]))
        if k == "pprice":
            if num(txt) <= 0: return await m.reply_text("Narx noto'g'ri")
            ex("update products set price=? where id=?", (num(txt), st[1])); done(); return await show(update, v_prod(st[1]))
        if k == "badd":
            if not photo: return await m.reply_text("Rasm yuboring")
            ex("insert into banners(img,link) values(?,?)", (photo, txt if link_ok(txt) else "")); done(); return await show(update, v_banners())
        if k in ("cadd", "cedit"):
            parts = [p.strip() for p in txt.split("|")]
            if len(parts) < 2 or len(re.sub(r"\D", "", parts[0])) < 12: return await m.reply_text("Format: karta | ism | bank")
            number = re.sub(r"\D", "", parts[0]); number = " ".join(number[i:i+4] for i in range(0, len(number), 4))
            bank = parts[2].upper() if len(parts) > 2 and parts[2] else "UZCARD"
            if k == "cadd": ex("insert into cards(number,holder,bank) values(?,?,?)", (number, parts[1], bank)); done(); return await show(update, v_cards())
            ex("update cards set number=?,holder=?,bank=? where id=?", (number, parts[1], bank, st[1])); done(); return await show(update, v_card(st[1]))
        if k == "pradd":
            parts = [p.strip() for p in txt.split("|")]
            if len(parts) < 3 or num(parts[1]) <= 0: return await m.reply_text("Format: KOD | summa | limit")
            ex("insert or replace into promos(code,amount,left) values(?,?,?)", (parts[0].upper(), num(parts[1]), num(parts[2]))); done(); return await show(update, v_promos())
        if k == "chadd":
            cid = txt.strip()
            r = tg("getChat", chat_id=cid)
            if not r.get("ok"): return await m.reply_text("❌ Kanal topilmadi yoki bot u yerda yo'q.")
            c = r["result"]; link = f"https://t.me/{c['username']}" if c.get("username") else c.get("invite_link", "")
            if not link:
                link = tg("exportChatInviteLink", chat_id=c["id"]).get("result", "")
            ex("insert into channels(chat_id,title,link) values(?,?,?)", (str(c["id"]), c.get("title", ""), link)); done(); return await show(update, v_chs())
        if k == "bc":
            ctx.user_data["bcmsg"] = (m.chat_id, m.message_id); done()
            n = q1("select count(*) c from users where banned=0")["c"]
            return await show(update, (f"📨 Shu xabar {n} ta foydalanuvchiga yuboriladi. Tasdiqlaysizmi?", [[("✅ Yuborish", "a:bcgo"), ("❌ Bekor", "a:home")]]))
        if k == "set":
            key = st[1]
            if key == "welcome_img":
                if not photo: return await m.reply_text("Rasm yuboring")
                ss(key, photo)
            else:
                ss(key, re.sub(r"\D", "", txt) if key in ("min_topup", "card_ttl") else txt)
            done(); return await show(update, v_set())
        if k == "amadd":
            if not txt.isdigit(): return await m.reply_text("ID raqam yuboring")
            ex("insert or ignore into admins values(?)", (int(txt),)); done(); return await show(update, v_adms())
    except Exception as e:
        log.exception("on_msg")
        await m.reply_text(f"Xato: {e}")

# ---- topup / order decisions (button in notification or panel)
async def cb_decide(update, ctx):
    q = update.callback_query
    if not is_admin(q.from_user.id): return await q.answer("⛔", show_alert=True)
    kind, act, sid = q.data.split(":"); sid = int(sid)
    res = "Allaqachon ko'rilgan"
    if kind == "t":
        t = q1("select * from topups where id=?", (sid,))
        if t:
            st = "approved" if act == "ok" else "rejected"
            c = ex("update topups set status=? where id=? and status in ('new','pending')", (st, sid))
            if c.rowcount:
                if act == "ok":
                    ex("update users set balance=balance+? where id=?", (t["amount"], t["uid"]))
                    notify(t["uid"], f"✅ Balansingiz <b>{money(t['amount'])}</b> so'mga to'ldirildi.")
                    res = f"✅ Tasdiqlandi (@{E(q.from_user.username or str(q.from_user.id))})"
                else:
                    notify(t["uid"], f"❌ {money(t['amount'])} so'm to'ldirish so'rovi rad etildi. Yordam: support.")
                    res = "❌ Rad etildi"
    else:
        o = q1("select * from orders where id=?", (sid,))
        if o:
            st = "done" if act == "done" else "canceled"
            c = ex("update orders set status=? where id=? and status='pending'", (st, sid))
            if c.rowcount:
                if act == "done":
                    notify(o["uid"], f"✅ Buyurtma #{sid} bajarildi!\n🎮 {E(o['game'])} — {E(o['product'])}")
                    res = "✅ Bajarildi"
                else:
                    ex("update users set balance=balance+? where id=?", (o["price"], o["uid"]))
                    notify(o["uid"], f"❌ Buyurtma #{sid} bekor qilindi, <b>{money(o['price'])}</b> so'm balansga qaytarildi.")
                    res = "❌ Bekor qilindi, pul qaytarildi"
    await q.answer(res, show_alert=False)
    try:
        await q.edit_message_text((q.message.text_html or "") + f"\n\n<b>{res}</b>", parse_mode="HTML")
    except Exception: pass

async def post_init(app):
    if BASE_URL:
        try:
            await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Ilova", web_app=WebAppInfo(url=webapp_url())))
        except Exception as e: log.warning("menu button: %s", e)

def keepalive():
    while True:
        time.sleep(600)
        try: requests.get(BASE_URL + "/health", timeout=15)
        except Exception: pass

def main():
    if not TOKEN: raise SystemExit("BOT_TOKEN kiritilmagan!")
    init_db()
    threading.Thread(target=lambda: web.run(host="0.0.0.0", port=PORT, use_reloader=False, threaded=True), daemon=True).start()
    if BASE_URL: threading.Thread(target=keepalive, daemon=True).start()
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(cb_chk, pattern="^chk$"))
    app.add_handler(CallbackQueryHandler(adm_cb, pattern="^a:"))
    app.add_handler(CallbackQueryHandler(cb_decide, pattern="^[to]:"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, on_msg))
    log.info("Syrexa ishga tushdi. Admins: %s", all_admins())
    app.run_polling(drop_pending_updates=True)

# ============================ MINI APP (frontend) ============================
INDEX = r"""<!DOCTYPE html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<title>__BOT__</title><script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:#eef0f7;--card:#fff;--tx:#151a30;--mut:#8a8fa8;--p:#7c5cff;--p2:#a78bfa;--bd:#e3e6f2;--ok:#16a34a;--er:#e11d48}
body.dk{--bg:#0a0c18;--card:#151932;--tx:#f1f2fb;--mut:#8b90ab;--bd:#242949}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,"Segoe UI",Roboto,sans-serif;font-size:15px}
#app{padding:14px 14px 100px;max-width:520px;margin:auto}
.row{display:flex;align-items:center;gap:10px}.sp{flex:1}.mut{color:var(--mut)}.sm{font-size:12px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:18px;padding:14px;margin-bottom:12px}
.btn{border:0;border-radius:14px;padding:13px 18px;font-weight:700;font-size:15px;color:#fff;background:linear-gradient(135deg,var(--p),var(--p2));width:100%;cursor:pointer}
.btn.sm{width:auto;padding:10px 16px;font-size:13px}.btn.g{background:linear-gradient(135deg,#16a34a,#22c55e)}.btn.o{background:var(--card);color:var(--tx);border:1px solid var(--bd)}
.btn:disabled{opacity:.5}
.ib{width:38px;height:38px;border-radius:12px;background:var(--card);border:1px solid var(--bd);display:flex;align-items:center;justify-content:center;font-weight:700;cursor:pointer;font-size:13px}
.av{width:44px;height:44px;border-radius:50%;background:linear-gradient(135deg,var(--p),var(--p2));display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:18px}
.bal{display:flex;align-items:center;gap:12px}.bal b{font-size:26px}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px 8px}
.gc{text-align:center;font-size:11px;cursor:pointer}.gi{aspect-ratio:1;border-radius:16px;overflow:hidden;background:var(--card);border:1px solid var(--bd);margin-bottom:5px}
.gi img{width:100%;height:100%;object-fit:cover}.ph{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:800;color:#fff;background:linear-gradient(135deg,var(--p),var(--p2))}
.ban{display:flex;gap:10px;overflow-x:auto;scroll-snap-type:x mandatory;margin-bottom:12px;border-radius:18px}
.ban>*{flex:0 0 100%;scroll-snap-align:center;border-radius:18px;overflow:hidden;aspect-ratio:2.1;background:linear-gradient(135deg,#1b1147,#6d3df0)}
.ban img{width:100%;height:100%;object-fit:cover;display:block}
.hero{display:flex;align-items:center;justify-content:center;color:#fff;font-size:30px;font-weight:900;letter-spacing:2px}
h3{margin:6px 2px 10px;font-size:17px}.hd{display:flex;justify-content:space-between;align-items:center}.hd a{color:var(--p);font-weight:700;font-size:13px}
.seg{display:flex;background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:4px;margin-bottom:12px}
.seg div{flex:1;text-align:center;padding:10px;border-radius:11px;font-weight:700;font-size:13px;cursor:pointer;color:var(--mut)}.seg .on{background:linear-gradient(135deg,var(--p),var(--p2));color:#fff}
input{width:100%;padding:14px;border-radius:14px;border:1.5px solid var(--bd);background:var(--card);color:var(--tx);font-size:16px;outline:none}input:focus{border-color:var(--p)}
.chips{display:flex;gap:8px;margin:10px 0}.chips div{flex:1;text-align:center;padding:10px 0;border-radius:12px;border:1px solid var(--bd);background:var(--card);font-weight:700;font-size:13px;cursor:pointer}.chips .on{border-color:var(--p);color:var(--p)}
.nav{position:fixed;left:50%;transform:translateX(-50%);bottom:12px;width:calc(100% - 24px);max-width:496px;background:var(--card);border:1px solid var(--bd);border-radius:26px;display:flex;padding:6px;box-shadow:0 8px 30px rgba(0,0,0,.18)}
.nav div{flex:1;text-align:center;padding:8px 0;border-radius:20px;font-size:10px;color:var(--mut);cursor:pointer}.nav i{display:block;font-style:normal;font-size:19px}.nav .on{background:linear-gradient(135deg,var(--p),var(--p2));color:#fff}
.prod{display:flex;justify-content:space-between;align-items:center;padding:14px;border-radius:14px;border:1.5px solid var(--bd);background:var(--card);margin-bottom:8px;cursor:pointer;font-weight:600}.prod.on{border-color:var(--p);background:rgba(124,92,255,.1)}
.tag{font-size:11px;padding:3px 9px;border-radius:20px;font-weight:700}.pending,.new{background:#fff3cd;color:#a16207}.done,.approved{background:#dcfce7;color:#15803d}.canceled,.rejected{background:#ffe4e6;color:#be123c}
.cn{background:linear-gradient(135deg,#10132a,#2a2170);color:#fff;border-radius:18px;padding:16px;margin-bottom:12px}.cn .n{font-size:21px;font-weight:800;letter-spacing:1px;margin:8px 0}
.warn{background:rgba(225,29,72,.08);border:1px solid rgba(225,29,72,.3);border-radius:14px;padding:12px;font-size:13px;margin-bottom:12px}
.tm{font-weight:800;color:var(--p)}.bar{height:5px;border-radius:5px;background:var(--bd);overflow:hidden;margin-top:8px}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--p),var(--p2))}
#toast{position:fixed;top:14px;left:50%;transform:translateX(-50%);background:#151a30;color:#fff;padding:11px 18px;border-radius:14px;font-size:14px;z-index:9;display:none;max-width:90%}
.empty{text-align:center;padding:50px 10px;color:var(--mut)}
</style></head><body><div id="toast"></div><div id="app"></div>
<script>
const tg=window.Telegram.WebApp;tg.ready();tg.expand();
const $=s=>document.querySelector(s);
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=n=>Number(n).toLocaleString('ru-RU').replace(/\u00a0/g,' ');
const S={lang:localStorage.lang||'uz',dark:localStorage.dark==='1',tab:'home',d:null,amt:50000,seg:'game',oseg:'o',q:'',player:'',pid:null};
const T={uz:{hi:'Salom',bal:'BALANS',top:'To\'ldirish',promo:'Promokodlar',sup:'Yordam',pop:'Mashhur o\'yinlar',all:'Barchasi',home:'Asosiy',games:'O\'yinlar',orders:'Buyurtmalar',prof:'Profil',tx:'Tranzaksiyalar',search:'O\'yin yoki xizmatni qidiring',
amount:'Summani kiriting',min:'Minimum',steps:'To\'ldirish qadamlari',s1:'To\'lov summasini tanlang',s2:'Ko\'rsatilgan kartaga AYNAN shu summani o\'tkazing',s3:'"Men to\'ladim" tugmasini bosing',s4:'Admin tasdiqlagach balansingiz to\'ldiriladi',
exact:'Aynan shu summani o\'tkazing',one:'Faqat BITTA o\'tkazma',onet:'Summani bo\'lmang va yaxlitlamang.',valid:'Karta amal qilish vaqti',card:'Karta raqami',copy:'Nusxalash',copied:'Nusxalandi',paid:'Men to\'ladim',rules:'To\'lov qoidalari',r1:'Summani 1 so\'mga ham o\'zgartirmang',r2:'Vaqt ichida to\'lang',r3:'Boshqa summa yubormang',r4:'Summani ikkiga bo\'lmang',
buy:'Sotib olish',pid_:'ID kiriting',pick:'Mahsulotni tanlang',noprod:'Mahsulotlar hali qo\'shilmagan',noord:'Buyurtmalar yo\'q',notx:'Tranzaksiyalar yo\'q',nobal:'Balans yetarli emas',ok:'Muvaffaqiyatli!',
pending:'Kutilmoqda',done:'Bajarildi',canceled:'Bekor',approved:'Tasdiqlandi',rejected:'Rad etildi',new:'Yangi',pr_in:'PROMOKOD',act:'Faollashtirish',lang:'Til',sub:'Botdan foydalanish uchun kanalga obuna bo\'ling',chk:'Tekshirish',nocard:'Hozircha to\'lov usuli mavjud emas',err:'Xatolik',bad:'Kod topilmadi yoki ishlatilgan',added:'Qo\'shildi',expired:'Vaqt tugadi',maint:'Texnik ishlar',confirm:'Tasdiqlaysizmi?',adm:'Admin panel uchun botda /admin yozing',bonus:'Balans to\'ldirildi'},
ru:{hi:'Привет',bal:'БАЛАНС',top:'Пополнить',promo:'Промокоды',sup:'Поддержка',pop:'Популярные игры',all:'Все',home:'Главная',games:'Игры',orders:'Заказы',prof:'Профиль',tx:'Транзакции',search:'Поиск игры или услуги',
amount:'Введите сумму',min:'Минимум',steps:'Шаги пополнения',s1:'Выберите сумму',s2:'Переведите на указанную карту ТОЧНО эту сумму',s3:'Нажмите «Я оплатил»',s4:'После подтверждения баланс пополнится',
exact:'Переведите ровно',one:'Только ОДИН перевод',onet:'Не разбивайте и не округляйте сумму.',valid:'Карта действует',card:'Номер карты',copy:'Копировать',copied:'Скопировано',paid:'Я оплатил',rules:'Правила оплаты',r1:'Не меняйте сумму даже на 1 сум',r2:'Оплатите в течение времени',r3:'Не отправляйте другую сумму',r4:'Не разбивайте сумму на два перевода',
buy:'Купить',pid_:'Введите ID',pick:'Выберите товар',noprod:'Товары ещё не добавлены',noord:'Нет заказов',notx:'Нет транзакций',nobal:'Недостаточно средств',ok:'Успешно!',
pending:'Ожидание',done:'Выполнен',canceled:'Отменён',approved:'Подтверждён',rejected:'Отклонён',new:'Новый',pr_in:'ПРОМОКОД',act:'Активировать',lang:'Язык',sub:'Подпишитесь на канал, чтобы пользоваться ботом',chk:'Проверить',nocard:'Способ оплаты пока недоступен',err:'Ошибка',bad:'Код не найден или использован',added:'Добавлено',expired:'Время истекло',maint:'Технические работы',confirm:'Подтвердить?',adm:'Для админ-панели напишите боту /admin',bonus:'Баланс пополнен'}};
const t=k=>(T[S.lang]||T.uz)[k]||k;
function toast(m){const e=$('#toast');e.textContent=m;e.style.display='block';clearTimeout(S.tt);S.tt=setTimeout(()=>e.style.display='none',2600)}
async function api(p,body){const r=await fetch(p,{method:body!==undefined?'POST':'GET',headers:{'Content-Type':'application/json','X-Init':tg.initData},body:body!==undefined?JSON.stringify(body):undefined});const j=await r.json().catch(()=>({}));if(!r.ok)throw j;return j}
function ask(m,cb){tg.showConfirm?tg.showConfirm(m,ok=>ok&&cb()):(confirm(m)&&cb())}
function gimg(g,cls){return g.img?`<img src="/img/${g.img}" loading="lazy">`:`<div class="ph">${esc(g.name[0])}</div>`}
function gcard(g){return `<div class="gc" onclick="openGame(${g.id})"><div class="gi">${gimg(g)}</div>${esc(g.name)}</div>`}
function go(tab,arg){S.tab=tab;S.arg=arg;clearInterval(S.tm);if(tab!='game')S.g=null;
 const back=(tab=='game'||tab=='pay');back?tg.BackButton.show():tg.BackButton.hide();render();window.scrollTo(0,0)}
tg.BackButton.onClick(()=>go(S.tab=='pay'?'topup':'games'));
function head(){const u=S.d.user;return `<div class="row" style="margin-bottom:12px"><div class="av">${esc((u.name||'?')[0])}</div><div><div class="mut sm">${t('hi')} 👋</div><b>${esc(u.name)}</b></div><div class="sp"></div><div class="ib" onclick="setLang()">${S.lang.toUpperCase()}</div><div class="ib" onclick="setDark()">${S.dark?'☀️':'🌙'}</div></div>`}
function balCard(){return `<div class="card bal"><div style="font-size:26px">💳</div><div class="sp"><div class="mut sm">${t('bal')}</div><b>${money(S.d.user.balance)}</b> <span class="mut sm">so'm</span></div><button class="btn sm" onclick="go('topup')">+ ${t('top')}</button></div>`}
function nav(){const a=[['home','🏠',t('home')],['games','🎮',t('games')],['topup','👛',t('top')],['orders','🕘',t('orders')],['prof','👤',t('prof')]];
 return `<div class="nav">${a.map(x=>`<div class="${S.tab==x[0]?'on':''}" onclick="go('${x[0]}')"><i>${x[1]}</i>${x[2]}</div>`).join('')}</div>`}
function render(){const A=$('#app');const d=S.d;if(!d)return;
 if(d.sub&&d.sub.length){A.innerHTML=`<div class="card" style="margin-top:40px;text-align:center"><div style="font-size:42px">📢</div><p>${t('sub')}</p>${d.sub.map(c=>`<button class="btn o" style="margin-bottom:8px" onclick="tg.openTelegramLink('${esc(c.link)}')">${esc(c.title)}</button>`).join('')}<button class="btn" onclick="boot()">${t('chk')}</button></div>`;return}
 let h='';const m=S.tab;
 if(m=='home')h=vHome();else if(m=='games')h=vGames();else if(m=='game')h=vGame();else if(m=='topup')h=vTopup();else if(m=='pay')h=vPay();else if(m=='orders')h=vOrders();else h=vProf();
 A.innerHTML=h+((m=='game'||m=='pay')?'':nav());
 if(m=='pay')tick();}
function vHome(){const d=S.d;
 const bn=d.banners.length?d.banners.map(b=>`<div onclick="${b.link?`tg.openLink('${esc(b.link)}')`:''}"><img src="/img/${b.img}"></div>`).join(''):`<div class="hero">${esc(d.cfg.bot)}</div>`;
 return head()+balCard()+`<div class="row" style="margin-bottom:12px"><button class="btn o" onclick="go('prof')">🎟 ${t('promo')}</button><button class="btn o" onclick="sup()">🎧 ${t('sup')}</button></div><div class="ban">${bn}</div>
 <div class="hd"><h3>${t('pop')}</h3><a onclick="go('games')">${t('all')}</a></div><div class="grid">${d.games.filter(g=>g.cat=='game').slice(0,8).map(gcard).join('')}</div>`}
function sup(){const l=S.d.cfg.support;l?tg.openTelegramLink(l):toast(t('sup'))}
function vGames(){const L=S.d.games.filter(g=>g.cat==S.seg&&g.name.toLowerCase().includes(S.q.toLowerCase()));
 return `<h3>${t('games')}</h3><input id="sq" placeholder="🔍 ${t('search')}" value="${esc(S.q)}" oninput="S.q=this.value;gridUpd()" style="margin-bottom:12px"><div class="seg"><div class="${S.seg=='game'?'on':''}" onclick="S.seg='game';render()">${t('games')}</div><div class="${S.seg=='promo'?'on':''}" onclick="S.seg='promo';render()">${t('promo')}</div></div><div class="grid" id="gg">${L.map(gcard).join('')}</div>`}
function gridUpd(){const L=S.d.games.filter(g=>g.cat==S.seg&&g.name.toLowerCase().includes(S.q.toLowerCase()));$('#gg').innerHTML=L.map(gcard).join('')}
async function openGame(id){S.g=null;S.pid=null;S.player='';go('game',id);try{S.g=await api('/api/game/'+id);render()}catch(e){toast(t('err'));go('games')}}
function vGame(){const g=S.g;if(!g)return `<div class="empty">⏳</div>`;const p=g.products.find(x=>x.id==S.pid);
 return `<div class="row" style="margin-bottom:14px"><div class="gi" style="width:64px;margin:0">${gimg(g)}</div><h3 style="margin:0">${esc(g.name)}</h3></div>
 <input id="pl" placeholder="${esc(g.field)}" value="${esc(S.player)}" oninput="S.player=this.value" style="margin-bottom:12px">
 ${g.products.length?g.products.map(x=>`<div class="prod ${x.id==S.pid?'on':''}" onclick="S.pid=${x.id};render()"><span>${esc(x.name)}</span><span>${money(x.price)} so'm</span></div>`).join(''):`<div class="empty">${t('noprod')}</div>`}
 <div style="position:fixed;left:14px;right:14px;bottom:14px;max-width:492px;margin:auto"><button class="btn" onclick="buy()" ${g.products.length?'':'disabled'}>${t('buy')}${p?' — '+money(p.price)+' so\'m':''}</button></div>`}
async function buy(){const g=S.g,p=g.products.find(x=>x.id==S.pid);if(!p)return toast(t('pick'));if((S.player||'').trim().length<2)return toast(t('pid_'));
 ask(`${g.name} — ${p.name}\n${money(p.price)} so'm\n${g.field}: ${S.player}`,async()=>{try{const r=await api('/api/order',{product_id:p.id,player:S.player.trim()});S.d.user.balance=r.balance;toast('✅ '+t('ok'));S.oseg='o';go('orders')}catch(e){if(e.err=='balance'){toast(t('nobal'));go('topup')}else toast(t('err'))}})}
function vTopup(){const A=[10000,50000,100000,200000,500000];
 return `<h3>${t('top')}</h3>`+balCard().replace(/<button.*<\/button>/,'')+`<div class="card"><div class="mut sm">${t('amount')}</div><input id="am" inputmode="numeric" value="${money(S.amt)}" oninput="S.amt=+this.value.replace(/\\D/g,'')||0"><div class="chips">${[50000,100000,200000,500000].map(a=>`<div class="${S.amt==a?'on':''}" onclick="S.amt=${a};render()">${a/1000}k</div>`).join('')}</div><div class="mut sm">${t('min')}: ${money(S.d.cfg.min)}</div></div>
 <div class="card"><b>${t('steps')}</b>${[1,2,3,4].map(i=>`<div class="row" style="margin-top:10px"><div class="ib" style="width:26px;height:26px;border-radius:50%;font-size:12px">${i}</div><div class="sm">${t('s'+i)}</div></div>`).join('')}</div><button class="btn" onclick="mkTop()">${t('top')}</button>`}
async function mkTop(){if(S.amt<S.d.cfg.min)return toast(t('min')+': '+money(S.d.cfg.min));try{S.pay=await api('/api/topup',{amount:S.amt});S.pay.t0=Date.now();go('pay')}catch(e){toast(e.err=='nocard'?t('nocard'):t('err'))}}
function vPay(){const p=S.pay;return `<h3>${t('top')}</h3><div class="card" style="text-align:center;background:rgba(124,92,255,.1)"><div class="mut sm">${t('exact')}</div><div style="font-size:30px;font-weight:900;margin:6px 0" onclick="cp('${p.amount}')">${money(p.amount)} so'm ⧉</div></div>
 <div class="warn"><b>⚠️ ${t('one')}</b><br>${t('onet')}</div>
 <div class="card"><div class="row"><span class="mut sm">${t('valid')}</span><div class="sp"></div><span class="tm" id="tm"></span></div><div class="bar"><i id="br" style="width:100%"></i></div></div>
 <div class="cn"><div class="sm" style="opacity:.7">${t('card')} · ${esc(p.card.bank)}</div><div class="n" onclick="cp('${p.card.number.replace(/\\s/g,'')}')">${esc(p.card.number)}</div><div class="sm">${esc(p.card.holder)}</div><button class="btn o" style="margin-top:12px;background:rgba(255,255,255,.12);color:#fff;border:0" onclick="cp('${p.card.number.replace(/\\s/g,'')}')">⧉ ${t('copy')}</button></div>
 <div class="card"><b>📋 ${t('rules')}</b>${['✅ r1','✅ r2','❌ r3','❌ r4'].map(x=>{const a=x.split(' ');return `<div class="sm" style="margin-top:8px">${a[0]} ${t(a[1])}</div>`}).join('')}</div><button class="btn g" onclick="paid()">${t('paid')}</button>`}
function tick(){const p=S.pay;const f=()=>{const left=Math.max(0,p.ttl-Math.floor((Date.now()-p.t0)/1000));const e=$('#tm');if(!e)return clearInterval(S.tm);e.textContent=left?`${Math.floor(left/60)}:${String(left%60).padStart(2,'0')}`:t('expired');$('#br').style.width=(left/p.ttl*100)+'%'};f();S.tm=setInterval(f,1000)}
function cp(x){(navigator.clipboard?navigator.clipboard.writeText(x):Promise.reject()).catch(()=>{const a=document.createElement('textarea');a.value=x;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove()});toast('✅ '+t('copied'));tg.HapticFeedback&&tg.HapticFeedback.impactOccurred('light')}
async function paid(){try{await api('/api/topup/'+S.pay.id+'/paid',{});toast('✅ '+t('ok'));S.oseg='t';go('orders')}catch(e){toast(t('err'))}}
function vOrders(){setTimeout(loadH,0);const H=S.h;
 let body=!H?`<div class="empty">⏳</div>`:S.oseg=='o'?(H.orders.length?H.orders.map(o=>`<div class="card row"><div class="sp"><b>${esc(o.game)}</b><div class="mut sm">${esc(o.product)} · #${o.id}</div><div class="mut sm">${ts(o.created)}</div></div><div style="text-align:right"><b>${money(o.price)}</b><div><span class="tag ${o.status}">${t(o.status)}</span></div></div></div>`).join(''):`<div class="empty">🕘<br>${t('noord')}</div>`)
 :(H.tx.length?H.tx.map(o=>`<div class="card row"><div class="sp"><b>${t('top')}</b><div class="mut sm">#${o.id} · ${ts(o.created)}</div></div><div style="text-align:right"><b>+${money(o.amount)}</b><div><span class="tag ${o.status}">${t(o.status)}</span></div></div></div>`).join(''):`<div class="empty">💳<br>${t('notx')}</div>`);
 return `<h3>${t('orders')}</h3><div class="seg"><div class="${S.oseg=='o'?'on':''}" onclick="S.oseg='o';render()">${t('orders')}</div><div class="${S.oseg=='t'?'on':''}" onclick="S.oseg='t';render()">${t('tx')}</div></div>`+body}
function ts(x){const d=new Date((x+18000)*1000);const p=n=>String(n).padStart(2,'0');return `${p(d.getUTCDate())}.${p(d.getUTCMonth()+1)} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`}
async function loadH(){if(S.hl)return;S.hl=1;try{S.h=await api('/api/history');const u=await api('/api/init');S.d.user=u.user;S.hl=0;if(S.tab=='orders')render()}catch(e){S.hl=0}}
function vProf(){const u=S.d.user;return `<div class="card" style="text-align:center"><div class="av" style="margin:auto;width:70px;height:70px;font-size:30px">${esc((u.name||'?')[0])}</div><h3 style="margin:10px 0 2px">${esc(u.name)}</h3><div class="mut sm">${u.username?'@'+esc(u.username)+' · ':''}ID: ${u.id}</div></div>`+balCard()+
 `<div class="card"><b>🎟 ${t('promo')}</b><input id="pc" placeholder="${t('pr_in')}" style="margin:10px 0;text-transform:uppercase"><button class="btn" onclick="actPromo()">${t('act')}</button></div>
 <div class="card"><b>🌐 ${t('lang')}</b><div class="seg" style="margin:10px 0 0"><div class="${S.lang=='uz'?'on':''}" onclick="setLang('uz')">O'zbekcha</div><div class="${S.lang=='ru'?'on':''}" onclick="setLang('ru')">Русский</div></div></div>${u.admin?`<div class="card sm mut">🛠 ${t('adm')}</div>`:''}`}
async function actPromo(){const c=$('#pc').value.trim();if(!c)return;try{const r=await api('/api/promo',{code:c});S.d.user.balance=r.balance;toast('✅ +'+money(r.amount));render()}catch(e){toast(t('bad'))}}
function setLang(l){S.lang=l||(S.lang=='uz'?'ru':'uz');localStorage.lang=S.lang;S.d.user.lang=S.lang;api('/api/lang',{lang:S.lang}).catch(()=>{});render()}
function setDark(){S.dark=!S.dark;localStorage.dark=S.dark?'1':'0';document.body.classList.toggle('dk',S.dark);render()}
async function boot(){try{S.d=await api('/api/init');if(!localStorage.lang)S.lang=S.d.user.lang||'uz';document.body.classList.toggle('dk',S.dark||(!localStorage.dark&&tg.colorScheme=='dark'));render()}
 catch(e){$('#app').innerHTML=`<div class="empty" style="margin-top:80px">${e.err=='maintenance'?'🛠 '+t('maint'):e.err=='banned'?'🚫':'Telegram ichida oching'}</div>`}}
boot();
</script></body></html>"""

if __name__ == "__main__":
    main()
