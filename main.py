# pyright: reportUnusedImport=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportOptionalMemberAccess=false

import re
import pickle
import os
from collections import defaultdict
from typing import Optional, Union, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, MessageHandler,
                          CallbackQueryHandler, ContextTypes, filters)

# Тип для reply_markup
ReplyMarkupType = Optional[Union[InlineKeyboardMarkup, Any]]

# Файл для сохранения данных
DATA_FILE = "bot_data.pkl"


# Загрузка данных при старте
def load_data():
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "rb") as f:
                data = pickle.load(f)
                # Преобразуем viewed_ankets обратно в defaultdict
                data['viewed_ankets'] = defaultdict(set, data['viewed_ankets'])
                return data
    except Exception as e:
        print(f"Ошибка загрузки данных: {e}")

    return {
        'user_ankets': {},
        'banned_users': set(),
        'viewed_ankets': defaultdict(set),
        'ankets_list': []
    }


# Сохранение данных
def save_data():
    try:
        data = {
            'user_ankets': user_ankets,
            'banned_users': banned_users,
            'viewed_ankets': dict(viewed_ankets),  # defaultdict -> dict
            'ankets_list': ankets_list
        }
        with open(DATA_FILE, "wb") as f:
            pickle.dump(data, f)
    except Exception as e:
        print(f"Ошибка сохранения данных: {e}")


# Инициализация данных
data = load_data()
user_ankets = data['user_ankets']
banned_users = data['banned_users']
viewed_ankets = data['viewed_ankets']
ankets_list = data['ankets_list']
ANKETS_PER_PAGE = 5


def is_admin(user_id: int) -> bool:
    return user_id == 1340811422  # Замените на ваш ID


async def safe_reply(update: Update,
                     text: str,
                     reply_markup: ReplyMarkupType = None) -> None:
    """Безопасная отправка сообщения"""
    try:
        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(
                text, reply_markup=reply_markup)
    except Exception as e:
        print(f"Ошибка отправки сообщения: {e}")


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
            "/help - Помощь")
    await safe_reply(update, text)


async def add_anket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    if user_id in banned_users:
        await safe_reply(update, "❌ Вы заблокированы")
        return

    if user_id in user_ankets:
        await safe_reply(update,
                         "❌ У вас уже есть анкета. Сначала удалите текущую")
        return

    await safe_reply(
        update,
        "📝 Отправьте ссылку на Google Forms и комментарий через пробел:")
    if context.user_data is None:
        context.user_data = {}
    context.user_data['awaiting_anket'] = True


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    if not context.user_data.get('awaiting_anket', False):
        return

    text = update.message.text.strip()
    if not text:
        await safe_reply(update, "❌ Сообщение не может быть пустым")
        return

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await safe_reply(update, "❌ Нужна ссылка И комментарий через пробел")
        return

    url, comment = parts
    if not re.match(r'^https:\/\/(docs\.google\.com|forms\.office\.com)\/.+',
                    url):
        await safe_reply(
            update, "❌ Это не ссылка на Google Forms или Microsoft Forms")
        return

    user_id = update.effective_user.id
    user_ankets[user_id] = (url, comment)
    ankets_list.append((user_id, url, comment))
    save_data()  # Сохраняем данные
    if context.user_data is None:
        context.user_data = {}
    context.user_data['awaiting_anket'] = False


async def view_ankets(update: Update,
                      context: ContextTypes.DEFAULT_TYPE,
                      page: int = 0):
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    if not ankets_list:
        await safe_reply(update, "😢 Пока нет доступных анкет")
        return

    # Фильтрация анкет
    unseen = [
        i for i, (uid, _, _) in enumerate(ankets_list)
        if uid != user_id and i not in viewed_ankets[user_id]
    ]

    if not unseen:
        await safe_reply(update, "✨ Вы просмотрели все доступные анкеты!")
        return

    # Подготовка клавиатуры
    keyboard = []
    for idx in unseen[page * ANKETS_PER_PAGE:(page + 1) * ANKETS_PER_PAGE]:
        _, url, comment = ankets_list[idx]
        btn_text = f"Анкета {idx+1}: {comment[:30]}..."
        keyboard.append(
            [InlineKeyboardButton(btn_text, callback_data=f"view_{idx}")])

    # Кнопка "Далее" если есть еще анкеты
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

    if data is None:
        print("Error: data is None")
        await safe_reply(update, "❌ Ошибка при обработке страницы")
        return

    if data.startswith("view_"):
        idx = int(data[5:])
        _, url, comment = ankets_list[idx]
        viewed_ankets[user_id].add(idx)
        save_data()  # Сохраняем данные
        await query.edit_message_text(
            f"🔗 Ссылка: {url}\n📝 Комментарий: {comment}\n\n"
            "Чтобы вернуться, используйте /view")
    elif data.startswith("page_"):
        try:
            page = int(data[5:])
            await view_ankets(update, context, page)
        except (ValueError, IndexError) as e:
            print(f"Error processing page data: {e}")
            await safe_reply(update, "❌ Ошибка при обработке страницы")


async def delete_anket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return

    user_id = update.effective_user.id
    if user_id not in user_ankets:
        await safe_reply(update, "❌ У вас нет анкеты для удаления")
        return

    del user_ankets[user_id]
    ankets_list[:] = [a for a in ankets_list if a[0] != user_id]
    save_data()  # Сохраняем данные
    await safe_reply(update, "✅ Ваша анкета успешно удалена")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = ("📚 Справка по командам:\n\n"
                 "/start - Начало работы с ботом\n"
                 "/add - Добавить новую анкету\n"
                 "/view - Просмотреть доступные анкеты\n"
                 "/delete - Удалить свою анкету\n"
                 "/help - Показать эту справку\n\n"
                 "Просто отправьте ссылку на анкету и комментарий, "
                 "чтобы добавить её в базу данных.")
    await safe_reply(update, help_text)


def main():
    try:
        TOKEN = "7820852763:AAFdFqpQmNxd5m754fuOPnDGj5MNJs5Lw4w"  # Замените на ваш токен
        application = Application.builder().token(TOKEN).build()

        # Регистрация обработчиков
        handlers = [
            CommandHandler("start", start),
            CommandHandler("add", add_anket),
            CommandHandler("view", lambda u, c: view_ankets(u, c, 0)),
            CommandHandler("delete", delete_anket),
            CommandHandler("help", help_command),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
            CallbackQueryHandler(button_handler)
        ]

        for handler in handlers:
            application.add_handler(handler)

        print("🟢 Бот успешно запущен!")
        application.run_polling()
    except Exception as e:
        print(f"🔴 Ошибка: {e}")
    finally:
        save_data()  # Гарантированное сохранение данных при завершении


if __name__ == "__main__":
    main()
