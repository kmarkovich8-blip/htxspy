"""
HTX P2P Monitor — THB/USDT
Полностью кнопочный интерфейс, без ручного ввода команд
"""

import asyncio
import logging
import os

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)

# ═══════════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ═══════════════════════════════════════════════════════════

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_СЮДА")

HTX_URL  = "https://otc-api.htx.com/v1/data/trade-market"
COIN_ID  = 2    # USDT
CURR_ID  = 19   # THB
SIDE     = "sell"   # "sell" = продавцы USDT | "buy" = покупатели USDT
POLL_SEC = 20   # проверка каждые 20 секунд

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#  СОСТОЯНИЕ
# ═══════════════════════════════════════════════════════════

state = {
    "chat_id":    None,
    "range":      None,       # {"low": 32.5, "high": 33.5, "seen": set()}
    "watches":    {},         # { ник_lower: {"nick", "my_price", "last_price", "alerted"} }
}

# ConversationHandler states
(
    S_ALERT_LOW, S_ALERT_HIGH,
    S_WATCH_NICK, S_WATCH_PRICE,
) = range(4)

# ═══════════════════════════════════════════════════════════
#  КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📌  Алерт на диапазон цен",    callback_data="menu_alert")],
        [InlineKeyboardButton("👤  Следить за трейдером",     callback_data="menu_watch")],
        [InlineKeyboardButton("📋  Мои алерты и слежки",      callback_data="menu_list")],
        [InlineKeyboardButton("📊  Стакан прямо сейчас",      callback_data="menu_now")],
    ])

def kb_back():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠  Главное меню", callback_data="menu_home")],
    ])

def kb_cancel():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌  Отмена", callback_data="menu_home")],
    ])

# ═══════════════════════════════════════════════════════════
#  HTX API
# ═══════════════════════════════════════════════════════════

