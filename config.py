"""
config.py — управление конфигурацией пользователя
"""
import json
import logging
from pathlib import Path

from search import WEEKDAY_NAMES

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("data/config.json")

DEFAULT_CONFIG: dict = {
    "ignav_key": "",
    "origin": "WAW",
    "destinations": ["anywhere"],
    "dep_weekday": 4,           # 0=Пн … 6=Вс; 4 = Пятница
    "ret_weekday": 6,           # 6 = Воскресенье
    "dep_from_hour": 16,
    "dep_to_hour": 23,
    "ret_from_hour": 16,
    "ret_to_hour": 23,
    "max_price": 1500,          # PLN
    "adults": 1,
    "results_count": 5,
    "auto_search_enabled": False,
    "auto_search_day": "monday",
    "auto_search_hour": 8,
    "auto_search_chat_id": None,
}


def load() -> dict:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return {**DEFAULT_CONFIG, **data}
        except Exception as e:
            logger.error(f"Config load error: {e}")
    return DEFAULT_CONFIG.copy()


def save(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def format_summary(cfg: dict) -> str:
    dests = cfg.get("destinations", ["anywhere"])
    dest_str = ", ".join(dests) if dests[0] != "anywhere" else "🌍 Везде (топ-24 города)"
    dep_day = WEEKDAY_NAMES.get(cfg.get("dep_weekday", 4), "?")
    ret_day = WEEKDAY_NAMES.get(cfg.get("ret_weekday", 6), "?")
    auto_status = "✅ включён" if cfg.get("auto_search_enabled") else "❌ выключен"
    auto_day = cfg.get("auto_search_day", "monday").capitalize()
    auto_hour = cfg.get("auto_search_hour", 8)
    key_set = "✅ задан" if cfg.get("ignav_key") else "❌ не задан (/setkey)"

    return (
        f"⚙️ <b>Текущие настройки</b>\n\n"
        f"🔑 <b>Ignav API ключ:</b> {key_set}\n"
        f"🛫 <b>Вылет из:</b> {cfg.get('origin', 'WAW')}\n"
        f"🌍 <b>Направления:</b> {dest_str}\n\n"
        f"📅 <b>День туда:</b> {dep_day}\n"
        f"📅 <b>День обратно:</b> {ret_day}\n"
        f"🕐 <b>Окно вылета туда:</b> {cfg.get('dep_from_hour', 16)}:00 – {cfg.get('dep_to_hour', 23)}:59\n"
        f"🕐 <b>Окно вылета обратно:</b> {cfg.get('ret_from_hour', 16)}:00 – {cfg.get('ret_to_hour', 23)}:59\n\n"
        f"💰 <b>Макс. бюджет:</b> {cfg.get('max_price', 1500)} PLN / чел.\n"
        f"👥 <b>Пассажиры:</b> {cfg.get('adults', 1)}\n"
        f"📊 <b>Кол-во результатов:</b> {cfg.get('results_count', 5)}\n\n"
        f"🤖 <b>Автопоиск:</b> {auto_status}\n"
        f"⏰ <b>Расписание:</b> каждый {auto_day} в {auto_hour:02d}:00\n"
    )
