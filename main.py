import re
import pickle
import os
import time
import asyncio
import socket
import sys
import signal
import logging
from datetime import datetime
from collections import defaultdict
from typing import Optional, Union, Any
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from flask import Flask
import telegram
from telegram import __version__ as telegram_version 
from telegram import error as telegram_error

# ====== Настройка логгирования ======
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ====== Константы ======
DATA_FILE = "bot_data.pkl"
CHANNEL_ID = "@VLV_LP"
POST_COOLDOWN = 3600
BANNED_WORDS = ["тупая", "дура", "блять"]
ADMIN_ID = 1340811422
YOOMONEY_LINK = "https://yoomoney.ru/to/4100118961510419"
ANKETS_PER_PAGE = 5
LOCK_FILE = "bot.lock"

# Тип для reply_markup
ReplyMarkupType = Optional[Union[InlineKeyboardMarkup, Any]]

# Инициализация данных
data = {
    'user_ankets': {},
    'banned_users': set(),
    'viewed_ankets': defaultdict(set),
    'ankets_list': [],
    'last_post_times': {},
    'channel_posts': {}
}

try:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "rb") as f:
            loaded_data = pickle.load(f)
            data['viewed_ankets'] = defaultdict(set, loaded_data.get('viewed_ankets', {}))
            data.update(loaded_data)
except Exception as e:
    logger.error(f"Ошибка загрузки данных: {e}")

user_ankets = data['user_ankets']
banned_users = data['banned_users']
viewed_ankets = data['viewed_ankets']
ankets_list = data['ankets_list']
last_post_times = data['last_post_times']
channel_posts = data['channel_posts']

def save_data():
    try:
        data_to_save = {
            'user_ankets': user_ankets,
            'banned_users': banned_users,
            'viewed_ankets': dict(viewed_ankets),
            'ankets_list': ankets_list,
            'last_post_times': last_post_times,
            'channel_posts': channel_posts
        }
        with open(DATA_FILE, "wb") as f:
            pickle.dump(data_to_save, f)
    except Exception as e:
        logger.error(f"Ошибка сохранения данных: {e}")

# ====== Flask App ======
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

@app.route('/health')
def health():
    return "OK", 200

def run_flask():
    """Запускает Flask сервер"""
    port = int(os.environ.get('PORT', 10000))
    
    # Настройка логгирования Flask
    flask_log = logging.getLogger('werkzeug')
    flask_log.setLevel(logging.WARNING)
    
    logger.info(f"🟢 Flask запускается на порту {port}")
    
    try:
        from waitress import serve
        serve(app, host="0.0.0.0", port=port, threads=1)
    except ImportError:
        logger.warning("⚠️ Waitress не установлен, используем dev-сервер")
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ====== Telegram Bot Functions ======
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

async def safe_reply(update: Update, text: str, reply_markup: ReplyMarkupType = None):
    try:
        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    if user_id in banned_users:
        await safe_reply(update, "❌ Вы заблокированы")
        return

    text = ("👋 Привет! Я бот для обмена анкетами.\n"
            "Доступные команды:\n"
            "/add - Добавить анкету\n"
            "/view - Просмотреть анкеты\n"
            "/delete - Удалить свою анкету\n"
            "/help - Помощь\n"
            "/help_create - Как создать анкету\n"
            "/donate - Поддержать проект")

    if is_admin(user_id):
        text += "\n\nАдминистратору доступны команды:\n/admin - Панель управления"

    await safe_reply(update, text)

async def help_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📝 Как создать анкету:\n\n"
        "1. Перейдите на Google Forms\n"
        "2. Создайте новую форму\n"
        "3. Настройте вопросы\n"
        "4. Отправьте мне ссылку на форму и комментарий через пробел\n\n"
        "Пример:\n"
        "https://forms.google.com/ваша_форма Хочу познакомиться!")
    await safe_reply(update, help_text)

async def donate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("💖 Поддержать проект:\n\n"
            f"ЮMoney: {YOOMONEY_LINK}\n"
            "Спасибо за вашу поддержку!")
    await safe_reply(update, text)

