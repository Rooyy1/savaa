import asyncio
import logging
import asyncpg
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ChatJoinRequest, ReplyKeyboardMarkup, KeyboardButton, 
    ReplyKeyboardRemove, Message, CallbackQuery
)
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

load_dotenv()

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
DATABASE_URL = os.getenv("DATABASE_URL")

# ============================================================
# 🔽 FILE_ID
# ============================================================
VIDEO_FILE_ID = "DQACAgUAAxkBAAMJakDQjZu8tZg_TBVbbziDyXfdVosAAjAgAAKTzMhVfPNg-l_q5-Q8BA"
EFIR_FILE_ID = "BAACAgIAAxkBAAMIakDQjVmUbPdKmJVXU672vRB0B9EAAkKfAAJ03ulJz-Mk6UdCjjQ8BA"
LESSON_COMPLETED_VIDEO_NOTE = "DQACAgUAAxkBAAMKakDQjfu5muxHsaWpgF8jO59uZsUAAjEgAAKTzMhVkgT4ZJBsd4o8BA"

REMINDER_PHOTO_1 = "CgACAgIAAxkBAANUakDTFcJDEJME8zLrcGZjBLUwdewAAnKPAAJO1XhJHz8oRKytuE88BA"
REMINDER_PHOTO_3 = "AgACAgUAAxkBAAMGakDQjcO9AAFw8suzPkav7JX7M4MtAAKeD2sbL9f5VAbHQRhexYKKAQADAgADdwADPAQ"
REMINDER_PHOTO_4 = "AgACAgUAAxkBAAMFakDQjeF_UY4pyqpaNHM4VYMqdagAAp0Paxsv1_lU9qzDaXr5RZ4BAAMCAAN5AAM8BA"
REMINDER_PHOTO_5 = "AgACAgIAAxkBAAMDakDQjQOm0PAMP19VVH2vsdv_BwgAAlUNaxu_IqhKA70S8Hz8zZYBAAMCAAN5AAM8BA"
REMINDER_PHOTO_6 = "AgACAgUAAxkBAAMEakDQjXj38sA0D19gTYS7gXiJy8EAApwPaxsv1_lUSOiAqNxgmbcBAAMCAAN5AAM8BA"

BONUS_PHOTO_ID = "AgACAgUAAxkBAAMCakDQjeT9TirQVVtRZ_tv1o_0oX0AAsMPaxu3TvBV5Twk7ayM7hQBAAMCAAN5AAM8BA"

# Ссылки
BONUS_LINK = "https://s.bothelp.io/r/dbxlpu.1er"
HURRY_LINK = "https://s.bothelp.io/r/hazk1l.1er"
ANALYSIS_LINK = "https://t.me/m/JqFJgQ8lMzE1"
# ============================================================

CHANNEL_LINK = "https://t.me/+PzMX_gyP5CQ0ODUy"
CHANNEL_ID = -1002463613187

# --- Состояния ---
class SurveyStates(StatesGroup):
    age = State()
    job = State()
    current_income = State()
    desired_income = State()
    source = State()
    ready = State()
    capital = State()

class BroadcastStates(StatesGroup):
    waiting_for_message = State()

# --- Инициализация ---
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)
db_pool = None


# ============================================================
# БАЗА ДАННЫХ
# ============================================================
async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        statement_cache_size=0,
        command_timeout=60,
        max_inactive_connection_lifetime=300,
        min_size=1,
        max_size=5
    )
    async with db_pool.acquire() as conn:
        await conn.execute("SELECT 1")
        await conn.execute("""
            ALTER TABLE users 
            ADD COLUMN IF NOT EXISTS lesson_watched BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS reminder_sent INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS lesson_shown_at TIMESTAMP DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS analysis_shown_at TIMESTAMP DEFAULT NULL,
            ADD COLUMN IF NOT EXISTS unreachable BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS analysis_reminder_sent INTEGER DEFAULT 0,
            ADD COLUMN IF NOT EXISTS reminder_4_sent BOOLEAN DEFAULT FALSE
        """)
    print("✅ Supabase подключена")


async def save_user(user_id, username, first_name):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO users (user_id, username, first_name, first_seen)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (user_id) DO NOTHING
        """, user_id, username, first_name)


async def save_answer(user_id, question, answer):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO user_answers (user_id, question, answer, timestamp)
            VALUES ($1, $2, $3, NOW())
        """, user_id, question, answer)


