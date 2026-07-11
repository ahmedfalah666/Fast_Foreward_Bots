import re
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, MessageOriginChannel, MessageOriginChat
from telegram.error import BadRequest, TelegramError
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes
from sqlalchemy import select, delete
from db import AsyncSessionLocal, User, DraftMenuButton, ProductionMenuButton, Broadcast, BroadcastRecipient, sync_draft_to_production, push_table_to_postgres
from keyboards import build_menu_keyboard, build_button_edit_keyboard, build_color_picker_keyboard, build_parent_selector_keyboard, build_copy_source_keyboard, build_sources_manage_keyboard
from config import ADMIN_IDS, STORAGE_CHANNEL_ID

async def check_admin(user_id: int) -> bool:
    """Helper to check if user has admin privileges."""
    if user_id in ADMIN_IDS:
        return True
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).filter_by(user_id=user_id))
        user = result.scalars().first()
        return user.is_admin if user else False

async def show_staging_menu(message_or_query, parent_id: int | None):
    """Renders and sends/updates the staging navigation editor menu."""
    keyboard = await build_menu_keyboard(parent_id, is_draft=True)
    menu_title = "Main Menu"
    if parent_id is not None:
        async with AsyncSessionLocal() as session:
            stmt = select(DraftMenuButton).where(DraftMenuButton.id == parent_id)
            res = await session.execute(stmt)
            btn = res.scalars().first()
            if btn:
                menu_title = btn.title
                
    text = (
        "✦ ─── ⚜️ ─── ✦\n"
        f"*WORKSPACE EDITOR: {menu_title}*\n\n"
        "Configure categories, add materials, or publish staging changes live:"
    )
    if hasattr(message_or_query, "edit_message_text"):
        try:
            await message_or_query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="Markdown")
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                pass
            else:
                raise
    else:
        await message_or_query.reply_text(text=text, reply_markup=keyboard, parse_mode="Markdown")

async def show_admin_manage_mode(query, parent_id: int | None):
    """Renders the staging list in management/edit mode."""
    keyboard = await build_menu_keyboard(parent_id, is_draft=True, is_manage_mode=True)
    menu_title = "Main Menu"
    if parent_id is not None:
        async with AsyncSessionLocal() as session:
            stmt = select(DraftMenuButton).where(DraftMenuButton.id == parent_id)
            res = await session.execute(stmt)
            btn = res.scalars().first()
            if btn:
                menu_title = btn.title
                
    text = (
        "✦ ─── ⚜️ ─── ✦\n"
        f"*MANAGEMENT DIRECTORY: {menu_title}*\n\n"
        "Select any button below to open its dedicated editor control panel:"
    )
    try:
        await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="Markdown")
    except BadRequest as e:
        if "message is not modified" in str(e).lower():
            pass
        else:
            raise

async def show_admin_edit_panel(message_or_query, btn_id: int):
    """Renders the control panel details for a specific button."""
    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()
        
    if not btn:
        if hasattr(message_or_query, "edit_message_text"):
            await message_or_query.edit_message_text("❌ Button not found.")
        else:
            await message_or_query.reply_text("❌ Button not found.")
        return
        
    keyboard = await build_button_edit_keyboard(btn_id)
    style_display = btn.button_style.upper() if btn.button_style else "DEFAULT (GRAY)"
    safe_title = escape_markdown(btn.title or "", version=1)
    
    if btn.button_type == "menu":
        dest_display = "Submenu Directory Folder"
    elif btn.button_type == "link":
        extra = json.loads(btn.extra_sources) if btn.extra_sources else []
        total = 1 + len(extra)
        dest_display = f"{total} file(s) — Primary ID: {btn.source_message_id}"
    else:
        dest_display = f"Redirect URL: {btn.credit_text}"
        
    details_text = (
        "✦ ─── ⚜️ ─── ✦\n"
        "*BUTTON CONTROL PANEL*\n\n"
        f"• *Label:* {safe_title}\n"
        f"• *Type:* `{btn.button_type.upper()}`\n"
        f"• *Background Color:* `{style_display}`\n"
        f"• *Destination:* `{dest_display}`\n"
    )
    if btn.button_type == "link":
        credits_display = btn.credit_text if btn.credit_text else "None"
        details_text += f"• *Attached Credits:* `{credits_display}`\n"
        
    if hasattr(message_or_query, "edit_message_text"):
        try:
            await message_or_query.edit_message_text(text=details_text, reply_markup=keyboard, parse_mode="Markdown")
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                pass  # Silent ignore
            else:
                raise
    else:
        await message_or_query.reply_text(text=details_text, reply_markup=keyboard, parse_mode="Markdown")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles the /admin command. Checks permissions and launches the draft editor.
    """
    user_id = update.effective_user.id
    if not await check_admin(user_id):
        await update.message.reply_text("❌ Permission Denied. Admins only.")
        return
        
    await show_staging_menu(update.message, None)

async def admin_menu_navigation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles admin navigation in draft mode (dm:<id> and db:<parent_id>).
    """
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("db:"):
        parent_raw = data.split(":")[1]
        parent_id = None if parent_raw == "root" else int(parent_raw)
    elif data.startswith("dm:"):
        parent_id = int(data.split(":")[1])
    else:
        return
        
    await show_staging_menu(query, parent_id)

async def admin_manage_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Switches the editor to Management Mode (manage:<parent_id>).
    """
    query = update.callback_query
    await query.answer()
    
    parent_raw = query.data.split(":")[1]
    parent_id = None if parent_raw == "root" else int(parent_raw)
    
    await show_admin_manage_mode(query, parent_id)

async def admin_edit_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Opens the edit panel for a specific button (edit:<btn_id>).
    """
    query = update.callback_query
    await query.answer()
    
    btn_id = int(query.data.split(":")[1])
    await show_admin_edit_panel(query, btn_id)

async def _safe_reply(query, text, parse_mode=None, reply_markup=None):
    """Reply to callback query, falling back to DM if message was deleted."""
    if query.message:
        await query.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    else:
        await query.get_bot().send_message(
            chat_id=query.from_user.id, text=text, parse_mode=parse_mode, reply_markup=reply_markup
        )

