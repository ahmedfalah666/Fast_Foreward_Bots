import asyncio
import logging
import os
import socket
import sys
from datetime import datetime, timedelta

from sqlalchemy import select

# Windows compatibility: psycopg requires SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from telegram import Update, BotCommand
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes
)

from config import BOT_TOKEN
from db import init_db, AsyncSessionLocal, AsyncSessionPG, BotLock, pull_from_postgres, push_table_to_postgres
from handlers.user import (
    start_command,
    user_menu_navigation,
    user_link_click
)
from handlers.admin import (
    admin_command,
    admin_menu_navigation,
    admin_add_click,
    admin_delete_click,
    admin_publish_click,
    handle_admin_input,
    broadcast_command,
    broadcasts_command,
    broadcast_action_handler,
    handle_broadcast_edit_input,
    admin_manage_mode,
    admin_edit_panel,
    admin_rename_click,
    admin_reorder_execute,
    admin_edit_back_click,
    admin_add_skip_credits,
    admin_color_click,
    admin_set_color_execute,
    admin_add_style_selection,
    admin_edit_link_click,
    admin_move_to_click,
    admin_move_to_nav,
    admin_move_to_select,
    admin_copy_click,
    admin_copy_nav,
    admin_copy_execute,
    admin_add_more_file,
    admin_finish_files,
    admin_source_manage,
    admin_source_replace,
    admin_source_delete,
    admin_source_add,
    admin_edit_credit_click,
    admin_edit_credit_skip,
    users_command,
    users_page_callback
)

# Setup Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ─── Failover Lock ────────────────────────────────────────────────────
LOCK_STALE_SECONDS = 30
HEARTBEAT_INTERVAL = 10
INSTANCE_NAME = os.getenv("BOT_INSTANCE_NAME") or socket.gethostname()
_conflict_detected = False
_application_ref = None  # Set in post_init, used by heartbeat to stop on conflict

async def _try_acquire_lock() -> bool:
    """Try to claim the singleton BotLock on PostgreSQL. Returns True if acquired."""
    async with AsyncSessionPG() as session:
        result = await session.execute(select(BotLock).where(BotLock.id == 1))
        lock = result.scalars().first()
        now = datetime.utcnow()

        if lock is None:
            session.add(BotLock(id=1, instance_name=INSTANCE_NAME, last_heartbeat=now))
            await session.commit()
            return True

        stale = (now - lock.last_heartbeat).total_seconds() > LOCK_STALE_SECONDS
        if lock.instance_name == INSTANCE_NAME or stale:
            lock.instance_name = INSTANCE_NAME
            lock.last_heartbeat = now
            await session.commit()
            return True

        return False

async def _release_lock():
    """Mark the lock as stale so another instance can claim it."""
    async with AsyncSessionPG() as session:
        result = await session.execute(select(BotLock).where(BotLock.id == 1))
        lock = result.scalars().first()
        if lock and lock.instance_name == INSTANCE_NAME:
            lock.last_heartbeat = datetime.utcnow() - timedelta(seconds=3600)
            await session.commit()

async def _heartbeat_loop():
    """Background task: update last_heartbeat on PostgreSQL every HEARTBEAT_INTERVAL seconds."""
    global _conflict_detected
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL)
        if _conflict_detected:
            logger.warning("409 Conflict detected — releasing lock and stopping")
            await _release_lock()
            app = _application_ref
            if app:
                await app.stop()
            return
        try:
            async with AsyncSessionPG() as session:
                result = await session.execute(select(BotLock).where(BotLock.id == 1))
                lock = result.scalars().first()
                if lock and lock.instance_name == INSTANCE_NAME:
                    lock.last_heartbeat = datetime.utcnow()
                    await session.commit()
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")

