"""
search.py — поиск авиабилетов через Ignav API
"""
import asyncio
import logging
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

POPULAR_DESTINATIONS = [
    {"code": "BCN", "name": "Барселона 🇪🇸"},
    {"code": "FCO", "name": "Рим 🇮🇹"},
    {"code": "LIS", "name": "Лиссабон 🇵🇹"},
    {"code": "ATH", "name": "Афины 🇬🇷"},
    {"code": "AMS", "name": "Амстердам 🇳🇱"},
    {"code": "VIE", "name": "Вена 🇦🇹"},
    {"code": "BUD", "name": "Будапешт 🇭🇺"},
    {"code": "PRG", "name": "Прага 🇨🇿"},
    {"code": "DUB", "name": "Дублин 🇮🇪"},
    {"code": "MAD", "name": "Мадрид 🇪🇸"},
    {"code": "MXP", "name": "Милан 🇮🇹"},
    {"code": "CPH", "name": "Копенгаген 🇩🇰"},
    {"code": "LHR", "name": "Лондон 🇬🇧"},
    {"code": "NAP", "name": "Неаполь 🇮🇹"},
    {"code": "OPO", "name": "Порто 🇵🇹"},
    {"code": "SVQ", "name": "Севилья 🇪🇸"},
    {"code": "BEG", "name": "Белград 🇷🇸"},
    {"code": "SKG", "name": "Салоники 🇬🇷"},
    {"code": "SOF", "name": "София 🇧🇬"},
    {"code": "BRU", "name": "Брюссель 🇧🇪"},
    {"code": "TLV", "name": "Тель-Авив 🇮🇱"},
    {"code": "DXB", "name": "Дубай 🇦🇪"},
    {"code": "IST", "name": "Стамбул 🇹🇷"},
    {"code": "RAK", "name": "Марракеш 🇲🇦"},
]

WEEKDAY_NAMES = {
    0: "Понедельник", 1: "Вторник", 2: "Среда",
    3: "Четверг", 4: "Пятница", 5: "Суббота", 6: "Воскресенье",
}

WEEKDAY_SHORT = {
    0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс",
}


def get_next_matching_dates(weekday_idx: int, count: int = 4) -> list[datetime]:
    """Возвращает следующие N дат, соответствующих дню недели (0=Пн, 6=Вс)."""
    today = datetime.today()
    dates = []
    for i in range(1, 90):
        d = today + timedelta(days=i)
        if d.weekday() == weekday_idx:
            dates.append(d)
        if len(dates) == count:
            break
    return dates


async def _search_one(client: httpx.AsyncClient, api_key: str, body: dict) -> list:
    """Один запрос к Ignav API."""
    try:
        r = await client.post(
            "https://ignav.com/api/fares/round-trip",
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        if r.status_code == 401:
            raise ValueError("Неверный Ignav API ключ — проверь /setkey")
        if not r.is_success:
            return []
        return r.json().get("itineraries", [])
    except ValueError:
        raise
    except Exception as e:
        logger.warning(f"Ignav request failed: {e}")
        return []


async def find_cheap_flights(api_key: str, cfg: dict) -> list[dict]:
    """
    Основная функция поиска. Возвращает список результатов, отсортированных по цене.

    Ожидаемые ключи в cfg:
        origin          — IATA аэропорт вылета (default: WAW)
        destinations    — список IATA-кодов или ["anywhere"]
        dep_weekday     — 0–6
        ret_weekday     — 0–6
        dep_from_hour   — час начала окна вылета (0–23)
        dep_to_hour     — час конца окна вылета (0–23)
        ret_from_hour   — час начала окна возврата
        ret_to_hour     — час конца окна возврата
        max_price       — макс. цена в валюте рынка
        adults          — число пассажиров
    """
    dep_dates = get_next_matching_dates(cfg["dep_weekday"], 3)
    ret_dates = get_next_matching_dates(cfg["ret_weekday"], 7)

    pairs = []
    for dep in dep_dates:
        ret = next((r for r in ret_dates if r > dep), None)
        if ret:
            pairs.append((dep, ret))

    destinations_cfg = cfg.get("destinations", ["anywhere"])
    if not destinations_cfg or destinations_cfg[0].lower() == "anywhere":
        dest_list = [d["code"] for d in POPULAR_DESTINATIONS]
    else:
        dest_list = [d.upper() for d in destinations_cfg]

    results = []

    async with httpx.AsyncClient() as client:

        async def search_one_dest(dest_code: str, dep: datetime, ret: datetime):
            body = {
                "origin": cfg.get("origin", "WAW"),
                "destination": dest_code,
                "departure_date": dep.strftime("%Y-%m-%d"),
                "return_date": ret.strftime("%Y-%m-%d"),
                "adults": cfg.get("adults", 1),
                "departure_time_range": {
                    "earliest_hour": cfg.get("dep_from_hour", 16),
                    "latest_hour": cfg.get("dep_to_hour", 23),
                },
                "return_time_range": {
                    "earliest_hour": cfg.get("ret_from_hour", 16),
                    "latest_hour": cfg.get("ret_to_hour", 23),
                },
            }
            if cfg.get("max_price"):
                body["max_price"] = cfg["max_price"]

            itins = await _search_one(client, api_key, body)
            if not itins:
                return None

            best = itins[0]
            dest_info = next((d for d in POPULAR_DESTINATIONS if d["code"] == dest_code), None)
            dest_name = dest_info["name"] if dest_info else dest_code

            out_segs = best.get("outbound", {}).get("segments", [{}])
            in_segs = best.get("inbound", {}).get("segments", [{}])

            return {
                "dest_code": dest_code,
                "dest_name": dest_name,
                "price": best["price"]["amount"],
                "currency": best["price"].get("currency", "USD"),
                "dep_date": dep.strftime("%d.%m"),
                "dep_weekday": WEEKDAY_SHORT[dep.weekday()],
                "ret_date": ret.strftime("%Y-%m-%d"),  # для ссылки
                "ret_date_fmt": ret.strftime("%d.%m"),
                "ret_weekday": WEEKDAY_SHORT[ret.weekday()],
                "airline": best.get("outbound", {}).get("carrier", "?"),
                "stops": max(0, len(out_segs) - 1),
                "duration_min": best.get("outbound", {}).get("duration_minutes"),
                "dep_time": out_segs[0].get("departure_time_local", "")[-14:-9] if out_segs else "",
                "ret_time": in_segs[0].get("departure_time_local", "")[-14:-9] if in_segs else "",
                "ignav_id": best.get("ignav_id", ""),
                "dep_date_iso": dep.strftime("%Y-%m-%d"),
            }

        # Запускаем все задачи параллельно
        tasks = [
            search_one_dest(dest, dep, ret)
            for dest in dest_list
            for (dep, ret) in pairs
        ]
        raw = await asyncio.gather(*tasks, return_exceptions=False)

    results = [r for r in raw if r is not None]
    results.sort(key=lambda x: x["price"])
    return results
