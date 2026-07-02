import asyncio
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy import select, delete

from db import init_db, AsyncSessionLocal, User, DraftMenuButton, ProductionMenuButton
from handlers.user import start_command, user_menu_navigation, user_link_click
from handlers.admin import (
    admin_add_click, handle_admin_input, admin_add_skip_credits,
    admin_add_style_selection, admin_delete_click, admin_publish_click,
    admin_color_click, admin_set_color_execute
)

class MockUser:
    def __init__(self, id, first_name="Test User", username="test_user"):
        self.id = id
        self.first_name = first_name
        self.username = username

class MockChat:
    def __init__(self, id):
        self.id = id

class MockMessage:
    def __init__(self, text=None, forward_origin=None):
        self.text = text
        self.forward_origin = forward_origin
        self.message_id = 999
        self.replies = []
        self.edits = []

    async def reply_text(self, text, reply_markup=None, parse_mode=None):
        self.replies.append({"text": text, "markup": reply_markup, "parse_mode": parse_mode})
        return self

class MockCallbackQuery:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message or MockMessage()
        self.from_user = MockUser(id=1434789841)
        self.answered = False
        self.answer_text = None
        self.edits = []

    async def answer(self, text=None):
        self.answered = True
        self.answer_text = text

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
        self.edits.append({"text": text, "markup": reply_markup})

    def get_bot(self):
        return self.message.bot if hasattr(self.message, 'bot') else MockBot()