async def route_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Routes incoming text messages or documents/photos.
    Determines whether it is an admin command flow or a user suggestion flow.
    """
    state = context.user_data.get("admin_state")
    if state:
        if state == "waiting_broadcast_edit":
            await handle_broadcast_edit_input(update, context)
        else:
            await handle_admin_input(update, context)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log exceptions and notify admins directly on Telegram."""
    import traceback
    import html
    import json
    import datetime
    from telegram.error import Conflict as _Conflict
    from config import ADMIN_IDS

    global _conflict_detected
    if isinstance(context.error, _Conflict):
        logger.warning("409 Conflict detected — another instance is active")
        _conflict_detected = True
        return

    logger.error("Exception while handling an update:", exc_info=context.error)

    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)

    try:
        with open("errors.log", "a", encoding="utf-8") as f:
            f.write(f"--- ERROR AT {datetime.datetime.utcnow().isoformat()} ---\n")
            f.write(f"Update: {update}\n")
            f.write(tb_string)
            f.write("\n\n")
    except Exception as log_err:
        logger.error(f"Failed to write to errors.log: {log_err}")

    # Format Telegram traceback message
    update_dict = update.to_dict() if hasattr(update, "to_dict") else str(update)
    update_str = html.escape(json.dumps(update_dict, indent=2, ensure_ascii=False))
    tb_escaped = html.escape(tb_string)

    error_msg = (
        f"⚠️ <b>An Exception Occurred!</b>\n\n"
        f"<b>Update:</b>\n<pre><code class=\"language-json\">{update_str[:1000]}</code></pre>\n\n"
        f"<b>Traceback:</b>\n<pre><code class=\"language-python\">{tb_escaped[:2500]}</code></pre>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=error_msg,
                parse_mode=ParseMode.HTML
            )
        except Exception as notify_err:
            logger.error(f"Failed to send error notification to admin {admin_id}: {notify_err}")

