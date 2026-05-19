"""
bot.py — Telegram бот Flight Hunter
Запуск: python bot.py
"""
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config as cfg_module
from search import find_cheap_flights, POPULAR_DESTINATIONS, WEEKDAY_NAMES

load_dotenv()
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
# Если не пусто — бот принимает команды только от этих chat_id
_raw_ids = os.getenv("ALLOWED_CHAT_IDS", "")
ALLOWED_IDS: set[int] = {int(x) for x in _raw_ids.split(",") if x.strip().lstrip("-").isdigit()}

scheduler = AsyncIOScheduler(timezone="Europe/Warsaw")

# ────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────

def authorized(update: Update) -> bool:
    if not ALLOWED_IDS:
        return True
    return update.effective_chat.id in ALLOWED_IDS


def _get_api_key(cfg: dict) -> str:
    return cfg.get("ignav_key") or os.getenv("IGNAV_API_KEY", "")


def format_results(results: list[dict], count: int = 5) -> str:
    if not results:
        return (
            "😔 <b>Ничего не нашлось</b> по твоим критериям.\n\n"
            "Попробуй:\n"
            "• увеличить бюджет (/setbudget)\n"
            "• расширить окно времени (/settime)\n"
            "• проверить настройки (/settings)"
        )

    top = results[:count]
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")
    msg = f"✈️ <b>Топ-{len(top)} вылетов из WAW</b>\n<i>{ts}</i>\n\n"

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]
    for i, f in enumerate(top):
        medal = medals[i] if i < len(medals) else f"{i + 1}."
        stops_str = "🟢 прямой" if f["stops"] == 0 else f"🔸 {f['stops']} пер."
        dur = f"  ·  {f['duration_min'] // 60}ч {f['duration_min'] % 60}м" if f.get("duration_min") else ""
        dep_t = f" {f['dep_time']}" if f.get("dep_time") else ""
        ret_t = f" {f['ret_time']}" if f.get("ret_time") else ""

        msg += (
            f"{medal} <b>{f['dest_name']}</b>\n"
            f"   🛫 {f['dep_weekday']} {f['dep_date']}{dep_t}  →  "
            f"🛬 {f['ret_weekday']} {f['ret_date_fmt']}{ret_t}\n"
            f"   {stops_str}{dur}  ·  {f['airline']}\n"
            f"   💰 <b>{f['price']} {f['currency']}</b> / чел.\n\n"
        )

    msg += "─────────────────\n"
    msg += "📌 Цены могут меняться. Проверяй перед покупкой."
    return msg


