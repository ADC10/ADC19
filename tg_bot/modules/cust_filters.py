import re
from typing import Optional

import telegram
from telegram import ParseMode, InlineKeyboardMarkup, Message, Chat
from telegram import Update, Bot
from telegram.error import BadRequest
from telegram.ext import CommandHandler, MessageHandler, DispatcherHandlerStop, run_async
from telegram.utils.helpers import escape_markdown

from tg_bot import dispatcher, LOGGER
from tg_bot.modules.disable import DisableAbleCommandHandler
from tg_bot.modules.helper_funcs.chat_status import user_admin
from tg_bot.modules.helper_funcs.extraction import extract_text
from tg_bot.modules.helper_funcs.filters import CustomFilters
from tg_bot.modules.helper_funcs.misc import build_keyboard
from tg_bot.modules.helper_funcs.string_handling import split_quotes, button_markdown_parser
from tg_bot.modules.sql import cust_filters_sql as sql

from tg_bot.modules.connection import connected

HANDLER_GROUP = 10
BASIC_FILTER_STRING = "*Filters in this chat:*\n"


@run_async
def list_handlers(bot: Bot, update: Update):
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user  # type: Optional[User]

    conn = connected(bot, update, chat, user.id, need_admin=False)
    if not conn == False:
        chat_id = conn
        chat_name = dispatcher.bot.getChat(conn).title
        filter_list = "*Filters in {}:*\n"
    else:
        chat_id = update.effective_chat.id
        if chat.type == "private":
            chat_name = "local filters"
            filter_list = "*local filters:*\n"
        else:
            chat_name = chat.title
            filter_list = "*Filters in {}*:\n"


    all_handlers = sql.get_chat_triggers(chat_id)

    if not all_handlers:
        update.effective_message.reply_text("No filters in {}!".format(chat_name))
        return

    for keyword in all_handlers:
        entry = " - {}\n".format(escape_markdown(keyword))
        if len(entry) + len(filter_list) > telegram.MAX_MESSAGE_LENGTH:
            update.effective_message.reply_text(filter_list, parse_mode=telegram.ParseMode.MARKDOWN)
            filter_list = entry
        else:
            filter_list += entry

    if not filter_list == BASIC_FILTER_STRING:
        update.effective_message.reply_text(filter_list, parse_mode=telegram.ParseMode.MARKDOWN)


# NOT ASYNC BECAUSE DISPATCHER HANDLER RAISED
@user_admin
def filters(bot: Bot, update: Update):
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user  # type: Optional[User]
    msg = update.effective_message  # type: Optional[Message]
    args = msg.text.split(None, 1)  # use python's maxsplit to separate Cmd, keyword, and reply_text

    conn = connected(bot, update, chat, user.id)
    if not conn == False:
        chat_id = conn
        chat_name = dispatcher.bot.getChat(conn).title
    else:
        chat_id = update.effective_chat.id
        if chat.type == "private":
            chat_name = "local filters"
        else:
            chat_name = chat.title

    if len(args) < 2:
        return

    extracted = split_quotes(args[1])
    if len(extracted) < 1:
        return
    # set trigger -> lower, so as to avoid adding duplicate filters with different cases
    keyword = extracted[0].lower()

    is_sticker = False
    is_document = False
    is_image = False
    is_voice = False
    is_audio = False
    is_video = False
    buttons = []

    # determine what the contents of the filter are - text, image, sticker, etc
    if len(extracted) >= 2:
        offset = len(extracted[1]) - len(msg.text)  # set correct offset relative to command + notename
        content, buttons = button_markdown_parser(extracted[1], entities=msg.parse_entities(), offset=offset)
        content = content.strip()
        if not content:
            msg.reply_text("There is no note message - You can't JUST have buttons, you need a message to go with it!")
            return

    elif msg.reply_to_message and msg.reply_to_message.sticker:
        content = msg.reply_to_message.sticker.file_id
        is_sticker = True

    elif msg.reply_to_message and msg.reply_to_message.document:
        content = msg.reply_to_message.document.file_id
        is_document = True

    elif msg.reply_to_message and msg.reply_to_message.photo:
        offset = len(msg.reply_to_message.caption)
        ignore_underscore_case, buttons = button_markdown_parser(msg.reply_to_message.caption, entities=msg.reply_to_message.parse_entities(), offset=offset)
        content = msg.reply_to_message.photo[-1].file_id  # last elem = best quality
        is_image = True

    elif msg.reply_to_message and msg.reply_to_message.audio:
        content = msg.reply_to_message.audio.file_id
        is_audio = True

    elif msg.reply_to_message and msg.reply_to_message.voice:
        content = msg.reply_to_message.voice.file_id
        is_voice = True

    elif msg.reply_to_message and msg.reply_to_message.video:
        content = msg.reply_to_message.video.file_id
        is_video = True

    else:
        msg.reply_text("¡No especificaste con qué responder!")
        return

    # Add the filter
    # Note: perhaps handlers can be removed somehow using sql.get_chat_filters
    for handler in dispatcher.handlers.get(HANDLER_GROUP, []):
        if handler.filters == (keyword, chat.id):
            dispatcher.remove_handler(handler, HANDLER_GROUP)

    sql.add_filter(chat_id, keyword, content, is_sticker, is_document, is_image, is_audio, is_voice, is_video,
                   buttons)

    msg.reply_text("Handler '{}' added in *{}*!".format(keyword, chat_name), parse_mode=telegram.ParseMode.MARKDOWN)
    raise DispatcherHandlerStop