async def post_init(application: Application):
    """Initialize DB, acquire lock, sync from PG, start heartbeat, set commands."""
    global _application_ref, _conflict_detected
    _application_ref = application
    _conflict_detected = False

    await init_db()
    logger.info("Database initialized successfully.")

    # ── Acquire failover lock ──
    logger.info(f"Instance: {INSTANCE_NAME} — acquiring failover lock...")
    while True:
        acquired = await _try_acquire_lock()
        if acquired:
            logger.info("Lock acquired — this instance is now active")
            break
        logger.info("Lock held by another instance — retrying in 10s...")
        await asyncio.sleep(10)

    # ── Sync from PostgreSQL → SQLite ──
    logger.info("Pulling data from PostgreSQL into local SQLite...")
    await pull_from_postgres()
    logger.info("Sync complete")

    # ── Start heartbeat ──
    asyncio.create_task(_heartbeat_loop())
    logger.info("Heartbeat task started")

    # Register Bot Commands
    logger.info("Setting bot command autocomplete menu...")
    commands = [
        BotCommand("start", "Start the bot & open main menu"),
        BotCommand("admin", "Open admin staging editor"),
        BotCommand("broadcast", "Broadcast message (admin only)"),
        BotCommand("broadcasts", "List & manage sent broadcasts (admin only)"),
        BotCommand("users", "List all registered users (admin only)")
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands set successfully.")

async def post_shutdown(application: Application):
    """Releases the failover lock on shutdown."""
    logger.info("Releasing failover lock...")
    await _release_lock()
    logger.info("Failover lock released.")

def _build_application():
    """Build and return the Application with all handlers registered."""
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("broadcasts", broadcasts_command))
    application.add_handler(CommandHandler("users", users_command))

    application.add_handler(CallbackQueryHandler(user_menu_navigation, pattern=r"^(m|b):"))
    application.add_handler(CallbackQueryHandler(user_link_click, pattern=r"^(d)?l:"))

    application.add_handler(CallbackQueryHandler(admin_menu_navigation, pattern=r"^(dm|db):"))
    application.add_handler(CallbackQueryHandler(admin_add_click, pattern=r"^add:"))
    application.add_handler(CallbackQueryHandler(admin_delete_click, pattern=r"^del:"))
    application.add_handler(CallbackQueryHandler(admin_publish_click, pattern=r"^publish_"))

    application.add_handler(CallbackQueryHandler(admin_manage_mode, pattern=r"^manage:"))
    application.add_handler(CallbackQueryHandler(admin_edit_panel, pattern=r"^edit:"))
    application.add_handler(CallbackQueryHandler(admin_rename_click, pattern=r"^rename:"))
    application.add_handler(CallbackQueryHandler(admin_reorder_execute, pattern=r"^move:"))
    application.add_handler(CallbackQueryHandler(admin_edit_back_click, pattern=r"^(back_edit|cancel_color):"))
    application.add_handler(CallbackQueryHandler(admin_add_skip_credits, pattern=r"^add_no_credits$"))
    application.add_handler(CallbackQueryHandler(admin_color_click, pattern=r"^color:"))
    application.add_handler(CallbackQueryHandler(admin_set_color_execute, pattern=r"^set_color:"))
    application.add_handler(CallbackQueryHandler(admin_add_style_selection, pattern=r"^add_style:"))
    application.add_handler(CallbackQueryHandler(admin_edit_link_click, pattern=r"^edit_link:"))

    application.add_handler(CallbackQueryHandler(admin_move_to_click, pattern=r"^parent_sel:"))
    application.add_handler(CallbackQueryHandler(admin_move_to_nav, pattern=r"^parent_nav:"))
    application.add_handler(CallbackQueryHandler(admin_move_to_select, pattern=r"^parent_pick:"))

    application.add_handler(CallbackQueryHandler(admin_copy_click, pattern=r"^copy_sel:"))
    application.add_handler(CallbackQueryHandler(admin_copy_nav, pattern=r"^copy_nav:"))
    application.add_handler(CallbackQueryHandler(admin_copy_execute, pattern=r"^copy_pick:"))

    application.add_handler(CallbackQueryHandler(admin_add_more_file, pattern=r"^add_more_file$"))
    application.add_handler(CallbackQueryHandler(admin_finish_files, pattern=r"^add_finish_files$"))

    application.add_handler(CallbackQueryHandler(admin_source_manage, pattern=r"^src_manage:"))
    application.add_handler(CallbackQueryHandler(admin_source_replace, pattern=r"^src_replace:"))
    application.add_handler(CallbackQueryHandler(admin_source_delete, pattern=r"^src_del:"))
    application.add_handler(CallbackQueryHandler(admin_source_add, pattern=r"^src_add:"))

    application.add_handler(CallbackQueryHandler(admin_edit_credit_click, pattern=r"^edit_credit:"))
    application.add_handler(CallbackQueryHandler(admin_edit_credit_skip, pattern=r"^edit_credit_skip:"))

    application.add_handler(CallbackQueryHandler(broadcast_action_handler, pattern=r"^(bedit|bdelete|bdel_yes|bdel_cancel):"))

    application.add_handler(CallbackQueryHandler(users_page_callback, pattern=r"^users_page:"))

    application.add_handler(
        MessageHandler(
            filters.ALL & (~filters.COMMAND),
            route_all_messages
        )
    )

    application.add_error_handler(error_handler)
    return application

def main():
    """Starts the Telegram bot with failover support."""
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN is missing! Please set it in your environment or .env file.")
        sys.exit(1)

    while True:
        logger.info("Starting Telegram Bot Application...")
        application = _build_application()

        try:
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as e:
            logger.error(f"Application stopped with error: {e}")

        if _conflict_detected:
            logger.info("Standby — lock released due to conflict, retrying in 10s...")
            asyncio.run(asyncio.sleep(10))
            continue

        logger.info("Bot stopped. Exiting.")
        break

if __name__ == "__main__":
    main()