async def mark_ready(user_id, is_ready):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ready_stats (user_id, is_ready)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE SET is_ready = EXCLUDED.is_ready
        """, user_id, is_ready)


async def mark_lesson_watched(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET lesson_watched = TRUE WHERE user_id = $1", user_id)


async def mark_lesson_shown(user_id: int):
    """Фиксирует момент, когда юзеру показали видеоурок.
    Это единственный момент, от которого отсчитываются ВСЕ догревы урока (1-4),
    включая 4-й, который должен прийти независимо от lesson_watched."""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """UPDATE users 
               SET lesson_shown_at = NOW(), reminder_sent = 0, reminder_4_sent = FALSE
               WHERE user_id = $1 AND lesson_shown_at IS NULL""",
            user_id
        )


async def mark_analysis_shown(user_id: int):
    """Фиксирует момент, когда юзер получил разбор"""
    async with db_pool.acquire() as conn:
        await conn.execute(
            """UPDATE users 
               SET analysis_shown_at = NOW(), analysis_reminder_sent = 0 
               WHERE user_id = $1 AND analysis_shown_at IS NULL""",
            user_id
        )


async def get_user_info(user_id: int):
    async with db_pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT first_name, username FROM users WHERE user_id = $1",
            user_id
        )


async def get_all_users():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, first_name, username FROM users ORDER BY first_seen DESC")
        return rows


async def get_ready_count():
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT COUNT(*) FROM ready_stats WHERE is_ready = TRUE")
        return row[0]


async def get_users_for_lesson_reminder():
    """Догревы 1-3: ТОЛЬКО для тех, кто ещё не посмотрел урок.
    Если урок посмотрен — эти три догрева больше не нужны."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, reminder_sent, lesson_shown_at
            FROM users
            WHERE lesson_watched = FALSE
            AND lesson_shown_at IS NOT NULL
            AND reminder_sent < 3
            AND unreachable = FALSE
        """)
        return rows


async def get_users_for_reminder_4():
    """Догрев №4 (24 часа после показа видео): отправляется АБСОЛЮТНО ВСЕМ,
    кому показывали видео — независимо от lesson_watched и от reminder_sent.
    Единственное условие — флаг reminder_4_sent ещё не выставлен."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, lesson_shown_at
            FROM users
            WHERE lesson_shown_at IS NOT NULL
            AND reminder_4_sent = FALSE
            AND unreachable = FALSE
        """)
        return rows


async def mark_reminder_4_sent(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET reminder_4_sent = TRUE WHERE user_id = $1",
            user_id
        )


async def get_users_for_analysis_reminder():
    """Догревы для разбора (только если юзер дошёл до экрана разбора)"""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, analysis_reminder_sent, analysis_shown_at
            FROM users
            WHERE analysis_shown_at IS NOT NULL
            AND analysis_reminder_sent < 2
            AND unreachable = FALSE
        """)
        return rows


async def mark_unreachable(user_id: int):
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET unreachable = TRUE WHERE user_id = $1",
            user_id
        )


# ============================================================
# ДОГРЕВЫ ДЛЯ ВИДЕОУРОКА (1-3, только для тех, кто не посмотрел)
# ============================================================
async def send_lesson_reminder(user_id: int, step: int):
    """Отправляет догрев 1, 2 или 3 (для тех, кто не посмотрел урок)"""
    try:
        user_data = await get_user_info(user_id)
        if not user_data:
            return False
        
        user_name = user_data['first_name'] or "Друг"
        
        texts = [
            # Шаг 1 — через 15 минут после видео
            (
                f"<b>Эй, ты вроде был рядом… но не нажал кнопку 👀</b>\n\n"
                f"Ты же хотел узнать, как делать деньги на товарке с полного нуля?\n\n"
                f"<b>🚨 Такой шанс реально может проскочить мимо:</b>\n\n"
                f"— без опыта, с полным 0 в кармане, но с четкой стратегией\n"
                f"— ты реально можешь найти товар и начать делать продажи.\n\n"
                f"Материал ещё доступен, но ненадолго.\n\n"
                f"<b>Посмотри видеоурок, пока доступ не закрыл.</b>",
                REMINDER_PHOTO_1,
                "🎬 Смотреть урок",
                "watch_lesson",
                "animation"
            ),
            # Шаг 2 — через 1 час после 1-го
            (
                f"<b>ауууу, успел глянуть? 👀</b>\n\n"
                f"В видеоуроке я разложил без воды стратегию, как <b>с нуля выйти на первые 100–150к без вложений.</b>\n\n"
                f"Это та инфа, которую обычно прячут за платным входом.\n\n"
                f"Не теряй время: пока материал доступен\n\n"
                f"<b>Жми и смотри 👇</b>",
                None,
                "🎬 Смотреть урок",
                "watch_lesson",
                "none"
            ),
            # Шаг 3 — через 5 часов после 2-го
            (
                f"<b>👋 Куда перевести 80к?</b>\n\n"
                f"Если ты ещё не видишь таких сообщений у себя в телефоне — значит, всё ещё не начал делать деньги на товарке.\n\n"
                f"А ведь стартовать можно с нуля и без вложений.\n\n"
                f"Просто повтори то, что я уже разложил в видеоуроке. 0 ₽ на старте. Никаких сложных схем. Только реальные шаги.\n\n"
                f"<b>Ещё раз: смотришь видеоурок → внедряешь → получаешь первые 100–200К.</b>\n\n"
                f"Не нужно изобретать велосипед. Метод работает у меня, у ребят из команды, у учеников. Чем ты хуже?\n\n"
                f"<b>🔥 Внутри видеоурока:</b>\n\n"
                f"<i>— с чего начинать и что нужно знать</i>\n"
                f"<i>— как проанализировать товар и найти поставщика</i>\n"
                f"<i>— как начать зарабатывать уделяя 1-2 часа в день</i>\n"
                f"<i>— как не вложив ни 1 рубля сделать первые 100.000₽</i>\n\n"
                f"Спасибо в карман не положишь. А вот результат — положишь.\n\n"
                f"Сделаешь и напишешь мне? Я сам скажу: «Брат, красавчик» и помогу двигаться дальше. Вопрос только в твоём желании.\n\n"
                f"<b>Доступ ещё открыт👇</b>",
                REMINDER_PHOTO_6,
                "🎬 Смотреть урок",
                "watch_lesson",
                "photo"
            )
        ]
        
        if step < len(texts):
            text, photo_id, button_text, callback_data, file_type = texts[step]
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=button_text, callback_data=callback_data)]
            ])
            
            try:
                if photo_id:
                    if file_type == "animation":
                        await bot.send_animation(
                            chat_id=user_id, animation=photo_id, caption=text,
                            parse_mode="HTML", reply_markup=keyboard
                        )
                    else:
                        await bot.send_photo(
                            chat_id=user_id, photo=photo_id, caption=text,
                            parse_mode="HTML", reply_markup=keyboard
                        )
                else:
                    await bot.send_message(
                        chat_id=user_id, text=text,
                        parse_mode="HTML", reply_markup=keyboard
                    )
                
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE users SET reminder_sent = reminder_sent + 1 WHERE user_id = $1",
                        user_id
                    )
                return True
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                print(f"⚠️ Не могу отправить сообщение {user_id}: {e}")
                await mark_unreachable(user_id)
                return False
        return False
    except Exception as e:
        print(f"❌ Ошибка при отправке догрева урока {user_id}: {e}")
        try:
            await mark_unreachable(user_id)
        except Exception:
            pass
        return False


