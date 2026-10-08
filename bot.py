import datetime as dt
import logging
import os
import random
import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup as Markup
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from questions import CATEGORIES, QUESTIONS, TERMS

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ["BOT_TOKEN"]  # your Telegram token, the only key needed
DB_FILE = os.environ.get("DB_PATH", "users.db")
DEFAULT_TZ = "Africa/Lagos"
DEFAULT_TIME = "08:00"
POINTS = 10
LETTERS = "ABCD"


# ---------- storage ----------
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY, name TEXT,
            points INTEGER DEFAULT 0, correct INTEGER DEFAULT 0,
            total INTEGER DEFAULT 0, streak INTEGER DEFAULT 0,
            best INTEGER DEFAULT 0, daily_time TEXT,
            tz TEXT DEFAULT 'Africa/Lagos')"""
    )
    return conn


def ensure_user(chat_id, name):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO users (chat_id, name) VALUES (?, ?)", (chat_id, name))
        c.execute("UPDATE users SET name=? WHERE chat_id=?", (name, chat_id))


def record_answer(chat_id, is_correct):
    with db() as c:
        if is_correct:
            c.execute(
                "UPDATE users SET points=points+?, correct=correct+1, total=total+1, "
                "streak=streak+1, best=MAX(best, streak+1) WHERE chat_id=?",
                (POINTS, chat_id),
            )
        else:
            c.execute(
                "UPDATE users SET total=total+1, streak=0 WHERE chat_id=?", (chat_id,)
            )


def get_stats(chat_id):
    with db() as c:
        row = c.execute(
            "SELECT points, correct, total, streak, best FROM users WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        rank = c.execute(
            "SELECT COUNT(*)+1 FROM users WHERE points > "
            "(SELECT points FROM users WHERE chat_id=?)",
            (chat_id,),
        ).fetchone()[0]
    return row, rank


def stats_text(chat_id):
    row, rank = get_stats(chat_id)
    points, correct, total, streak, best = row
    accuracy = round(correct / total * 100) if total else 0
    return (
        "📊 Your stats\n\n"
        f"⭐ Points: {points}\n"
        f"✅ Correct: {correct}/{total} ({accuracy}%)\n"
        f"🔥 Current streak: {streak}\n"
        f"🏆 Best streak: {best}\n"
        f"🌍 Rank: #{rank}"
    )


def set_daily(chat_id, hhmm=None, tz=None):
    with db() as c:
        if hhmm is not None:
            c.execute("UPDATE users SET daily_time=? WHERE chat_id=?", (hhmm or None, chat_id))
        if tz is not None:
            c.execute("UPDATE users SET tz=? WHERE chat_id=?", (tz, chat_id))


def get_daily(chat_id):
    with db() as c:
        return c.execute(
            "SELECT daily_time, tz FROM users WHERE chat_id=?", (chat_id,)
        ).fetchone()


# ---------- quiz helpers ----------
def pick_question(cat, seen):
    pool = [i for i, q in enumerate(QUESTIONS) if cat == "all" or q[0] == cat]
    fresh = [i for i in pool if i not in seen]
    if not fresh:  # went through everything, start over
        seen[:] = [i for i in seen if i not in pool]
        fresh = pool
    return random.choice(fresh)


def question_text(qid, perm):
    cat, q, opts, _, _ = QUESTIONS[qid]
    lines = [CATEGORIES[cat], "", q, ""]
    for pos, orig in enumerate(perm):
        lines.append(f"{LETTERS[pos]}. {opts[int(orig)]}")
    return "\n".join(lines)


def answer_keyboard(qid, perm):
    return Markup(
        [[Btn(LETTERS[p], callback_data=f"a:{qid}:{perm}:{p}") for p in range(4)]]
    )


async def send_question(bot, chat_id, user_data, cat):
    seen = user_data.setdefault("seen", [])
    qid = pick_question(cat, seen)
    seen.append(qid)
    perm = "".join(random.sample("0123", 4))  # shuffled order of the options
    await bot.send_message(
        chat_id, question_text(qid, perm), reply_markup=answer_keyboard(qid, perm)
    )


def category_keyboard():
    items = list(CATEGORIES.items())
    rows = []
    for i in range(0, len(items), 2):
        rows.append([Btn(label, callback_data=f"c:{key}") for key, label in items[i:i + 2]])
    return Markup(rows)


# ---------- commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_chat.id, update.effective_user.first_name)
    await update.message.reply_text(
        "👋 Welcome to the Forex Quiz Bot!\n\n"
        "Test your forex knowledge, earn points and build a streak.\n\n"
        "/quiz - start a quiz\n"
        "/term - learn a forex term\n"
        "/stats - your score and streak\n"
        "/leaderboard - top players\n"
        f"/daily 08:00 - get a daily term and question (default {DEFAULT_TIME})\n"
        "/stopdaily - stop the daily message\n"
        f"/timezone Africa/Lagos - set your timezone (default {DEFAULT_TZ})\n\n"
        "Send /quiz to begin!"
    )


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_chat.id, update.effective_user.first_name)
    await update.message.reply_text("Pick a category:", reply_markup=category_keyboard())


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_chat.id, update.effective_user.first_name)
    await update.message.reply_text(stats_text(update.effective_chat.id))


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows = c.execute(
            "SELECT name, points FROM users WHERE total > 0 "
            "ORDER BY points DESC LIMIT 10"
        ).fetchall()
    if not rows:
        await update.message.reply_text("No scores yet. Be the first! Send /quiz")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 Top players\n"]
    for i, (name, points) in enumerate(rows):
        lines.append(f"{medals[i] if i < 3 else str(i + 1) + '.'} {name} - {points} pts")
    await update.message.reply_text("\n".join(lines))


async def term(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name, meaning = random.choice(list(TERMS.items()))
    await update.message.reply_text(f"📖 {name}\n\n{meaning}\n\nSend /term for another one.")


# ---------- daily message ----------
async def send_daily(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.chat_id
    name, meaning = random.choice(list(TERMS.items()))
    await context.bot.send_message(
        chat_id, f"☀️ Good morning! Term of the day:\n\n{name}: {meaning}\n\nNow today's question 👇"
    )
    await send_question(context.bot, chat_id, context.user_data, "all")


def schedule(app: Application, chat_id: int, hhmm: str, tz: str):
    for job in app.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    h, m = map(int, hhmm.split(":"))
    app.job_queue.run_daily(
        send_daily,
        time=dt.time(h, m, tzinfo=ZoneInfo(tz)),
        chat_id=chat_id,
        user_id=chat_id,
        name=str(chat_id),
    )


async def restore_jobs(app: Application):
    with db() as c:
        rows = c.execute(
            "SELECT chat_id, daily_time, tz FROM users WHERE daily_time IS NOT NULL"
        ).fetchall()
    for chat_id, hhmm, tz in rows:
        schedule(app, chat_id, hhmm, tz)


async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_user(chat_id, update.effective_user.first_name)
    hhmm = DEFAULT_TIME
    if context.args:
        try:
            h, m = map(int, context.args[0].split(":"))
            assert 0 <= h < 24 and 0 <= m < 60
            hhmm = f"{h:02d}:{m:02d}"
        except Exception:
            await update.message.reply_text("Use the format /daily 08:00")
            return
    set_daily(chat_id, hhmm=hhmm)
    tz = get_daily(chat_id)[1] or DEFAULT_TZ
    schedule(context.application, chat_id, hhmm, tz)
    await update.message.reply_text(
        f"Done! You'll get a forex term and a question every day at {hhmm} ({tz})."
    )


async def stopdaily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    set_daily(chat_id, hhmm="")
    for job in context.application.job_queue.get_jobs_by_name(str(chat_id)):
        job.schedule_removal()
    await update.message.reply_text("Daily message stopped. Send /daily to turn it back on.")


async def timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ensure_user(chat_id, update.effective_user.first_name)
    if not context.args:
        await update.message.reply_text("Use the format /timezone Africa/Lagos")
        return
    tz = context.args[0]
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        await update.message.reply_text(
            "I don't know that timezone. Try Africa/Lagos or Europe/London."
        )
        return
    set_daily(chat_id, tz=tz)
    hhmm = get_daily(chat_id)[0]
    if hhmm:
        schedule(context.application, chat_id, hhmm, tz)
    await update.message.reply_text(f"Timezone set to {tz}.")


# ---------- buttons ----------
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    ensure_user(chat_id, query.from_user.first_name)
    parts = query.data.split(":")
    kind = parts[0]

    if kind in ("c", "n"):  # category chosen, or "next question"
        cat = parts[1] if parts[1] in CATEGORIES else "all"
        context.user_data["cat"] = cat
        if kind == "c":
            await query.edit_message_text(f"Category: {CATEGORIES[cat]}")
        else:
            await query.edit_message_reply_markup(reply_markup=None)
        await send_question(context.bot, chat_id, context.user_data, cat)

    elif kind == "s":  # stop
        await query.edit_message_reply_markup(reply_markup=None)
        await context.bot.send_message(chat_id, "Nice session! 👏\n\n" + stats_text(chat_id) + "\n\nSend /quiz anytime.")

    elif kind == "a":  # answer
        try:
            qid, perm, pick = int(parts[1]), parts[2], int(parts[3])
            _, _, opts, ans, why = QUESTIONS[qid]
            correct = int(perm[pick]) == ans
        except (ValueError, IndexError):
            await query.edit_message_text("That question expired. Send /quiz to continue.")
            return

        done = context.user_data.setdefault("done", set())
        key = (chat_id, query.message.message_id)
        if key in done:  # ignore double taps
            return
        done.add(key)

        record_answer(chat_id, correct)
        if correct:
            row, _ = get_stats(chat_id)
            verdict = f"✅ Correct! +{POINTS} points\n🔥 Streak: {row[3]}"
        else:
            verdict = f"❌ Not quite.\nCorrect answer: {LETTERS[perm.index(str(ans))]}. {opts[ans]}"

        text = f"{question_text(qid, perm)}\n\nYou chose: {LETTERS[pick]}\n\n{verdict}\n\n💡 {why}"
        cat = context.user_data.get("cat", "all")
        keyboard = Markup(
            [[Btn("➡️ Next question", callback_data=f"n:{cat}"), Btn("🏁 I'm done", callback_data="s")]]
        )
        await query.edit_message_text(text, reply_markup=keyboard)


def main():
    app = Application.builder().token(TOKEN).post_init(restore_jobs).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("term", term))
    app.add_handler(CommandHandler("daily", daily))
    app.add_handler(CommandHandler("stopdaily", stopdaily))
    app.add_handler(CommandHandler("timezone", timezone))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling()


if __name__ == "__main__":
    main()
