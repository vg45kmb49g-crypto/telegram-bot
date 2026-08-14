import os
import sqlite3
import secrets
from datetime import datetime, date, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)

# Ekonomi ayarları:
# 1000 puan = 1 TL ve 1 TL = USDT_RATE USDT.
POINTS_PER_TL = float(os.getenv("POINTS_PER_TL", "1000"))
USDT_RATE = float(os.getenv("USDT_RATE", "0.0"))

DB_PATH = os.getenv("DB_PATH", "bot.db")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        points REAL NOT NULL DEFAULT 0,
        ref_code TEXT UNIQUE,
        referred_by INTEGER,
        daily_claim TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS withdrawals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        points REAL NOT NULL,
        tl REAL NOT NULL,
        usdt REAL NOT NULL,
        method TEXT NOT NULL,
        address TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        processed_at TEXT
    );
    """)
    con.commit()
    con.close()


def ensure_user(tg_user, referred_by=None):
    con = db()
    row = con.execute("SELECT * FROM users WHERE user_id=?", (tg_user.id,)).fetchone()
    if row is None:
        code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]
        while con.execute("SELECT 1 FROM users WHERE ref_code=?", (code,)).fetchone():
            code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]

        if referred_by == tg_user.id:
            referred_by = None

        con.execute(
            """INSERT INTO users
            (user_id, username, first_name, points, ref_code, referred_by, created_at)
            VALUES (?, ?, ?, 0, ?, ?, ?)""",
            (tg_user.id, tg_user.username or "", tg_user.first_name or "",
             code, referred_by, datetime.now(timezone.utc).isoformat())
        )
        con.commit()
    else:
        con.execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (tg_user.username or "", tg_user.first_name or "", tg_user.id)
        )
        con.commit()
    row = con.execute("SELECT * FROM users WHERE user_id=?", (tg_user.id,)).fetchone()
    con.close()
    return row


def menu():
    return ReplyKeyboardMarkup(
        [
            ["🎮 OYNA", "💰 BAKİYEM"],
            ["🎁 GÜNLÜK ÖDÜL", "👥 DAVET ET"],
            ["🏆 LİDERLİK", "💸 ÇEKİM"]
        ],
        resize_keyboard=True
    )


def admin_menu():
    return ReplyKeyboardMarkup(
        [
            ["📊 İSTATİSTİK", "💸 BEKLEYEN ÇEKİMLER"],
            ["⚙️ EKONOMİ", "📋 SON ÇEKİMLER"]
        ],
        resize_keyboard=True
    )


def values(points):
    tl = points / POINTS_PER_TL if POINTS_PER_TL else 0
    usdt = tl * USDT_RATE
    return tl, usdt


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    referred_by = None
    if context.args:
        try:
            referred_by = int(context.args[0])
        except ValueError:
            referred_by = None

    before = get_user(update.effective_user.id)
    user = ensure_user(update.effective_user, referred_by)

    # Yeni kullanıcı gerçekten ilk kez oluşturulduysa referans ödülü ver.
    if before is None and referred_by and referred_by != update.effective_user.id:
        con = db()
        ref = con.execute("SELECT user_id FROM users WHERE user_id=?", (referred_by,)).fetchone()
        if ref:
            con.execute("UPDATE users SET points=points+100 WHERE user_id=?", (referred_by,))
            con.execute("UPDATE users SET points=points+50 WHERE user_id=?", (update.effective_user.id,))
            con.commit()
        con.close()

    await update.message.reply_text(
        "🎮 Tikla-Kazan oyununa hoş geldin!\n\n"
        "🎯 OYNA'ya her bastığında 1 puan kazanırsın.\n"
        "🎁 Günlük ödül ve 👥 davet bonusları da var.",
        reply_markup=menu()
    )


def get_user(user_id):
    con = db()
    row = con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    con.close()
    return row


async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    con = db()
    con.execute("UPDATE users SET points=points+1 WHERE user_id=?", (user["user_id"],))
    con.commit()
    row = con.execute("SELECT points FROM users WHERE user_id=?", (user["user_id"],)).fetchone()
    con.close()
    tl, usdt = values(row["points"])
    await update.message.reply_text(
        f"🎮 +1 puan kazandın!\n\n"
        f"💰 Bakiye: {row['points']:.0f} puan\n"
        f"≈ {tl:.4f} TL\n"
        f"≈ {usdt:.6f} USDT"
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    tl, usdt = values(user["points"])
    await update.message.reply_text(
        f"💰 BAKİYEN\n\n"
        f"⭐ {user['points']:.0f} puan\n"
        f"💵 ≈ {tl:.4f} TL\n"
        f"🪙 ≈ {usdt:.6f} USDT\n\n"
        f"Kur: {POINTS_PER_TL:g} puan = 1 TL\n"
        f"USDT kuru: 1 TL = {USDT_RATE:g} USDT"
    )


async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    today = date.today().isoformat()
    if user["daily_claim"] == today:
        await update.message.reply_text("🎁 Bugünkü ödülünü zaten aldın. Yarın tekrar gel!")
        return
    con = db()
    con.execute(
        "UPDATE users SET points=points+25, daily_claim=? WHERE user_id=?",
        (today, user["user_id"])
    )
    con.commit()
    con.close()
    await update.message.reply_text("🎁 Günlük ödülün: +25 puan!")


async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    me = await context.bot.get_me()
    link = f"https://t.me/{me.username}?start={user['user_id']}"
    con = db()
    count = con.execute("SELECT COUNT(*) c FROM users WHERE referred_by=?", (user["user_id"],)).fetchone()["c"]
    con.close()
    await update.message.reply_text(
        f"👥 DAVET ET\n\n"
        f"Bağlantın:\n{link}\n\n"
        f"👤 Davet ettiğin kişi: {count}\n"
        f"🎁 Her başarılı davet: 100 puan\n"
        f"🎁 Yeni kullanıcı: 50 puan"
    )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    con = db()
    rows = con.execute(
        "SELECT first_name, username, points FROM users ORDER BY points DESC LIMIT 10"
    ).fetchall()
    con.close()
    text = "🏆 LİDERLİK\n\n"
    for i, r in enumerate(rows, 1):
        name = r["first_name"] or r["username"] or "Kullanıcı"
        text += f"{i}. {name} — {r['points']:.0f} puan\n"
    await update.message.reply_text(text)


async def withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = ensure_user(update.effective_user)
    tl, usdt = values(user["points"])
    if user["points"] < POINTS_PER_TL:
        await update.message.reply_text(
            f"💸 Çekim için en az {POINTS_PER_TL:g} puan gerekli.\n"
            f"Mevcut: {user['points']:.0f} puan"
        )
        return
    context.user_data["withdraw_step"] = "method"
    await update.message.reply_text(
        f"💸 ÇEKİM TALEBİ\n\n"
        f"Bakiye: {user['points']:.0f} puan ≈ {tl:.4f} TL ≈ {usdt:.6f} USDT\n\n"
        f"Yöntemi yaz: TL veya USDT"
    )


async def handle_withdraw_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get("withdraw_step")
    if not step:
        return False

    user = ensure_user(update.effective_user)
    text = update.message.text.strip()

    if step == "method":
        method = text.upper()
        if method not in ("TL", "USDT"):
            await update.message.reply_text("Lütfen sadece TL veya USDT yaz.")
            return True
        context.user_data["withdraw_method"] = method
        context.user_data["withdraw_step"] = "address"
        await update.message.reply_text(
            "Şimdi ödeme adresini/hesap bilgisini gönder.\n"
            "USDT için ağ bilgisini de yazman iyi olur (örn. TRC20)."
        )
        return True

    if step == "address":
        method = context.user_data["withdraw_method"]
        points = float(user["points"])
        tl, usdt = values(points)

        con = db()
        con.execute(
            """INSERT INTO withdrawals
            (user_id, points, tl, usdt, method, address, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user["user_id"], points, tl, usdt, method, text,
             datetime.now(timezone.utc).isoformat())
        )
        # Talep açılınca puanı bloke etmek için sıfırlıyoruz.
        con.execute("UPDATE users SET points=0 WHERE user_id=?", (user["user_id"],))
        con.commit()
        wid = con.execute("SELECT last_insert_rowid() id").fetchone()["id"]
        con.close()

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Çekim talebin oluşturuldu.\n\n"
            f"Talep #{wid}\n"
            f"{points:.0f} puan\n"
            f"≈ {tl:.4f} TL\n"
            f"≈ {usdt:.6f} USDT\n"
            f"Yöntem: {method}\n\n"
            f"Ödeme admin onayından sonra yapılır."
        )
        if ADMIN_ID:
            await context.bot.send_message(
                ADMIN_ID,
                f"💸 YENİ ÇEKİM #{wid}\n"
                f"Kullanıcı: {user['user_id']}\n"
                f"Puan: {points:.0f}\n"
                f"TL: {tl:.4f}\n"
                f"USDT: {usdt:.6f}\n"
                f"Yöntem: {method}\n"
                f"Adres: {text}"
            )
        return True

    return False


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    con = db()
    users = con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    points = con.execute("SELECT COALESCE(SUM(points),0) s FROM users").fetchone()["s"]
    pending = con.execute("SELECT COUNT(*) c FROM withdrawals WHERE status='pending'").fetchone()["c"]
    pending_tl = con.execute(
        "SELECT COALESCE(SUM(tl),0) s FROM withdrawals WHERE status='pending'"
    ).fetchone()["s"]
    con.close()
    await update.message.reply_text(
        f"📊 ADMİN İSTATİSTİK\n\n"
        f"👤 Kullanıcı: {users}\n"
        f"⭐ Dolaşımdaki puan: {points:.0f}\n"
        f"💸 Bekleyen çekim: {pending}\n"
        f"💵 Bekleyen TL: {pending_tl:.4f}\n\n"
        f"⚙️ Kur: {POINTS_PER_TL:g} puan = 1 TL\n"
        f"🪙 1 TL = {USDT_RATE:g} USDT",
        reply_markup=admin_menu()
    )