async def fetch_ads() -> list[dict]:
    ads = []
    async with aiohttp.ClientSession() as s:
        for page in range(1, 6):
            try:
                r = await s.get(HTX_URL, params={
                    "coinId": COIN_ID, "currency": CURR_ID,
                    "tradeType": SIDE, "currPage": page,
                    "payMethod": 0, "online": 1, "range": 0, "amount": "",
                }, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.htx.com/"}, timeout=10)
                data = await r.json(content_type=None)
                batch = data.get("data", {}).get("list", [])
                if not batch:
                    break
                ads.extend(batch)
            except Exception as e:
                log.warning(f"Fetch p{page}: {e}")
                break
    return ads

def norm(raw: dict) -> dict:
    return {
        "id":    str(raw.get("id", "")),
        "name":  str(raw.get("userName", "")).strip(),
        "price": float(raw.get("price", 0)),
        "min":   float(raw.get("minTradeLimit", 0)),
        "max":   float(raw.get("maxTradeLimit", 0)),
        "qty":   float(raw.get("tradeCount", 0)),
        "deals": int(raw.get("tradeNum", 0)),
    }

# ═══════════════════════════════════════════════════════════
#  ГЛАВНОЕ МЕНЮ
# ═══════════════════════════════════════════════════════════

MAIN_TEXT = (
    "👋 *HTX P2P Monitor — THB/USDT*\n\n"
    "Выбери что хочешь сделать:"
)

async def show_main(update: Update, edit: bool = False):
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(MAIN_TEXT, reply_markup=kb_main(), parse_mode="Markdown")
    else:
        msg = update.message or update.callback_query.message
        await msg.reply_text(MAIN_TEXT, reply_markup=kb_main(), parse_mode="Markdown")

async def cmd_start(update: Update, _):
    state["chat_id"] = update.effective_chat.id
    await show_main(update)

async def cb_home(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    state["chat_id"] = update.effective_chat.id
    await show_main(update, edit=True)
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════
#  ПОТОК: АЛЕРТ НА ДИАПАЗОН
# ═══════════════════════════════════════════════════════════

async def cb_menu_alert(update: Update, _):
    q = update.callback_query
    await q.answer()

    # Показать текущий алерт если есть
    extra = ""
    if state["range"]:
        r = state["range"]
        extra = f"\n\n_Сейчас активен алерт: {r['low']} – {r['high']}_"

    await q.edit_message_text(
        f"📌 *Алерт на диапазон цен*{extra}\n\n"
        "Я пришлю пуш, когда кто-то новый встанет в заданный диапазон.\n\n"
        "Введи *нижнюю* границу диапазона:\n"
        "_(например: `32.50`)_",
        reply_markup=kb_cancel(),
        parse_mode="Markdown"
    )
    return S_ALERT_LOW

async def got_alert_low(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        low = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "❌ Не понял. Введи число, например `32.50`",
            reply_markup=kb_cancel(), parse_mode="Markdown"
        )
        return S_ALERT_LOW

    ctx.user_data["alert_low"] = low
    await update.message.reply_text(
        f"Нижняя граница: *{low}*\n\n"
        f"Теперь введи *верхнюю* границу:\n_(например: `33.50`)_",
        reply_markup=kb_cancel(), parse_mode="Markdown"
    )
    return S_ALERT_HIGH

async def got_alert_high(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        high = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "❌ Не понял. Введи число, например `33.50`",
            reply_markup=kb_cancel(), parse_mode="Markdown"
        )
        return S_ALERT_HIGH

    low = ctx.user_data.get("alert_low", 0)
    if high <= low:
        await update.message.reply_text(
            f"❌ Верхняя граница должна быть больше нижней ({low})\nПопробуй снова:",
            reply_markup=kb_cancel(), parse_mode="Markdown"
        )
        return S_ALERT_HIGH

    state["range"] = {"low": low, "high": high, "seen": set()}

    await update.message.reply_text(
        f"✅ *Алерт включён!*\n\n"
        f"Диапазон: *{low} – {high}* THB/USDT\n\n"
        f"Пришлю пуш как только кто-то новый встанет в этот диапазон 🔔",
        reply_markup=kb_back(), parse_mode="Markdown"
    )
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════
#  ПОТОК: СЛЕЖКА ЗА ТРЕЙДЕРОМ
# ═══════════════════════════════════════════════════════════

async def cb_menu_watch(update: Update, _):
    q = update.callback_query
    await q.answer()
    count = len(state["watches"])
    extra = f"\n\n_Сейчас активно слежек: {count}_" if count else ""

    await q.edit_message_text(
        f"👤 *Следить за трейдером*{extra}\n\n"
        "Введи *ник трейдера* точно как он написан в HTX:\n"
        "_(регистр не важен)_",
        reply_markup=kb_cancel(),
        parse_mode="Markdown"
    )
    return S_WATCH_NICK

async def got_watch_nick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    nick = update.message.text.strip()
    if not nick:
        await update.message.reply_text("❌ Ник не может быть пустым. Попробуй снова:")
        return S_WATCH_NICK

    ctx.user_data["watch_nick"] = nick
    await update.message.reply_text(
        f"Ник: `{nick}`\n\n"
        "Введи *твою цену*.\n"
        "Пришлю пуш когда этот трейдер встанет *ниже* этой цены:\n"
        "_(например: `32.50`)_",
        reply_markup=kb_cancel(), parse_mode="Markdown"
    )
    return S_WATCH_PRICE

async def got_watch_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        my_price = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "❌ Не понял. Введи число, например `32.50`",
            reply_markup=kb_cancel(), parse_mode="Markdown"
        )
        return S_WATCH_PRICE

    nick = ctx.user_data.get("watch_nick", "")

    # Сразу проверить есть ли трейдер в стакане
    checking_msg = await update.message.reply_text(f"🔍 Ищу `{nick}` в стакане...", parse_mode="Markdown")
    ads = [norm(a) for a in await fetch_ads()]
    found = next((a for a in ads if a["name"].lower() == nick.lower()), None)

    already_below = False
    if found:
        already_below = found["price"] < my_price
        status = (
            f"Нашёл его в стакане. Цена сейчас: *{found['price']:.4f}*\n"
            + ("⚠️ Уже ниже твоей цены!" if already_below else "✅ Выше твоей цены")
        )
    else:
        status = "Сейчас нет в стакане — пришлю пуш когда появится ниже твоей цены"

    state["watches"][nick.lower()] = {
        "nick":       nick,
        "my_price":   my_price,
        "last_price": found["price"] if found else None,
        "alerted":    already_below,
    }

    await checking_msg.edit_text(
        f"✅ *Слежка добавлена!*\n\n"
        f"Трейдер: `{nick}`\n"
        f"Твоя цена: *{my_price}*\n\n"
        f"{status}",
        reply_markup=kb_back(), parse_mode="Markdown"
    )
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════
#  МОИ АЛЕРТЫ И СЛЕЖКИ
# ═══════════════════════════════════════════════════════════

async def cb_menu_list(update: Update, _):
    q = update.callback_query
    await q.answer()

    lines = ["📋 *Мои алерты и слежки*\n"]
    buttons = []
    has_anything = False

    # Алерт диапазона
    if state["range"]:
        r = state["range"]
        has_anything = True
        lines.append(
            f"📌 *Алерт диапазона*\n"
            f"  {r['low']} – {r['high']} THB/USDT\n"
            f"  Объявлений в диапазоне: {len(r['seen'])}"
        )
        buttons.append([InlineKeyboardButton("🗑  Удалить алерт диапазона", callback_data="del_range")])

    # Слежки за трейдерами
    if state["watches"]:
        has_anything = True
        lines.append("\n👤 *Слежки за трейдерами:*")
        for w in state["watches"].values():
            price_str = f"{w['last_price']:.4f}" if w["last_price"] else "нет в стакане"
            flag = " 🔴" if w["alerted"] else " ✅"
            lines.append(
                f"  • `{w['nick']}` — моя цена {w['my_price']}\n"
                f"    Сейчас: {price_str}{flag}"
            )
            buttons.append([InlineKeyboardButton(
                f"🗑  Удалить слежку: {w['nick']}",
                callback_data=f"del_watch_{w['nick'].lower()}"
            )])

    if not has_anything:
        lines.append("_Пока ничего нет_\n\nНастрой алерты через главное меню 👇")

    buttons.append([InlineKeyboardButton("🏠  Главное меню", callback_data="menu_home")])

    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

async def cb_del_range(update: Update, _):
    await update.callback_query.answer("Алерт удалён")
    state["range"] = None
    await cb_menu_list(update, None)

async def cb_del_watch(update: Update, _):
    q = update.callback_query
    nick_key = q.data.replace("del_watch_", "")
    if nick_key in state["watches"]:
        name = state["watches"][nick_key]["nick"]
        del state["watches"][nick_key]
        await q.answer(f"Слежка за {name} удалена")
    await cb_menu_list(update, None)

# ═══════════════════════════════════════════════════════════
#  СТАКАН ПРЯМО СЕЙЧАС
# ═══════════════════════════════════════════════════════════

async def cb_menu_now(update: Update, _):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("⏳ Загружаю стакан...", reply_markup=None)

    ads = [norm(a) for a in await fetch_ads()][:20]

    if not ads:
        await q.edit_message_text("❌ Не удалось получить данные. Попробуй позже.", reply_markup=kb_back())
        return

    side_label = "SELL (продают USDT)" if SIDE == "sell" else "BUY (покупают USDT)"
    lines = [f"📊 *THB/USDT — {side_label}*\n"]

    for i, a in enumerate(ads, 1):
        # Пометить трейдеров за которыми следим
        watch_mark = ""
        if a["name"].lower() in state["watches"]:
            w = state["watches"][a["name"].lower()]
            watch_mark = " 👁" + (" 🔴" if a["price"] < w["my_price"] else " ✅")

        # Пометить если в диапазоне
        range_mark = ""
        if state["range"]:
            r = state["range"]
            if r["low"] <= a["price"] <= r["high"]:
                range_mark = " 📌"

        lines.append(
            f"{i}. *{a['price']:.4f}*{range_mark} — `{a['name']}`{watch_mark}\n"
            f"   {a['min']:.0f}–{a['max']:.0f} THB | {a['qty']:.0f} USDT | {a['deals']} сд."
        )

    legend = []
    if state["watches"]:
        legend.append("👁 = слежу | 🔴 ниже тебя | ✅ выше тебя")
    if state["range"]:
        legend.append("📌 = в твоём диапазоне")
    if legend:
        lines.append("\n" + " | ".join(legend))

    await q.edit_message_text("\n".join(lines), reply_markup=kb_back(), parse_mode="Markdown")

# ═══════════════════════════════════════════════════════════
#  ФОНОВЫЙ МОНИТОРИНГ
# ═══════════════════════════════════════════════════════════

async def monitor(app: Application):
    log.info("Monitor started")
    while True:
        await asyncio.sleep(POLL_SEC)

        chat_id = state["chat_id"]
        if not chat_id:
            continue
        if not state["range"] and not state["watches"]:
            continue

        try:
            ads = [norm(a) for a in await fetch_ads()]
        except Exception as e:
            log.error(f"Fetch: {e}")
            continue

        # ── Алерт диапазона ──────────────────────────────────────────────────
        r = state["range"]
        if r:
            in_range  = [a for a in ads if r["low"] <= a["price"] <= r["high"]]
            new_ones  = [a for a in in_range if a["id"] not in r["seen"]]

            if new_ones:
                for a in in_range:
                    r["seen"].add(a["id"])

                lines = [f"🔔 *Кто-то встал в твой диапазон!*\n"
                         f"Диапазон: {r['low']} – {r['high']}\n"]
                for a in new_ones[:6]:
                    lines.append(
                        f"• *{a['price']:.4f}* — `{a['name']}`\n"
                        f"  {a['min']:.0f}–{a['max']:.0f} THB | {a['qty']:.0f} USDT"
                    )
                await app.bot.send_message(
                    chat_id, "\n".join(lines),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("📊 Открыть стакан", callback_data="menu_now")
                    ]])
                )
            else:
                r["seen"] = {a["id"] for a in in_range}

        # ── Слежка за трейдерами ─────────────────────────────────────────────
        if state["watches"]:
            by_name = {a["name"].lower(): a for a in ads}

            for key, w in list(state["watches"].items()):
                ad        = by_name.get(key)
                new_price = ad["price"] if ad else None

                if new_price is not None and new_price < w["my_price"] and not w["alerted"]:
                    await app.bot.send_message(
                        chat_id,
                        f"⚠️ *{w['nick']} встал ниже тебя!*\n\n"
                        f"Его цена:  *{new_price:.4f}*\n"
                        f"Твоя цена: {w['my_price']}\n"
                        f"Разница:   {w['my_price'] - new_price:.4f} THB",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("📊 Открыть стакан", callback_data="menu_now")
                        ]])
                    )
                    w["alerted"] = True

                elif new_price is not None and new_price >= w["my_price"] and w["alerted"]:
                    await app.bot.send_message(
                        chat_id,
                        f"✅ *{w['nick']} вернулся выше тебя*\n"
                        f"Его цена: *{new_price:.4f}* | Твоя: {w['my_price']}",
                        parse_mode="Markdown"
                    )
                    w["alerted"] = False

                elif new_price is None and w["last_price"] is not None:
                    await app.bot.send_message(
                        chat_id,
                        f"👻 `{w['nick']}` исчез из стакана\n"
                        f"Последняя цена: {w['last_price']:.4f}",
                        parse_mode="Markdown"
                    )

                w["last_price"] = new_price