# ============================================================
# ДОГРЕВ №4 (24 часа после видео) — ОТПРАВЛЯЕТСЯ ВСЕМ
# ============================================================
async def send_reminder_4(user_id: int):
    """
    Отправляет 4-й догрев (бонус) через 24ч после показа видео.
    Идёт ВСЕМ пользователям, которым показывали видео — вне зависимости
    от того, посмотрели они урок, прошли анкету или получили разбор.
    """
    try:
        user_data = await get_user_info(user_id)
        if not user_data:
            return False
        
        user_name = user_data['first_name'] or "Друг"
        
        text = (
            f"{user_name}, ты супер!\n\n"
            f"😎 Попал в число 3-х счастливчиков\n\n"
            f"Забирай обещанный бонус: <b>«БАЗА 100+ ПРОВЕРЕННЫХ ПОСТАВЩИКОВ»</b>, с помощью которой уже можно начать делать первые продажи.\n\n"
            f"❗️Также я в скором времени отвечу тебе – и расскажу о формате встречи. Обязательно читай личные сообщения от меня: @savvazltrv\n\n"
            f"P.S. - твой бонус тоже скину тебе в личные сообщения после ответа"
        )
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Забрать бонус", url=BONUS_LINK)]
        ])
        
        try:
            await bot.send_photo(
                chat_id=user_id,
                photo=REMINDER_PHOTO_5,
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
            await mark_reminder_4_sent(user_id)
            return True
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            print(f"⚠️ Не могу отправить 4-й догрев {user_id}: {e}")
            await mark_unreachable(user_id)
            return False
    except Exception as e:
        print(f"❌ Ошибка при отправке 4-го догрева {user_id}: {e}")
        try:
            await mark_unreachable(user_id)
        except Exception:
            pass
        return False


# ============================================================
# ДОГРЕВЫ ДЛЯ РАЗБОРА
# ============================================================
async def send_analysis_reminder(user_id: int, step: int):
    """Отправляет догрев для разбора"""
    try:
        user_data = await get_user_info(user_id)
        if not user_data:
            return False
        
        user_name = user_data['first_name'] or "Друг"
        
        texts = [
            # Шаг 1 — через 3 часа после разбора
            (
                f"<b>Представь: кто-то сейчас зашёл, забрал место и уже двигается к своим 100–200к.</b>\n\n"
                f"А ты через неделю опять думаешь: «эх, надо было тогда нажать».\n\n"
                f"<b>Ты серьёзно готов снова профукать шанс?</b>\n\n"
                f"Пока разбор бесплатный, места ещё есть. Но это ненадолго.\n\n"
                f"<b>Жми и забирай 👇</b>",
                REMINDER_PHOTO_3,
                "⏰ Успеть",
                ANALYSIS_LINK
            ),
            # Шаг 2 — через 6 часов после 1-го
            (
                f"<b>✅ Пополнение. Счет RUB. 48 000₽.</b>\n\n"
                f"{user_name}, такие уведомления получают ребята из моей команды работая по моей системе.\n\n"
                f"И это без вложений, команды и «чёрных тем». Всё строится по одной системе — купил подешевле, продал подороже.\n\n"
                f"<b>Тебе нужно всего лишь:</b>\n\n"
                f"<i>— найти поставщика</i>\n"
                f"<i>— выложить объявление</i>\n"
                f"<i>— обработать клиента</i>\n"
                f"<i>— и сделать продажу</i>\n\n"
                f"<b>😲 Но в одиночку это адски долго. Можно год тупить, а можно за месяц выйти на 100–200к.</b>\n\n"
                f"6 из 20 мест на разбор уже заняты. Дальше — платно.\n\n"
                f"<b>Забирай место пока открыто 👇</b>",
                REMINDER_PHOTO_4,
                "🔥 Забрать место",
                ANALYSIS_LINK
            )
        ]
        
        if step < len(texts):
            text, photo_id, button_text, url = texts[step]
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=button_text, url=url)]
            ])
            
            try:
                if photo_id:
                    await bot.send_photo(
                        chat_id=user_id, photo=photo_id, caption=text,
                        parse_mode="HTML", reply_markup=keyboard
                    )
                else:
                    await bot.send_message(
                        chat_id=user_id, text=text,
                        parse_mode="HTML", reply_markup=keyboard
                    )
                
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE users SET analysis_reminder_sent = analysis_reminder_sent + 1 WHERE user_id = $1",
                        user_id
                    )
                return True
            except (TelegramForbiddenError, TelegramBadRequest) as e:
                print(f"⚠️ Не могу отправить сообщение {user_id}: {e}")
                await mark_unreachable(user_id)
                return False
        return False
    except Exception as e:
        print(f"❌ Ошибка при отправке догрева разбора {user_id}: {e}")
        try:
            await mark_unreachable(user_id)
        except Exception:
            pass
        return False


