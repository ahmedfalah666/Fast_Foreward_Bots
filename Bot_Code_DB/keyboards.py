import json
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
                # Copy contents from another menu
                keyboard.append([
                    InlineKeyboardButton("📋 Copy Contents From...", callback_data=f"copy_sel:{parent_str}")
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

async def build_sources_manage_keyboard(btn_id: int) -> InlineKeyboardMarkup:
    """Builds the sources management view for a link button."""
    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(DraftMenuButton.id == btn_id)
        res = await session.execute(stmt)
        btn = res.scalars().first()

    primary_label = "📄 Source 1 (primary)"
    extra = json.loads(btn.extra_sources) if btn and btn.extra_sources else []

    keyboard = [
        [InlineKeyboardButton(primary_label, callback_data=f"src_replace:{btn_id}")]
    ]

    for i, src in enumerate(extra, start=2):
        keyboard.append([
            InlineKeyboardButton(f"📄 Source {i} (delete)", callback_data=f"src_del:{btn_id}:{i - 2}")
        ])

    keyboard.append([
        InlineKeyboardButton("➕ Add Another Source", callback_data=f"src_add:{btn_id}")
    ])

    keyboard.append([
        InlineKeyboardButton("🔙 Back to Edit Panel", callback_data=f"back_edit:{btn_id}")
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
        if btn.button_type == "link":
            extra = json.loads(btn.extra_sources) if btn.extra_sources else []
            file_count = 1 + len(extra)
            keyboard.append([
                InlineKeyboardButton(f"📂 Manage Sources ({file_count} files)", callback_data=f"src_manage:{btn_id}")
            ])
            keyboard.append([
                InlineKeyboardButton("✏️ Edit Credits", callback_data=f"edit_credit:{btn_id}")
            ])
        else:
            keyboard.append([
                InlineKeyboardButton("✏️ Edit Bot URL", callback_data=f"edit_link:{btn_id}")
            ])
        
    # Reordering controls
    keyboard.append([
        InlineKeyboardButton("⬆️ Move Up", callback_data=f"move:up:{btn_id}"),
        InlineKeyboardButton("⬇️ Move Down", callback_data=f"move:down:{btn_id}")
    ])
    
    # Move to a different parent
    keyboard.append([
        InlineKeyboardButton("📂 Change Parent", callback_data=f"parent_sel:{btn_id}")
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

async def build_parent_selector_keyboard(current_parent: int | None, moving_btn_id: int, exclude_ids: set[int]) -> InlineKeyboardMarkup:
    """Builds a navigation tree to pick a new parent for a button.
    
    Shows only menu-type buttons as clickable folders.
    current_parent = None means showing root level.
    exclude_ids are the moving button + its descendants (to prevent circular refs).
    """
    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(
            DraftMenuButton.parent_id == current_parent,
            DraftMenuButton.button_type == "menu"
        ).order_by(DraftMenuButton.order_index)
        result = await session.execute(stmt)
        submenus = result.scalars().all()

    keyboard = []

    # "Select this folder" button
    parent_str = "root" if current_parent is None else str(current_parent)
    keyboard.append([
        InlineKeyboardButton("✅ Select This Folder", callback_data=f"parent_pick:{parent_str}:{moving_btn_id}")
    ])

    # List submenu folders as navigation targets
    for m in submenus:
        if m.id in exclude_ids:
            continue
        label = f"📁 {m.title}"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"parent_nav:{m.id}:{moving_btn_id}")
        ])

    # Back button
    if current_parent is not None:
        async with AsyncSessionLocal() as session2:
            stmt2 = select(DraftMenuButton).where(DraftMenuButton.id == current_parent)
            res2 = await session2.execute(stmt2)
            parent_btn = res2.scalars().first()
            grandparent_id = parent_btn.parent_id if parent_btn else None
        gp_str = "root" if grandparent_id is None else str(grandparent_id)
        keyboard.append([
            InlineKeyboardButton("🔙 Back", callback_data=f"parent_nav:{gp_str}:{moving_btn_id}")
        ])

    return InlineKeyboardMarkup(keyboard)

async def build_copy_source_keyboard(current_parent: int | None, target_parent: int | None) -> InlineKeyboardMarkup:
    """Builds a navigation tree to pick a source menu to copy contents from.
    
    Shows only menu-type buttons as clickable folders.
    Excludes copying from the target itself.
    """
    async with AsyncSessionLocal() as session:
        stmt = select(DraftMenuButton).where(
            DraftMenuButton.parent_id == current_parent,
            DraftMenuButton.button_type == "menu"
        ).order_by(DraftMenuButton.order_index)
        result = await session.execute(stmt)
        submenus = result.scalars().all()

    keyboard = []

    current_str = "root" if current_parent is None else str(current_parent)
    target_str = "root" if target_parent is None else str(target_parent)

    # "Select This Folder" — allow selecting root only if target is a submenu
    if current_parent is not None or target_parent is not None:
        select_label = "✅ Select Main Menu (Root)" if current_parent is None else "✅ Select This Folder"
        keyboard.append([
            InlineKeyboardButton(select_label, callback_data=f"copy_pick:{current_str}:{target_str}")
        ])

    for m in submenus:
        if m.id == target_parent:
            continue
        label = f"📁 {m.title}"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"copy_nav:{m.id}:{target_str}")
        ])

    if current_parent is not None:
        async with AsyncSessionLocal() as session2:
            stmt2 = select(DraftMenuButton).where(DraftMenuButton.id == current_parent)
            res2 = await session2.execute(stmt2)
            parent_btn = res2.scalars().first()
            grandparent_id = parent_btn.parent_id if parent_btn else None
        gp_str = "root" if grandparent_id is None else str(grandparent_id)
        keyboard.append([
            InlineKeyboardButton("🔙 Back", callback_data=f"copy_nav:{gp_str}:{target_str}")
        ])

    return InlineKeyboardMarkup(keyboard)
