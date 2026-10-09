# SYREXA V3 changelog

## Qo‘shilgan imkoniyatlar
- Yordam markazi: foydalanuvchi murojaat yuboradi; admin javob beradi, murojaatni yopadi/qayta ochadi; javob bot orqali yetkaziladi.
- Sevimli mahsulotlar uchun API va katalogda saqlash tugmasi.
- Referral havolalari va bir martalik taklif bonusi.
- Admin audit log: muhim admin harakatlari qayd etiladi.
- Tranzaksiya ro‘yxati va CSV eksporti (foydalanuvchilar, buyurtmalar, tranzaksiyalar).
- Oxirgi 7 kun bo‘yicha buyurtma/savdo/top-up hisoboti.
- Ommaviy xabarlar tarixi panelda ko‘rinadi.
- Admin tomonidan buyurtma/top-up holati o‘zgarganda foydalanuvchiga xabar yuboriladi.
- Balans to‘ldirish minimum/maksimum miqdorlari va referral bonusi sozlanadi.
- Loyiha arxivida `templates/index.html` saqlanadi — Flask sahifasi ishlashi uchun bu papka zarur.

## Tekshiruv natijalari
- `main.py`: Python sintaksis/AST tekshiruvi o‘tdi.
- `templates/index.html`: inline JavaScript `node --check` tekshiruvi o‘tdi.
- SQLite schema: yangi va avvalgi jadvallarni yaratish tekshiruvi o‘tdi.
- Flask dependency bu muhitda o‘rnatilmadi (tarmoq/DNS cheklovi), shuning uchun serverning haqiqiy HTTP/integratsion testi bajarilmadi.

## Hali tashqi ulanish talab qiladiganlar
- O‘yin UC/diamond avtomatik yetkazib berish uchun rasmiy supplier API.
- To‘lovni avtomatik aniqlash uchun bank/to‘lov provayderi API.
- Rasmlar URL orqali kiritiladi; ichki fayl yuklash ombori ulanmagan.
