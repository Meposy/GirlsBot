import re
import pickle
import os
import time
import asyncio
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
from flask import Flask, request, jsonify
import telegram
from telegram import __version__ as telegram_version

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
POST_COOLDOWN = 3600  # 1 час
BANNED_WORDS = ["тупая", "дура", "блять"]
ADMIN_ID = 1340811422
YOOMONEY_LINK = "https://yoomoney.ru/to/4100118961510419"
ANKETS_PER_PAGE = 5
TOKEN = os.getenv('TELEGRAM_TOKEN', '7820852763:AAFdFqpQmNxd5m754fuOPnDGj5MNJs5Lw4w')
WEBHOOK_URL = f"https://girlsbot.onrender.com/{TOKEN}"

# Тип для reply_markup
ReplyMarkupType = Optional[Union[InlineKeyboardMarkup, Any]]

# ====== Инициализация данных ======
def load_data():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "rb") as f:
                return pickle.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки данных: {e}")
    
    return {
        'user_ankets': {},
        'banned_users': set(),
        'viewed_ankets': defaultdict(set),
        'ankets_list': [],
        'last_post_times': {},
        'channel_posts': {}
    }

def save_data():
    try:
        with open(DATA_FILE, "wb") as f:
            pickle.dump({
                'user_ankets': user_ankets,
                'banned_users': banned_users,
                'viewed_ankets': dict(viewed_ankets),
                'ankets_list': ankets_list,
                'last_post_times': last_post_times,
                'channel_posts': channel_posts
            }, f)
    except Exception as e:
        logger.error(f"Ошибка сохранения данных: {e}")

# Загрузка данных
data = load_data()
user_ankets = data['user_ankets']
banned_users = data['banned_users']
viewed_ankets = data['viewed_ankets']
ankets_list = data['ankets_list']
last_post_times = data['last_post_times']
channel_posts = data['channel_posts']

# ====== Инициализация Flask ======
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

@app.route('/health')
def health():
    return jsonify({"status": "ok"})

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

async def publish_to_channel(user_id: int, url: str, comment: str,
                           context: ContextTypes.DEFAULT_TYPE):
    try:
        user = await context.bot.get_chat(user_id)
        username = f"@{user.username}" if user.username else f"ID:{user_id}"

        message = (f"📌 Новая анкета от {username}:\n\n"
                  f"{comment}\n\n"
                  f"🔗 {url}\n\n"
                  f"#анкета #знакомства")

        sent_message = await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            disable_web_page_preview=True
        )
        logger.info(f"Сообщение отправлено в канал! ID: {sent_message.message_id}")

        channel_posts[user_id] = sent_message.message_id
        save_data()
        return True
    except telegram.error.BadRequest as e:
        logger.error(f"❌ Ошибка публикации (BadRequest): {str(e)}")
        return False
    except telegram.error.Unauthorized:
        logger.error("❌ Бот не имеет доступа к каналу")
        return False
    except Exception as e:
        logger.error(f"❌ Неизвестная ошибка публикации: {str(e)}")
        return False

def log_action(action: str, user_id: int, details: str = ""):
    logger.info(f"Action: {action}, User: {user_id}, Details: {details}")