# ═══════════════════════════════════════════════════════════
#  СБОРКА И ЗАПУСК
# ═══════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandler — алерт диапазона
    alert_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_menu_alert, pattern="^menu_alert$")],
        states={
            S_ALERT_LOW:  [MessageHandler(filters.TEXT & ~filters.COMMAND, got_alert_low)],
            S_ALERT_HIGH: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_alert_high)],
        },
        fallbacks=[
            CallbackQueryHandler(cb_home, pattern="^menu_home$"),
            CommandHandler("start", cmd_start),
        ],
        per_message=False,
    )

    # ConversationHandler — слежка за трейдером
    watch_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_menu_watch, pattern="^menu_watch$")],
        states={
            S_WATCH_NICK:  [MessageHandler(filters.TEXT & ~filters.COMMAND, got_watch_nick)],
            S_WATCH_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_watch_price)],
        },
        fallbacks=[
            CallbackQueryHandler(cb_home, pattern="^menu_home$"),
            CommandHandler("start", cmd_start),
        ],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(alert_conv)
    app.add_handler(watch_conv)
    app.add_handler(CallbackQueryHandler(cb_home,      pattern="^menu_home$"))
    app.add_handler(CallbackQueryHandler(cb_menu_list, pattern="^menu_list$"))
    app.add_handler(CallbackQueryHandler(cb_menu_now,  pattern="^menu_now$"))
    app.add_handler(CallbackQueryHandler(cb_del_range, pattern="^del_range$"))
    app.add_handler(CallbackQueryHandler(cb_del_watch, pattern="^del_watch_"))

    async def post_init(application: Application):
        asyncio.create_task(monitor(application))

    app.post_init = post_init
    log.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
