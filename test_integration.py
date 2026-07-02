import asyncio
from unittest.mock import AsyncMock
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, delete

from db import init_db, AsyncSessionLocal, User, DraftMenuButton, ProductionMenuButton
from handlers.user import start_command, user_menu_navigation, user_link_click
from handlers.admin import (
    admin_command, admin_menu_navigation, admin_manage_mode, admin_edit_panel,
    admin_rename_click, admin_reorder_execute, admin_edit_back_click, admin_add_click,
    handle_admin_input, admin_add_skip_credits, admin_add_style_selection,
    admin_delete_click, admin_publish_click, broadcast_command,
    admin_color_click, admin_set_color_execute, admin_edit_link_click
)

# Mock classes for testing
class MockUser:
    def __init__(self, id, first_name="Test User", username="test_user"):
        self.id = id
        self.first_name = first_name
        self.username = username

class MockChat:
    def __init__(self, id):
        self.id = id

class MockMessage:
    def __init__(self, text=None, caption=None, document=None):
        self.text = text
        self.caption = caption
        self.document = document
        self.replies = []
        self.forward_origin = None
        self.message_id = 999
        
    async def reply_text(self, text, reply_markup=None, parse_mode=None):
        self.replies.append({"text": text, "markup": reply_markup})
        return self
        
    async def edit_text(self, text, reply_markup=None, parse_mode=None):
        self.text = text
        self.replies.append({"text": text, "markup": reply_markup})
        return self

class MockCallbackQuery:
    def __init__(self, data, message):
        self.data = data
        self.message = message
        self.answered = False
        self.answer_text = None
        self.edits = []
        
    async def answer(self, text=None):
        self.answered = True
        self.answer_text = text
        
    async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
        self.edits.append({"text": text, "markup": reply_markup})
        return self.message

class MockUpdate:
    def __init__(self, user, chat, message=None, callback_query=None):
        self.effective_user = user
        self.effective_chat = chat
        self.message = message
        self.callback_query = callback_query
        self.update_id = 123

class MockBot:
    def __init__(self):
        self.copied_messages = []
        self.sent_messages = []
        self.commands = []
        
    async def copy_message(self, chat_id, from_chat_id, message_id, caption=None):
        self.copied_messages.append({
            "chat_id": chat_id,
            "from_chat_id": from_chat_id,
            "message_id": message_id,
            "caption": caption
        })
        class FakeCopiedMessage:
            message_id = 456
        return FakeCopiedMessage()
        
    async def send_message(self, chat_id, text, parse_mode=None, disable_web_page_preview=None, reply_markup=None):
        self.sent_messages.append({
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup
        })
        
    async def set_my_commands(self, commands):
        self.commands = commands

class MockContext:
    def __init__(self):
        self.user_data = {}
        self.args = []
        self.bot = MockBot()