# NOT ASYNC BECAUSE DISPATCHER HANDLER RAISED
@user_admin
def stop_filter(bot: Bot, update: Update):
    chat = update.effective_chat  # type: Optional[Chat]
    user = update.effective_user  # type: Optional[User]
    args = update.effective_message.text.split(None, 1)

    conn = connected(bot, update, chat, user.id)
    if not conn == False:
        chat_id = conn
        chat_name = dispatcher.bot.getChat(conn).title
    else:
        chat_id = chat.id
        if chat.type == "private":
            chat_name = "local notes"
        else:
            chat_name = chat.title

    if len(args) < 2:
        return

    chat_filters = sql.get_chat_triggers(chat_id)

    if not chat_filters:
        update.effective_message.reply_text("¡No hay filtros activos aquí!")
        return

    for keyword in chat_filters:
        if keyword == args[1]:
            sql.remove_filter(chat_id, args[1])
            update.effective_message.reply_text("Sí, dejaré de responder a eso en * {} *"..format(chat_name), parse_mode=telegram.ParseMode.MARKDOWN)
            raise DispatcherHandlerStop

    update.effective_message.reply_text("Ese no es un filtro actual: ejecutar /filters para todos los filtros activos".)


@run_async
def reply_filter(bot: Bot, update: Update):
    chat = update.effective_chat  # type: Optional[Chat]
    message = update.effective_message  # type: Optional[Message]
    to_match = extract_text(message)
    if not to_match:
        return

    # my custom thing
    if message.reply_to_message:
        message = message.reply_to_message
    ad_filter = ""
    # my custom thing

    chat_filters = sql.get_chat_triggers(chat.id)
    for keyword in chat_filters:
        pattern = r"( |^|[^\w])" + re.escape(keyword) + r"( |$|[^\w])"
        if re.search(pattern, to_match, flags=re.IGNORECASE):
            filt = sql.get_filter(chat.id, keyword)
            buttons = sql.get_buttons(chat.id, filt.keyword)
            if filt.is_sticker:
                message.reply_sticker(filt.reply)
            elif filt.is_document:
                message.reply_document(filt.reply)
            elif filt.is_image:
                if len(buttons) > 0:
                    keyb = build_keyboard(buttons)
                    keyboard = InlineKeyboardMarkup(keyb)
                    message.reply_photo(filt.reply, reply_markup=keyboard)
                else:
                    message.reply_photo(filt.reply)
            elif filt.is_audio:
                message.reply_audio(filt.reply)
            elif filt.is_voice:
                message.reply_voice(filt.reply)
            elif filt.is_video:
                message.reply_video(filt.reply)
            elif filt.has_markdown:
                keyb = build_keyboard(buttons)
                keyboard = InlineKeyboardMarkup(keyb)

                should_preview_disabled = True
                if "telegra.ph" in filt.reply or "youtu.be" in filt.reply:
                    should_preview_disabled = False

                try:
                    message.reply_text(ad_filter + "\n" + filt.reply, parse_mode=ParseMode.MARKDOWN,
                                       disable_web_page_preview=should_preview_disabled,
                                       reply_markup=keyboard)
                except BadRequest as excp:
                    if excp.message == "Protocolo de URL no admitido":
                        message.reply_text("Parece que estás intentando utilizar un protocolo de URL no compatible. Telegram"
                                           "no admite botones para algunos protocolos, como tg: //. Intente"
                                           "nuevamente, o solicite ayuda en @kingpipo18".
                    elif excp.message == "Reply message not found":
                        bot.send_message(chat.id, filt.reply, parse_mode=ParseMode.MARKDOWN,
                                         disable_web_page_preview=True,
                                         reply_markup=keyboard)
                    else:
                        message.reply_text("Esta nota no se pudo enviar, ya que está formateada incorrectamente. Preguntar"
                                           "¡@kingpipo18 si no puedes entender por qué!")
                        LOGGER.warning("No se pudo analizar el mensaje %s", str(filt.reply))
                        LOGGER.exception("No se pudo analizar el filtro %s en el chat %s", str(filt.keyword), str(chat.id))

            else:
                # LEGACY - all new filters will have has_markdown set to True.
                message.reply_text(ad_filter + "\n" + filt.reply)
            break


def __stats__():
    return "{} filters, across {} chats.".format(sql.num_filters(), sql.num_chats())


def __migrate__(old_chat_id, new_chat_id):
    sql.migrate_chat(old_chat_id, new_chat_id)


def __chat_settings__(chat_id, user_id):
    cust_filters = sql.get_chat_triggers(chat_id)
    return "There are `{}` custom filters here.".format(len(cust_filters))


__help__ = """
 - /filters: list all active filters in this chat.

*Admin only:*
 - /filter <keyword> <reply message>: agregue un filtro a este chat. El bot ahora responderá ese mensaje cada vez que 'palabra clave' \
es mencionado. Si responde a una pegatina con una palabra clave, el bot responderá con esa pegatina. NOTA: todos filtran
Las palabras clave están en minúsculas. Si desea que su palabra clave sea una oración, use comillas. por ejemplo: /filter "hey there" Cómo \
doin?
 - /stop <filter keyword>: para ese filtro
"""

__mod_name__ = "Filters"

FILTER_HANDLER = CommandHandler("filter", filters)
STOP_HANDLER = CommandHandler("stop", stop_filter)
LIST_HANDLER = DisableAbleCommandHandler("filters", list_handlers, admin_ok=True)
CUST_FILTER_HANDLER = MessageHandler(CustomFilters.has_text, reply_filter, edited_updates=True)

dispatcher.add_handler(FILTER_HANDLER)
dispatcher.add_handler(STOP_HANDLER)
dispatcher.add_handler(LIST_HANDLER)
dispatcher.add_handler(CUST_FILTER_HANDLER, HANDLER_GROUP)