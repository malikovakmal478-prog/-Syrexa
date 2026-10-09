# SYREXA V3 — Telegram o‘yinlar do‘koni

## Ichidagi funksiyalar
- Telegram bot + Telegram Mini App do‘koni (Flask backend).
- O‘zbekcha/ruscha til, o‘yinlar va paketlar katalogi.
- Admin: statistika, o‘yinlar, mahsulotlar, buyurtmalar, balans to‘ldirish so‘rovlari, foydalanuvchi ban/unban, balansni qo‘shish/ayirish/belgilash, bank kartalari, promokodlar, sozlamalar va adminlar.
- Yangi qo‘shimchalar: qo‘llab-quvvatlash murojaatlari va javoblari, sevimlilar, taklif havolasi/bonus, tranzaksiya tarixi, admin audit log, 7 kunlik hisobot, CSV eksport va ommaviy xabarlar tarixi.
- SQLite baza; Render persistent disk `/data` ga ulanadi.

## GitHub
Repository ildizida `main.py`, `requirements.txt`, `render.yaml` va `templates/index.html` bo‘lishi kerak. `templates` papkasini yo‘qotmang.

## Render
Build: `pip install -r requirements.txt`
Start: `gunicorn main:app --workers 1 --threads 8 --timeout 120`

Environment Variables:
- `BOT_TOKEN`: BotFather tokeni
- `ADMIN_IDS`: asosiy admin Telegram ID’lari, vergul bilan
- `BOT_USERNAME`: bot username, `@` belgisiz
- `WEBAPP_URL`: Render HTTPS URL (oxirida `/` bo‘lmasin)
- `REQUIRED_CHANNELS`: ixtiyoriy, `@kanal1,@kanal2`
- `DB_PATH`: `/data/syrexa.db` (render.yaml da sozlangan)

## Muhim xavfsizlik va real ishlash eslatmalari
- Admin API’lari Telegram Mini App `initData` imzosini va admin ID’ni tekshiradi. Admin panelni bot ichidagi `/admin` buyrug‘i bilan oching. Oddiy brauzerda Telegram initData bo‘lmasa, admin API ishlamaydi.
- To‘lovlar admin tomonidan qo‘lda tasdiqlanadi; bank/to‘lov provayderi API ulanmagan. O‘yin UC/diamond avtomatik yetkazish API’si ulanmagan, shuning uchun buyurtma admin tomonidan bajariladi.
- Boshlang‘ich katalog va narxlar namuna sifatida kiritilgan; haqiqiy narxlarni admin panelida tekshirib yangilang.
- Telegram bot bilan foydalanuvchi avval chat boshlamagan yoki botni bloklagan bo‘lsa, xabar yetmasligi mumkin.
- `render.yaml` ichida persistent disk uchun pullik/plan cheklovlari bo‘lishi mumkin; Render panelidagi amaldagi tarifni tekshiring.

## Tekshiruv
Python fayli sintaksis jihatidan tekshiriladi. Ishlab turgan Render URL, BotFather tokeni va Telegram Mini App orqali integratsion sinov o‘tkazilmaguncha production tayyorligi kafolatlanmaydi.