def _make_autosearch_keyboard(cfg: dict) -> InlineKeyboardMarkup:
    enabled = cfg.get("auto_search_enabled", False)
    day_names = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    ru_days = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
    cur_day = cfg.get("auto_search_day", "monday")
    cur_hour = cfg.get("auto_search_hour", 8)

    keyboard = [
        [
            InlineKeyboardButton(
                f"{'✅' if enabled else '⬜'} Включён" if enabled else "▶️ Включить",
                callback_data="auto:on"
            ),
            InlineKeyboardButton(
                "⏹ Выключить",
                callback_data="auto:off"
            ),
        ],
        [InlineKeyboardButton("── Выбери день ──", callback_data="noop")],
        [
            InlineKeyboardButton(
                f"{'✅ ' if cur_day == day_names[i] else ''}{ru_days[i]}",
                callback_data=f"auto:day:{day_names[i]}"
            )
            for i in range(7)
        ],
        [InlineKeyboardButton("── Выбери час ──", callback_data="noop")],
        [
            InlineKeyboardButton(
                f"{'✅ ' if cur_hour == h else ''}{h:02d}:00",
                callback_data=f"auto:hour:{h}"
            )
            for h in [7, 8, 9, 12, 18, 20]
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def reschedule(app: Application, cfg: dict) -> None:
    """Перезапускает APScheduler с текущими настройками."""
    scheduler.remove_all_jobs()
    if not cfg.get("auto_search_enabled"):
        return
    chat_id = cfg.get("auto_search_chat_id")
    if not chat_id:
        logger.warning("auto_search_chat_id not set — skipping schedule")
        return

    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    day_num = day_map.get(cfg.get("auto_search_day", "monday"), 0)
    hour = cfg.get("auto_search_hour", 8)

    scheduler.add_job(
        _auto_search_job,
        CronTrigger(day_of_week=day_num, hour=hour, minute=0),
        args=[app, chat_id],
        id="auto_search",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info(f"Автопоиск запланирован: day_of_week={day_num}, hour={hour}")


async def _auto_search_job(app: Application, chat_id: int) -> None:
    """Задача APScheduler — запускается автоматически."""
    cfg = cfg_module.load()
    await _do_search(app, chat_id, cfg)


async def _do_search(app: Application, chat_id: int, cfg: dict) -> None:
    """Выполняет поиск и отправляет результаты в chat_id."""
    api_key = _get_api_key(cfg)
    if not api_key:
        await app.bot.send_message(
            chat_id,
            "❌ Ignav API ключ не задан.\nИспользуй: <code>/setkey ВАШ_КЛЮЧ</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    status_msg = await app.bot.send_message(chat_id, "🔍 Ищу дешёвые билеты, подожди немного...")
    try:
        results = await find_cheap_flights(api_key, cfg)
        text = format_results(results, cfg.get("results_count", 5))
        await status_msg.edit_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except ValueError as e:
        await status_msg.edit_text(f"❌ Ошибка API: {e}", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.exception(f"Search failed: {e}")
        await status_msg.edit_text("❌ Что-то пошло не так. Попробуй позже или проверь /settings.")


# ────────────────────────────────────────────────
# Command handlers
# ────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    cfg = cfg_module.load()
    has_key = bool(_get_api_key(cfg))

    text = (
        "👋 <b>Привет! Я Flight Hunter.</b>\n\n"
        "Ищу самые дешёвые авиабилеты из <b>Варшавы Шопен (WAW)</b> "
        "по твоим настройкам и присылаю сюда.\n\n"
        "<b>Команды:</b>\n"
        "/search — 🔍 Найти билеты прямо сейчас\n"
        "/settings — ⚙️ Все настройки\n"
        "/autosearch — 🤖 Автоматический поиск\n"
        "/help — 📖 Список всех команд\n\n"
    )
    if not has_key:
        text += (
            "⚠️ <b>Первый шаг — задай Ignav API ключ:</b>\n"
            "<code>/setkey ВАШ_КЛЮЧ</code>\n\n"
            "🆓 Бесплатный ключ: ignav.com/signup"
        )
    else:
        text += "✅ Всё готово! Жми /search"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    text = (
        "📖 <b>Команды бота</b>\n\n"

        "<b>🔍 Поиск</b>\n"
        "/search — запустить поиск сейчас\n\n"

        "<b>⚙️ Настройки</b>\n"
        "/settings — показать текущие настройки\n"
        "/setkey KEY — Ignav API ключ\n"
        "/setdest CODE — направление\n"
        "/setdays DEP RET — дни недели (0=Пн, 6=Вс)\n"
        "/settime DF DT RF RT — часы вылета (0–23)\n"
        "/setbudget СУММА — макс. цена в PLN\n"
        "/setadults N — число пассажиров (1–9)\n"
        "/setcount N — кол-во результатов (1–10)\n\n"

        "<b>🤖 Автопоиск</b>\n"
        "/autosearch — настройка расписания\n\n"

        "<b>Примеры</b>\n"
        "<code>/setdest anywhere</code> — искать везде\n"
        "<code>/setdest BCN,FCO,LIS</code> — несколько городов\n"
        "<code>/setdays 4 6</code> — пятница → воскресенье\n"
        "<code>/settime 16 23 16 23</code> — только вечер\n"
        "<code>/setbudget 1200</code> — до 1200 PLN\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    cfg = cfg_module.load()
    await update.message.reply_text(cfg_module.format_summary(cfg), parse_mode=ParseMode.HTML)


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    cfg = cfg_module.load()
    await _do_search(ctx.application, update.effective_chat.id, cfg)


async def cmd_setkey(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    if not ctx.args:
        await update.message.reply_text(
            "Использование: <code>/setkey ВАШ_IGNAV_КЛЮЧ</code>\n\n"
            "Получить бесплатный ключ: ignav.com/signup",
            parse_mode=ParseMode.HTML,
        )
        return
    key = ctx.args[0].strip()
    cfg = cfg_module.load()
    cfg["ignav_key"] = key
    cfg_module.save(cfg)
    # Удаляем сообщение с ключом из чата для безопасности
    try:
        await update.message.delete()
    except Exception:
        pass
    await ctx.bot.send_message(
        update.effective_chat.id,
        "✅ Ignav API ключ сохранён!\n\n"
        "<i>Сообщение с ключом удалено из чата.</i>\n\n"
        "Попробуй /search",
        parse_mode=ParseMode.HTML,
    )


async def cmd_setdest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    if not ctx.args:
        popular = "\n".join(f"  <code>{d['code']}</code> — {d['name']}" for d in POPULAR_DESTINATIONS[:12])
        await update.message.reply_text(
            "Использование:\n"
            "<code>/setdest anywhere</code> — искать везде\n"
            "<code>/setdest BCN</code> — один город\n"
            "<code>/setdest BCN,FCO,LIS</code> — несколько городов\n\n"
            f"<b>Популярные коды:</b>\n{popular}",
            parse_mode=ParseMode.HTML,
        )
        return
    raw = " ".join(ctx.args).upper().strip()
    dests = [d.strip() for d in raw.replace(" ", ",").split(",") if d.strip()]
    cfg = cfg_module.load()
    cfg["destinations"] = dests
    cfg_module.save(cfg)
    dest_str = ", ".join(dests)
    await update.message.reply_text(f"✅ Направление: <b>{dest_str}</b>", parse_mode=ParseMode.HTML)


async def cmd_setdays(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    if len(ctx.args) != 2:
        await update.message.reply_text(
            "Использование: <code>/setdays ДЕНЬ_ТУДА ДЕНЬ_ОБРАТНО</code>\n"
            "0=Пн  1=Вт  2=Ср  3=Чт  4=Пт  5=Сб  6=Вс\n\n"
            "Пример: <code>/setdays 4 6</code>  (пятница → воскресенье)",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        dep, ret = int(ctx.args[0]), int(ctx.args[1])
        assert 0 <= dep <= 6 and 0 <= ret <= 6
    except Exception:
        await update.message.reply_text("❌ Числа от 0 до 6.")
        return
    cfg = cfg_module.load()
    cfg["dep_weekday"] = dep
    cfg["ret_weekday"] = ret
    cfg_module.save(cfg)
    await update.message.reply_text(
        f"✅ Дни: <b>{WEEKDAY_NAMES[dep]}</b> → <b>{WEEKDAY_NAMES[ret]}</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_settime(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    if len(ctx.args) != 4:
        await update.message.reply_text(
            "Использование: <code>/settime DF DT RF RT</code>\n"
            "DF=час начала туда  DT=час конца туда\n"
            "RF=час начала обратно  RT=час конца обратно\n"
            "Часы от 0 до 23.\n\n"
            "Пример: <code>/settime 16 23 16 23</code>  (только вечер)",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        df, dt, rf, rt = [int(x) for x in ctx.args]
        assert all(0 <= h <= 23 for h in [df, dt, rf, rt])
        assert df <= dt and rf <= rt
    except Exception:
        await update.message.reply_text("❌ Неверный формат. Часы 0–23, начало ≤ конец.")
        return
    cfg = cfg_module.load()
    cfg.update({"dep_from_hour": df, "dep_to_hour": dt, "ret_from_hour": rf, "ret_to_hour": rt})
    cfg_module.save(cfg)
    await update.message.reply_text(
        f"✅ Время вылета туда: <b>{df}:00 – {dt}:59</b>\n"
        f"Время вылета обратно: <b>{rf}:00 – {rt}:59</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_setbudget(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    if not ctx.args:
        await update.message.reply_text("Пример: <code>/setbudget 1500</code>", parse_mode=ParseMode.HTML)
        return
    try:
        amount = int(ctx.args[0])
        assert amount > 0
    except Exception:
        await update.message.reply_text("❌ Укажи положительное число.")
        return
    cfg = cfg_module.load()
    cfg["max_price"] = amount
    cfg_module.save(cfg)
    await update.message.reply_text(f"✅ Бюджет: <b>{amount} PLN</b> / чел.", parse_mode=ParseMode.HTML)


async def cmd_setadults(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    if not ctx.args:
        await update.message.reply_text("Пример: <code>/setadults 2</code>", parse_mode=ParseMode.HTML)
        return
    try:
        n = int(ctx.args[0])
        assert 1 <= n <= 9
    except Exception:
        await update.message.reply_text("❌ Число от 1 до 9.")
        return
    cfg = cfg_module.load()
    cfg["adults"] = n
    cfg_module.save(cfg)
    await update.message.reply_text(f"✅ Пассажиры: <b>{n}</b>", parse_mode=ParseMode.HTML)


async def cmd_setcount(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    if not ctx.args:
        await update.message.reply_text("Пример: <code>/setcount 5</code>", parse_mode=ParseMode.HTML)
        return
    try:
        n = int(ctx.args[0])
        assert 1 <= n <= 10
    except Exception:
        await update.message.reply_text("❌ Число от 1 до 10.")
        return
    cfg = cfg_module.load()
    cfg["results_count"] = n
    cfg_module.save(cfg)
    await update.message.reply_text(f"✅ Кол-во результатов: <b>{n}</b>", parse_mode=ParseMode.HTML)


async def cmd_autosearch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorized(update): return
    cfg = cfg_module.load()
    cfg["auto_search_chat_id"] = update.effective_chat.id
    cfg_module.save(cfg)
    status = "✅ включён" if cfg.get("auto_search_enabled") else "❌ выключен"
    text = (
        f"🤖 <b>Автоматический поиск</b>\n\n"
        f"Статус: {status}\n"
        f"Расписание: каждый {cfg.get('auto_search_day','monday')} "
        f"в {cfg.get('auto_search_hour', 8):02d}:00\n\n"
        "Выбери действие:"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=_make_autosearch_keyboard(cfg),
    )


async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "noop":
        return

    cfg = cfg_module.load()
    cfg["auto_search_chat_id"] = query.message.chat_id

    if data == "auto:on":
        cfg["auto_search_enabled"] = True
        cfg_module.save(cfg)
        reschedule(ctx.application, cfg)
        await query.edit_message_text(
            "✅ <b>Автопоиск включён!</b>\n\nБуду присылать результаты по расписанию.\n"
            f"Сейчас: каждый {cfg['auto_search_day']} в {cfg['auto_search_hour']:02d}:00",
            parse_mode=ParseMode.HTML,
        )

    elif data == "auto:off":
        cfg["auto_search_enabled"] = False
        cfg_module.save(cfg)
        scheduler.remove_all_jobs()
        await query.edit_message_text("⏹ Автопоиск выключен.")

    elif data.startswith("auto:day:"):
        day = data.split(":")[-1]
        cfg["auto_search_day"] = day
        cfg_module.save(cfg)
        if cfg.get("auto_search_enabled"):
            reschedule(ctx.application, cfg)
        await query.edit_message_text(
            f"✅ День автопоиска: <b>{day.capitalize()}</b>\n\nДля применения обнови /autosearch",
            parse_mode=ParseMode.HTML,
        )

    elif data.startswith("auto:hour:"):
        hour = int(data.split(":")[-1])
        cfg["auto_search_hour"] = hour
        cfg_module.save(cfg)
        if cfg.get("auto_search_enabled"):
            reschedule(ctx.application, cfg)
        await query.edit_message_text(
            f"✅ Время автопоиска: <b>{hour:02d}:00</b>\n\nДля применения обнови /autosearch",
            parse_mode=ParseMode.HTML,
        )


# ────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────

def main() -> None:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не задан! Проверь .env файл.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("setkey", cmd_setkey))
    app.add_handler(CommandHandler("setdest", cmd_setdest))
    app.add_handler(CommandHandler("setdays", cmd_setdays))
    app.add_handler(CommandHandler("settime", cmd_settime))
    app.add_handler(CommandHandler("setbudget", cmd_setbudget))
    app.add_handler(CommandHandler("setadults", cmd_setadults))
    app.add_handler(CommandHandler("setcount", cmd_setcount))
    app.add_handler(CommandHandler("autosearch", cmd_autosearch))
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Восстанавливаем расписание при старте
    cfg = cfg_module.load()
    if cfg.get("auto_search_enabled") and cfg.get("auto_search_chat_id"):
        reschedule(app, cfg)

    scheduler.start()
    logger.info("✈️  Flight Hunter Bot запущен!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
