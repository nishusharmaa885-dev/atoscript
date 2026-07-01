#!/usr/bin/env python3
"""
ATOMICBUX Telegram Bot - Single File Architecture
Python 3.12+ | python-telegram-bot v22+
"""

import asyncio
import json as _json
import logging
import os
import random
import time
from datetime import datetime, timezone

import requests as req

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.request import HTTPXRequest

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ============================================
# LOGGING
# ============================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============================================
# CONSTANTS
# ============================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8952586022:AAE7MZu1Hb2mHhcS1quRh290pRWuCmK_q3E")

PROVIDERS = [
    "adexium",
    "onclicka",
    "home_adexium",
    "home_onclicka",
    "monetag",
    "adsgram",
    "home_monetag",
    "home_adsgram",
]

MAX_CYCLES = 20
AD_WATCH_SECONDS = 11
API_BASE = "https://atomicbux.online/backend/api"

# ============================================
# USER STATE STORAGE
# ============================================

user_tokens: dict[int, str] = {}
user_tasks: dict[int, asyncio.Task] = {}
user_stop_flags: dict[int, bool] = {}
user_progress_msgs: dict[int, int] = {}
user_start_times: dict[int, float] = {}
user_balances: dict[int, float | None] = {}

# Conversation states
WAITING_TOKEN = 0

# ============================================
# HTTP HELPERS
# ============================================


def _headers(token: str) -> dict[str, str]:
    return {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 16; K) "
            "AppleWebKit/537.36 Chrome/149.0.7827.91 Safari/537.36"
        ),
        "x-requested-with": "org.telegram.messenger.web",
        "origin": "https://atomicbux.online",
        "referer": "https://atomicbux.online/",
    }

# Create a session that bypasses system proxy & env vars for AtomicBux API
_session = req.Session()
_session.trust_env = False
_session.verify = False
_session.proxies = {"http": "", "https": ""}


def _api_get(url: str, headers: dict[str, str], timeout: int = 15) -> tuple[int, str]:
    """GET request via requests Session (proxy-safe, cross-platform)."""
    try:
        resp = _session.get(url, headers=headers, timeout=timeout)
        return resp.status_code, resp.text
    except req.exceptions.Timeout:
        return 0, "Request timed out"
    except Exception as e:
        return 0, str(e)


def _api_post(url: str, headers: dict[str, str], data: dict, timeout: int = 15) -> tuple[int, str]:
    """POST request via requests Session (proxy-safe, cross-platform)."""
    try:
        resp = _session.post(url, headers=headers, json=data, timeout=timeout)
        return resp.status_code, resp.text
    except req.exceptions.Timeout:
        return 0, "Request timed out"
    except Exception as e:
        return 0, str(e)


def _sync_get_balance(token: str) -> float | None:
    status, body = _api_get(f"{API_BASE}/user", _headers(token))
    logger.info("get_balance: status=%d", status)
    if status == 200:
        try:
            return _json.loads(body).get("usdt", 0)
        except Exception:
            pass
    return None


async def get_balance(token: str) -> float | None:
    """Fetch current USDT balance from backend."""
    return await asyncio.to_thread(_sync_get_balance, token)


def _sync_watch_ad(token: str, provider: str) -> bool:
    status, body = _api_post(
        f"{API_BASE}/watch-ad", _headers(token), {"provider": provider}
    )
    if status == 200:
        try:
            return _json.loads(body).get("status") == "success"
        except Exception:
            pass
    return False


async def watch_ad(token: str, provider: str) -> bool:
    """Simulate watching an ad then post result to backend."""
    watch_time = AD_WATCH_SECONDS + random.uniform(0, 1)
    await asyncio.sleep(watch_time)
    return await asyncio.to_thread(_sync_watch_ad, token, provider)


def _sync_validate_token(token: str) -> tuple[bool, int, str]:
    logger.info("validate_token: starting request to %s", API_BASE)
    status, body = _api_get(f"{API_BASE}/user", _headers(token), timeout=15)
    logger.info("validate_token: got status=%d body=%s", status, body[:200])
    return (status == 200, status, body[:300])


async def validate_token(token: str) -> tuple[bool, int, str]:
    """Check if token is valid by calling the user endpoint."""
    return await asyncio.to_thread(_sync_validate_token, token)


# ============================================
# PROGRESS MESSAGE BUILDER
# ============================================


