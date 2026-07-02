from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from db import AsyncSessionLocal, DraftMenuButton, ProductionMenuButton

async def build_menu_keyboard(parent_id: int | None, is_draft: bool, is_manage_mode: bool = False) -> InlineKeyboardMarkup:
    """
    Builds the inline keyboard dynamically from the database.
    If is_draft is True, it queries draft_menu_buttons.
    If is_draft is False, it queries production_menu_buttons.
    If is_manage_mode is True, clicking buttons triggers the edit panel.
    """
    async with AsyncSessionLocal() as session:
        table = DraftMenuButton if is_draft else ProductionMenuButton
        
        # Query buttons ordered by order_index ascending
        stmt = select(table).where(table.parent_id == parent_id).order_by(table.order_index)
        result = await session.execute(stmt)
        buttons = result.scalars().all()
        
        keyboard = []
        current_row = []
        prefix = "d" if is_draft else ""
        
        for btn in buttons:
            display_title = btn.title
            kwargs = {"text": display_title}
            if btn.button_style:
                kwargs["style"] = btn.button_style
            
            # If in management mode, clicking any button goes to edit panel
            if is_manage_mode:
                display_title = f"⚙️ {display_title}"
                kwargs["text"] = display_title
                kwargs["callback_data"] = f"edit:{btn.id}"
            else:
                if btn.button_type == "menu":
                    kwargs["callback_data"] = f"{prefix}m:{btn.id}"
                elif btn.button_type == "link":
                    kwargs["callback_data"] = f"{prefix}l:{btn.id}"
                elif btn.button_type in ("fb", "feedback"):
                    # Feedback buttons are now direct URL links redirecting to the feedback bot
                    kwargs["url"] = btn.credit_text if btn.credit_text else "https://t.me/"
                else:
                    kwargs["callback_data"] = "none"
            
            button_obj = InlineKeyboardButton(**kwargs)
            
            if btn.button_type in ("menu", "fb", "feedback"):
                # Folders and feedback buttons get their own full row
                if current_row:
                    keyboard.append(current_row)
                    current_row = []
                keyboard.append([button_obj])
            else:
                # Link buttons are grouped in rows of 2
                current_row.append(button_obj)
                if len(current_row) == 2:
                    keyboard.append(current_row)
                    current_row = []
                    
        if current_row:
            keyboard.append(current_row)
            
        parent_str = str(parent_id) if parent_id is not None else "root"
        
        # Add Staging controls
        if is_draft:
            if is_manage_mode:
                # In manage mode, show Back to Navigation
                keyboard.append([
                    InlineKeyboardButton("🔙 Back to Navigation", callback_data=f"db:{parent_str}")
                ])
            else:
                # In navigation mode, show Add controls
                keyboard.append([
                    InlineKeyboardButton("➕ Submenu", callback_data=f"add:menu:{parent_str}"),
                    InlineKeyboardButton("➕ Link", callback_data=f"add:link:{parent_str}"),
                    InlineKeyboardButton("➕ Feedback", callback_data=f"add:fb:{parent_str}")
                ])
                # Show Manage layout button if there are buttons to edit
                if buttons:
                    keyboard.append([
                        InlineKeyboardButton("⚙️ Manage Buttons", callback_data=f"manage:{parent_str}")
                    ])
                # If we are at root, show Publish option
                if parent_id is None:
                    keyboard.append([
                        InlineKeyboardButton("🚀 PUBLISH DRAFT TO LIVE 🚀", callback_data="publish_confirm")
                    ])
                    
        # Add Back button
        if parent_id is not None:
            # Find the grandparent_id to go back to it
            grandparent_id = None
            async with AsyncSessionLocal() as session2:
                stmt2 = select(table).where(table.id == parent_id)
                res2 = await session2.execute(stmt2)
                parent_btn = res2.scalars().first()
                if parent_btn:
                    grandparent_id = parent_btn.parent_id
                    
            gp_str = str(grandparent_id) if grandparent_id is not None else "root"
            back_text = "🔙 Back"
            
            # If in manage mode, back should stay in manage mode at grandparent level
            if is_manage_mode:
                keyboard.append([
                    InlineKeyboardButton(back_text, callback_data=f"manage:{gp_str}")
                ])
            else:
                keyboard.append([
                    InlineKeyboardButton(back_text, callback_data=f"db:{gp_str}" if is_draft else f"b:{gp_str}")
                ])
                
        return InlineKeyboardMarkup(keyboard)

async def build_button_edit_keyboard(btn_id: int) -> InlineKeyboardMarkup:
    """Builds the inline control panel for editing a specific button."""
    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()
        
    keyboard = []
    
    # Rename and color setting
    keyboard.append([
        InlineKeyboardButton("✏️ Rename Button", callback_data=f"rename:{btn_id}"),
        InlineKeyboardButton("🎨 Change Color", callback_data=f"color:{btn_id}")
    ])
    
    # Destination editing for files/URLs
    if btn and btn.button_type in ("link", "fb", "feedback"):
        edit_label = "✏️ Edit File Source" if btn.button_type == "link" else "✏️ Edit Bot URL"
        keyboard.append([
            InlineKeyboardButton(edit_label, callback_data=f"edit_link:{btn_id}")
        ])
        
    # Reordering controls
    keyboard.append([
        InlineKeyboardButton("⬆️ Move Up", callback_data=f"move:up:{btn_id}"),
        InlineKeyboardButton("⬇️ Move Down", callback_data=f"move:down:{btn_id}")
    ])
    
    # Deletion controls
    keyboard.append([
        InlineKeyboardButton("🗑 Delete Button", callback_data=f"del:{btn_id}")
    ])
    
    # Back button
    keyboard.append([
        InlineKeyboardButton("🔙 Back to List", callback_data=f"back_edit:{btn_id}")
    ])
    
    return InlineKeyboardMarkup(keyboard)

async def build_color_picker_keyboard(btn_id: int) -> InlineKeyboardMarkup:
    """Builds the background color style picker menu."""
    keyboard = [
        [
            InlineKeyboardButton("⬜ Default (Gray)", callback_data=f"set_color:{btn_id}:none"),
            InlineKeyboardButton("🟦 Blue (Primary)", callback_data=f"set_color:{btn_id}:primary")
        ],
        [
            InlineKeyboardButton("🟩 Green (Success)", callback_data=f"set_color:{btn_id}:success"),
            InlineKeyboardButton("🟥 Red (Danger)", callback_data=f"set_color:{btn_id}:danger")
        ],
        [
            InlineKeyboardButton("🔙 Cancel", callback_data=f"cancel_color:{btn_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)