# ====== Обработчики команд ======
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
    if not update.effective_user or not update.message:
        return

    user_id = update.effective_user.id
    if user_id in banned_users:
        await safe_reply(update, "❌ Вы заблокированы")
        return

    if user_id in user_ankets:
        last_time = last_post_times.get(user_id, 0)
        if time.time() - last_time < POST_COOLDOWN:
            remaining = int((POST_COOLDOWN - (time.time() - last_time)) // 60)
            await safe_reply(update, f"❌ Подождите {remaining} минут")
            return

        await safe_reply(update, "❌ Сначала удалите текущую анкету (/delete)")
        return

    if context.user_data is None:
        context.user_data = {}

    context.user_data['awaiting_anket'] = True
    context.user_data['anket_user_id'] = user_id

    await safe_reply(
        update,
        "📝 Отправьте ссылку на Google Forms и комментарий через пробел:\n"
        "Пример:\n"
        "https://forms.google.com/... Хочу познакомиться!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return

    if context.user_data is None:
        context.user_data = {}

    user_id = update.effective_user.id
    text = update.message.text or ""

    if not context.user_data.get('awaiting_anket', False) or context.user_data.get('anket_user_id') != user_id:
        return

    if any(word in text.lower() for word in BANNED_WORDS):
        await safe_reply(update, "❌ Ваше сообщение содержит запрещённые слова")
        log_action("BANNED_CONTENT", user_id, text)
        context.user_data['awaiting_anket'] = False
        return

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await safe_reply(update, "❌ Нужна ссылка И комментарий через пробел")
        context.user_data['awaiting_anket'] = False
        return

    url, comment = parts

    if not re.match(
            r'^https:\/\/(docs\.google\.com|forms\.office\.com|forms\.gle)\/.+',
            url):
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

    if await publish_to_channel(user_id, url, comment, context):
        await safe_reply(
            update, "✅ Ваша анкета успешно добавлена и опубликована!")
    else:
        await safe_reply(
            update,
            "✅ Анкета сохранена, но возникла проблема с публикацией. Админ уведомлен."
        )
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⚠️ Ошибка публикации анкеты от @{update.effective_user.username}"
        )

    context.user_data['awaiting_anket'] = False
    if 'anket_user_id' in context.user_data:
        del context.user_data['anket_user_id']

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
    query = update.callback_query
    if not query:
        return
        
    await query.answer()
    
    if not query.message:
        return
        
    try:
        if query.data.startswith("view_"):
            idx = int(query.data[5:])
            if 0 <= idx < len(ankets_list):
                _, url, comment = ankets_list[idx]
                if not is_admin(query.from_user.id):
                    viewed_ankets[query.from_user.id].add(idx)
                    save_data()
                await query.edit_message_text(
                    f"🔗 Ссылка: {url}\n📝 Комментарий: {comment}\n\n"
                    "Чтобы вернуться, используйте /view")

        elif query.data.startswith("page_"):
            try:
                page = int(query.data[5:])
                await view_ankets(update, context, page)
            except (ValueError, IndexError) as e:
                logger.error(f"Error processing page data: {e}")
                await query.message.reply_text("❌ Ошибка при обработке страницы")

        elif query.data.startswith("admin_"):
            if not is_admin(query.from_user.id):
                return
                
            if query.data == "admin_view_all":
                await admin_view_all_ankets(update, context)
            elif query.data == "admin_ban":
                await query.message.reply_text("Введите ID пользователя для блокировки:")
                context.user_data['awaiting_ban'] = True
            elif query.data == "admin_delete":
                await query.message.reply_text("Введите номер анкеты для удаления:")
                context.user_data['awaiting_delete'] = True
            elif query.data == "admin_unban":
                await query.message.reply_text("Введите ID пользователя для разблокировки:")
                context.user_data['awaiting_unban'] = True
    except Exception as e:
        logger.error(f"Error in button_handler: {e}")
        if query.message:
            await query.message.reply_text("❌ Произошла ошибка при обработке запроса")

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
    if not update.effective_user or not is_admin(update.effective_user.id):
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
    if update and update.effective_user:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"Ошибка в боте:\n{error}\nUser: {update.effective_user.id}"
        )

# ====== Инициализация бота ======
application = None

def create_application():
    app = Application.builder().token(TOKEN).build()
    register_handlers(app)
    return app

def register_handlers(app: Application):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add_anket))
    app.add_handler(CommandHandler("view", lambda u, c: view_ankets(u, c, 0)))
    app.add_handler(CommandHandler("delete", delete_anket))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("help_create", help_create))
    app.add_handler(CommandHandler("donate", donate))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.User(ADMIN_ID),
        handle_message))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_ID),
        handle_admin_commands))
    app.add_error_handler(error_handler)

# ====== Обработчик вебхука ======
@app.route(f'/{TOKEN}', methods=['POST'])
async def webhook():
    global application
    if request.method == "POST":
        try:
            json_data = request.get_json()
            logger.info(f"Получено обновление: {json_data}")
            
            if application is None:
                application = create_application()
                await application.initialize()
                await application.bot.set_webhook(
                    url=WEBHOOK_URL,
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True
                )
            
            update = Update.de_json(json_data, application.bot)
            await application.process_update(update)
            
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            logger.error(f"Ошибка обработки вебхука: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500
    
    return jsonify({"status": "method not allowed"}), 405

# ====== Запуск сервера ======
def run_flask():
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🟢 Flask запускается на порту {port}")
    
    from waitress import serve
    serve(app, host="0.0.0.0", port=port)

# ====== Основная функция ======
def main():
    global application
    
    try:
        application = create_application()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.bot.set_webhook(
            url=WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        ))
        logger.info(f"🟢 Вебхук установлен: {WEBHOOK_URL}")
        
        flask_thread = Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        while True:
            time.sleep(10)
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
    finally:
        if application:
            loop.run_until_complete(application.shutdown())
        save_data()
        logger.info("🛑 Приложение завершило работу")

if __name__ == '__main__':
    main()
