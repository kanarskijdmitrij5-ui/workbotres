#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import math
import logging
from datetime import datetime, timezone
from typing import Literal, Optional

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

# ========== НАСТРОЙКА ЛОГГИРОВАНИЯ ==========
logging.basicConfig(
    level=os.getenv("LOGLEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("timetracker")

# ========== КОНФИГУРАЦИЯ ==========
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_NS = os.getenv("REDIS_NS", "timetracker")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("Переменная окружения TELEGRAM_BOT_TOKEN не задана")

ADMIN_IDS = {742587575, 64408195}
SQM_IDS = {904273869, 5955218562, 742587575}
DRIVER_IDS = {748721414, 742587575}
SQM_ONLY_IDS = {904273869, 5955218562}

# ========== ХЕЛПЕРЫ ДЛЯ REDIS ==========
class RedisManager:
    def __init__(self, url: str, namespace: str):
        self.redis = None
        self.url = url
        self.ns = namespace

    async def __aenter__(self):
        self.redis = await redis.from_url(self.url, decode_responses=True)
        return self.redis, self.ns

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.redis:
            await self.redis.close()

def redis_client():
    return RedisManager(REDIS_URL, REDIS_NS)

# ========== HELPER FUNCTIONS ==========
async def tg(method: str, data: dict) -> dict:
    """Вызов Telegram Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json=data)
        r.raise_for_status()
        return r.json()

async def send_msg(chat_id: int, text: str, reply_markup: dict | None = None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    await tg("sendMessage", payload)

# ========== КЛАВИАТУРЫ ==========
def main_menu_kb() -> dict:
    return {
        "keyboard": [
            [{"text": "🟢 Начать смену"}, {"text": "🔴 Завершить смену"}],
            [{"text": "📦 Склад: начать"}, {"text": "📦 Склад: завершить"}],
            [{"text": "💰 Зарплата"}, {"text": "👤 Мой кабинет"}],
            [{"text": "📊 Премии/Штрафы"}, {"text": "↩️ Отменить"}],
        ],
        "resize_keyboard": True
    }

def location_kb(prompt: str) -> dict:
    return {
        "keyboard": [[{"text": prompt, "request_location": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

WORKPLACE_POLICIES = """
📑 <b>ПРАВИЛА ВНУТРЕННЕГО ТРУДОВОГО РАСПОРЯДКА</b>
────────────────────────────────────

<b>I. РАБОЧЕЕ ВРЕМЯ И ДИСЦИПЛИНА</b>
1.1. Сотрудник обязан соблюдать установленный график работы. Опоздания и неявки без уважительной причины являются нарушением трудовой дисциплины.
1.2. Начало и окончание рабочей смены фиксируются исключительно через систему учёта (данный бот).
1.3. Сотрудник обязан выполнять распоряжения непосредственного руководителя.

<b>II. МАТЕРИАЛЬНАЯ ОТВЕТСТВЕННОСТЬ</b>
2.1. Сотрудник несёт полную материальную ответственность за вверенное имущество, оборудование и инструменты.
2.2. О любых поломках, неисправностях или повреждениях необходимо незамедлительно уведомить руководителя.
2.3. Ущерб, причинённый по вине сотрудника, подлежит возмещению в установленном порядке.

<b>III. ВНЕШНИЙ ВИД И ПОВЕДЕНИЕ</b>
3.1. Сотрудник обязан поддерживать опрятный внешний вид, соответствующий стандартам компании.
3.2. На рабочем месте соблюдать этику делового общения, вежливо относиться к коллегам и клиентам.

<b>IV. БЕЗОПАСНОСТЬ И ОХРАНА ТРУДА</b>
4.1. Соблюдать правила техники безопасности и охраны труда.
4.2. Содержать рабочее место в чистоте и порядке.
4.3. Использовать средства индивидуальной защиты (при необходимости).
4.4. Категорически запрещается находиться на рабочем месте в состоянии алкогольного, наркотического или иного токсического опьянения. Нарушение данного пункта влечёт немедленное отстранение от работы и дисциплинарное взыскание вплоть до увольнения.

<b>V. КОНФИДЕНЦИАЛЬНОСТЬ</b>
5.1. Не разглашать коммерческую тайну и внутреннюю информацию компании третьим лицам.

<b>VI. УЧЁТ РАБОЧЕГО ВРЕМЕНИ</b>
6.1. При начале и окончании смены отправлять геолокацию для подтверждения присутствия.
6.2. Заработная плата рассчитывается на основании фактически отработанных часов.
6.3. Отключение таймера рабочей смены должно производиться строго на рабочем месте в момент фактического окончания работы. Отключение таймера не вовремя или находясь вне рабочего места является нарушением трудовой дисциплины и влечёт наложение штрафа.

<b>VII. ПРЕМИИ И ВЗЫСКАНИЯ</b>
7.1. За добросовестное выполнение обязанностей могут быть назначены премии.
7.2. За нарушение настоящих правил могут быть применены дисциплинарные взыскания и штрафы.

────────────────────────────────────
Подтвердите ознакомление с правилами внутреннего трудового распорядка:
"""

def agreement_kb() -> dict:
    return {
        "keyboard": [[{"text": "✅ Согласен"}, {"text": "❌ Не согласен"}]],
        "resize_keyboard": True,
        "one_time_keyboard": True
    }

def admin_menu_kb() -> dict:
    return {
        "keyboard": [
            [{"text": "📋 Сотрудники"}, {"text": "💰 Зарплата отчёт"}],
            [{"text": "➕ Начислить"}, {"text": "✏️ Изменить ставку"}, {"text": "⏹️ Завершить смену"}],
            [{"text": "🚪 Уволить"}, {"text": "◀️ Назад"}],
        ],
        "resize_keyboard": True
    }

def is_admin(tg_id: int) -> bool:
    return tg_id in ADMIN_IDS

def is_sqm_worker(tg_id: int) -> bool:
    return tg_id in SQM_IDS

def is_driver(tg_id: int) -> bool:
    return tg_id in DRIVER_IDS

def sqm_menu_kb(tg_id: int) -> dict:
    if tg_id in SQM_ONLY_IDS:
        base = [
            [{"text": "📦 Склад: начать"}, {"text": "📦 Склад: завершить"}],
            [{"text": "🏕️ Монтаж шатров"}, {"text": "🏠 Монтаж полов"}],
            [{"text": "💰 Зарплата"}, {"text": "👤 Мой кабинет"}],
            [{"text": "📊 Премии/Штрафы"}, {"text": "↩️ Отменить"}],
        ]
    else:
        base = [
            [{"text": "🟢 Начать смену"}, {"text": "🔴 Завершить смену"}],
            [{"text": "📦 Склад: начать"}, {"text": "📦 Склад: завершить"}],
            [{"text": "💰 Зарплата"}, {"text": "👤 Мой кабинет"}],
            [{"text": "📊 Премии/Штрафы"}, {"text": "↩️ Отменить"}],
            [{"text": "🏕️ Монтаж шатров"}, {"text": "🏠 Монтаж полов"}],
        ]
    if is_driver(tg_id):
        base.append([{"text": "🚗 Водитель: начать"}, {"text": "🚗 Водитель: завершить"}])
    if is_admin(tg_id):
        base.append([{"text": "🛠️ Сотрудники"}, {"text": "🛠️ Начислить"}, {"text": "🛠️ Уволить"}])
    return {"keyboard": base, "resize_keyboard": True}

def get_kb(tg_id: int) -> dict:
    if is_sqm_worker(tg_id) or is_driver(tg_id):
        return sqm_menu_kb(tg_id)
    if is_admin(tg_id):
        return admin_menu_kb()
    return main_menu_kb()

# ========== РАБОТА С ДАННЫМИ ==========
async def get_employee(redis, ns: str, tg_id: int) -> dict | None:
    raw = await redis.get(f"{ns}:employee:{tg_id}")
    return json.loads(raw) if raw else None

async def save_employee(redis, ns: str, tg_id: int, data: dict):
    await redis.set(f"{ns}:employee:{tg_id}", json.dumps(data, ensure_ascii=False))
    await redis.sadd(f"{ns}:employees_index", str(tg_id))

async def get_session(redis, ns: str, tg_id: int) -> dict | None:
    raw = await redis.get(f"{ns}:session:{tg_id}")
    return json.loads(raw) if raw else None

async def save_session(redis, ns: str, tg_id: int, data: dict | None):
    key = f"{ns}:session:{tg_id}"
    if data is None:
        await redis.delete(key)
    else:
        await redis.set(key, json.dumps(data, ensure_ascii=False))

async def get_shifts(redis, ns: str, tg_id: int) -> list:
    raw = await redis.get(f"{ns}:shifts:{tg_id}")
    return json.loads(raw) if raw else []

async def save_shifts(redis, ns: str, tg_id: int, shifts: list):
    await redis.set(f"{ns}:shifts:{tg_id}", json.dumps(shifts, ensure_ascii=False))

async def get_bonuses(redis, ns: str, tg_id: int) -> list:
    raw = await redis.get(f"{ns}:bonuses:{tg_id}")
    return json.loads(raw) if raw else []

async def save_bonuses(redis, ns: str, tg_id: int, bonuses: list):
    await redis.set(f"{ns}:bonuses:{tg_id}", json.dumps(bonuses, ensure_ascii=False))

async def get_driver_shifts(redis, ns: str, tg_id: int) -> list:
    raw = await redis.get(f"{ns}:driver_shifts:{tg_id}")
    return json.loads(raw) if raw else []

async def save_driver_shifts(redis, ns: str, tg_id: int, shifts: list):
    await redis.set(f"{ns}:driver_shifts:{tg_id}", json.dumps(shifts, ensure_ascii=False))

async def get_sqm_jobs(redis, ns: str, tg_id: int) -> list:
    raw = await redis.get(f"{ns}:sqm_jobs:{tg_id}")
    return json.loads(raw) if raw else []

async def save_sqm_jobs(redis, ns: str, tg_id: int, jobs: list):
    await redis.set(f"{ns}:sqm_jobs:{tg_id}", json.dumps(jobs, ensure_ascii=False))

async def get_all_employee_ids(redis, ns: str) -> list[str]:
    members = await redis.smembers(f"{ns}:employees_index")
    return [m if isinstance(m, str) else m.decode() for m in members]

# ========== ОСНОВНАЯ ЛОГИКА БОТА ==========
async def handle_registration(chat_id: int, tg_id: int, text: str, session: dict | None, redis, ns: str) -> bool:
    if session and session.get("state") == "awaiting_name":
        if len(text) < 3:
            await send_msg(chat_id, "❌ Введите полное ФИО (минимум 3 символа):")
            return True
        await save_session(redis, ns, tg_id, {"state": "awaiting_rate", "name": text})
        await send_msg(chat_id, f"✅ <b>{text}</b>\n\nВведите вашу <b>почасовую ставку</b> (основная, руб/час):")
        return True

    if session and session.get("state") == "awaiting_rate":
        try:
            rate = float(text.replace(",", "."))
            if rate <= 0:
                raise ValueError
        except ValueError:
            await send_msg(chat_id, "❌ Введите корректное число (например, 500 или 1200.50):")
            return True
        await save_session(redis, ns, tg_id, {"state": "awaiting_warehouse_rate", "name": session["name"], "rate": rate})
        await send_msg(chat_id, f"✅ Основная ставка: <b>{rate} руб/час</b>\n\nТеперь введите <b>складскую ставку</b> (руб/час):")
        return True

    if session and session.get("state") == "awaiting_warehouse_rate":
        try:
            wrate = float(text.replace(",", "."))
            if wrate <= 0:
                raise ValueError
        except ValueError:
            await send_msg(chat_id, "❌ Введите корректное число:")
            return True
        await save_session(redis, ns, tg_id, {"state": "awaiting_agreement", "name": session["name"], "rate": session["rate"], "warehouse_rate": wrate})
        await send_msg(chat_id, WORKPLACE_POLICIES, agreement_kb())
        return True

    if session and session.get("state") == "awaiting_agreement":
        if "Согласен" in text:
            employee = {
                "name": session["name"],
                "hourly_rate": session["rate"],
                "warehouse_rate": session.get("warehouse_rate", session["rate"]),
                "registered_at": datetime.now(timezone.utc).isoformat(),
                "tg_id": tg_id,
                "policies_accepted": True,
                "policies_accepted_at": datetime.now(timezone.utc).isoformat(),
            }
            await save_employee(redis, ns, tg_id, employee)
            await save_session(redis, ns, tg_id, None)
            await send_msg(
                chat_id,
                f"🎉 <b>Регистрация завершена!</b>\n\n"
                f"👤 {employee['name']}\n"
                f"💰 Ставка: {session['rate']} руб/час\n"
                f"✅ Правила приняты\n\n"
                f"Используйте кнопки ниже для работы.",
                get_kb(tg_id),
            )
            return True
        elif "Не согласен" in text:
            await save_session(redis, ns, tg_id, None)
            await send_msg(
                chat_id,
                "❌ Вы отклонили правила. Регистрация отменена.\n\n"
                "Для повторной попытки отправьте /start",
            )
            return True
        else:
            await send_msg(chat_id, "Нажмите <b>✅ Согласен</b> или <b>❌ Не согласен</b>", agreement_kb())
            return True
    return False

async def handle_sqm_flow(chat_id: int, tg_id: int, text: str, session: dict | None, redis, ns: str) -> bool:
    if not is_sqm_worker(tg_id) and not is_driver(tg_id):
        return False
    if "Монтаж шатров" in text and not (session and session.get("state", "").startswith("sqm_")):
        await save_session(redis, ns, tg_id, {"state": "sqm_rate", "work_type": "tent"})
        await send_msg(chat_id, "🏕️ <b>Монтаж шатров</b>\n\nВведите <b>ставку за м²</b> (руб):")
        return True
    if "Монтаж полов" in text and not (session and session.get("state", "").startswith("sqm_")):
        await save_session(redis, ns, tg_id, {"state": "sqm_rate", "work_type": "floor"})
        await send_msg(chat_id, "🏠 <b>Монтаж полов</b>\n\nВведите <b>ставку за м²</b> (руб):")
        return True
    if not session or not (session.get("state", "").startswith("sqm_") or session.get("state", "").startswith("awaiting_driver") or session.get("state", "") == "on_driver_shift"):
        return False
    state = session["state"]
    if state == "awaiting_driver_rate":
        try:
            d_rate = float(text.replace(",", "."))
            if d_rate <= 0:
                raise ValueError
        except ValueError:
            await send_msg(chat_id, "❌ Введите корректное число (например: 300):")
            return True
        await save_session(redis, ns, tg_id, {"state": "on_driver_shift", "start_time": datetime.now(timezone.utc).isoformat(), "driver_rate": d_rate})
        await send_msg(chat_id, f"🚗 <b>Водительская смена начата!</b>\n💰 Ставка: {d_rate} руб/ч\n\nНажмите <b>🚗 Водитель: завершить</b> когда закончите.", get_kb(tg_id))
        return True
    if state == "sqm_rate":
        try:
            rate = float(text.replace(",", "."))
            if rate <= 0:
                raise ValueError
        except ValueError:
            await send_msg(chat_id, "❌ Введите корректное число (например: 70):")
            return True
        await save_session(redis, ns, tg_id, {**session, "state": "sqm_volume", "sqm_rate": rate})
        await send_msg(chat_id, f"✅ Ставка: <b>{rate} руб/м²</b>\n\nВведите <b>объём работы</b> (м²):")
        return True
    if state == "sqm_volume":
        try:
            volume = float(text.replace(",", "."))
            if volume <= 0:
                raise ValueError
        except ValueError:
            await send_msg(chat_id, "❌ Введите корректное число (например: 25):")
            return True
        rate = session["sqm_rate"]
        work_type = session["work_type"]
        earned = round(rate * volume, 2)
        label = "Монтаж шатров" if work_type == "tent" else "Монтаж полов"
        icon = "🏕️" if work_type == "tent" else "🏠"
        job = {"type": work_type, "rate_per_sqm": rate, "volume_sqm": volume, "earned": earned, "date": datetime.now(timezone.utc).isoformat()}
        jobs = await get_sqm_jobs(redis, ns, tg_id)
        jobs.append(job)
        await save_sqm_jobs(redis, ns, tg_id, jobs)
        await save_session(redis, ns, tg_id, None)
        await send_msg(chat_id, f"{icon} <b>{label} — записано!</b>\n\n📐 Объём: <b>{volume} м²</b>\n💰 Ставка: <b>{rate} руб/м²</b>\n💵 Заработано: <b>{earned} руб</b>", get_kb(tg_id))
        return True
    return False

async def handle_location(chat_id: int, tg_id: int, loc: dict, session: dict, emp: dict, redis, ns: str) -> bool:
    lat, lon = loc["latitude"], loc["longitude"]
    now = datetime.now(timezone.utc).isoformat()

    if session.get("state") == "awaiting_warehouse_start_loc":
        await save_session(redis, ns, tg_id, {
            "state": "on_warehouse_shift",
            "start_time": now, "start_lat": lat, "start_lon": lon,
        })
        await send_msg(chat_id, f"📦 <b>Складская смена начата!</b>\n📍 {lat:.5f}, {lon:.5f}", get_kb(tg_id))
        return True

    if session.get("state") == "awaiting_warehouse_end_loc":
        start_time = datetime.fromisoformat(session["start_time"])
        end_time = datetime.fromisoformat(now)
        hours_rounded = round((end_time - start_time).total_seconds() / 3600, 2)
        wrate = emp.get("warehouse_rate", emp["hourly_rate"])
        earned = round(hours_rounded * wrate, 2)
        shift = {
            "start_time": session["start_time"], "end_time": now,
            "start_lat": session["start_lat"], "start_lon": session["start_lon"],
            "end_lat": lat, "end_lon": lon,
            "hours": hours_rounded, "earned": earned, "type": "warehouse",
        }
        shifts = await get_shifts(redis, ns, tg_id)
        shifts.append(shift)
        await save_shifts(redis, ns, tg_id, shifts)
        await save_session(redis, ns, tg_id, None)
        await send_msg(chat_id, f"📦 <b>Складская смена завершена!</b>\n\n⏱️ {hours_rounded} ч\n💵 {earned} руб ({wrate} руб/ч)", get_kb(tg_id))
        return True

    if session.get("state") == "awaiting_start_location":
        await save_session(redis, ns, tg_id, {
            "state": "on_shift",
            "start_time": now,
            "start_lat": lat,
            "start_lon": lon,
        })
        await send_msg(
            chat_id,
            f"✅ <b>Смена начата!</b>\n"
            f"📍 Локация: {lat:.5f}, {lon:.5f}\n"
            f"⏱️ Время: {datetime.fromisoformat(now).strftime('%H:%M %d.%m.%Y')}\n\n"
            f"Хорошего рабочего дня! Нажмите \"🔴 Завершить смену\" когда закончите.",
            get_kb(tg_id),
        )
        return True

    if session.get("state") == "awaiting_end_location":
        start_time = datetime.fromisoformat(session["start_time"])
        end_time = datetime.fromisoformat(now)
        hours_rounded = round((end_time - start_time).total_seconds() / 3600, 2)
        earned = round(hours_rounded * emp["hourly_rate"], 2)

        shift = {
            "start_time": session["start_time"], "end_time": now,
            "start_lat": session["start_lat"], "start_lon": session["start_lon"],
            "end_lat": lat, "end_lon": lon,
            "hours": hours_rounded, "earned": earned,
        }
        shifts = await get_shifts(redis, ns, tg_id)
        shifts.append(shift)
        await save_shifts(redis, ns, tg_id, shifts)
        await save_session(redis, ns, tg_id, None)

        await send_msg(
            chat_id,
            f"🛑 <b>Смена завершена!</b>\n\n"
            f"⏱️ Отработано: <b>{hours_rounded} ч</b>\n"
            f"💵 Заработано: <b>{earned} руб</b>\n"
            f"📍 Локация: {lat:.5f}, {lon:.5f}",
            get_kb(tg_id),
        )
        return True
    return False

async def cmd_salary(chat_id: int, tg_id: int, emp: dict, redis, ns: str):
    shifts = await get_shifts(redis, ns, tg_id)
    bonuses = await get_bonuses(redis, ns, tg_id)
    now = datetime.now(timezone.utc)
    month_shifts = [s for s in shifts if datetime.fromisoformat(s["start_time"]).month == now.month and datetime.fromisoformat(s["start_time"]).year == now.year]
    total_hours = sum(s["hours"] for s in month_shifts)
    base_salary = round(total_hours * emp["hourly_rate"], 2)
    month_bonuses = [b for b in bonuses if datetime.fromisoformat(b["date"]).month == now.month and datetime.fromisoformat(b["date"]).year == now.year]
    total_bonuses = sum(b["amount"] for b in month_bonuses if b["type"] == "bonus")
    total_penalties = sum(b["amount"] for b in month_bonuses if b["type"] == "penalty")
    sqm_jobs = await get_sqm_jobs(redis, ns, tg_id)
    month_sqm = [j for j in sqm_jobs if datetime.fromisoformat(j["date"]).month == now.month and datetime.fromisoformat(j["date"]).year == now.year]
    sqm_total = round(sum(j["earned"] for j in month_sqm), 2)
    d_shifts_all = await get_driver_shifts(redis, ns, tg_id)
    month_drv = [d for d in d_shifts_all if datetime.fromisoformat(d["date"]).month == now.month and datetime.fromisoformat(d["date"]).year == now.year]
    driver_total = round(sum(d["earned"] for d in month_drv), 2)
    total = round(base_salary + sqm_total + driver_total + total_bonuses - total_penalties, 2)
    sqm_line = f"📐 Монтаж (м²): <b>{sqm_total} руб</b>\n" if sqm_total > 0 else ""
    drv_line = f"🚗 Водитель: <b>{driver_total} руб</b>\n" if driver_total > 0 else ""
    await send_msg(
        chat_id,
        f"💰 <b>Зарплата за {now.strftime('%B %Y')}</b>\n\n"
        f"⏱️ Часы: <b>{round(total_hours, 2)} ч</b>\n"
        f"💵 Ставка: {emp['hourly_rate']} руб/час\n"
        f"💵 Базовая: <b>{base_salary} руб</b>\n"
        f"{sqm_line}"
        f"{drv_line}"
        f"🎁 Премии: <b>+{total_bonuses} руб</b>\n"
        f"⚠️ Штрафы: <b>-{total_penalties} руб</b>\n"
        f"\n💰 <b>Итого: {total} руб</b>",
        get_kb(tg_id),
    )

async def cmd_profile(chat_id: int, tg_id: int, emp: dict, session: dict | None, redis, ns: str):
    shifts = await get_shifts(redis, ns, tg_id)
    recent = shifts[-5:] if shifts else []
    status = "🟢 На смене" if (session and session.get("state") == "on_shift") else "⚪ Не на смене"
    shifts_text = ""
    for s in reversed(recent):
        st = datetime.fromisoformat(s["start_time"]).strftime("%d.%m %H:%M")
        shifts_text += f"  • {st} — {s['hours']} ч — {s['earned']} руб\n"
    if not shifts_text:
        shifts_text = "  Нет смен\n"
    await send_msg(
        chat_id,
        f"👤 <b>Личный кабинет</b>\n\n"
        f"📋 ФИО: <b>{emp['name']}</b>\n"
        f"💰 Ставка: {emp['hourly_rate']} руб/час\n"
        f"📊 Статус: {status}\n"
        f"⏱️ Всего смен: {len(shifts)}\n\n"
        f"📅 <b>Последние смены:</b>\n{shifts_text}",
        get_kb(tg_id),
    )

async def cmd_undo(chat_id: int, tg_id: int, redis, ns: str):
    session = await get_session(redis, ns, tg_id)
    if session:
        state = session.get("state", "")
        if state in ("on_shift", "on_warehouse_shift", "on_driver_shift") or state.startswith("awaiting_") or state.startswith("sqm_") or state.startswith("admin_"):
            await save_session(redis, ns, tg_id, None)
            await send_msg(chat_id, "↩️ <b>Отменено:</b> текущая операция сброшена", get_kb(tg_id))
            return True
    # Нет активной сессии — удаляем последнее действие
    candidates = []
    shifts = await get_shifts(redis, ns, tg_id)
    if shifts:
        last = shifts[-1]
        dt = last.get("end_time") or last.get("start_time", "")
        stype = "Склад" if last.get("type") == "warehouse" else "Смена"
        candidates.append((dt, "shift", f"{stype}: {last.get('hours', 0)} ч, {last.get('earned', 0)} руб"))
    sqm = await get_sqm_jobs(redis, ns, tg_id)
    if sqm:
        last = sqm[-1]
        label = "Шатры" if last.get("type") == "tent" else "Полы"
        candidates.append((last.get("date", ""), "sqm", f"Монтаж {label}: {last.get('volume_sqm', 0)} м², {last.get('earned', 0)} руб"))
    d_shifts = await get_driver_shifts(redis, ns, tg_id)
    if d_shifts:
        last = d_shifts[-1]
        candidates.append((last.get("date", ""), "driver", f"Водитель: {last.get('hours', 0)} ч, {last.get('earned', 0)} руб"))
    if not candidates:
        await send_msg(chat_id, "❌ Нет действий для отмены.", get_kb(tg_id))
        return
    candidates.sort(key=lambda x: x[0], reverse=True)
    latest_type = candidates[0][1]
    latest_desc = candidates[0][2]
    if latest_type == "shift":
        shifts.pop()
        await save_shifts(redis, ns, tg_id, shifts)
    elif latest_type == "sqm":
        sqm.pop()
        await save_sqm_jobs(redis, ns, tg_id, sqm)
    elif latest_type == "driver":
        d_shifts.pop()
        await save_driver_shifts(redis, ns, tg_id, d_shifts)
    await send_msg(chat_id, f"↩️ <b>Отменено:</b>\n{latest_desc}", get_kb(tg_id))

async def cmd_bonuses(chat_id: int, tg_id: int, redis, ns: str):
    bonuses = await get_bonuses(redis, ns, tg_id)
    if not bonuses:
        await send_msg(chat_id, "📊 У вас пока нет премий и штрафов.", get_kb(tg_id))
        return
    lines = ""
    for b in reversed(bonuses[-10:]):
        icon = "🎁" if b["type"] == "bonus" else "⚠️"
        sign = "+" if b["type"] == "bonus" else "-"
        dt = datetime.fromisoformat(b["date"]).strftime("%d.%m.%Y")
        lines += f"  {icon} {dt}: <b>{sign}{b['amount']} руб</b> — {b['reason']}\n"
    await send_msg(chat_id, f"📊 <b>Премии и штрафы</b>\n\n{lines}", get_kb(tg_id))

# ========== АДМИНСКИЕ ФУНКЦИИ ==========
async def cmd_admin_employee_detail(chat_id: int, tg_id: int, target_tg_id: int, redis, ns: str):
    emp = await get_employee(redis, ns, target_tg_id)
    if not emp:
        await send_msg(chat_id, "❌ Сотрудник не найден", admin_menu_kb())
        return None
    session = await get_session(redis, ns, target_tg_id)
    shifts = await get_shifts(redis, ns, target_tg_id)
    bonuses = await get_bonuses(redis, ns, target_tg_id)
    sqm_jobs = await get_sqm_jobs(redis, ns, target_tg_id)
    driver_shifts = await get_driver_shifts(redis, ns, target_tg_id)
    total_hours = round(sum(s["hours"] for s in shifts), 2)
    total_base = round(total_hours * emp["hourly_rate"], 2)
    total_sqm = round(sum(j["earned"] for j in sqm_jobs), 2)
    total_driver = round(sum(d["earned"] for d in driver_shifts), 2)
    bonus_sum = sum(b["amount"] for b in bonuses if b["type"] == "bonus")
    penalty_sum = sum(b["amount"] for b in bonuses if b["type"] == "penalty")
    total_salary = round(total_base + total_sqm + total_driver + bonus_sum - penalty_sum, 2)
    status = "🟢 На смене" if (session and session.get("state") == "on_shift") else "⚪ Не на смене"
    last_shift = shifts[-1] if shifts else None
    last_shift_info = f"{last_shift['hours']} ч / {last_shift['earned']} руб" if last_shift else "Нет"
    text = (
        f"👤 <b>{emp['name']}</b>\n"
        f"🆔 ID: {target_tg_id}\n"
        f"📊 Статус: {status}\n"
        f"💰 Ставка: {emp['hourly_rate']} руб/ч\n"
        f"📦 Складская: {emp.get('warehouse_rate', emp['hourly_rate'])} руб/ч\n"
        f"─── <b>Статистика (всего)</b> ───\n"
        f"⏱️ Часы: {total_hours} ч\n"
        f"🏗️ Монтаж: {total_sqm} руб\n"
        f"🚗 Водитель: {total_driver} руб\n"
        f"📈 Смен: {len(shifts)}\n"
        f"🎁 Премии: +{bonus_sum} руб\n"
        f"⚠️ Штрафы: -{penalty_sum} руб\n"
        f"💵 <b>Итого: {total_salary} руб</b>\n"
        f"─── <b>Последняя смена</b> ───\n"
        f"{last_shift_info}"
    )
    await save_session(redis, ns, tg_id, {"state": "admin_viewing", "target_id": target_tg_id, "target_name": emp['name']})
    kb = {
        "keyboard": [
            [{"text": "✏️ Сменить ставку"}, {"text": "⏹️ Завершить смену"}],
            [{"text": "📊 Отчёт за месяц"}, {"text": "🚪 Уволить"}],
            [{"text": "◀️ Назад к списку"}],
        ],
        "resize_keyboard": True
    }
    await send_msg(chat_id, text, kb)
    return target_tg_id

async def cmd_admin_change_rate_start(chat_id: int, tg_id: int, target_tg_id: int, redis, ns: str):
    emp = await get_employee(redis, ns, target_tg_id)
    if not emp:
        await send_msg(chat_id, "❌ Сотрудник не найден", admin_menu_kb())
        return
    await save_session(redis, ns, tg_id, {"state": "admin_change_rate", "target_id": target_tg_id, "target_name": emp['name']})
    await send_msg(chat_id, f"Сотрудник: <b>{emp['name']}</b>\nТекущая ставка: {emp['hourly_rate']} руб/ч\n\nВведите <b>новую основную ставку</b> (руб/ч):")

async def cmd_admin_change_rate_amount(chat_id: int, tg_id: int, text: str, session: dict, redis, ns: str):
    try:
        new_rate = float(text.replace(",", "."))
        if new_rate <= 0:
            raise ValueError
    except ValueError:
        await send_msg(chat_id, "❌ Введите корректное число (например: 500)")
        return
    target_id = session["target_id"]
    emp = await get_employee(redis, ns, target_id)
    if not emp:
        await send_msg(chat_id, "❌ Сотрудник не найден")
        await save_session(redis, ns, tg_id, None)
        return
    old_rate = emp["hourly_rate"]
    emp["hourly_rate"] = new_rate
    await save_employee(redis, ns, target_id, emp)
    await save_session(redis, ns, tg_id, None)
    await send_msg(chat_id, f"✅ Ставка для <b>{emp['name']}</b> изменена:\n{old_rate} → {new_rate} руб/ч", admin_menu_kb())
    try:
        await send_msg(target_id, f"🔄 Ваша почасовая ставка изменена: <b>{old_rate} → {new_rate} руб/ч</b>")
    except:
        pass

async def cmd_admin_force_end_shift(chat_id: int, tg_id: int, target_tg_id: int, redis, ns: str):
    session = await get_session(redis, ns, target_tg_id)
    if not session or session.get("state") != "on_shift":
        await send_msg(chat_id, "❌ У этого сотрудника нет активной смены", admin_menu_kb())
        return
    emp = await get_employee(redis, ns, target_tg_id)
    now = datetime.now(timezone.utc).isoformat()
    start_time = datetime.fromisoformat(session["start_time"])
    end_time = datetime.fromisoformat(now)
    hours_rounded = round((end_time - start_time).total_seconds() / 3600, 2)
    earned = round(hours_rounded * emp["hourly_rate"], 2)
    shift = {
        "start_time": session["start_time"], "end_time": now,
        "start_lat": session.get("start_lat"), "start_lon": session.get("start_lon"),
        "end_lat": None, "end_lon": None,
        "hours": hours_rounded, "earned": earned,
        "forced_end": True, "ended_by_admin": tg_id
    }
    shifts = await get_shifts(redis, ns, target_tg_id)
    shifts.append(shift)
    await save_shifts(redis, ns, target_tg_id, shifts)
    await save_session(redis, ns, target_tg_id, None)
    await send_msg(chat_id, f"✅ Смена <b>{emp['name']}</b> принудительно завершена.\n⏱️ {hours_rounded} ч, 💵 {earned} руб", admin_menu_kb())
    try:
        await send_msg(target_tg_id, f"⚠️ Администратор завершил вашу смену.\n⏱️ {hours_rounded} ч, 💵 {earned} руб")
    except:
        pass

async def cmd_admin_salary_report(chat_id: int, tg_id: int, redis, ns: str, period: str = "month"):
    ids = await get_all_employee_ids(redis, ns)
    now = datetime.now(timezone.utc)
    report = []
    for eid in ids:
        emp = await get_employee(redis, ns, int(eid))
        if not emp:
            continue
        shifts = await get_shifts(redis, ns, int(eid))
        bonuses = await get_bonuses(redis, ns, int(eid))
        sqm_jobs = await get_sqm_jobs(redis, ns, int(eid))
        driver_shifts = await get_driver_shifts(redis, ns, int(eid))
        if period == "month":
            shifts = [s for s in shifts if datetime.fromisoformat(s["start_time"]).month == now.month]
            bonuses = [b for b in bonuses if datetime.fromisoformat(b["date"]).month == now.month]
            sqm_jobs = [j for j in sqm_jobs if datetime.fromisoformat(j["date"]).month == now.month]
            driver_shifts = [d for d in driver_shifts if datetime.fromisoformat(d["date"]).month == now.month]
        total_hours = round(sum(s["hours"] for s in shifts), 2)
        base = round(total_hours * emp["hourly_rate"], 2)
        sqm = round(sum(j["earned"] for j in sqm_jobs), 2)
        driver = round(sum(d["earned"] for d in driver_shifts), 2)
        bonus_sum = sum(b["amount"] for b in bonuses if b["type"] == "bonus")
        penalty_sum = sum(b["amount"] for b in bonuses if b["type"] == "penalty")
        total = round(base + sqm + driver + bonus_sum - penalty_sum, 2)
        report.append((emp['name'], total_hours, total))
    report.sort(key=lambda x: x[2], reverse=True)
    period_text = f"{now.strftime('%B %Y')}" if period == "month" else "Всё время"
    lines = f"📊 <b>Отчёт по зарплате ({period_text})</b>\n\n"
    for name, hours, total in report[:20]:
        lines += f"👤 {name}\n   ⏱️ {hours} ч → 💵 {total} руб\n\n"
    await send_msg(chat_id, lines, admin_menu_kb())

async def handle_admin_cmd(chat_id: int, tg_id: int, text: str, session: dict | None, redis, ns: str) -> bool:
    import re
    if not is_admin(tg_id):
        return False

    # Сотрудники
    if "Сотрудники" in text and not (session and session.get("state", "").startswith("admin_")):
        ids = await get_all_employee_ids(redis, ns)
        if not ids:
            await send_msg(chat_id, "📋 Нет зарегистрированных сотрудников.", admin_menu_kb())
            return True
        btns = []
        for eid in ids:
            emp = await get_employee(redis, ns, int(eid))
            if emp:
                ses = await get_session(redis, ns, int(eid))
                status = "🟢" if (ses and ses.get("state") == "on_shift") else "⚪"
                btns.append([{"text": f"{status} {emp['name']} (ID: {eid})"}])
        btns.append([{"text": "◀️ Назад"}])
        await save_session(redis, ns, tg_id, {"state": "admin_employee_list"})
        await send_msg(chat_id, "📋 <b>Выберите сотрудника:</b>", {"keyboard": btns, "resize_keyboard": True})
        return True

    # Зарплата отчёт
    if "Зарплата отчёт" in text and not (session and session.get("state", "").startswith("admin_")):
        await save_session(redis, ns, tg_id, {"state": "admin_report_period"})
        await send_msg(chat_id, "📊 Выберите период:", {"keyboard": [[{"text": "📅 За месяц"}, {"text": "📆 За всё время"}], [{"text": "◀️ Назад"}]], "resize_keyboard": True})
        return True

    # Изменить ставку
    if "Изменить ставку" in text and not (session and session.get("state", "").startswith("admin_")):
        ids = await get_all_employee_ids(redis, ns)
        if not ids:
            await send_msg(chat_id, "Нет сотрудников.", admin_menu_kb())
            return True
        btns = []
        for eid in ids:
            emp = await get_employee(redis, ns, int(eid))
            if emp:
                btns.append([{"text": f"{emp['name']} (ID: {eid})"}])
        btns.append([{"text": "◀️ Назад"}])
        await save_session(redis, ns, tg_id, {"state": "admin_rate_pick"})
        await send_msg(chat_id, "✏️ <b>Выберите сотрудника</b> для изменения ставки:", {"keyboard": btns, "resize_keyboard": True})
        return True

    # Завершить смену принудительно
    if "Завершить смену" in text and not (session and session.get("state", "").startswith("admin_")):
        ids = await get_all_employee_ids(redis, ns)
        btns = []
        for eid in ids:
            emp = await get_employee(redis, ns, int(eid))
            ses = await get_session(redis, ns, int(eid))
            if emp and ses and ses.get("state") == "on_shift":
                btns.append([{"text": f"{emp['name']} (ID: {eid})"}])
        if not btns:
            await send_msg(chat_id, "✅ Нет сотрудников с активной сменой.", admin_menu_kb())
            return True
        btns.append([{"text": "◀️ Назад"}])
        await save_session(redis, ns, tg_id, {"state": "admin_force_end_pick"})
        await send_msg(chat_id, "⏹️ <b>Выберите сотрудника</b> для завершения смены:", {"keyboard": btns, "resize_keyboard": True})
        return True

    # Назад
    if "Назад" in text or "◀️ Назад" in text:
        await save_session(redis, ns, tg_id, None)
        await send_msg(chat_id, "◀️ Возврат в админ-меню", admin_menu_kb())
        return True

    # Начислить (бонус/штраф)
    if "Начислить" in text and not (session and session.get("state", "").startswith("admin_")):
        ids = await get_all_employee_ids(redis, ns)
        if not ids:
            await send_msg(chat_id, "Нет сотрудников.", admin_menu_kb())
            return True
        btns = []
        for eid in ids:
            emp = await get_employee(redis, ns, int(eid))
            if emp:
                btns.append([{"text": f"{emp['name']} (ID: {eid})"}])
        btns.append([{"text": "❌ Отмена"}])
        await save_session(redis, ns, tg_id, {"state": "admin_pick_emp"})
        await send_msg(chat_id, "💰 <b>Выберите сотрудника:</b>", {"keyboard": btns, "resize_keyboard": True, "one_time_keyboard": True})
        return True

    # Уволить
    if "Уволить" in text and not (session and session.get("state", "").startswith("admin_")):
        ids = await get_all_employee_ids(redis, ns)
        if not ids:
            await send_msg(chat_id, "Нет сотрудников.", admin_menu_kb())
            return True
        btns = []
        for eid in ids:
            emp = await get_employee(redis, ns, int(eid))
            if emp:
                btns.append([{"text": f"{emp['name']} (ID: {eid})"}])
        btns.append([{"text": "❌ Отмена"}])
        await save_session(redis, ns, tg_id, {"state": "admin_fire_pick"})
        await send_msg(chat_id, "🚪 <b>Выберите сотрудника для увольнения:</b>", {"keyboard": btns, "resize_keyboard": True, "one_time_keyboard": True})
        return True

    # Многошаговые админ-сессии
    if session and session.get("state", "").startswith("admin_"):
        state = session["state"]
        if "Отмена" in text:
            await save_session(redis, ns, tg_id, None)
            await send_msg(chat_id, "Отменено.", admin_menu_kb())
            return True

        if state == "admin_employee_list":
            m = re.search(r"ID:\s*(\d+)", text)
            if not m or "Назад" in text:
                await save_session(redis, ns, tg_id, None)
                await send_msg(chat_id, "◀️ Назад", admin_menu_kb())
                return True
            target_id = int(m.group(1))
            await cmd_admin_employee_detail(chat_id, tg_id, target_id, redis, ns)
            return True

        if state == "admin_rate_pick":
            m = re.search(r"ID:\s*(\d+)", text)
            if not m or "Назад" in text:
                await save_session(redis, ns, tg_id, None)
                await send_msg(chat_id, "◀️ Назад", admin_menu_kb())
                return True
            target_id = int(m.group(1))
            await cmd_admin_change_rate_start(chat_id, tg_id, target_id, redis, ns)
            return True

        if state == "admin_change_rate":
            await cmd_admin_change_rate_amount(chat_id, tg_id, text, session, redis, ns)
            return True

        if state == "admin_force_end_pick":
            m = re.search(r"ID:\s*(\d+)", text)
            if not m or "Назад" in text:
                await save_session(redis, ns, tg_id, None)
                await send_msg(chat_id, "◀️ Назад", admin_menu_kb())
                return True
            target_id = int(m.group(1))
            await cmd_admin_force_end_shift(chat_id, tg_id, target_id, redis, ns)
            await save_session(redis, ns, tg_id, None)
            return True

        if state == "admin_report_period":
            if "За месяц" in text:
                await cmd_admin_salary_report(chat_id, tg_id, redis, ns, "month")
            elif "За всё время" in text:
                await cmd_admin_salary_report(chat_id, tg_id, redis, ns, "all")
            await save_session(redis, ns, tg_id, None)
            return True

        if state == "admin_viewing":
            target_id = session.get("target_id")
            if "Сменить ставку" in text:
                await cmd_admin_change_rate_start(chat_id, tg_id, target_id, redis, ns)
                return True
            if "Завершить смену" in text:
                await cmd_admin_force_end_shift(chat_id, tg_id, target_id, redis, ns)
                await cmd_admin_employee_detail(chat_id, tg_id, target_id, redis, ns)
                return True
            if "Отчёт за месяц" in text:
                emp = await get_employee(redis, ns, target_id)
                await send_msg(chat_id, f"📊 Отчёт для <b>{emp['name']}</b>\n\nФункция в разработке", get_kb(tg_id))
                return True
            if "Уволить" in text:
                await save_session(redis, ns, tg_id, {"state": "admin_fire_confirm", "tid": target_id, "tname": session.get("target_name")})
                await send_msg(chat_id, f"⚠️ Уволить <b>{session.get('target_name')}</b>? Это удалит сотрудника из системы.", 
                              {"keyboard": [[{"text": "✅ Да, уволить"}], [{"text": "❌ Отмена"}]], "resize_keyboard": True})
                return True
            if "Назад к списку" in text:
                await save_session(redis, ns, tg_id, None)
                ids = await get_all_employee_ids(redis, ns)
                btns = []
                for eid in ids:
                    emp = await get_employee(redis, ns, int(eid))
                    if emp:
                        ses = await get_session(redis, ns, int(eid))
                        status = "🟢" if (ses and ses.get("state") == "on_shift") else "⚪"
                        btns.append([{"text": f"{status} {emp['name']} (ID: {eid})"}])
                btns.append([{"text": "◀️ Назад"}])
                await save_session(redis, ns, tg_id, {"state": "admin_employee_list"})
                await send_msg(chat_id, "📋 <b>Выберите сотрудника:</b>", {"keyboard": btns, "resize_keyboard": True})
                return True

        if state == "admin_fire_pick":
            m = re.search(r"ID:\s*(\d+)", text)
            if not m:
                await send_msg(chat_id, "Выберите из списка.")
                return True
            tid = int(m.group(1))
            emp = await get_employee(redis, ns, tid)
            if not emp:
                await send_msg(chat_id, "Не найден.", admin_menu_kb())
                return True
            await save_session(redis, ns, tg_id, {"state": "admin_fire_confirm", "tid": tid, "tname": emp["name"]})
            await send_msg(chat_id, f"⚠️ Уволить <b>{emp['name']}</b>?\n\nЭто удалит сотрудника из системы.", {"keyboard": [[{"text": "✅ Да, уволить"}], [{"text": "❌ Отмена"}]], "resize_keyboard": True, "one_time_keyboard": True})
            return True

        if state == "admin_fire_confirm":
            if "Да" in text:
                tid = session["tid"]
                tname = session["tname"]
                await redis.srem(f"{ns}:employees_index", str(tid))
                await redis.delete(f"{ns}:employee:{tid}")
                await redis.delete(f"{ns}:session:{tid}")
                await save_session(redis, ns, tg_id, None)
                await send_msg(chat_id, f"✅ <b>{tname}</b> уволен и удалён из системы.", admin_menu_kb())
                try:
                    await send_msg(tid, "❌ Ваш аккаунт был деактивирован. Обратитесь к руководителю.")
                except Exception:
                    logger.warning(f"Could not notify fired employee {tid}")
                return True
            await save_session(redis, ns, tg_id, None)
            await send_msg(chat_id, "Отменено.", admin_menu_kb())
            return True

        if state == "admin_pick_emp":
            m = re.search(r"ID:\s*(\d+)", text)
            if not m:
                await send_msg(chat_id, "Выберите из списка.")
                return True
            tid = int(m.group(1))
            emp = await get_employee(redis, ns, tid)
            if not emp:
                await send_msg(chat_id, "Не найден.", admin_menu_kb())
                return True
            await save_session(redis, ns, tg_id, {"state": "admin_pick_type", "tid": tid, "tname": emp["name"]})
            await send_msg(chat_id, f"Сотрудник: <b>{emp['name']}</b>\nВыберите тип:", {"keyboard": [[{"text": "🎁 Премия"}], [{"text": "⚠️ Штраф"}], [{"text": "❌ Отмена"}]], "resize_keyboard": True, "one_time_keyboard": True})
            return True

        if state == "admin_pick_type":
            bt = "bonus" if "Премия" in text else "penalty" if "Штраф" in text else None
            if not bt:
                await send_msg(chat_id, "Выберите: Премия или Штраф.")
                return True
            await save_session(redis, ns, tg_id, {**session, "state": "admin_amount", "bt": bt})
            await send_msg(chat_id, f"Введите <b>сумму</b> ({'премии' if bt == 'bonus' else 'штрафа'}) в рублях:")
            return True

        if state == "admin_amount":
            try:
                amt = float(text.replace(",", "."))
                if amt <= 0:
                    raise ValueError
            except ValueError:
                await send_msg(chat_id, "Введите корректное число (например: 500):")
                return True
            await save_session(redis, ns, tg_id, {**session, "state": "admin_reason", "amt": amt})
            await send_msg(chat_id, "Введите <b>причину</b>:")
            return True

        if state == "admin_reason":
            tid = session["tid"]
            bt = session["bt"]
            amt = session["amt"]
            tname = session["tname"]
            bonuses = await get_bonuses(redis, ns, tid)
            entry = {"amount": amt, "reason": text, "type": bt, "date": datetime.now(timezone.utc).isoformat()}
            bonuses.append(entry)
            await save_bonuses(redis, ns, tid, bonuses)
            await save_session(redis, ns, tg_id, None)
            label = "Премия" if bt == "bonus" else "Штраф"
            icon = "🎁" if bt == "bonus" else "⚠️"
            await send_msg(chat_id, f"✅ <b>{label} начислен(а)!</b>\n\n👤 {tname}\n💰 {amt} руб\n📝 {text}", admin_menu_kb())
            try:
                await send_msg(tid, f"{icon} <b>Новое начисление: {label}</b>\n\n💰 Сумма: <b>{amt} руб</b>\n📝 Причина: {text}", get_kb(tid))
            except Exception:
                logger.warning(f"Could not notify employee {tid}")
            return True

    return False

async def handle_command(chat_id: int, tg_id: int, text: str, session: dict | None, emp: dict, redis, ns: str) -> bool:
    if "Начать смену" in text:
        if session and session.get("state") == "on_shift":
            await send_msg(chat_id, "⚠️ У вас уже открыта смена! Сначала завершите текущую.")
            return True
        await save_session(redis, ns, tg_id, {"state": "awaiting_start_location"})
        await send_msg(chat_id, "📍 Отправьте вашу <b>геолокацию</b> для начала смены:", location_kb("📍 Отправить геолокацию"))
        return True

    if "Завершить смену" in text:
        if not session or session.get("state") != "on_shift":
            await send_msg(chat_id, "⚠️ У вас нет открытой смены. Сначала начните смену.", get_kb(tg_id))
            return True
        await save_session(redis, ns, tg_id, {**session, "state": "awaiting_end_location"})
        await send_msg(chat_id, "📍 Отправьте вашу <b>геолокацию</b> для завершения смены:", location_kb("📍 Отправить геолокацию"))
        return True

    if "Водитель: начать" in text and is_driver(tg_id):
        if session and session.get("state") == "on_driver_shift":
            await send_msg(chat_id, "⚠️ Водительская смена уже открыта!")
            return True
        await save_session(redis, ns, tg_id, {"state": "awaiting_driver_rate"})
        await send_msg(chat_id, "🚗 <b>Водитель</b>\n\nВведите <b>ставку водителя</b> (руб/час):")
        return True

    if "Водитель: завершить" in text and is_driver(tg_id):
        if not session or session.get("state") != "on_driver_shift":
            await send_msg(chat_id, "⚠️ Нет открытой водительской смены.", get_kb(tg_id))
            return True
        now_dt = datetime.now(timezone.utc)
        start_dt = datetime.fromisoformat(session["start_time"])
        raw_h = (now_dt - start_dt).total_seconds() / 3600
        rounded_h = max(1, math.ceil(raw_h))
        d_rate = session["driver_rate"]
        earned = round(rounded_h * d_rate, 2)
        shift = {"start_time": session["start_time"], "end_time": now_dt.isoformat(), "raw_hours": round(raw_h, 2), "hours": rounded_h, "rate": d_rate, "earned": earned, "date": now_dt.isoformat()}
        d_shifts = await get_driver_shifts(redis, ns, tg_id)
        d_shifts.append(shift)
        await save_driver_shifts(redis, ns, tg_id, d_shifts)
        await save_session(redis, ns, tg_id, None)
        await send_msg(chat_id, f"🚗 <b>Водительская смена завершена!</b>\n\n⏱️ Факт.: {round(raw_h, 2)} ч → Округлено: <b>{rounded_h} ч</b> (мин. 1ч)\n💰 {d_rate} руб/ч\n💵 <b>{earned} руб</b>", get_kb(tg_id))
        return True

    if "Склад: начать" in text:
        if session and session.get("state") == "on_warehouse_shift":
            await send_msg(chat_id, "⚠️ Складская смена уже открыта!")
            return True
        await save_session(redis, ns, tg_id, {"state": "awaiting_warehouse_start_loc"})
        await send_msg(chat_id, "📦 Отправьте <b>геолокацию</b> для начала складской смены:", location_kb("📍 Отправить геолокацию"))
        return True

    if "Склад: завершить" in text:
        if not session or session.get("state") != "on_warehouse_shift":
            await send_msg(chat_id, "⚠️ Нет открытой складской смены.", get_kb(tg_id))
            return True
        await save_session(redis, ns, tg_id, {**session, "state": "awaiting_warehouse_end_loc"})
        await send_msg(chat_id, "📦 Отправьте <b>геолокацию</b> для завершения складской смены:", location_kb("📍 Отправить геолокацию"))
        return True

    if "Зарплата" in text:
        await cmd_salary(chat_id, tg_id, emp, redis, ns)
        return True

    if "Отменить" in text:
        await cmd_undo(chat_id, tg_id, redis, ns)
        return True

    if "Мой кабинет" in text:
        await cmd_profile(chat_id, tg_id, emp, session, redis, ns)
        return True

    if "Премии" in text or "Штрафы" in text:
        await cmd_bonuses(chat_id, tg_id, redis, ns)
        return True

    return False

async def handle_update(update: dict):
    msg = update.get("message")
    if not msg:
        return
    chat_id = msg["chat"]["id"]
    tg_id = msg["from"]["id"]
    text = msg.get("text", "").strip()
    loc = msg.get("location")

    async with redis_client() as (redis, ns):
        session = await get_session(redis, ns, tg_id)
        emp = await get_employee(redis, ns, tg_id)

        # Админские команды
        if is_admin(tg_id) and await handle_admin_cmd(chat_id, tg_id, text, session, redis, ns):
            return

        # Начало или принудительная регистрация
        if text == "/start" or (not emp and not session):
            await save_session(redis, ns, tg_id, {"state": "awaiting_name"})
            await send_msg(chat_id, "👋 <b>Добро пожаловать!</b>\n\nДля регистрации введите ваше <b>ФИО</b>:")
            return

        # Шаги регистрации
        if await handle_registration(chat_id, tg_id, text, session, redis, ns):
            return

        # Если не зарегистрирован
        if not emp:
            await save_session(redis, ns, tg_id, {"state": "awaiting_name"})
            await send_msg(chat_id, "Вы не зарегистрированы. Введите ваше <b>ФИО</b>:")
            return

        # SQM flow
        if is_sqm_worker(tg_id) or is_driver(tg_id):
            if await handle_sqm_flow(chat_id, tg_id, text, session, redis, ns):
                return

        # Обработка геолокации
        if loc and session:
            if await handle_location(chat_id, tg_id, loc, session, emp, redis, ns):
                return

        # Обычные команды
        if text:
            if await handle_command(chat_id, tg_id, text, session, emp, redis, ns):
                return

        # fallback
        await send_msg(chat_id, "Используйте кнопки ниже ⬇️", get_kb(tg_id))

# ========== FASTAPI ==========
app = FastAPI(title="Telegram TimeTracker Bot", description="Учёт рабочего времени", version="2.0.0")

class SetWebhookRequest(BaseModel):
    webhook_url: str

class SetWebhookResponse(BaseModel):
    success: bool
    bot_configured: bool
    message: str

@app.post("/", response_model=SetWebhookResponse)
async def set_telegram_webhook(request: SetWebhookRequest):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/setWebhook"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, json={"url": request.webhook_url})
        r.raise_for_status()
        data = r.json()
    return SetWebhookResponse(
        success=data.get("ok", False),
        bot_configured=True,
        message=data.get("description", "Webhook set")
    )

@app.post("/webhook")
async def telegram_webhook(request: Request):
    raw = await request.json()
    # support relay format
    if "payload" in raw and "defaultInputs" in raw:
        update = raw["payload"]
    else:
        update = raw
    await handle_update(update)
    return {"ok": True}

@app.get("/health")
async def health():
    return {"status": "ok"}

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)