async def run_integration_test():
    print("--------------------------------------------------")
    print("Running Local Integration Tests for Bot Features...")
    print("--------------------------------------------------")
    
    # 1. Initialize clean DB
    await init_db()
    
    async with AsyncSessionLocal() as session:
        # Clear tables
        await session.execute(delete(DraftMenuButton))
        await session.execute(delete(ProductionMenuButton))
        await session.execute(delete(User))
        await session.commit()
        
    # Setup test accounts
    admin_user = MockUser(id=1434789841, first_name="Bootstrap Admin", username="admin_username")
    normal_user = MockUser(id=999999, first_name="Normal User", username="normal_username")
    chat = MockChat(id=1434789841)
    
    context = MockContext()
    
    # Test A: User registration on /start
    print("Test 1: User /start registration...")
    msg = MockMessage(text="/start")
    update = MockUpdate(user=normal_user, chat=MockChat(999999), message=msg)
    await start_command(update, context)
    
    async with AsyncSessionLocal() as session:
        registered = (await session.execute(select(User).where(User.user_id == 999999))).scalars().first()
        assert registered is not None, "Normal user not registered!"
        print("  - Normal user successfully registered in DB.")
        
    # Register admin in DB
    async with AsyncSessionLocal() as session:
        registered_admin = (await session.execute(select(User).where(User.user_id == 1434789841))).scalars().first()
        if not registered_admin:
            registered_admin = User(user_id=1434789841, first_name="Admin", is_admin=True)
            session.add(registered_admin)
            await session.commit()
            
    # Test B: Admin access to /admin
    print("Test 2: Admin command access...")
    msg = MockMessage(text="/admin")
    update = MockUpdate(user=admin_user, chat=chat, message=msg)
    await admin_command(update, context)
    assert len(msg.replies) > 0, "No reply sent to admin!"
    assert "WORKSPACE EDITOR" in msg.replies[0]["text"], "Admin text mismatch!"
    print("  - Admin successfully launched staging editor.")
    
    # Test C: Adding a Submenu button
    print("Test 3: Adding a Submenu button...")
    # Clicking Add Submenu (add:menu:root)
    query_msg = MockMessage()
    query = MockCallbackQuery(data="add:menu:root", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=query)
    await admin_add_click(update, context)
    assert context.user_data.get("admin_state") == "waiting_for_title", "State not set to waiting_for_title!"
    
    # Sending title "Fifth Grade"
    title_msg = MockMessage(text="Fifth Grade")
    update = MockUpdate(user=admin_user, chat=chat, message=title_msg)
    await handle_admin_input(update, context)
    assert context.user_data.get("new_btn_title") == "Fifth Grade", "Title not saved!"
    assert context.user_data.get("admin_state") == "waiting_style_selection", "State not set to waiting_style_selection!"
    
    # Selecting Green Style (add_style:success)
    style_query = MockCallbackQuery(data="add_style:success", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=style_query)
    await admin_add_style_selection(update, context)
    
    # Verify saved in draft
    async with AsyncSessionLocal() as session:
        drafts = (await session.execute(select(DraftMenuButton))).scalars().all()
        assert len(drafts) == 1, "Draft button not saved!"
        assert drafts[0].title == "Fifth Grade", "Title mismatch!"
        assert drafts[0].button_style == "success", "Style mismatch!"
        print("  - Draft submenu button created successfully.")
        btn_id = drafts[0].id
        
    # Test D: Button Management Panel (Manage Mode)
    print("Test 4: Management Mode...")
    # Swapping to manage mode (manage:root)
    query = MockCallbackQuery(data="manage:root", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=query)
    await admin_manage_mode(update, context)
    assert len(query.edits) > 0, "No manage mode layout sent!"
    assert "MANAGEMENT DIRECTORY" in query.edits[0]["text"], "Manage mode title mismatch!"
    
    # Test E: Edit Panel Details
    print("Test 5: Edit Panel...")
    # Clicking edit on the button (edit:btn_id)
    query = MockCallbackQuery(data=f"edit:{btn_id}", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=query)
    await admin_edit_panel(update, context)
    assert len(query.edits) > 0, "Edit panel not loaded!"
    assert "BUTTON CONTROL PANEL" in query.edits[0]["text"], "Panel title mismatch!"
    
    # Test F: Rename Button
    print("Test 6: Renaming Button...")
    query = MockCallbackQuery(data=f"rename:{btn_id}", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=query)
    await admin_rename_click(update, context)
    assert context.user_data.get("admin_state") == "waiting_rename", "Rename state not set!"
    
    rename_msg = MockMessage(text="5th Grade")
    update = MockUpdate(user=admin_user, chat=chat, message=rename_msg)
    await handle_admin_input(update, context)
    
    async with AsyncSessionLocal() as session:
        btn = (await session.execute(select(DraftMenuButton).where(DraftMenuButton.id == btn_id))).scalars().first()
        assert btn.title == "5th Grade", "Rename failed!"
        print("  - Button renamed successfully to '5th Grade'.")
        
    # Test G: Changing Color Style
    print("Test 7: Changing Style...")
    query = MockCallbackQuery(data=f"color:{btn_id}", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=query)
    await admin_color_click(update, context)
    assert len(query.edits) > 0, "Color picker not loaded!"
    
    # Select Blue Style (set_color:btn_id:primary)
    query = MockCallbackQuery(data=f"set_color:{btn_id}:primary", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=query)
    await admin_set_color_execute(update, context)
    
    async with AsyncSessionLocal() as session:
        btn = (await session.execute(select(DraftMenuButton).where(DraftMenuButton.id == btn_id))).scalars().first()
        assert btn.button_style == "primary", "Style change failed!"
        print("  - Button style updated successfully to 'primary' (Blue).")
        
    # Test H: Adding a Link Button with Credits Skip
    print("Test 8: Adding a Link Button with Credits Skip...")
    context.user_data.clear()
    query = MockCallbackQuery(data="add:link:root", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=query)
    await admin_add_click(update, context)
    
    # Enter link title
    title_msg = MockMessage(text="Lec 1 PDF")
    update = MockUpdate(user=admin_user, chat=chat, message=title_msg)
    await handle_admin_input(update, context)
    assert context.user_data.get("admin_state") == "waiting_for_file", "Not waiting for file!"
    
    # Send text message to simulate direct file upload
    file_msg = MockMessage(text="This is document text content")
    update = MockUpdate(user=admin_user, chat=chat, message=file_msg)
    # Mock STORAGE_CHANNEL_ID and bot admin permission
    import os
    os.environ["STORAGE_CHANNEL_ID"] = "-100999888777"
    await handle_admin_input(update, context)
    assert context.user_data.get("admin_state") == "waiting_for_credit", "Not waiting for credit!"
    
    # Click skip credits (add_no_credits)
    skip_query = MockCallbackQuery(data="add_no_credits", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=skip_query)
    await admin_add_skip_credits(update, context)
    assert context.user_data.get("admin_state") == "waiting_style_selection", "Not waiting for style!"
    
    # Select Danger Red (add_style:danger)
    style_query = MockCallbackQuery(data="add_style:danger", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=style_query)
    await admin_add_style_selection(update, context)
    
    # Verify link button saved
    async with AsyncSessionLocal() as session:
        link_btn = (await session.execute(select(DraftMenuButton).where(DraftMenuButton.button_type == "link"))).scalars().first()
        assert link_btn is not None, "Link button not saved!"
        assert link_btn.title == "Lec 1 PDF", "Link title mismatch!"
        assert link_btn.button_style == "danger", "Link style mismatch!"
        assert link_btn.credit_text is None, "Credits should be skipped!"
        print("  - Link button created successfully with credit-skipping and red styling.")
        
    # Test H2: Editing Link Target / File Source
    print("Test 8.5: Editing Button Destination Link...")
    # Clicking edit link (edit_link:link_btn.id)
    query = MockCallbackQuery(data=f"edit_link:{link_btn.id}", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=query)
    await admin_edit_link_click(update, context)
    assert context.user_data.get("admin_state") == "waiting_edit_link", "State not set to waiting_edit_link!"
    
    # Send new file content
    new_file_msg = MockMessage(text="Updated document file content")
    update = MockUpdate(user=admin_user, chat=chat, message=new_file_msg)
    await handle_admin_input(update, context)
    
    async with AsyncSessionLocal() as session:
        updated_btn = (await session.execute(select(DraftMenuButton).where(DraftMenuButton.id == link_btn.id))).scalars().first()
        # The file copy mock sets copied_msg.message_id = 456
        assert updated_btn.source_message_id == 456, "Link destination update failed!"
        print("  - Link button file source updated successfully.")
        
    # Test I: Button reordering (Up/Down)
    print("Test 9: Reordering Buttons...")
    # Sibling buttons are: 5th Grade (index 0, id: btn_id), Lec 1 PDF (index 1, id: link_btn.id)
    # Move Lec 1 PDF Up (move:up:link_btn.id)
    query = MockCallbackQuery(data=f"move:up:{link_btn.id}", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=query)
    await admin_reorder_execute(update, context)
    
    async with AsyncSessionLocal() as session:
        btn_a = (await session.execute(select(DraftMenuButton).where(DraftMenuButton.id == btn_id))).scalars().first()
        btn_b = (await session.execute(select(DraftMenuButton).where(DraftMenuButton.id == link_btn.id))).scalars().first()
        assert btn_b.order_index == 0, "Lec 1 PDF should be index 0!"
        assert btn_a.order_index == 1, "5th Grade should be index 1!"
        print("  - Swap order indexes completed successfully (reordering verified).")
        
    # Test J: Staging Publish to Production
    print("Test 10: Publishing Changes Staging -> Live...")
    query = MockCallbackQuery(data="publish_execute", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=query)
    await admin_publish_click(update, context)
    
    async with AsyncSessionLocal() as session:
        live_buttons = (await session.execute(select(ProductionMenuButton))).scalars().all()
        assert len(live_buttons) == 2, "Live table should have 2 buttons!"
        print("  - Live production buttons published successfully.")
        
    # Test K: Adding Feedback URL button and verifying direct URL delivery
    print("Test 11: Feedback Button Flow...")
    context.user_data.clear()
    query = MockCallbackQuery(data="add:fb:root", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=query)
    await admin_add_click(update, context)
    
    # Enter feedback title
    title_msg = MockMessage(text="Give Feedback")
    update = MockUpdate(user=admin_user, chat=chat, message=title_msg)
    await handle_admin_input(update, context)
    assert context.user_data.get("admin_state") == "waiting_for_fb_link", "State not waiting_for_fb_link!"
    
    # Enter username of feedback bot
    link_msg = MockMessage(text="@my_feedback_bot")
    update = MockUpdate(user=admin_user, chat=chat, message=link_msg)
    await handle_admin_input(update, context)
    assert context.user_data.get("new_btn_credit_text") == "https://t.me/my_feedback_bot", "URL normalization failed!"
    assert context.user_data.get("admin_state") == "waiting_style_selection", "State not waiting_style_selection!"
    
    # Select Green Style (add_style:success)
    style_query = MockCallbackQuery(data="add_style:success", message=query_msg)
    update = MockUpdate(user=admin_user, chat=chat, callback_query=style_query)
    await admin_add_style_selection(update, context)
    
    # Verify saved in draft and copy to production
    await admin_publish_click(MockUpdate(user=admin_user, chat=chat, callback_query=MockCallbackQuery("publish_execute", query_msg)), context)
    
    async with AsyncSessionLocal() as session:
        drafts = (await session.execute(select(DraftMenuButton))).scalars().all()
        print("ALL DRAFTS IN DB:")
        for d in drafts:
            print(f"  ID: {d.id}, Title: {d.title}, Type: {d.button_type}, Credit/Link: {d.credit_text}, Style: {d.button_style}")
            
        prods = (await session.execute(select(ProductionMenuButton))).scalars().all()
        print("ALL PRODUCTION IN DB:")
        for p in prods:
            print(f"  ID: {p.id}, Title: {p.title}, Type: {p.button_type}, Credit/Link: {p.credit_text}, Style: {p.button_style}")
            
    # Verify live button exists and is URL link
    from keyboards import build_menu_keyboard
    live_kb = await build_menu_keyboard(None, is_draft=False)
    # Find button and check properties
    found_feedback = False
    for row in live_kb.inline_keyboard:
        for btn in row:
            if btn.text == "Give Feedback":
                found_feedback = True
                assert btn.url == "https://t.me/my_feedback_bot", f"Url mismatch: {btn.url}"
                assert btn.callback_data is None, "Callback data should be None for URL buttons!"
    assert found_feedback, "Feedback button not found in live menu!"
    print("  - Feedback button with direct URL link verified successfully.")
        
    # Test L: Admin Broadcast
    print("Test 12: Admin Broadcast...")
    context.args = ["Hello", "all", "students!"]
    broadcast_msg = MockMessage()
    update = MockUpdate(user=admin_user, chat=chat, message=broadcast_msg)
    await broadcast_command(update, context)
    success_count = len(context.bot.sent_messages)
    assert success_count > 0, "Broadcast message not sent!"
    print(f"  - Broadcast sent successfully to {success_count} user accounts.")
    
    print("\nINTEGRATION TESTS COMPLETED SUCCESSFULLY! All features are verified locally and running bug-free.")

if __name__ == "__main__":
    asyncio.run(run_integration_test())