# ============================================================
# ПРОВЕРКА ДОГРЕВОВ
# ============================================================
async def lesson_reminders_loop():
    """Догревы 1-3 (только для тех, кто ещё не посмотрел урок)"""
    print("🔄 Запущен lesson_reminders_loop (догревы 1-3)")
    while True:
        try:
            users = await get_users_for_lesson_reminder()
            now = datetime.utcnow()
            
            for user_id, sent_count, lesson_shown_at in users:
                if sent_count >= 3:
                    continue
                try:
                    await bot.send_chat_action(user_id, 'typing')
                except Exception:
                    await mark_unreachable(user_id)
                    continue
                
                elapsed = (now - lesson_shown_at).total_seconds() if lesson_shown_at else 0
                should_send = False
                
                if sent_count == 0 and elapsed >= 900:       # 15 минут
                    should_send = True
                elif sent_count == 1 and elapsed >= 4500:     # 1ч15м (15м + 1ч)
                    should_send = True
                elif sent_count == 2 and elapsed >= 22500:    # 6ч15м (4500 + 5ч)
                    should_send = True
                
                if should_send:
                    print(f"📨 Догрев урока {sent_count + 1}/3 → {user_id}")
                    await send_lesson_reminder(user_id, sent_count)
                    await asyncio.sleep(0.3)
            
            await asyncio.sleep(10)
        except Exception as e:
            print(f"❌ Ошибка в lesson_reminders_loop: {e}")
            await asyncio.sleep(10)


async def reminder_4_loop():
    """
    Догрев №4: 24 часа после lesson_shown_at, ВСЕМ без исключения —
    независимо от lesson_watched, от прогресса 1-3 догревов, от разбора.
    Работает полностью отдельно от lesson_reminders_loop.
    """
    print("🔄 Запущен reminder_4_loop (догрев №4 — всем через 24ч)")
    while True:
        try:
            users = await get_users_for_reminder_4()
            now = datetime.utcnow()
            
            for user_id, lesson_shown_at in users:
                if lesson_shown_at is None:
                    continue
                
                elapsed = (now - lesson_shown_at).total_seconds()
                if elapsed < 86400:  # ещё не прошло 24 часа
                    continue
                
                try:
                    await bot.send_chat_action(user_id, 'typing')
                except Exception:
                    await mark_unreachable(user_id)
                    continue
                
                print(f"📨 Догрев №4 (24ч, всем) → {user_id}")
                await send_reminder_4(user_id)
                await asyncio.sleep(0.3)
            
            await asyncio.sleep(30)  # 24ч — это долгий интервал, можно реже опрашивать
        except Exception as e:
            print(f"❌ Ошибка в reminder_4_loop: {e}")
            await asyncio.sleep(30)


async def analysis_reminders_loop():
    """Догревы для разбора (только для тех, кто получил разбор)"""
    print("🔄 Запущен analysis_reminders_loop")
    while True:
        try:
            users = await get_users_for_analysis_reminder()
            now = datetime.utcnow()
            
            for user_id, sent_count, analysis_shown_at in users:
                if sent_count >= 2:
                    continue
                try:
                    await bot.send_chat_action(user_id, 'typing')
                except Exception:
                    await mark_unreachable(user_id)
                    continue
                
                elapsed = (now - analysis_shown_at).total_seconds() if analysis_shown_at else 0
                should_send = False
                
                if sent_count == 0 and elapsed >= 10800:   # 3 часа
                    should_send = True
                elif sent_count == 1 and elapsed >= 32400:  # 6 часов после 1го (9ч всего)
                    should_send = True
                
                if should_send:
                    print(f"📨 Догрев разбора {sent_count + 1}/2 → {user_id}")
                    await send_analysis_reminder(user_id, sent_count)
                    await asyncio.sleep(0.3)
            
            await asyncio.sleep(10)
        except Exception as e:
            print(f"❌ Ошибка в analysis_reminders_loop: {e}")
            await asyncio.sleep(10)


# ============================================================
# КЛАВИАТУРЫ
# ============================================================
def get_age_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="до 14"), KeyboardButton(text="14-16")],
            [KeyboardButton(text="16-18"), KeyboardButton(text="18-25")],
            [KeyboardButton(text="25+")]
        ],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_job_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Работаю по найму")],
            [KeyboardButton(text="Своё дело")],
            [KeyboardButton(text="Учусь")],
            [KeyboardButton(text="В поиске")],
            [KeyboardButton(text="Другое")]
        ],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_income_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="до 20.000 ₽"), KeyboardButton(text="20.000 - 50.000 ₽")],
            [KeyboardButton(text="50.000 - 100.000 ₽"), KeyboardButton(text="100.000 - 200.000 ₽")],
            [KeyboardButton(text="200.000 ₽ +")]
        ],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_source_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="YouTube"), KeyboardButton(text="Instagram")],
            [KeyboardButton(text="Друг/Знакомый"), KeyboardButton(text="Реклама")],
            [KeyboardButton(text="Другое")]
        ],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_ready_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Да, готов")],
            [KeyboardButton(text="⏳ Пока думаю")]
        ],
        resize_keyboard=True, one_time_keyboard=True
    )