async def pending_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    con = db()
    rows = con.execute(
        "SELECT * FROM withdrawals WHERE status='pending' ORDER BY id DESC LIMIT 20"
    ).fetchall()
    con.close()
    if not rows:
        await update.message.reply_text("⏳ Bekleyen çekim talebi bulunmuyor.")
        return
    text = "💸 BEKLEYEN ÇEKİMLER\n\n"
    for r in rows:
        text += (
            f"#{r['id']} | user {r['user_id']}\n"
            f"{r['points']:.0f} puan | {r['tl']:.4f} TL | {r['usdt']:.6f} USDT\n"
            f"{r['method']} | {r['address']}\n\n"
        )
    await update.message.reply_text(text)


async def economy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        f"⚙️ EKONOMİ\n\n"
        f"{POINTS_PER_TL:g} puan = 1 TL\n"
        f"1 TL = {USDT_RATE:g} USDT\n\n"
        f"Bu sürümde değerler ortam değişkenlerinden ayarlanır:\n"
        f"POINTS_PER_TL\nUSDT_RATE"
    )


async def last_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    con = db()
    rows = con.execute(
        "SELECT * FROM withdrawals ORDER BY id DESC LIMIT 20"
    ).fetchall()
    con.close()
    if not rows:
        await update.message.reply_text("Henüz çekim yok.")
        return
    text = "📋 SON ÇEKİMLER\n\n"
    for r in rows:
        text += f"#{r['id']} | {r['status']} | {r['tl']:.4f} TL | user {r['user_id']}\n"
    await update.message.reply_text(text)


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Yetkin yok.")
        return
    await update.message.reply_text("👨‍💻 Admin paneli açıldı.", reply_markup=admin_menu())


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await handle_withdraw_text(update, context):
        return

    text = update.message.text.strip()
    if text == "🎮 OYNA":
        await play(update, context)
    elif text == "💰 BAKİYEM":
        await balance(update, context)
    elif text == "🎁 GÜNLÜK ÖDÜL":
        await daily(update, context)
    elif text == "👥 DAVET ET":
        await referral(update, context)
    elif text == "🏆 LİDERLİK":
        await leaderboard(update, context)
    elif text == "💸 ÇEKİM":
        await withdrawal(update, context)
    elif text == "📊 İSTATİSTİK":
        await admin_stats(update, context)
    elif text == "💸 BEKLEYEN ÇEKİMLER":
        await pending_withdrawals(update, context)
    elif text == "⚙️ EKONOMİ":
        await economy(update, context)
    elif text == "📋 SON ÇEKİMLER":
        await last_withdrawals(update, context)


async def post_init(application: Application):
    init_db()


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN ortam değişkeni ayarlı değil.")
    if ADMIN_ID == 0:
        raise RuntimeError("ADMIN_ID ortam değişkeni ayarlı değil.")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    print("Bot çalışıyor...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