class MockBot:
    def __init__(self):
        self.copied_messages = []
        self.sent_messages = []

    async def copy_message(self, chat_id, from_chat_id, message_id, caption=None):
        self.copied_messages.append({"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id})
        class FakeCopiedMessage:
            message_id = 456
        return FakeCopiedMessage()

    async def send_message(self, chat_id, text, parse_mode=None, disable_web_page_preview=None, reply_markup=None):
        self.sent_messages.append({"chat_id": chat_id, "text": text})

class MockUpdate:
    def __init__(self, user, chat, message=None, callback_query=None):
        self.effective_user = user
        self.effective_chat = chat
        self.message = message
        self.callback_query = callback_query
        self.update_id = 123

class MockContext:
    def __init__(self):
        self.user_data = {}
        self.args = []
        self.bot = MockBot()

async def setup_admin_and_db():
    await init_db()
    async with AsyncSessionLocal() as session:
        await session.execute(delete(DraftMenuButton))
        await session.execute(delete(ProductionMenuButton))
        await session.execute(delete(User))
        await session.commit()
    # Ensure admin exists in DB
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(select(User).where(User.user_id == 1434789841))).scalars().first()
        if not existing:
            session.add(User(user_id=1434789841, first_name="Admin", is_admin=True))
            await session.commit()

async def add_draft_button(session, title, btn_type, parent_id=None, style=None,
                           source_chat_id=None, source_message_id=None, credit_text=None):
    from sqlalchemy import func
    max_order = await session.execute(
        select(func.coalesce(func.max(DraftMenuButton.order_index), -1) + 1)
        .where(DraftMenuButton.parent_id == parent_id)
    )
    next_order = max_order.scalar()
    btn = DraftMenuButton(
        title=title, button_type=btn_type, parent_id=parent_id,
        order_index=next_order, button_style=style,
        source_chat_id=source_chat_id, source_message_id=source_message_id,
        credit_text=credit_text
    )
    session.add(btn)
    await session.flush()
    return btn.id

_prod_id_counter = 10000
async def add_production_button(session, title, btn_type, parent_id=None, style=None,
                                source_chat_id=None, source_message_id=None, credit_text=None):
    global _prod_id_counter
    _prod_id_counter += 1
    from sqlalchemy import func
    max_order = await session.execute(
        select(func.coalesce(func.max(ProductionMenuButton.order_index), -1) + 1)
        .where(ProductionMenuButton.parent_id == parent_id)
    )
    next_order = max_order.scalar()
    btn = ProductionMenuButton(
        id=_prod_id_counter,
        title=title, button_type=btn_type, parent_id=parent_id,
        order_index=next_order, button_style=style,
        source_chat_id=source_chat_id, source_message_id=source_message_id,
        credit_text=credit_text
    )
    session.add(btn)
    await session.flush()
    return btn.id

async def test_user_link_click_success():
    """Test 1: user_link_click — the path that was crashing production."""
    print("Gap Test 1: user_link_click (production crash path)...")
    await setup_admin_and_db()
    async with AsyncSessionLocal() as session:
        btn_id = await add_production_button(
            session, "Lec 1 PDF", "link", parent_id=None, style="danger",
            source_chat_id=-100123456789, source_message_id=42, credit_text="@source"
        )
        await session.commit()

    # Verify button exists before calling handler
    async with AsyncSessionLocal() as session:
        check = (await session.execute(
            select(ProductionMenuButton).where(ProductionMenuButton.id == btn_id)
        )).scalars().first()
        assert check is not None, f"Button {btn_id} not found in DB after insert!"
        assert check.source_chat_id == -100123456789
        assert check.source_message_id == 42

    bot = MockBot()
    msg = MockMessage()
    msg.bot = bot
    query = MockCallbackQuery(data=f"l:{btn_id}", message=msg)
    user = MockUser(id=999999, first_name="Student")
    chat = MockChat(id=999999)
    update = MockUpdate(user=user, chat=chat, callback_query=query)
    context = MockContext()
    context.bot = bot

    await user_link_click(update, context)

    # Should have edited the menu message (loading + result)
    assert len(query.edits) >= 2, f"Expected 2+ edits, got {len(query.edits)}"
    assert "⏳" in query.edits[0]["text"], "First edit should show loading"

    # copy_message should deliver to user
    assert len(bot.copied_messages) == 1, f"copy_message not called!"
    assert bot.copied_messages[0]["chat_id"] == 999999
    assert bot.copied_messages[0]["from_chat_id"] == -100123456789
    assert bot.copied_messages[0]["message_id"] == 42

    # Should send credits
    assert len(bot.sent_messages) == 1, "credits message not sent!"
    assert "@source" in bot.sent_messages[0]["text"]

    # Final edit should show success
    assert "sent successfully" in query.edits[-1]["text"]
    print("  PASS — loading shown, copy_message delivered, credits sent, success confirmed.\n")

async def test_user_link_click_missing_source():
    """Test 2: user_link_click with missing source returns error."""
    print("Gap Test 2: user_link_click missing source...")
    await setup_admin_and_db()
    async with AsyncSessionLocal() as session:
        btn_id = await add_production_button(session, "Broken Link", "link",
                                              source_chat_id=None, source_message_id=None)
        await session.commit()
    bot = MockBot()
    msg = MockMessage()
    msg.bot = bot
    query = MockCallbackQuery(data=f"l:{btn_id}", message=msg)
    update = MockUpdate(user=MockUser(999999), chat=MockChat(999999), callback_query=query)
    context = MockContext()
    context.bot = bot

    await user_link_click(update, context)

    # Should show loading on the menu, then error (no new message)
    assert len(query.edits) >= 2, f"Expected 2+ edits, got {len(query.edits)}"
    assert "⏳" in query.edits[0]["text"], "First edit should show loading"
    assert "not found" in query.edits[-1]["text"], "Final edit should show error"
    assert len(bot.sent_messages) == 0, "should not send new messages on missing source"
    print("  PASS — error message returned, no crash.\n")

async def test_draft_link_click():
    """Test 3: Draft dl: button works for admin testing."""
    print("Gap Test 3: Draft link click (dl: callbacks)...")
    await setup_admin_and_db()
    async with AsyncSessionLocal() as session:
        btn_id = await add_draft_button(session, "Draft File", "link",
                                          source_chat_id=-100999, source_message_id=77, credit_text="@draft_source")
        await session.commit()

    bot = MockBot()
    msg = MockMessage()
    msg.bot = bot
    query = MockCallbackQuery(data=f"dl:{btn_id}", message=msg)
    update = MockUpdate(user=MockUser(1434789841), chat=MockChat(1434789841), callback_query=query)
    context = MockContext()
    context.bot = bot

    await user_link_click(update, context)

    assert len(query.edits) >= 2, f"Expected 2+ edits, got {len(query.edits)}"
    assert "⏳" in query.edits[0]["text"]
    assert len(bot.copied_messages) == 1, "copy_message not called for draft!"
    assert bot.copied_messages[0]["from_chat_id"] == -100999
    assert bot.copied_messages[0]["message_id"] == 77
    assert len(bot.sent_messages) == 1, "credits not sent!"
    assert "sent successfully" in query.edits[-1]["text"]
    print("  PASS — draft dl: button works, copy_message + credits sent.\n")

async def test_cascade_delete_folder_with_children():
    """Test 4: Deleting a parent folder cascades to its children."""
    print("Gap Test 4: Cascade delete — folder with nested children...")
    await setup_admin_and_db()
    async with AsyncSessionLocal() as session:
        folder_id = await add_draft_button(session, "Folder", "menu", style="primary")
        child_id = await add_draft_button(session, "Child Folder", "menu", parent_id=folder_id, style="success")
        link_id = await add_draft_button(session, "Deep Link", "link", parent_id=child_id,
                                          source_chat_id=-1, source_message_id=1)
        await session.commit()

    # Delete the root folder
    query = MockCallbackQuery(data=f"del:{folder_id}")
    update = MockUpdate(user=MockUser(1434789841), chat=MockChat(1434789841), callback_query=query)
    context = MockContext()
    await admin_delete_click(update, context)

    async with AsyncSessionLocal() as session:
        remaining = (await session.execute(select(DraftMenuButton))).scalars().all()
        assert len(remaining) == 0, f"Expected 0 remaining, got {len(remaining)} — CASCADE not working!"
    print("  PASS — all children deleted via CASCADE.\n")

async def test_url_normalization_edge_cases():
    """Test 5: URL normalization handles @, t.me/, raw, and https formats."""
    print("Gap Test 5: URL normalization edge cases...")
    await setup_admin_and_db()

    cases = [
        ("@my_bot", "https://t.me/my_bot"),
        ("t.me/my_bot", "https://t.me/my_bot"),
        ("https://t.me/my_bot", "https://t.me/my_bot"),
        ("my_bot", "https://t.me/my_bot"),
    ]

    for raw, expected in cases:
        async with AsyncSessionLocal() as session:
            # Add fb button and advance to the waiting_for_fb_link state
            btn_id = await add_draft_button(session, "Feedback", "fb")

        # Simulate the handle_admin_input flow for waiting_for_fb_link
        context = MockContext()
        context.user_data["admin_state"] = "waiting_for_fb_link"
        msg = MockMessage(text=raw)
        update = MockUpdate(user=MockUser(1434789841), chat=MockChat(1434789841), message=msg)
        await handle_admin_input(update, context)

        normalized = context.user_data.get("new_btn_credit_text")
        assert normalized == expected, f"FAIL: '{raw}' -> '{normalized}', expected '{expected}'"
        print(f"  PASS: '{raw}' -> '{normalized}'")

async def test_color_change_persistence():
    """Test 6: Color change persists in DB after set_color callback."""
    print("\nGap Test 6: Color change persistence...")
    await setup_admin_and_db()
    async with AsyncSessionLocal() as session:
        btn_id = await add_draft_button(session, "Test", "menu", style="success")
        await session.commit()

    # Change to primary via callback
    query = MockCallbackQuery(data=f"set_color:{btn_id}:primary")
    update = MockUpdate(user=MockUser(1434789841), chat=MockChat(1434789841), callback_query=query)
    context = MockContext()
    await admin_set_color_execute(update, context)

    async with AsyncSessionLocal() as session:
        btn = (await session.execute(select(DraftMenuButton).where(DraftMenuButton.id == btn_id))).scalars().first()
        assert btn.button_style == "primary", f"Expected primary, got {btn.button_style}"
    print("  PASS — style updated and persisted.\n")

async def run_all():
    print("=" * 60)
    print("RUNNING COVERAGE GAP TESTS")
    print("=" * 60)
    await test_user_link_click_success()
    await test_user_link_click_missing_source()
    await test_draft_link_click()
    await test_cascade_delete_folder_with_children()
    await test_url_normalization_edge_cases()
    await test_color_change_persistence()
    print("=" * 60)
    print("ALL GAP TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(run_all())