def get_capital_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="до 5.000 ₽"), KeyboardButton(text="5.000 - 15.000 ₽")],
            [KeyboardButton(text="15.000 - 30.000 ₽"), KeyboardButton(text="30.000 - 100.000 ₽")],
            [KeyboardButton(text="100.000 ₽ +")]
        ],
        resize_keyboard=True, one_time_keyboard=True
    )


def get_read_article_button():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📖 Смотреть видеоурок", callback_data="read_article")]
    ])

def get_subscription_buttons():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_LINK)],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_subscription")]
    ])

def get_watch_lesson_button():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Прошёл урок", callback_data="lesson_completed")]
    ])

def get_analysis_button():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔍 Получить разбор", callback_data="get_analysis")]
    ])


# ============================================================
# ПРОВЕРКА ПОДПИСКИ
# ============================================================
async def check_subscription(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        print(f"❌ Ошибка проверки подписки {user_id}: {e}")
        return False


# ============================================================
# ОБРАБОТЧИКИ
# ============================================================
# @dp.chat_join_request()
# async def handle_join_request(request: ChatJoinRequest):
#     user = request.from_user
#     print(f"\n🔔 Новая заявка от {user.first_name} (@{user.username})")
    
#     try:
#         await request.approve()
#         print(f"✅ Заявка {user.id} одобрена")
#         await save_user(user.id, user.username, user.first_name)
#         await asyncio.sleep(1.5)
        
#         try:
#             await bot.send_video_note(chat_id=user.id, video_note=VIDEO_FILE_ID)
#             await asyncio.sleep(1)
            
#             await bot.send_message(
#                 chat_id=user.id,
#                 text=f"<b>🚀 КАК СТАРТАНУТЬ В ТОВАРКЕ?</b>\n\n"
#                      f"<i>{user.first_name}</i>, я подготовил инструкцию, как выйти на доход <b>50.000 — 100.000 ₽</b>\n\n"
#                      f"👇 <b>Нажми на кнопку ниже</b>",
#                 parse_mode="HTML",
#                 reply_markup=get_read_article_button()
#             )
#             print(f"✅ Сообщение отправлено {user.id}")
            
#         except Exception as e:
#             print(f"❌ Ошибка при отправке сообщения {user.id}: {e}")
#             await asyncio.sleep(2)
#             try:
#                 await bot.send_message(
#                     chat_id=user.id,
#                     text=f"<b>🚀 Привет, {user.first_name}!</b>\n\n"
#                          f"Напиши /start, чтобы начать обучение",
#                     parse_mode="HTML"
#                 )
#             except Exception:
#                 await mark_unreachable(user.id)
            
#     except Exception as e:
#         print(f"❌ Ошибка в handle_join_request: {e}")


@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = message.from_user
    await save_user(user.id, user.username, user.first_name)
    await message.answer_video_note(video_note=VIDEO_FILE_ID)
    await asyncio.sleep(1)
    await message.answer(
        f"<b>🚀 ПРИВЕТ, {user.first_name}!</b>\n\n"
        f"Я помогу тебе <i>стартануть в товарке</i>!\n\n"
        f"👇 <b>Нажми на кнопку</b>",
        parse_mode="HTML",
        reply_markup=get_read_article_button()
    )


@dp.callback_query(F.data == "read_article")
async def read_article(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        f"<b>📝 Чтобы получить видеоурок, подпишись на канал:</b>\n\n"
        f"{CHANNEL_LINK}\n\n"
        f"<i>После подписки нажми «Проверить»</i> 👇",
        parse_mode="HTML",
        reply_markup=get_subscription_buttons()
    )


@dp.callback_query(F.data == "check_subscription")
async def check_subscription_handler(callback: CallbackQuery):
    await callback.answer()
    
    if await check_subscription(callback.from_user.id):
        await callback.message.delete()
        
        lesson_text = (
            "<b>100.000 – 200.000₽ на товарке</b>\n\n"
            "Я собрал для тебя пошаговый метод, как с нуля практически без вложений выйти на первые деньги и закрепиться в нише.\n\n"
            "Для кого-то это реально будет шоком — всё настолько просто.\n\n"
            "<b>Что тебя ждёт внутри?</b>\n\n"
            "<i>— как быстро стартануть в товарке с полного 0</i>\n"
            "<i>— как создать свой интернет магазин</i>\n"
            "<i>— как находить клиентов и обрабатывать их</i>\n"
            "<i>— как обходить конкурентов и быть всегда на 1 месте</i>\n"
            "<i>— как выйти на первые 50.000 — 100.000₽</i>\n\n"
            "<b>Без вложений, команды и уделяя по 1 — 2 часа в день.</b>\n\n"
            "📲 Время просмотра видео : 10 минут.\n\n"
            "<b>Жми кнопку и забирай материал 👇</b>"
        )
        
        await callback.message.answer_video(
            video=EFIR_FILE_ID,
            caption=lesson_text,
            parse_mode="HTML",
            reply_markup=get_watch_lesson_button()
        )
        
        # Это единственная точка запуска ВСЕХ догревов урока, включая 4-й
        await mark_lesson_shown(callback.from_user.id)
        await callback.answer("✅ Подписка подтверждена!", show_alert=True)
    else:
        await callback.answer("❌ Ты не подписан на канал!", show_alert=True)


@dp.callback_query(F.data == "watch_lesson")
async def watch_lesson(callback: CallbackQuery):
    """Срабатывает, когда юзер нажимает 'Смотреть урок' из догрева"""
    await callback.answer()
    
    lesson_text = (
        "<b>100.000 – 200.000₽ на товарке</b>\n\n"
        "Я собрал для тебя пошаговый метод, как с нуля практически без вложений выйти на первые деньги и закрепиться в нише.\n\n"
        "Для кого-то это реально будет шоком — всё настолько просто.\n\n"
        "<b>Что тебя ждёт внутри?</b>\n\n"
        "<i>— как быстро стартануть в товарке с полного 0</i>\n"
        "<i>— как создать свой интернет магазин</i>\n"
        "<i>— как находить клиентов и обрабатывать их</i>\n"
        "<i>— как обходить конкурентов и быть всегда на 1 месте</i>\n"
        "<i>— как выйти на первые 50.000 — 100.000₽</i>\n\n"
        "<b>Без вложений, команды и уделяя по 1 — 2 часа в день.</b>\n\n"
        "📲 Время просмотра видео : 10 минут.\n\n"
        "<b>Жми кнопку и забирай материал 👇</b>"
    )
    
    await callback.message.answer_video(
        video=EFIR_FILE_ID,
        caption=lesson_text,
        parse_mode="HTML",
        reply_markup=get_watch_lesson_button()
    )
    
    await mark_lesson_shown(callback.from_user.id)


@dp.callback_query(F.data == "lesson_completed")
async def lesson_completed(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await mark_lesson_watched(callback.from_user.id)
    
    await callback.message.answer_video_note(video_note=LESSON_COMPLETED_VIDEO_NOTE)
    await asyncio.sleep(1)
    
    await callback.message.delete()
    
    await callback.message.answer(
        f"<b>📋 Давай познакомимся поближе!</b>\n\n"
        f"<b>Сколько тебе лет?</b>",
        parse_mode="HTML",
        reply_markup=get_age_keyboard()
    )
    await state.set_state(SurveyStates.age)


# --- Вопросы анкеты ---
@dp.message(StateFilter(SurveyStates.age))
async def ask_age(message: Message, state: FSMContext):
    valid_ages = ["до 14", "14-16", "16-18", "18-25", "25+"]
    if message.text not in valid_ages:
        await message.answer("Пожалуйста, выбери вариант из кнопок ниже:", reply_markup=get_age_keyboard())
        return
    
    await state.update_data(age=message.text)
    await message.answer(
        f"<b>Чем занимаешься?</b>",
        parse_mode="HTML",
        reply_markup=get_job_keyboard()
    )
    await state.set_state(SurveyStates.job)


@dp.message(StateFilter(SurveyStates.job))
async def ask_job(message: Message, state: FSMContext):
    valid_jobs = ["Работаю по найму", "Своё дело", "Учусь", "В поиске", "Другое"]
    if message.text not in valid_jobs:
        await message.answer("Пожалуйста, выбери вариант из кнопок ниже:", reply_markup=get_job_keyboard())
        return
    
    await state.update_data(job=message.text)
    await message.answer(
        f"<b>Сколько зарабатываешь? (честно 💯)</b>",
        parse_mode="HTML",
        reply_markup=get_income_keyboard()
    )
    await state.set_state(SurveyStates.current_income)


@dp.message(StateFilter(SurveyStates.current_income))
async def ask_current_income(message: Message, state: FSMContext):
    valid_incomes = ["до 20.000 ₽", "20.000 - 50.000 ₽", "50.000 - 100.000 ₽", "100.000 - 200.000 ₽", "200.000 ₽ +"]
    if message.text not in valid_incomes:
        await message.answer("Пожалуйста, выбери вариант из кнопок ниже:", reply_markup=get_income_keyboard())
        return
    
    await state.update_data(current_income=message.text)
    await message.answer(
        f"<b>Сколько хочешь зарабатывать? (честно 💯)</b>",
        parse_mode="HTML",
        reply_markup=get_income_keyboard()
    )
    await state.set_state(SurveyStates.desired_income)


@dp.message(StateFilter(SurveyStates.desired_income))
async def ask_desired_income(message: Message, state: FSMContext):
    valid_incomes = ["до 20.000 ₽", "20.000 - 50.000 ₽", "50.000 - 100.000 ₽", "100.000 - 200.000 ₽", "200.000 ₽ +"]
    if message.text not in valid_incomes:
        await message.answer("Пожалуйста, выбери вариант из кнопок ниже:", reply_markup=get_income_keyboard())
        return
    
    await state.update_data(desired_income=message.text)
    await message.answer(
        f"<b>Откуда узнал про товарный бизнес?</b>",
        parse_mode="HTML",
        reply_markup=get_source_keyboard()
    )
    await state.set_state(SurveyStates.source)


@dp.message(StateFilter(SurveyStates.source))
async def ask_source(message: Message, state: FSMContext):
    valid_sources = ["YouTube", "Instagram", "Друг/Знакомый", "Реклама", "Другое"]
    if message.text not in valid_sources:
        await message.answer("Пожалуйста, выбери вариант из кнопок ниже:", reply_markup=get_source_keyboard())
        return
    
    await state.update_data(source=message.text)
    await message.answer(
        f"<b>Готов залетать в товарку?</b>",
        parse_mode="HTML",
        reply_markup=get_ready_keyboard()
    )
    await state.set_state(SurveyStates.ready)


@dp.message(StateFilter(SurveyStates.ready))
async def ask_capital(message: Message, state: FSMContext):
    valid_ready = ["✅ Да, готов", "⏳ Пока думаю"]
    if message.text not in valid_ready:
        await message.answer("Пожалуйста, выбери вариант из кнопок ниже:", reply_markup=get_ready_keyboard())
        return
    
    ready = message.text == "✅ Да, готов"
    await state.update_data(ready=ready)
    await mark_ready(message.from_user.id, ready)
    await message.answer(
        f"<b>Какой стартовый капитал? (честно 💯)</b>",
        parse_mode="HTML",
        reply_markup=get_capital_keyboard()
    )
    await state.set_state(SurveyStates.capital)


@dp.message(StateFilter(SurveyStates.capital))
async def finish_survey(message: Message, state: FSMContext):
    valid_capitals = ["до 5.000 ₽", "5.000 - 15.000 ₽", "15.000 - 30.000 ₽", "30.000 - 100.000 ₽", "100.000 ₽ +"]
    if message.text not in valid_capitals:
        await message.answer("Пожалуйста, выбери вариант из кнопок ниже:", reply_markup=get_capital_keyboard())
        return
    
    data = await state.get_data()
    data['capital'] = message.text
    user = message.from_user
    
    answers = [
        ("Сколько тебе лет?", data.get('age')),
        ("Чем занимаешься?", data.get('job')),
        ("Сколько зарабатываешь?", data.get('current_income')),
        ("Сколько хочешь зарабатывать?", data.get('desired_income')),
        ("Откуда узнал про товарку?", data.get('source')),
        ("Стартовый капитал?", data.get('capital'))
    ]
    
    for q, a in answers:
        if a:
            await save_answer(user.id, q, a)
    
    admin_text = f"📋 <b>НОВАЯ АНКЕТА!</b>\n\n"
    admin_text += f"👤 <b>Пользователь:</b> {user.first_name}"
    if user.username:
        admin_text += f" (@{user.username})"
    admin_text += f"\n\n"
    
    for q, a in answers:
        if a:
            admin_text += f"❓ <b>{q}</b>\n👉 {a}\n\n"
    admin_text += f"❓ <b>Готов залетать в товарку?</b>\n👉 {'✅ Да' if data.get('ready') else '⏳ Пока думаю'}\n\n"
    
    await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML")
    
    await message.answer(
        f"<b>✨ Спасибо, {user.first_name}!</b>\n\n"
        f"Твои ответы помогут мне лучше понять твою ситуацию и подготовить максимально полезный разбор специально для тебя.\n\n"
        f"<b>👇 Нажми на кнопку ниже, чтобы получить разбор</b>",
        parse_mode="HTML",
        reply_markup=get_analysis_button()
    )
    await state.clear()


@dp.callback_query(F.data == "get_analysis")
async def get_analysis(callback: CallbackQuery):
    await callback.answer()
    
    await mark_analysis_shown(callback.from_user.id)
    
    analysis_text = (
        f"<b>🔍 Проведу для тебя БЕСПЛАТНЫЙ РАЗБОР</b>\n\n"
        f"<i>В формате личного взаимодействия!</i>\n\n"
        f"Сейчас всё расскажу 😎\n\n"
        f"Приглашаю тебя на <b>онлайн-встречу</b> со мной: вместе с тобой создадим стратегию по выходу на первые <b>50.000 – 100.000 ₽</b> на товарке:\n\n"
        f"• Узнаешь как создать успешный интернет-магазин, полностью удалённо, уделяя <i>по 1-2 часа в день</i>\n"
        f"• Как <b>не совершать 90% всех ошибок</b> новичков\n"
        f"• Как с <b>полным 0</b> в кармане стартануть в товарном бизнесе\n"
        f"• <b>Секретные фишки</b> по продвижению на Авито\n\n"
        f"<b>РАЗБОР</b> — это твоя возможность пообщаться <i>лично со мной</i> или с членом моей команды и получить <b>пошаговый план действий</b>.\n\n"
        f"💫 Вникаю в ситуацию каждого и создаю <i>индивидуальный план</i>.\n\n"
        f"⚠️ <b>Таких разборов всего 20</b> — дальше я планирую делать места платными.\n\n"
        f"🔥 <b>Не теряй время и пиши кодовое слово «РАЗБОР»</b>\n\n"
        f"Мне в личку — <b>@SavvaAkyla</b>\n\n"
        f"Главное — твоё желание и решение зарабатывать. <i>С остальным я помогу</i>.\n\n"
        f"<b>🎁 БОНУСОМ</b> первые 3 человека получат от меня подарок <i>«БАЗА 100+ ПРОВЕРЕННЫХ ПОСТАВЩИКОВ»</i>, по которому можно уже начать делать первые продажи."
    )
    
    await callback.message.answer_photo(
        photo=BONUS_PHOTO_ID,
        caption=analysis_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔥 РАЗБОР 🔥", url="https://s.bothelp.io/r/lb553y")]
        ])
    )