async def admin_rename_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggers renaming flow (rename:<btn_id>).
    """
    query = update.callback_query
    await query.answer()
    
    btn_id = int(query.data.split(":")[1])
    
    context.user_data["admin_state"] = "waiting_rename"
    context.user_data["edit_btn_id"] = btn_id
    
    await _safe_reply(query, "✏️ Please send the new Title for the button:")

async def admin_edit_link_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Triggers changing destination target flow (edit_link:<btn_id>).
    """
    query = update.callback_query
    await query.answer()
    
    btn_id = int(query.data.split(":")[1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()
        
    if not btn:
        await _safe_reply(query, "❌ Button not found.")
        return
        
    context.user_data["admin_state"] = "waiting_edit_link"
    context.user_data["edit_btn_id"] = btn_id
    
    if btn.button_type == "link":
        await _safe_reply(
            query,
            "📂 Please *Forward* the new target message/file from your storage channel, "
            "or send the text/file directly to the bot:",
            parse_mode="Markdown"
        )
    elif btn.button_type in ("fb", "feedback"):
        await _safe_reply(
            query,
            "📝 Please send the new *Telegram Link or Username* of the feedback bot (e.g. `@your_feedback_bot` or `https://t.me/your_feedback_bot`):",
            parse_mode="Markdown"
        )

async def admin_color_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Opens background style color picker panel (color:<btn_id>).
    """
    query = update.callback_query
    await query.answer()
    
    btn_id = int(query.data.split(":")[1])
    
    keyboard = await build_color_picker_keyboard(btn_id)
    await query.edit_message_text(
        text="🎨 Choose a background color style for this button:",
        reply_markup=keyboard
    )

async def admin_set_color_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Updates the button's background style color in the DB (set_color:<btn_id>:<style_or_none>).
    """
    query = update.callback_query
    await query.answer("Updating style...")
    
    parts = query.data.split(":")
    btn_id = int(parts[1])
    style = parts[2]
    
    button_style = None if style == "none" else style
    
    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()
        if btn:
            btn.button_style = button_style
            await session.commit()
            await push_table_to_postgres(DraftMenuButton)
            
    await show_admin_edit_panel(query, btn_id)

async def admin_reorder_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles moving buttons up or down in sorting order (move:up:<btn_id> / move:down:<btn_id>).
    """
    query = update.callback_query
    await query.answer("Reordering...")
    
    parts = query.data.split(":")
    direction = parts[1]  # 'up' or 'down'
    btn_id = int(parts[2])
    
    parent_id = None
    reordered = False

    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()
        
        if not btn:
            await _safe_reply(query, "❌ Button not found.")
            return
            
        parent_id = btn.parent_id
        stmt_siblings = select(DraftMenuButton).where(DraftMenuButton.parent_id == parent_id).order_by(DraftMenuButton.order_index)
        res_siblings = await session.execute(stmt_siblings)
        siblings = res_siblings.scalars().all()
        
        for idx, s in enumerate(siblings):
            s.order_index = idx
            
        active_idx = next(i for i, s in enumerate(siblings) if s.id == btn_id)
        
        if direction == "up" and active_idx > 0:
            siblings[active_idx].order_index = active_idx - 1
            siblings[active_idx - 1].order_index = active_idx
            await session.commit()
            await push_table_to_postgres(DraftMenuButton)
            reordered = True
        elif direction == "down" and active_idx < len(siblings) - 1:
            siblings[active_idx].order_index = active_idx + 1
            siblings[active_idx + 1].order_index = active_idx
            await session.commit()
            await push_table_to_postgres(DraftMenuButton)
            reordered = True

    if reordered:
        await show_admin_manage_mode(query, parent_id)
    else:
        await query.answer("↕️ Already at the edge")

async def admin_edit_back_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Returns to list from edit panel (back_edit:<btn_id> / cancel_color:<btn_id>).
    """
    query = update.callback_query
    await query.answer()
    
    btn_id = int(query.data.split(":")[1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()
        parent_id = btn.parent_id if btn else None
        
    await show_admin_manage_mode(query, parent_id)

async def admin_add_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles clicks on dynamic add button buttons.
    """
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split(":")
    btn_type = parts[1]  # 'menu', 'link', 'fb'
    parent_raw = parts[2]
    parent_id = None if parent_raw == "root" else int(parent_raw)
    
    context.user_data["admin_state"] = "waiting_for_title"
    context.user_data["add_parent_id"] = parent_id
    context.user_data["add_button_type"] = btn_type
    
    type_name = "Submenu Folder" if btn_type == "menu" else "Link Button" if btn_type == "link" else "Feedback Button"
    
    await _safe_reply(
        query,
        f"➕ Adding a new *{type_name}*\n\n"
        "Please send the *Title* you want to appear on the button:",
        parse_mode="Markdown"
    )

def _parse_message_link(text: str) -> tuple[str | int | None, int | None]:
    """Parse a Telegram message link into (chat_id_or_username, message_id).
    
    Returns (chat_id, message_id) on success, (None, None) otherwise.
    For public links returns (username_str, message_id) — caller resolves to chat_id.
    
    Supports:
      - t.me/username/1234  or  https://t.me/username/1234
      - t.me/c/123456789/1234  or  https://t.me/c/123456789/1234
    """
    text = text.strip()
    
    # Private channel: t.me/c/123456789/1234
    m = re.match(r"(?:https?://)?t\.me/c/(\d+)/(\d+)", text)
    if m:
        chat_id = int(f"-100{m.group(1)}")
        return chat_id, int(m.group(2))
    
    # Public channel/user: t.me/username/1234
    m2 = re.match(r"(?:https?://)?t\.me/([a-zA-Z0-9_]{5,})/(\d+)", text)
    if m2:
        return m2.group(1), int(m2.group(2))
    
    return None, None

async def _resolve_source_for_link(update, context):
    """
    Resolves (source_chat_id, source_message_id) for a link button.
    Always stores content in the storage channel for reliable delivery.

    Priority:
    0. Admin pastes a Telegram message link → resolve and copy from source
    1. Forward origin (MessageOriginChannel / MessageOriginChat)
       → copy original content directly from source chat to storage channel
    2. Admin's own message text/file
       → copy from admin DM to storage channel

    Returns (chat_id, message_id) on success, (None, None) on failure.
    """
    # Priority 0: admin sent a Telegram message link as text
    if update.message.text and STORAGE_CHANNEL_ID:
        from_chat_id, from_msg_id = _parse_message_link(update.message.text)
        if from_msg_id is not None:
            # If it's a public link (username string), resolve to chat_id
            if isinstance(from_chat_id, str):
                try:
                    chat = await context.bot.get_chat(f"@{from_chat_id}")
                    from_chat_id = chat.id
                except Exception as e:
                    await update.message.reply_text(
                        f"❌ Could not resolve channel @{from_chat_id}: {str(e)}"
                    )
                    return None, None

            # If the linked message is already in the storage channel, no copy needed
            if from_chat_id == STORAGE_CHANNEL_ID:
                return STORAGE_CHANNEL_ID, from_msg_id

            try:
                copied = await context.bot.copy_message(
                    chat_id=STORAGE_CHANNEL_ID,
                    from_chat_id=from_chat_id,
                    message_id=from_msg_id
                )
                return STORAGE_CHANNEL_ID, copied.message_id
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Could not copy from the linked message: {str(e)}\n"
                    "Make sure the bot has access to the source channel."
                )
                return None, None

    # Priority 1: forwarded from a channel or group
    if update.message.forward_origin and STORAGE_CHANNEL_ID:
        origin = update.message.forward_origin
        from_chat_id = None
        from_msg_id = None
        if isinstance(origin, MessageOriginChannel):
            from_chat_id = origin.chat.id
            from_msg_id = getattr(origin, 'message_id', None)
        elif isinstance(origin, MessageOriginChat):
            from_chat_id = origin.sender_chat.id
            from_msg_id = getattr(origin, 'message_id', None)

        if from_chat_id and from_msg_id:
            try:
                copied = await context.bot.copy_message(
                    chat_id=STORAGE_CHANNEL_ID,
                    from_chat_id=from_chat_id,
                    message_id=from_msg_id
                )
                return STORAGE_CHANNEL_ID, copied.message_id
            except Exception:
                pass  # fall through to admin-message copy

    # Priority 2: admin sent content directly → copy to storage channel
    if STORAGE_CHANNEL_ID:
        try:
            copied = await context.bot.copy_message(
                chat_id=STORAGE_CHANNEL_ID,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )
            return STORAGE_CHANNEL_ID, copied.message_id
        except Exception as e:
            await update.message.reply_text(
                f"❌ Failed to copy to storage channel: {str(e)}\n"
                "Make sure STORAGE_CHANNEL_ID is set correctly and the bot is an admin there."
            )
            return None, None

    await update.message.reply_text(
        "❌ No storage channel configured and message wasn't forwarded from a channel.\n"
        "Please forward a message from your channel or set STORAGE_CHANNEL_ID."
    )
    return None, None

def _normalize_telegram_link(link: str) -> str:
    """Normalize @username, t.me/xxx, or bare text to https://t.me/ link."""
    normalized = link.strip()
    if normalized.startswith("@"):
        normalized = f"https://t.me/{normalized[1:]}"
    elif not normalized.startswith("http://") and not normalized.startswith("https://") and not normalized.startswith("t.me/"):
        normalized = f"https://t.me/{normalized}"
    elif normalized.startswith("t.me/"):
        normalized = f"https://{normalized}"
    return normalized

async def build_style_selection_keyboard() -> InlineKeyboardMarkup:
    """Builds the background style selection grid for button creation."""
    keyboard = [
        [
            InlineKeyboardButton("⬜ Default (Gray)", callback_data="add_style:none"),
            InlineKeyboardButton("🟦 Blue (Primary)", callback_data="add_style:primary")
        ],
        [
            InlineKeyboardButton("🟩 Green (Success)", callback_data="add_style:success"),
            InlineKeyboardButton("🟥 Red (Danger)", callback_data="add_style:danger")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Processes text/file inputs from admins during button creation or rename flows.
    """
    admin_state = context.user_data.get("admin_state")
    if not admin_state:
        return
        
    user_id = update.effective_user.id
    if not await check_admin(user_id):
        return
        
    if admin_state == "waiting_rename":
        new_title = update.message.text
        if not new_title:
            await update.message.reply_text("❌ Title cannot be empty.")
            return
            
        btn_id = context.user_data["edit_btn_id"]
        async with AsyncSessionLocal() as session:
            stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
            res = await session.execute(stmt)
            btn = res.scalars().first()
            if btn:
                btn.title = new_title
                await session.commit()
                await push_table_to_postgres(DraftMenuButton)
                await update.message.reply_text(f"✅ Button renamed to '{new_title}' successfully!")
                
        context.user_data.clear()
        await show_admin_edit_panel(update.message, btn_id)
        
    elif admin_state == "waiting_edit_link":
        btn_id = context.user_data["edit_btn_id"]
        
        async with AsyncSessionLocal() as session:
            stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
            res = await session.execute(stmt)
            btn = res.scalars().first()
            
            if not btn:
                await update.message.reply_text("❌ Button not found in database.")
                context.user_data.clear()
                return
                
            if btn.button_type == "link":
                chat_id, msg_id = await _resolve_source_for_link(update, context)
                if not chat_id or not msg_id:
                    return  # error already sent by helper

                btn.source_chat_id = chat_id
                btn.source_message_id = msg_id
                await session.commit()
                await push_table_to_postgres(DraftMenuButton)
                await update.message.reply_text("✅ Link button file source updated successfully!")
                
            elif btn.button_type in ("fb", "feedback"):
                link = update.message.text
                if not link:
                    await update.message.reply_text("❌ Link cannot be empty. Please send text.")
                    return
                    
                normalized_link = _normalize_telegram_link(link)
                btn.credit_text = normalized_link
                await session.commit()
                await push_table_to_postgres(DraftMenuButton)
                await update.message.reply_text("✅ Feedback bot link updated successfully!")
                
        context.user_data.clear()
        await show_admin_edit_panel(update.message, btn_id)
        
    elif admin_state == "waiting_for_fb_link":
        link = update.message.text
        if not link:
            await update.message.reply_text("❌ Link/username cannot be empty. Please send text.")
            return
            
        context.user_data["new_btn_credit_text"] = _normalize_telegram_link(link)
        
        # Transition to style picker
        context.user_data["admin_state"] = "waiting_style_selection"
        keyboard = await build_style_selection_keyboard()
        await update.message.reply_text(
            "🎨 Select a background color style for this button:",
            reply_markup=keyboard
        )
        
    elif admin_state == "waiting_for_title":
        title = update.message.text
        if not title:
            await update.message.reply_text("❌ Title cannot be empty. Please send text.")
            return
            
        context.user_data["new_btn_title"] = title
        btn_type = context.user_data["add_button_type"]
        
        if btn_type == "menu":
            # Move to color style picker
            context.user_data["admin_state"] = "waiting_style_selection"
            keyboard = await build_style_selection_keyboard()
            await update.message.reply_text(
                "🎨 Select a background color style for this button:",
                reply_markup=keyboard
            )
        elif btn_type == "fb":
            # Move to feedback link/username prompt
            context.user_data["admin_state"] = "waiting_for_fb_link"
            await update.message.reply_text(
                "📝 Please send the *Telegram Link or Username* of the feedback bot (e.g. `@your_feedback_bot` or `https://t.me/your_feedback_bot`):",
                parse_mode="Markdown"
            )
        elif btn_type == "link":
            # Link buttons need files
            context.user_data["admin_state"] = "waiting_for_file"
            await update.message.reply_text(
                "📂 Great! Now please *Forward* the target message/file from your storage channel, "
                "or send the text/file directly to the bot:",
                parse_mode="Markdown"
            )
            
    elif admin_state == "waiting_for_file":
        chat_id, msg_id = await _resolve_source_for_link(update, context)
        if not chat_id or not msg_id:
            return  # error already sent by helper

        context.user_data["new_btn_chat_id"] = chat_id
        context.user_data["new_btn_message_id"] = msg_id
        context.user_data["extra_sources"] = []
        
        context.user_data["admin_state"] = "add_more_prompt"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Another File", callback_data="add_more_file")],
            [InlineKeyboardButton("✅ Finish — Set Credits", callback_data="add_finish_files")]
        ])
        await update.message.reply_text(
            "✅ File saved! You can add more files or finish and set credits:",
            reply_markup=keyboard
        )
        
    elif admin_state == "waiting_for_more_file":
        chat_id, msg_id = await _resolve_source_for_link(update, context)
        if not chat_id or not msg_id:
            return

        extra_list = context.user_data.setdefault("extra_sources", [])
        extra_list.append({"chat_id": chat_id, "message_id": msg_id})

        context.user_data["admin_state"] = "add_more_prompt"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Another File", callback_data="add_more_file")],
            [InlineKeyboardButton("✅ Finish — Set Credits", callback_data="add_finish_files")]
        ])
        await update.message.reply_text(
            f"✅ File {len(extra_list) + 1} saved! Add more or finish:",
            reply_markup=keyboard
        )

    elif admin_state == "waiting_for_credit":
        text = update.message.text
        context.user_data["new_btn_credit_text"] = text
        
        # Transition to style picker
        context.user_data["admin_state"] = "waiting_style_selection"
        keyboard = await build_style_selection_keyboard()
        await update.message.reply_text(
            "🎨 Select a background color style for this button:",
            reply_markup=keyboard
        )

    elif admin_state == "waiting_replace_primary":
        btn_id = context.user_data["edit_btn_id"]
        chat_id, msg_id = await _resolve_source_for_link(update, context)
        if not chat_id or not msg_id:
            return

        async with AsyncSessionLocal() as session:
            stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
            res = await session.execute(stmt)
            btn = res.scalars().first()
            if btn:
                btn.source_chat_id = chat_id
                btn.source_message_id = msg_id
                await session.commit()
                await push_table_to_postgres(DraftMenuButton)

        context.user_data.clear()
        await update.message.reply_text("✅ Primary source replaced!")
        async with AsyncSessionLocal() as session:
            stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
            res = await session.execute(stmt)
            btn = res.scalars().first()
        if btn:
            extra = json.loads(btn.extra_sources) if btn.extra_sources else []
            keyboard = await build_sources_manage_keyboard(btn_id)
            await update.message.reply_text(
                f"📂 *Sources for:* {escape_markdown(btn.title or '', version=1)}\n\n"
                f"Primary: 1 file\n"
                f"Extra: {len(extra)} file(s)\n\n"
                "Tap a source to replace/delete it, or add a new one:",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )

    elif admin_state == "waiting_add_source":
        btn_id = context.user_data["edit_btn_id"]
        chat_id, msg_id = await _resolve_source_for_link(update, context)
        if not chat_id or not msg_id:
            return

        async with AsyncSessionLocal() as session:
            stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
            res = await session.execute(stmt)
            btn = res.scalars().first()
            if btn:
                extra = json.loads(btn.extra_sources) if btn.extra_sources else []
                extra.append({"chat_id": chat_id, "message_id": msg_id})
                btn.extra_sources = json.dumps(extra)
                await session.commit()
                await push_table_to_postgres(DraftMenuButton)

        context.user_data.clear()
        await update.message.reply_text("✅ Extra source added!")
        async with AsyncSessionLocal() as session:
            stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
            res = await session.execute(stmt)
            btn = res.scalars().first()
        if btn:
            extra = json.loads(btn.extra_sources) if btn.extra_sources else []
            keyboard = await build_sources_manage_keyboard(btn_id)
            await update.message.reply_text(
                f"📂 *Sources for:* {escape_markdown(btn.title or '', version=1)}\n\n"
                f"Primary: 1 file\n"
                f"Extra: {len(extra)} file(s)\n\n"
                "Tap a source to replace/delete it, or add a new one:",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )

    elif admin_state == "waiting_edit_credit":
        btn_id = context.user_data["edit_btn_id"]
        text = update.message.text

        async with AsyncSessionLocal() as session:
            stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
            res = await session.execute(stmt)
            btn = res.scalars().first()
            if btn:
                btn.credit_text = text
                await session.commit()
                await push_table_to_postgres(DraftMenuButton)

        context.user_data.clear()
        await update.message.reply_text("✅ Credits updated!")
        await show_admin_edit_panel(update.message, btn_id)

async def admin_add_skip_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'No Credits' callback click during adding."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    if not await check_admin(user_id):
        return
        
    context.user_data["new_btn_credit_text"] = None
    
    # Transition to style picker
    context.user_data["admin_state"] = "waiting_style_selection"
    keyboard = await build_style_selection_keyboard()
    await _safe_reply(
        query,
        "🎨 Select a background color style for this button:",
        reply_markup=keyboard
    )

async def admin_edit_credit_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'Skip / Keep Current' callback during credit editing."""
    query = update.callback_query
    await query.answer("Keeping current credits.")
    btn_id = int(query.data.split(":")[1])
    context.user_data.clear()
    await show_admin_edit_panel(query, btn_id)

async def admin_add_style_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles style background color selection and saves the new button."""
    query = update.callback_query
    await query.answer("Saving button...")
    
    user_id = update.effective_user.id
    if not await check_admin(user_id):
        return
        
    style = query.data.split(":")[1]
    button_style = None if style == "none" else style
    context.user_data["new_btn_style"] = button_style
    
    parent_id = context.user_data["add_parent_id"]
    title = context.user_data["new_btn_title"]
    
    await save_button_to_db(context)
    await _safe_reply(query, f"✅ Button '{title}' added successfully!")
    context.user_data.clear()
    
    await show_staging_menu(query, parent_id)

async def save_button_to_db(context: ContextTypes.DEFAULT_TYPE):
    """Saves button creation data from user_data directly into draft_menu_buttons."""
    parent_id = context.user_data["add_parent_id"]
    btn_type = context.user_data["add_button_type"]
    title = context.user_data["new_btn_title"]
    
    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.parent_id == parent_id)
        res = await session.execute(stmt)
        sibling_buttons = res.scalars().all()
        next_order = len(sibling_buttons)
        
        extra_list = context.user_data.get("extra_sources", [])
        extra_json = json.dumps(extra_list) if extra_list else None

        new_btn = DraftMenuButton(
            parent_id=parent_id,
            title=title,
            button_type=btn_type,
            order_index=next_order,
            button_style=context.user_data.get("new_btn_style"),
            source_chat_id=context.user_data.get("new_btn_chat_id"),
            source_message_id=context.user_data.get("new_btn_message_id"),
            credit_text=context.user_data.get("new_btn_credit_text"),
            extra_sources=extra_json
        )
        session.add(new_btn)
        await session.commit()
        await push_table_to_postgres(DraftMenuButton)

async def admin_delete_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles deletion of menu buttons in staging (del:<id>).
    """
    query = update.callback_query
    await query.answer("Deleting button...")
    
    btn_id = int(query.data.split(":")[1])
    
    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()
        
        if btn:
            parent_id = btn.parent_id
            await session.delete(btn)
            await session.commit()
            await push_table_to_postgres(DraftMenuButton)
            
            # Reorder remaining siblings
            stmt_siblings = select(DraftMenuButton).where(DraftMenuButton.parent_id == parent_id).order_by(DraftMenuButton.order_index)
            res_siblings = await session.execute(stmt_siblings)
            siblings = res_siblings.scalars().all()
            for idx, s in enumerate(siblings):
                s.order_index = idx
            await session.commit()
            await push_table_to_postgres(DraftMenuButton)
            
            await show_admin_manage_mode(query, parent_id)
        else:
            await _safe_reply(query, "❌ Button not found in database.")

async def _get_descendant_ids(session, parent_id: int) -> set[int]:
    """Recursively collect all descendant button IDs under a menu button."""
    stmt = select(DraftMenuButton).where(DraftMenuButton.parent_id == parent_id)
    res = await session.execute(stmt)
    children = res.scalars().all()
    ids = set()
    for child in children:
        ids.add(child.id)
        ids.update(await _get_descendant_ids(session, child.id))
    return ids

async def admin_move_to_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the parent-selector tree (parent_sel:<btn_id>)."""
    query = update.callback_query
    await query.answer()

    btn_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()

        if not btn:
            await _safe_reply(query, "❌ Button not found.")
            return

        # Build set of excluded IDs (the button itself + all its descendants)
        exclude = {btn_id}
        if btn.button_type == "menu":
            exclude.update(await _get_descendant_ids(session, btn_id))

    keyboard = await build_parent_selector_keyboard(None, btn_id, exclude)
    safe_title = escape_markdown(btn.title or "", version=1)
    await query.edit_message_text(
        f"📂 *Move Button to Another Folder*\n\n"
        f"Button: *{safe_title}*\n"
        f"Type: `{btn.button_type.upper()}`\n\n"
        "Navigate to the destination folder and tap \"Select This Folder\":",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def admin_move_to_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Navigates the parent-selector tree (parent_nav:<parent>:<btn_id>)."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    current_raw = parts[1]
    btn_id = int(parts[2])

    current_parent = None if current_raw == "root" else int(current_raw)

    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()

        if not btn:
            await _safe_reply(query, "❌ Button not found.")
            return

        exclude = {btn_id}
        if btn.button_type == "menu":
            exclude.update(await _get_descendant_ids(session, btn_id))

    keyboard = await build_parent_selector_keyboard(current_parent, btn_id, exclude)

    location = "Main Menu (Root)"
    if current_parent is not None:
        async with AsyncSessionLocal() as session2:
            stmt2 = select(DraftMenuButton).where(DraftMenuButton.id == current_parent)
            res2 = await session2.execute(stmt2)
            parent_btn = res2.scalars().first()
            if parent_btn:
                location = parent_btn.title

    safe_title = escape_markdown(btn.title or "", version=1)
    await query.edit_message_text(
        f"📂 *Move Button — {location}*\n\n"
        f"Button: *{safe_title}*\n\n"
        "Navigate to the destination folder and tap \"Select This Folder\":",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def admin_move_to_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirms the new parent and updates the DB (parent_pick:<parent>:<btn_id>)."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    parent_raw = parts[1]
    btn_id = int(parts[2])

    new_parent = None if parent_raw == "root" else int(parent_raw)

    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()

        if not btn:
            await _safe_reply(query, "❌ Button not found.")
            return

        old_parent = btn.parent_id

        # Safety: no-op if same parent
        if btn.parent_id == new_parent:
            await query.edit_message_text(
                f"⚠️ That button is already in that folder. No change made.",
                parse_mode="Markdown"
            )
            return

        # Move the button: update parent_id and append at end of new sibling list
        stmt_count = select(DraftMenuButton).where(DraftMenuButton.parent_id == new_parent)
        res_count = await session.execute(stmt_count)
        new_siblings = res_count.scalars().all()
        btn.parent_id = new_parent
        btn.order_index = len(new_siblings)

        # Re-index old siblings to close the gap
        stmt_old = select(DraftMenuButton).where(DraftMenuButton.parent_id == old_parent).order_by(DraftMenuButton.order_index)
        res_old = await session.execute(stmt_old)
        old_siblings = res_old.scalars().all()
        for idx, s in enumerate(old_siblings):
            s.order_index = idx

        await session.commit()
        await push_table_to_postgres(DraftMenuButton)

    parent_name = "Main Menu (Root)"
    if new_parent is not None:
        async with AsyncSessionLocal() as session2:
            stmt2 = select(DraftMenuButton).where(DraftMenuButton.id == new_parent)
            res2 = await session2.execute(stmt2)
            p = res2.scalars().first()
            if p:
                parent_name = p.title

    safe_title = escape_markdown(btn.title or "", version=1)
    await query.edit_message_text(
        f"✅ *{safe_title}* moved to *{parent_name}* successfully!",
        parse_mode="Markdown"
    )

    # Return to manage mode at the new parent level
    await show_admin_manage_mode(query, new_parent)

async def admin_copy_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the source-menu selector tree (copy_sel:<target_parent>)."""
    query = update.callback_query
    await query.answer()

    target_raw = query.data.split(":")[1]
    target_parent = None if target_raw == "root" else int(target_raw)

    keyboard = await build_copy_source_keyboard(None, target_parent)
    await query.edit_message_text(
        "📋 *Copy Contents From Another Menu*\n\n"
        "Navigate to the menu you want to copy buttons from and tap \"Select This Folder\":",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def admin_copy_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Navigates the source-selector tree (copy_nav:<source_parent>:<target>)."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    current_raw = parts[1]
    target_raw = parts[2]

    current_parent = None if current_raw == "root" else int(current_raw)
    target_parent = None if target_raw == "root" else int(target_raw)

    keyboard = await build_copy_source_keyboard(current_parent, target_parent)

    location = "Main Menu (Root)"
    if current_parent is not None:
        async with AsyncSessionLocal() as session:
            stmt = select(DraftMenuButton).where(DraftMenuButton.id == current_parent)
            res = await session.execute(stmt)
            p = res.scalars().first()
            if p:
                location = p.title

    await query.edit_message_text(
        f"📋 *Copy Contents — {location}*\n\n"
        "Navigate to the source menu and tap \"Select This Folder\":",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def admin_copy_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Copies all buttons from source menu to target menu (copy_pick:<source>:<target>)."""
    query = update.callback_query
    await query.answer("Copying...")

    parts = query.data.split(":")
    source_raw = parts[1]
    target_raw = parts[2]

    source_parent = None if source_raw == "root" else int(source_raw)
    target_parent = None if target_raw == "root" else int(target_raw)

    if source_parent == target_parent:
        await query.edit_message_text("⚠️ Source and target are the same. No buttons copied.")
        return

    async with AsyncSessionLocal() as session:
        # Fetch source buttons
        stmt = select(DraftMenuButton).where(
            DraftMenuButton.parent_id == source_parent
        ).order_by(DraftMenuButton.order_index)
        res = await session.execute(stmt)
        source_buttons = res.scalars().all()

        if not source_buttons:
            await query.edit_message_text("⚠️ Source menu has no buttons to copy.")
            return

        # Fetch existing target buttons to compute starting order_index
        stmt_t = select(DraftMenuButton).where(
            DraftMenuButton.parent_id == target_parent
        ).order_by(DraftMenuButton.order_index)
        res_t = await session.execute(stmt_t)
        target_buttons = res_t.scalars().all()
        next_order = len(target_buttons)

        # Copy each source button as a new button under target
        for src in source_buttons:
            new_btn = DraftMenuButton(
                parent_id=target_parent,
                title=src.title,
                button_type=src.button_type,
                order_index=next_order,
                button_style=src.button_style,
                source_chat_id=src.source_chat_id,
                source_message_id=src.source_message_id,
                credit_text=src.credit_text
            )
            session.add(new_btn)
            next_order += 1

        await session.commit()
        await push_table_to_postgres(DraftMenuButton)

    source_name = "Main Menu (Root)"
    if source_parent is not None:
        async with AsyncSessionLocal() as session2:
            stmt2 = select(DraftMenuButton).where(DraftMenuButton.id == source_parent)
            res2 = await session2.execute(stmt2)
            s = res2.scalars().first()
            if s:
                source_name = s.title

    await query.edit_message_text(
        f"✅ Successfully copied `{len(source_buttons)}` button(s) from *{source_name}*!",
        parse_mode="Markdown"
    )

    await show_admin_manage_mode(query, target_parent)

# ─── Multi-file add flow callbacks ───────────────────────────────────

async def admin_add_more_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switches to waiting_for_more_file (add_more_file)."""
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "waiting_for_more_file"
    await _safe_reply(query, "📂 Please forward or send the next file:")

async def admin_finish_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Proceeds to credits input (add_finish_files)."""
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "waiting_for_credit"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ No Credits", callback_data="add_no_credits")]
    ])
    await _safe_reply(
        query,
        "📝 Enter credits/source (e.g. '@username' or channel link), or click below to skip:",
        reply_markup=keyboard
    )

# ─── Source management handlers ──────────────────────────────────────

async def admin_source_manage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the sources management view (src_manage:<btn_id>)."""
    query = update.callback_query
    await query.answer()
    btn_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()

    if not btn:
        await _safe_reply(query, "❌ Button not found.")
        return

    extra = json.loads(btn.extra_sources) if btn.extra_sources else []
    keyboard = await build_sources_manage_keyboard(btn_id)

    await query.edit_message_text(
        f"📂 *Sources for:* {escape_markdown(btn.title or '', version=1)}\n\n"
        f"Primary: 1 file\n"
        f"Extra: {len(extra)} file(s)\n\n"
        "Tap a source to replace/delete it, or add a new one:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def admin_edit_credit_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Opens the credits editor (edit_credit:<btn_id>)."""
    query = update.callback_query
    await query.answer()
    btn_id = int(query.data.split(":")[1])

    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()

    if not btn:
        await _safe_reply(query, "❌ Button not found.")
        return

    current = btn.credit_text if btn.credit_text else "None"
    context.user_data["admin_state"] = "waiting_edit_credit"
    context.user_data["edit_btn_id"] = btn_id
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Skip / Keep Current", callback_data=f"edit_credit_skip:{btn_id}")]
    ])
    await _safe_reply(
        query,
        f"📝 Current credits: `{escape_markdown(current, version=1)}`\n\nSend the new credits text (e.g. '@username' or channel link), or click below to keep the current value:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def admin_source_replace(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Replaces the primary source (src_replace:<btn_id>)."""
    query = update.callback_query
    await query.answer()
    btn_id = int(query.data.split(":")[1])

    context.user_data["admin_state"] = "waiting_replace_primary"
    context.user_data["edit_btn_id"] = btn_id
    await _safe_reply(
        query,
        "📂 Forward or send the new primary file to replace Source 1:"
    )

async def admin_source_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes an extra source (src_del:<btn_id>:<index>)."""
    query = update.callback_query
    await query.answer("Deleting source...")
    parts = query.data.split(":")
    btn_id = int(parts[1])
    idx = int(parts[2])

    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()

        if btn and btn.extra_sources:
            extra = json.loads(btn.extra_sources)
            if 0 <= idx < len(extra):
                extra.pop(idx)
                btn.extra_sources = json.dumps(extra) if extra else None
                await session.commit()
                await push_table_to_postgres(DraftMenuButton)

    await admin_source_manage(update, context)

async def admin_source_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts adding an extra source (src_add:<btn_id>)."""
    query = update.callback_query
    await query.answer()
    btn_id = int(query.data.split(":")[1])

    context.user_data["admin_state"] = "waiting_add_source"
    context.user_data["edit_btn_id"] = btn_id
    await _safe_reply(
        query,
        "📂 Forward or send a file to add as an extra source:"
    )

async def admin_publish_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Confirms publishing staging changes to production.
    """
    query = update.callback_query
    await query.answer()
    
    if query.data == "publish_confirm":
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Publish Live", callback_data="publish_execute"),
                InlineKeyboardButton("❌ Cancel", callback_data="db:root")
            ]
        ])
        await query.edit_message_text(
            text="⚠️ *ARE YOU SURE?*\n\nThis will overwrite the current live menu for ALL users with your staging layout.",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    elif query.data == "publish_execute":
        try:
            await sync_draft_to_production()
            await push_table_to_postgres(DraftMenuButton)
            await push_table_to_postgres(ProductionMenuButton)
            await query.edit_message_text("🚀 *LIVE MENU UPDATED SUCCESSFULY!* 🚀\n\nAll users can now see your changes.")
        except Exception as e:
            await query.edit_message_text(f"❌ Synchronization failed: {str(e)}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles `/broadcast <message>` command. Sends and stores broadcast for later edit/delete.
    """
    user_id = update.effective_user.id
    if not await check_admin(user_id):
        await update.message.reply_text("❌ Permission Denied. Admins only.")
        return

    if not context.args:
        await update.message.reply_text("❌ Please specify the message. Usage: `/broadcast <message>`", parse_mode="Markdown")
        return

    broadcast_text = " ".join(context.args)

    async with AsyncSessionLocal() as session:
        stmt = select(User)
        res = await session.execute(stmt)
        users = res.scalars().all()

    success_count = 0
    fail_count = 0
    recipients_data = []

    status_msg = await update.message.reply_text(f"📢 Starting broadcast to {len(users)} users...")

    for u in users:
        try:
            sent = await context.bot.send_message(
                chat_id=u.user_id,
                text=broadcast_text,
                parse_mode="Markdown"
            )
            recipients_data.append({
                "user_id": u.user_id,
                "chat_id": sent.chat_id,
                "message_id": sent.message_id
            })
            success_count += 1
        except Exception:
            fail_count += 1

    # Store in DB
    async with AsyncSessionLocal() as session:
        broadcast = Broadcast(
            text=broadcast_text,
            parse_mode="Markdown",
            sent_count=success_count,
            fail_count=fail_count
        )
        session.add(broadcast)
        await session.flush()

        for r in recipients_data:
            session.add(BroadcastRecipient(
                broadcast_id=broadcast.id,
                user_id=r["user_id"],
                chat_id=r["chat_id"],
                message_id=r["message_id"]
            ))
        await session.commit()
        await push_table_to_postgres(Broadcast)
        await push_table_to_postgres(BroadcastRecipient)

    await status_msg.edit_text(
        f"✅ *Broadcast Complete!*\n\n"
        f"📈 Sent successfully: `{success_count}`\n"
        f"📉 Failed/Blocked: `{fail_count}`\n"
        f"🆔 Broadcast ID: `{broadcast.id}`",
        parse_mode="Markdown"
    )

async def broadcasts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists recent broadcasts with edit/delete buttons (/broadcasts)."""
    user_id = update.effective_user.id
    if not await check_admin(user_id):
        await update.message.reply_text("❌ Permission Denied. Admins only.")
        return

    async with AsyncSessionLocal() as session:
        stmt = select(Broadcast).order_by(Broadcast.sent_at.desc()).limit(10)
        res = await session.execute(stmt)
        broadcasts = res.scalars().all()

    if not broadcasts:
        await update.message.reply_text("📭 No broadcasts sent yet.")
        return

    text = "📢 *Sent Broadcasts (last 10):*\n\n"
    for b in broadcasts:
        preview = b.text[:50] + "..." if len(b.text) > 50 else b.text
        text += (
            f"`#{b.id}` {preview}\n"
            f"   ✓ {b.sent_count} sent, ✗ {b.fail_count} failed, "
            f"{b.sent_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        )

    keyboard = []
    for b in broadcasts:
        keyboard.append([
            InlineKeyboardButton(f"✏️ Edit #{b.id}", callback_data=f"bedit:{b.id}"),
            InlineKeyboardButton(f"🗑 Delete #{b.id}", callback_data=f"bdelete:{b.id}")
        ])

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def broadcast_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles broadcast edit/delete button clicks."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    if not await check_admin(user_id):
        return

    action, broadcast_id_str = query.data.split(":")
    broadcast_id = int(broadcast_id_str)

    async with AsyncSessionLocal() as session:
        stmt = select(Broadcast).where(Broadcast.id == broadcast_id)
        res = await session.execute(stmt)
        broadcast = res.scalars().first()

    if not broadcast:
        await query.edit_message_text("❌ Broadcast not found.")
        return

    if action == "bdelete":
        # Confirm delete
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Delete", callback_data=f"bdel_yes:{broadcast_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="bdel_cancel")
            ]
        ])
        preview = broadcast.text[:100] + "..." if len(broadcast.text) > 100 else broadcast.text
        await query.edit_message_text(
            f"⚠️ *Delete Broadcast #{broadcast_id}?*\n\n"
            f"Content: _{preview}_\n\n"
            f"This will remove the message from all {broadcast.sent_count} recipients.",
            reply_markup=keyboard, parse_mode="Markdown"
        )

    elif action == "bdel_yes":
        await _delete_broadcast(broadcast_id, query, context)

    elif action == "bdel_cancel":
        await query.edit_message_text("❌ Deletion cancelled.")

    elif action == "bedit":
        context.user_data["admin_state"] = "waiting_broadcast_edit"
        context.user_data["edit_broadcast_id"] = broadcast_id
        await _safe_reply(query, f"✏️ Send the new text for broadcast `#{broadcast_id}`:", parse_mode="Markdown")

async def _delete_broadcast(broadcast_id: int, query, context):
    """Deletes broadcast messages from all recipients."""
    await query.edit_message_text("🗑 Deleting broadcast messages...")

    async with AsyncSessionLocal() as session:
        stmt = select(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id)
        res = await session.execute(stmt)
        recipients = res.scalars().all()

        b_stmt = select(Broadcast).where(Broadcast.id == broadcast_id)
        b_res = await session.execute(b_stmt)
        broadcast = b_res.scalars().first()

    removed = 0
    failed = 0
    for r in recipients:
        try:
            await context.bot.delete_message(chat_id=r.chat_id, message_id=r.message_id)
            removed += 1
        except Exception:
            failed += 1

    async with AsyncSessionLocal() as session:
        stmt = select(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id)
        res = await session.execute(stmt)
        for r in res.scalars().all():
            await session.delete(r)
        if broadcast:
            await session.delete(broadcast)
        await session.commit()
        await push_table_to_postgres(Broadcast)
        await push_table_to_postgres(BroadcastRecipient)

    await query.edit_message_text(
        f"✅ *Broadcast #{broadcast_id} deleted!*\n\n"
        f"🗑 Removed: `{removed}`\n"
        f"❌ Failed: `{failed}`",
        parse_mode="Markdown"
    )

async def handle_broadcast_edit_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the new text input for editing a broadcast."""
    admin_state = context.user_data.get("admin_state")
    if admin_state != "waiting_broadcast_edit":
        return

    user_id = update.effective_user.id
    if not await check_admin(user_id):
        return

    new_text = update.message.text
    if not new_text:
        await update.message.reply_text("❌ Text cannot be empty.")
        return

    broadcast_id = context.user_data.get("edit_broadcast_id")
    context.user_data.clear()

    await update.message.reply_text(f"✏️ Updating broadcast `#{broadcast_id}` for all recipients...", parse_mode="Markdown")

    async with AsyncSessionLocal() as session:
        stmt = select(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id)
        res = await session.execute(stmt)
        recipients = res.scalars().all()

        b_stmt = select(Broadcast).where(Broadcast.id == broadcast_id)
        b_res = await session.execute(b_stmt)
        broadcast = b_res.scalars().first()

    updated = 0
    failed = 0
    for r in recipients:
        try:
            await context.bot.edit_message_text(
                chat_id=r.chat_id,
                message_id=r.message_id,
                text=new_text,
                parse_mode="Markdown"
            )
            updated += 1
        except Exception:
            try:
                await context.bot.send_message(
                    chat_id=r.chat_id,
                    text=f"✏️ *Updated:* {new_text}",
                    parse_mode="Markdown"
                )
                updated += 1
            except Exception:
                failed += 1

    if broadcast:
        async with AsyncSessionLocal() as session:
            broadcast.text = new_text
            await session.commit()
            await push_table_to_postgres(Broadcast)

    await update.message.reply_text(
        f"✅ *Broadcast #{broadcast_id} updated!*\n\n"
        f"✏️ Edited: `{updated}`\n"
        f"❌ Failed: `{failed}`",
        parse_mode="Markdown"
    )

USERS_PER_PAGE = 15

async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles /users — lists all registered users with pagination."""
    user_id = update.effective_user.id
    if not await check_admin(user_id):
        await update.message.reply_text("❌ Permission Denied. Admins only.")
        return

    async with AsyncSessionLocal() as session:
        stmt = select(User).order_by(User.joined_at.desc())
        res = await session.execute(stmt)
        users = res.scalars().all()

    if not users:
        await update.message.reply_text("📭 No users registered yet.")
        return

    context.user_data["users_list"] = users
    await _render_users_page(update.message, context, page=0)

async def users_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles pagination for /users."""
    query = update.callback_query
    try:
        await query.answer()
    except (BadRequest, TelegramError) as e:
        logger.warning(f"users_page_callback answer() failed: {e}")

    page_str = query.data.split(":")[1]
    if page_str == "noop":
        return
    page = int(page_str)
    await _render_users_page(query, context, page=page)

async def _render_users_page(message_or_query, context, page: int):
    """Renders a single page of the users list."""
    users = context.user_data.get("users_list", [])
    total = len(users)
    total_pages = max(1, (total + USERS_PER_PAGE - 1) // USERS_PER_PAGE)
    start = page * USERS_PER_PAGE
    end = start + USERS_PER_PAGE
    page_users = users[start:end]

    lines = [f"📊 *Total Users:* `{total}`\n"]
    for i, u in enumerate(page_users, start=start + 1):
        uname = escape_markdown(f"@{u.username}", version=1) if u.username else "—"
        fname = escape_markdown(u.first_name, version=1) if u.first_name else "—"
        admin_badge = " 👑" if u.is_admin else ""
        joined = u.joined_at.strftime("%Y-%m-%d") if u.joined_at else "?"
        lines.append(f"`{i}.` `{u.user_id}` | {uname} | {fname}{admin_badge} | `{joined}`")

    text = "\n".join(lines)

    buttons = []
    if total_pages > 1:
        row = []
        if page > 0:
            row.append(InlineKeyboardButton("◀️ Prev", callback_data=f"users_page:{page - 1}"))
        row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="users_page:noop"))
        if page < total_pages - 1:
            row.append(InlineKeyboardButton("Next ▶️", callback_data=f"users_page:{page + 1}"))
        buttons.append(row)

    buttons.append([InlineKeyboardButton("🔁 Refresh", callback_data="users_page:0")])
    keyboard = InlineKeyboardMarkup(buttons) if buttons else None

    if hasattr(message_or_query, "edit_message_text"):
        try:
            await message_or_query.edit_message_text(text=text, reply_markup=keyboard, parse_mode="Markdown")
        except (BadRequest, TelegramError) as e:
            logger.warning(f"users_page edit_message_text failed: {e}")
    else:
        await message_or_query.reply_text(text=text, reply_markup=keyboard, parse_mode="Markdown")