def build_progress_text(
    cycle: int,
    provider_results: dict[str, bool | None],
    total_earned: float,
    current_provider: str | None,
    stopped: bool = False,
) -> str:
    """Build the live progress message text."""
    lines: list[str] = []
    lines.append(f"🔄 Cycle {cycle}/{MAX_CYCLES}")
    lines.append("─" * 24)

    for prov in PROVIDERS:
        result = provider_results.get(prov)
        if result is True:
            lines.append(f"  ✅ {prov}")
        elif result is False:
            lines.append(f"  ❌ {prov}")
        elif prov == current_provider:
            lines.append(f"  ⏳ {prov} ...")
        else:
            lines.append(f"  ⬜ {prov}")

    lines.append("─" * 24)
    lines.append(f"💰 Earned this session: {total_earned:.4f} USDT")

    if stopped:
        lines.append("\n⛔ Task stopped by user.")

    return "\n".join(lines)


def build_summary_text(
    total_completed: int,
    total_earned: float,
    final_balance: float | None,
    elapsed: float,
    start_dt: datetime,
) -> str:
    """Build the completion / stop summary."""
    mins, secs = divmod(int(elapsed), 60)
    hours, mins = divmod(mins, 60)
    time_str = f"{hours}h {mins}m {secs}s"

    lines = [
        "🎯 Task Summary",
        "═" * 28,
        f"✅ Completed cycles: {total_completed}/{MAX_CYCLES}",
        f"💰 Total earned: {total_earned:.4f} USDT",
    ]
    if final_balance is not None:
        lines.append(f"💳 Final balance: {final_balance:.4f} USDT")
    lines.append(f"⏱ Execution time: {time_str}")
    lines.append(f"🕐 Started: {start_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(
        f"🕐 Finished: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    lines.append("═" * 28)
    return "\n".join(lines)


# ============================================
# BACKGROUND TASK
# ============================================


async def run_task(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main background task that runs cycles and updates progress."""
    token = user_tokens[chat_id]
    total_earned = 0.0
    completed_cycles = 0
    start_time = user_start_times[chat_id]
    start_dt = datetime.fromtimestamp(start_time, tz=timezone.utc)

    # Send initial progress message
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text="⏳ Starting task...",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⛔ Stop Task", callback_data="stop_task")]]
        ),
    )
    user_progress_msgs[chat_id] = msg.message_id

    try:
        for cycle in range(1, MAX_CYCLES + 1):
            if user_stop_flags.get(chat_id, False):
                break

            completed_cycles = cycle
            provider_results: dict[str, bool | None] = {p: None for p in PROVIDERS}

            # Update message for cycle start
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg.message_id,
                    text=build_progress_text(
                        cycle, provider_results, total_earned, None
                    ),
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "⛔ Stop Task", callback_data="stop_task"
                                )
                            ]
                        ]
                    ),
                )
            except Exception as e:
                logger.debug("Edit failed (cycle start): %s", e)

            for provider in PROVIDERS:
                if user_stop_flags.get(chat_id, False):
                    break

                # Mark current provider as running
                provider_results[provider] = None
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg.message_id,
                        text=build_progress_text(
                            cycle, provider_results, total_earned, provider
                        ),
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "⛔ Stop Task", callback_data="stop_task"
                                    )
                                ]
                            ]
                        ),
                    )
                except Exception as e:
                    logger.debug("Edit failed (provider): %s", e)

                success = await watch_ad(token, provider)
                provider_results[provider] = success

                if success:
                    total_earned += 0.0001

                # Update after each provider completes
                try:
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg.message_id,
                        text=build_progress_text(
                            cycle, provider_results, total_earned, None
                        ),
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "⛔ Stop Task", callback_data="stop_task"
                                    )
                                ]
                            ]
                        ),
                    )
                except Exception as e:
                    logger.debug("Edit failed (provider done): %s", e)

            if user_stop_flags.get(chat_id, False):
                break

    except asyncio.CancelledError:
        logger.info("Task cancelled for %d", chat_id)
    except Exception as e:
        logger.error("Task error for %d: %s", chat_id, e)
    finally:
        # Send completion summary
        elapsed = time.time() - start_time
        stopped = user_stop_flags.get(chat_id, False)
        final_balance = await get_balance(token)

        summary = build_summary_text(
            total_completed=completed_cycles if not stopped else completed_cycles - 1,
            total_earned=total_earned,
            final_balance=final_balance,
            elapsed=elapsed,
            start_dt=start_dt,
        )

        if stopped:
            summary = "⛔ Task Stopped Successfully\n\n" + summary
        else:
            summary = "🎉 Task Completed!\n\n" + summary

        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=summary,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🚀 Run Again", callback_data="run_task"
                            )
                        ]
                    ]
                ),
            )
        except Exception:
            pass

        # Cleanup
        user_tasks.pop(chat_id, None)
        user_stop_flags.pop(chat_id, None)
        user_progress_msgs.pop(chat_id, None)
        user_start_times.pop(chat_id, None)


# ============================================
# HANDLERS
# ============================================


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    keyboard = [[InlineKeyboardButton("🚀 Run Task", callback_data="run_task")]]
    await update.message.reply_text(
        "Welcome to <b>ATOMICBUX Bot</b>!\n\nClick below to start:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def cb_run_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle 'Run Task' button - ask for token."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    # If a task is already running, warn user
    if chat_id in user_tasks and not user_tasks[chat_id].done():
        await query.edit_message_text(
            text="⚠️ A task is already running!\n\nUse the stop button first.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⛔ Stop Task", callback_data="stop_task")]]
            ),
        )
        return ConversationHandler.END

    await query.edit_message_text(text="📝 Paste your token:\n\n<i>(Everything after 'Bearer ')</i>", parse_mode="HTML")
    return WAITING_TOKEN


async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive and validate user token, then start task."""
    chat_id = update.message.chat_id
    token = update.message.text.strip()

    # Strip 'Bearer ' prefix if user pastes the full header value
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    if not token:
        await update.message.reply_text("❌ Invalid token. Please paste a valid token:")
        return WAITING_TOKEN

    # Validate token
    await update.message.reply_text("⏳ Validating token...")

    is_valid, status_code, detail = await validate_token(token)
    logger.info(
        "Token validation for user %d: valid=%s status=%d detail=%s",
        chat_id, is_valid, status_code, detail,
    )
    if not is_valid:
        error_info = f"Status: {status_code}" if status_code else detail
        await update.message.reply_text(
            f"❌ Token validation failed.\n"
            f"<code>{error_info}</code>\n\n"
            f"📝 Paste your token:",
            parse_mode="HTML",
        )
        return WAITING_TOKEN

    # Store token
    user_tokens[chat_id] = token
    user_stop_flags[chat_id] = False
    user_start_times[chat_id] = time.time()

    # Get starting balance
    balance = await get_balance(token)
    user_balances[chat_id] = balance

    balance_str = f"{balance:.4f}" if balance is not None else "Unknown"

    await update.message.reply_text(
        f"✅ Token received successfully\n\n"
        f"📊 <b>Task Info</b>\n"
        f"├ Status: 🟢 Ready\n"
        f"├ Balance: {balance_str} USDT\n"
        f"├ Cycles: {MAX_CYCLES}\n"
        f"└ Providers: {len(PROVIDERS)}\n\n"
        f"🚀 Starting task...",
        parse_mode="HTML",
    )

    # Launch background task
    task = asyncio.create_task(run_task(chat_id, context))
    user_tasks[chat_id] = task

    return ConversationHandler.END


async def cb_stop_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle 'Stop Task' button."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id

    if chat_id in user_tasks and not user_tasks[chat_id].done():
        user_stop_flags[chat_id] = True

        # Cancel the asyncio task to interrupt any sleeping operations
        task = user_tasks[chat_id]
        task.cancel()

        await query.edit_message_text(text="⛔ Stopping task... Please wait.")
    else:
        await query.edit_message_text(
            text="ℹ️ No task is currently running.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🚀 Run Task", callback_data="run_task")]]
            ),
        )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel conversation."""
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


# ============================================
# MAIN
# ============================================


async def main() -> None:
    """Start the bot."""
    # Build HTTPXRequest for Telegram API
    # On Render (no proxy), proxy=None is fine; httpx uses direct connection
    # On corporate proxy servers, set HTTPS_PROXY env var and it will be picked up
    telegram_proxy = (
        os.environ.get("TELEGRAM_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or None
    )
    _request = HTTPXRequest(
        proxy=telegram_proxy,
        connect_timeout=15.0,
        read_timeout=30.0,
        write_timeout=30.0,
    )
    app = Application.builder().token(BOT_TOKEN).request(_request).build()

    # Conversation handler for token input flow
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_run_task, pattern="^run_task$"),
        ],
        states={
            WAITING_TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=True,
        per_user=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(cb_stop_task, pattern="^stop_task$"))

    logger.info("Bot started! Telegram proxy: %s", telegram_proxy or "none")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )

    # Keep running until interrupted
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