# ============================================================
# КОМАНДЫ
# ============================================================
@dp.message(Command("stats"))
async def stats_command(message: Message):
    users = await get_all_users()
    total = len(users)
    await message.answer(
        f"<b>📊 Статистика бота</b>\n\n"
        f"👥 <b>Всего пользователей:</b> {total}",
        parse_mode="HTML"
    )


@dp.message(Command("users"))
async def users_command(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет доступа к этой команде")
        return
    
    users = await get_all_users()
    if not users:
        await message.answer("📭 В боте пока нет пользователей")
        return
    
    total = len(users)
    chunk_size = 30
    chunks = [users[i:i + chunk_size] for i in range(0, total, chunk_size)]
    
    for idx, chunk in enumerate(chunks, 1):
        text = f"<b>👥 СПИСОК ПОЛЬЗОВАТЕЛЕЙ</b>\n"
        text += f"<i>Часть {idx} из {len(chunks)}</i>\n"
        text += f"<i>Всего: {total} пользователей</i>\n\n"
        
        for user in chunk:
            name = user['first_name'] or "Без имени"
            username = user['username'] or "Нет username"
            text += f"👤 <b>{name}</b>\n"
            text += f"   @{username}\n\n"
        
        await message.answer(text, parse_mode="HTML")
        await asyncio.sleep(0.5)


@dp.message(Command("rasilka"))
async def start_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    await message.answer(
        "<b>📨 РАССЫЛКА</b>\n\n"
        "Отправь сообщение для рассылки.\n\n"
        "<i>Поддерживается:</i>\n"
        "• Текст с HTML-тегами (<b>жирный</b>, <i>курсив</i>)\n"
        "• Фото\n"
        "• Видео\n\n"
        "Для отмены отправь /cancel",
        parse_mode="HTML"
    )
    await state.set_state(BroadcastStates.waiting_for_message)


@dp.message(StateFilter(BroadcastStates.waiting_for_message))
async def process_broadcast(message: Message, state: FSMContext):
    users = await get_all_users()
    total = len(users)
    
    if total == 0:
        await message.answer("❌ Нет пользователей для рассылки")
        await state.clear()
        return
    
    status_msg = await message.answer(f"📨 Начинаю рассылку <b>{total}</b> пользователям...", parse_mode="HTML")
    
    success = 0
    fail = 0
    
    for user in users:
        try:
            user_id = user['user_id']
            
            if message.text:
                await bot.send_message(user_id, message.text, parse_mode="HTML")
            elif message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption, parse_mode="HTML")
            elif message.video:
                await bot.send_video(user_id, message.video.file_id, caption=message.caption, parse_mode="HTML")
            
            success += 1
        except Exception:
            fail += 1
        
        await asyncio.sleep(0.05)
        
        if (success + fail) % 10 == 0:
            await status_msg.edit_text(f"📨 Рассылка... {success + fail}/{total}")
    
    await status_msg.edit_text(
        f"<b>✅ РАССЫЛКА ЗАВЕРШЕНА</b>\n\n"
        f"📤 <b>Отправлено:</b> {success}\n"
        f"❌ <b>Ошибок:</b> {fail}",
        parse_mode="HTML"
    )
    await state.clear()


