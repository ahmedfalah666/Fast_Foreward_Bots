import logging
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select
from db import AsyncSessionLocal, User, DraftMenuButton, ProductionMenuButton
from keyboards import build_menu_keyboard

logger = logging.getLogger(__name__)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the /start command. Registers the user and shows the main menu.
    """
    user_id = update.effective_user.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name
    
    # 1. Register User in Database
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).filter_by(user_id=user_id))
        db_user = result.scalars().first()
        
        if not db_user:
            db_user = User(user_id=user_id, username=username, first_name=first_name)
            session.add(db_user)
            await session.commit()
            
    # 2. Render Main Menu
    keyboard = await build_menu_keyboard(None, is_draft=False)
    welcome_text = (
        "✦ ─── ⚜️ ─── ✦\n"
        f"Hello {first_name}! Welcome to the Fast Forward directory.\n\n"
        "Explore categories and access resource files below:"
    )
    await update.message.reply_text(welcome_text, reply_markup=keyboard)

async def user_menu_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles user navigation buttons (m:<id> and b:<parent_id>).
    """
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    # Parse parent_id
    if data.startswith("b:"):
        parent_raw = data.split(":")[1]
        parent_id = None if parent_raw == "root" else int(parent_raw)
    elif data.startswith("m:"):
        parent_id = int(data.split(":")[1])
    else:
        return
        
    keyboard = await build_menu_keyboard(parent_id, is_draft=False)
    
    # Get menu title
    menu_title = "Main Menu"
    if parent_id is not None:
        async with AsyncSessionLocal() as session:
            stmt = select(ProductionMenuButton).where(ProductionMenuButton.id == parent_id)
            res = await session.execute(stmt)
            btn = res.scalars().first()
            if btn:
                menu_title = btn.title
                
    text = (
        "✦ ─── ⚜️ ─── ✦\n"
        f"*{menu_title.upper()}*\n\n"
        "Navigate subcategories or download resources below:"
    )
    await query.edit_message_text(text=text, reply_markup=keyboard)

async def user_link_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles link button clicks.
    - l:<id> = production link (user facing)
    - dl:<id> = draft link (admin testing from staging)
    """
    query = update.callback_query
    await query.answer()
    
    is_draft = query.data.startswith("dl:")
    btn_id = int(query.data.split(":")[1])
    user_id = update.effective_user.id
    
    # Show loading state on the menu message
    try:
        await query.edit_message_text("⏳ Fetching material, please wait...")
    except Exception:
        pass
    
    Table = DraftMenuButton if is_draft else ProductionMenuButton
    async with AsyncSessionLocal() as session:
        stmt = select(Table).where(Table.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()
        
    if not btn or not btn.source_chat_id or not btn.source_message_id:
        await query.edit_message_text("❌ Error: Material source not found for this button.")
        return
        
    try:
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=btn.source_chat_id,
            message_id=btn.source_message_id
        )
        
        if btn.credit_text:
            credits_text = f"ℹ️ *Source/Credits:* {btn.credit_text}"
            await context.bot.send_message(
                chat_id=user_id,
                text=credits_text,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        
        await query.edit_message_text(
            f"✅ *{btn.title}* sent successfully!\n\nTap a button below to continue:",
            reply_markup=await build_menu_keyboard(btn.parent_id, is_draft=is_draft)
        )
    except Exception as e:
        await query.edit_message_text(
            f"❌ Could not deliver this file.\n{str(e)[:100]}",
            reply_markup=await build_menu_keyboard(btn.parent_id, is_draft=is_draft) if btn else None
        )
        logger.error(f"Failed to deliver file for button {btn_id} (source {btn.source_chat_id}/{btn.source_message_id}): {e}")


