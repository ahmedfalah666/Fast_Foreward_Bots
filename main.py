import logging
import sys
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
from db import init_db
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
    admin_edit_link_click
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
    from config import ADMIN_IDS

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
    """Initializes the database tables and sets bot commands autocomplete list at startup."""
    logger.info("Initializing database tables...")
    await init_db()
    logger.info("Database initialized successfully.")
    
    # Register Bot Commands
    logger.info("Setting bot command autocomplete menu...")
    commands = [
        BotCommand("start", "Start the bot & open main menu"),
        BotCommand("admin", "Open admin staging editor"),
        BotCommand("broadcast", "Broadcast message (admin only)"),
        BotCommand("broadcasts", "List & manage sent broadcasts (admin only)")
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Bot commands set successfully.")

def main():
    """Starts the Telegram bot."""
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN is missing! Please set it in your environment or .env file.")
        sys.exit(1)
        
    logger.info("Starting Telegram Bot Application...")
    
    # Initialize application
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Register Commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("broadcasts", broadcasts_command))
    
    # Register Production (User) Callback Handlers
    application.add_handler(CallbackQueryHandler(user_menu_navigation, pattern=r"^(m|b):"))
    application.add_handler(CallbackQueryHandler(user_link_click, pattern=r"^(d)?l:"))
    
    # Register Staging (Admin) Callback Handlers
    application.add_handler(CallbackQueryHandler(admin_menu_navigation, pattern=r"^(dm|db):"))
    application.add_handler(CallbackQueryHandler(admin_add_click, pattern=r"^add:"))
    application.add_handler(CallbackQueryHandler(admin_delete_click, pattern=r"^del:"))
    application.add_handler(CallbackQueryHandler(admin_publish_click, pattern=r"^publish_"))
    
    # New Admin Management Handlers
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
    
    # Broadcast management handlers
    application.add_handler(CallbackQueryHandler(broadcast_action_handler, pattern=r"^(bedit|bdelete|bdel_yes|bdel_cancel):"))
    
    # Register generic message handler for text/file inputs
    application.add_handler(
        MessageHandler(
            filters.ALL & (~filters.COMMAND),
            route_all_messages
        )
    )
    
    # Register global error handler
    application.add_error_handler(error_handler)
    
    # Start Polling loop
    logger.info("Bot is polling. Press Ctrl+C to stop.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