@dp.message(Command("cancel"))
async def cancel_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("❌ Действие отменено")
    else:
        await message.answer("Нет активных действий для отмены")


# ============================================================
# ЗАПУСК
# ============================================================
async def main():
    await init_db()
    
    # Три независимых цикла — ни один не блокирует другой
    asyncio.create_task(lesson_reminders_loop())
    asyncio.create_task(reminder_4_loop())
    asyncio.create_task(analysis_reminders_loop())
    
    try:
        chat = await bot.get_chat(chat_id=CHANNEL_ID)
        print(f"\n✅ Канал: {chat.title}")
    except Exception as e:
        print(f"\n❌ Ошибка канала: {e}")
    
    print("\n" + "="*50)
    print("🤖 БОТ ЗАПУЩЕН (aiogram 3.26)")
    print("="*50)
    print(f"👨‍💼 Админ: {ADMIN_ID}")
    print("="*50)
    print("📌 ЛОГИКА ДОГРЕВОВ:")
    print("  ВИДЕОУРОК (1-3) — только если НЕ посмотрел урок:")
    print("    1й: через 15 мин после видео")
    print("    2й: через 1 час после 1го")
    print("    3й: через 5 часов после 2го")
    print("  ДОГРЕВ №4 — ВСЕМ без исключения, через 24ч после видео:")
    print("    Не зависит от lesson_watched, не зависит от 1-3 догревов")
    print("  РАЗБОР (1-2) — только если дошёл до экрана разбора:")
    print("    1й: через 3 часа после разбора")
    print("    2й: через 6 часов после 1го")
    print("="*50)
    print("📌 Команды:")
    print("  /start - начать работу")
    print("  /stats - показать количество пользователей")
    print("  /users - список всех пользователей (админ)")
    print("  /rasilka - сделать рассылку (админ)")
    print("  /cancel - отменить действие")
    print("="*50 + "\n")
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())