async def add_anket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("\n=== ОБРАБОТКА /add ===")
    try:
        if not update.message or not update.effective_user:
            logger.error("❌ Нет сообщения или пользователя")
            return

        user_id = update.effective_user.id
        logger.info(f"User ID: {user_id}")

        if user_id in banned_users:
            logger.info("⛔ Пользователь заблокирован")
            await safe_reply(update, "❌ Вы заблокированы")
            return

        if user_id in user_ankets:
            last_time = last_post_times.get(user_id, 0)
            if time.time() - last_time < POST_COOLDOWN:
                remaining = int((POST_COOLDOWN - (time.time() - last_time)) // 60)
                logger.info(f"⚠️ Лимит: {remaining} мин осталось")
                await safe_reply(update, f"❌ Подождите {remaining} минут")
                return

            logger.info("⚠️ Уже есть анкета")
            await safe_reply(update, "❌ Сначала удалите текущую анкету (/delete)")
            return

        if context.user_data is None:
            context.user_data = {}

        context.user_data['awaiting_anket'] = True
        context.user_data['anket_user_id'] = user_id
        logger.info("✅ Ожидаем анкету (awaiting_anket=True)")

        await safe_reply(
            update,
            "📝 Отправьте ссылку на Google Forms и комментарий через пробел:\n"
            "Пример:\n"
            "https://forms.google.com/... Хочу познакомиться!")

    except Exception as e:
        logger.error(f"🔥 Ошибка в add_anket: {e}")
        await safe_reply(update, "❌ Ошибка. Попробуйте позже.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("\n=== НОВОЕ СООБЩЕНИЕ ===")
    try:
        if not update.message or not update.effective_user:
            logger.error("❌ Нет сообщения или пользователя")
            return

        if context.user_data is None:
            context.user_data = {}

        user_id = update.effective_user.id
        text = update.message.text or ""

        logger.info(f"User ID: {user_id}")
        logger.info(f"Text: {text}")
        logger.info(f"Context user_data: {context.user_data}")

        if (not context.user_data.get('awaiting_anket', False)
                or context.user_data.get('anket_user_id') != user_id):
            logger.error("❌ Не ожидаем анкету")
            return

        if any(word in text.lower() for word in BANNED_WORDS):
            logger.error("❌ Найдены запрещенные слова")
            await safe_reply(update, "❌ Ваше сообщение содержит запрещённые слова")
            log_action("BANNED_CONTENT", user_id, text)
            context.user_data['awaiting_anket'] = False
            return

        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            logger.error("❌ Не хватает частей (нужны url и комментарий)")
            await safe_reply(update, "❌ Нужна ссылка И комментарий через пробел")
            return

        url, comment = parts
        logger.info(f"URL: {url}, Комментарий: {comment}")

        if not re.match(
                r'^https:\/\/(docs\.google\.com|forms\.office\.com|forms\.gle)\/.+',
                url):
            logger.error("❌ Невалидный URL")
            await safe_reply(
                update, "❌ Это не ссылка на Google Forms или Microsoft Forms")
            context.user_data['awaiting_anket'] = False
            return

        user_ankets[user_id] = {
            'url': url,
            'comment': comment,
            'time': time.time()
        }
        ankets_list.append((user_id, url, comment))
        last_post_times[user_id] = time.time()
        save_data()
        logger.info("✅ Анкета сохранена локально")

        if await publish_to_channel(user_id, url, comment, context):
            logger.info("✅ Анкета опубликована в канал")
            await safe_reply(
                update, "✅ Ваша анкета успешно добавлена и опубликована!")
        else:
            logger.error("❌ Ошибка публикации в канал")
            await safe_reply(
                update,
                "✅ Анкета сохранена, но возникла проблема с публикацией. Админ уведомлен."
            )
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ Ошибка публикации анкеты от @{update.effective_user.username}"
            )

    except Exception as e:
        logger.error(f"🔥 КРИТИЧЕСКАЯ ОШИБКА: {str(e)}")
        if update.message:
            await safe_reply(
                update,
                "❌ Произошла непредвиденная ошибка. Админ уже уведомлен.")
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🚨 Ошибка в handle_message:\n{str(e)}\n\nUser: {user_id}\nText: {text}"
        )

    finally:
        if context.user_data is not None:
            context.user_data['awaiting_anket'] = False
            if 'anket_user_id' in context.user_data:
                del context.user_data['anket_user_id']
            logger.info("✅ Флаги ожидания сброшены")

async def view_ankets(update: Update,
                      context: ContextTypes.DEFAULT_TYPE,
                      page: int = 0):
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    if not ankets_list:
        await safe_reply(update, "😢 Пока нет доступных анкет")
        return

    if is_admin(user_id):
        keyboard = []
        for idx, (_, url, comment) in enumerate(
                ankets_list[page * ANKETS_PER_PAGE:(page + 1) *
                            ANKETS_PER_PAGE], 1):
            btn_text = f"Анкета {idx}: {comment[:30]}..."
            keyboard.append([
                InlineKeyboardButton(btn_text, callback_data=f"view_{idx-1}")
            ])

        if len(ankets_list) > (page + 1) * ANKETS_PER_PAGE:
            keyboard.append([
                InlineKeyboardButton("Далее →", callback_data=f"page_{page+1}")
            ])

        reply_markup = InlineKeyboardMarkup(keyboard)
        await safe_reply(update,
                         "📋 Все анкеты (админ-режим):",
                         reply_markup=reply_markup)
        return

    unseen = [
        i for i, (uid, _, _) in enumerate(ankets_list)
        if uid != user_id and i not in viewed_ankets[user_id]
    ]

    if not unseen:
        await safe_reply(update, "✨ Вы просмотрели все доступные анкеты!")
        return

    keyboard = []
    for idx in unseen[page * ANKETS_PER_PAGE:(page + 1) * ANKETS_PER_PAGE]:
        _, url, comment = ankets_list[idx]
        btn_text = f"Анкета {idx+1}: {comment[:30]}..."
        keyboard.append(
            [InlineKeyboardButton(btn_text, callback_data=f"view_{idx}")])

    if len(unseen) > (page + 1) * ANKETS_PER_PAGE:
        keyboard.append(
            [InlineKeyboardButton("Далее →", callback_data=f"page_{page+1}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await safe_reply(update,
                     "📋 Выберите анкету для просмотра:",
                     reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query or not update.effective_user:
        return

    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data.startswith("view_"):
        idx = int(data[5:])
        _, url, comment = ankets_list[idx]

        if not is_admin(user_id):
            viewed_ankets[user_id].add(idx)
            save_data()

        await query.edit_message_text(
            f"🔗 Ссылка: {url}\n📝 Комментарий: {comment}\n\n"
            "Чтобы вернуться, используйте /view")

    elif data.startswith("page_"):
        try:
            page = int(data[5:])
            await view_ankets(update, context, page)
        except (ValueError, IndexError) as e:
            logger.error(f"Error processing page data: {e}")
            await safe_reply(update, "❌ Ошибка при обработке страницы")

    elif data.startswith("admin_"):
        if data == "admin_view_all":
            await admin_view_all_ankets(update, context)
        elif data == "admin_ban":
            await query.message.reply_text(
                "Введите ID пользователя для блокировки:")
            context.user_data['awaiting_ban'] = True
        elif data == "admin_delete":
            await query.message.reply_text("Введите номер анкеты для удаления:")
            context.user_data['awaiting_delete'] = True
        elif data == "admin_unban":
            await query.message.reply_text(
                "Введите ID пользователя для разблокировки:")
            context.user_data['awaiting_unban'] = True

async def delete_anket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    if user_id not in user_ankets:
        await safe_reply(update, "❌ У вас нет анкеты для удаления")
        return

    if user_id in channel_posts:
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID,
                                             message_id=channel_posts[user_id])
            del channel_posts[user_id]
        except Exception as e:
            logger.error(f"Ошибка удаления из канала: {e}")

    del user_ankets[user_id]
    ankets_list[:] = [a for a in ankets_list if a[0] != user_id]
    if user_id in last_post_times:
        del last_post_times[user_id]
    save_data()

    await safe_reply(update, "✅ Ваша анкета успешно удалена")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = ("📚 Справка по командам:\n\n"
                 "/start - Начало работы с ботом\n"
                 "/add - Добавить новую анкету\n"
                 "/view - Просмотреть доступные анкеты\n"
                 "/delete - Удалить свою анкету\n"
                 "/help_create - Как создать анкету\n"
                 "/donate - Поддержать проект\n"
                 "/help - Показать эту справку\n\n"
                 "Просто отправьте ссылку на анкету и комментарий, "
                 "чтобы добавить её в базу данных.")
    await safe_reply(update, help_text)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not is_admin(update.effective_user.id):
        return

    keyboard = [[
        InlineKeyboardButton("Список всех анкет",
                             callback_data="admin_view_all")
    ],
                [
                    InlineKeyboardButton("Заблокировать пользователя",
                                         callback_data="admin_ban")
                ],
                [
                    InlineKeyboardButton("Разблокировать пользователя",
                                         callback_data="admin_unban")
                ],
                [
                    InlineKeyboardButton("Удалить анкету",
                                         callback_data="admin_delete")
                ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Админ-панель:", reply_markup=reply_markup)

async def admin_view_all_ankets(update: Update,
                                context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    text = "Все анкеты:\n\n"
    for idx, (user_id, url, comment) in enumerate(ankets_list, 1):
        try:
            user = await context.bot.get_chat(user_id)
            username = f"@{user.username}" if user.username else "нет username"
            text += f"{idx}. {username} (ID: {user_id}): {comment}\nСсылка: {url}\n\n"
        except Exception:
            text += f"{idx}. [недоступный пользователь] (ID: {user_id}): {comment}\nСсылка: {url}\n\n"

    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])

async def handle_admin_commands(update: Update,
                                context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or not is_admin(
            update.effective_user.id):
        return

    if context.user_data is None:
        context.user_data = {}

    if context.user_data.get('awaiting_ban', False):
        try:
            user_id = int(update.message.text)
            banned_users.add(user_id)
            save_data()
            await update.message.reply_text(
                f"Пользователь {user_id} заблокирован")
            log_action("BAN_USER", update.effective_user.id,
                       f"Banned {user_id}")
            context.user_data['awaiting_ban'] = False
        except Exception:
            await update.message.reply_text("Неверный ID пользователя")

    elif context.user_data.get('awaiting_unban', False):
        try:
            user_id = int(update.message.text)
            if user_id in banned_users:
                banned_users.remove(user_id)
                save_data()
                await update.message.reply_text(
                    f"Пользователь {user_id} разблокирован")
                log_action("UNBAN_USER", update.effective_user.id,
                           f"Unbanned {user_id}")
            else:
                await update.message.reply_text(
                    "Этот пользователь не заблокирован")
            context.user_data['awaiting_unban'] = False
        except Exception:
            await update.message.reply_text("Неверный ID пользователя")

    elif context.user_data.get('awaiting_delete', False):
        try:
            text = update.message.text or ""
            if text.isdigit():
                idx = int(text) - 1
                if 0 <= idx < len(ankets_list):
                    user_id, url, comment = ankets_list.pop(idx)
                    if user_id in user_ankets:
                        del user_ankets[user_id]
                    if user_id in last_post_times:
                        del last_post_times[user_id]
                    save_data()

                    if user_id in channel_posts:
                        try:
                            await context.bot.delete_message(
                                chat_id=CHANNEL_ID,
                                message_id=channel_posts[user_id])
                            del channel_posts[user_id]
                        except Exception:
                            pass

                    await update.message.reply_text("Анкета удалена")
                    log_action("DELETE_ANKET", update.effective_user.id,
                               f"Deleted anketa {idx}")
                else:
                    await update.message.reply_text("Неверный номер анкеты")
                context.user_data['awaiting_delete'] = False
            else:
                await update.message.reply_text("Неверный номер анкеты")

        except ValueError:
            await update.message.reply_text("Неверный номер анкеты")
        except Exception:
            await update.message.reply_text("Неверный номер анкеты")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    logger.error(f'⚠️ Ошибка: {error}')

async def run_bot():
    """Запускает Telegram бота"""
    logger.info("=== Запуск Telegram бота ===")
    
    TOKEN = os.getenv('TELEGRAM_TOKEN', '7820852763:AAFdFqpQmNxd5m754fuOPnDGj5MNJs5Lw4w')
    application = Application.builder().token(TOKEN).build()

    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_anket))
    application.add_handler(CommandHandler("view", lambda u, c: view_ankets(u, c, 0)))
    application.add_handler(CommandHandler("delete", delete_anket))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("help_create", help_create))
    application.add_handler(CommandHandler("donate", donate))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.User(ADMIN_ID),
        handle_message))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_ID),
        handle_admin_commands))
    
    application.add_error_handler(error_handler)

    await application.initialize()
    await application.bot.delete_webhook(drop_pending_updates=True)
    await application.start()
    logger.info("🟢 Telegram бот успешно запущен!")
    
    # Бесконечный цикл для поддержания работы бота
    while True:
        await asyncio.sleep(3600)  # Проверка каждые 60 минут

    await application.stop()
    await application.shutdown()

def main():
    """Основная функция запуска"""
    # Запускаем Flask в отдельном потоке
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Запускаем бота в основном потоке
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("🛑 Получен сигнал завершения")
    except Exception as e:
        logger.error(f"🔴 Критическая ошибка: {e}")
    finally:
        save_data()
        logger.info("🛑 Приложение завершило работу")

if __name__ == '__main__':
    main()
