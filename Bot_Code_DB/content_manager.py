import json
import os
import logging
import mimetypes
from pathlib import Path

from nicegui import ui, app
from sqlalchemy import select, delete, func

from config import BOT_TOKEN, STORAGE_CHANNEL_ID
from db import AsyncSessionLocal, DraftMenuButton, CreditTemplate, init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("content_manager")

SUGGESTIONS_FILE = "suggestions.json"
COLOR_OPTIONS = [
    {"value": "primary", "label": "🔵 Blue (primary)"},
    {"value": "danger", "label": "🔴 Red (danger)"},
    {"value": "success", "label": "🟢 Green (success)"},
    {"value": "", "label": "⬜ Default (gray)"},
]
BUTTON_TYPES = [
    {"value": "link", "label": "📎 File Link"},
    {"value": "menu", "label": "📂 Folder / Menu"},
    {"value": "fb", "label": "🔗 URL / Feedback"},
]

state = {
    "suggestions": [],
    "approved": set(),
    "synced": False,
    "total_suggestions": 0,
}

@ui.refreshable
def suggestion_cards():
    if not state["suggestions"]:
        ui.label("No suggestions loaded. Use [Load AI Suggestions] below.").classes("text-gray-400 italic p-4")
        return
    for idx, s in enumerate(state["suggestions"]):
        is_approved = idx in state["approved"]
        with ui.card().classes("w-full mb-2 p-4") as card:
            if is_approved:
                card.classes("border-2 border-green-500 bg-green-50")
            with ui.row().classes("w-full items-center"):
                ui.icon("description", size="24px").classes("text-blue-500")
                ui.label(s.get("filename", "Unknown")).classes("text-lg font-bold")
                if is_approved:
                    ui.badge("✅ Approved", color="green").classes("ml-auto")
                else:
                    conf = s.get("confidence", 0)
                    color = "green" if conf >= 85 else "orange" if conf >= 60 else "gray"
                    ui.badge(f"🤖 {conf}%", color=color).classes("ml-auto")
            if s.get("ai_summary"):
                ui.label(s["ai_summary"]).classes("text-sm text-gray-600 italic mt-1")
            size_mb = s.get("size_bytes", 0) / (1024 * 1024)
            ui.label(f"📄 {s.get('mime', 'unknown')} · {size_mb:.1f} MB").classes("text-xs text-gray-400")

            parent_id = s.get("suggested_parent_id")
            parent_name = _get_parent_name(parent_id)

            ui.separator().classes("my-2")
            with ui.row().classes("w-full gap-4 items-center"):
                parent_select = ui.select(
                    label="📁 Target folder",
                    options=_build_folder_options(),
                    value=parent_id if parent_id in [o[0] for o in _build_folder_options()] else None,
                ).classes("min-w-[250px]")
                parent_select.on_value_change(lambda e, i=idx: _update_suggestion(i, "suggested_parent_id", e.value))

                color_select = ui.select(
                    label="🎨 Color",
                    options=[(c["value"], c["label"]) for c in COLOR_OPTIONS],
                    value=s.get("suggested_color", "primary"),
                ).classes("min-w-[180px]")
                color_select.on_value_change(lambda e, i=idx: _update_suggestion(i, "suggested_color", e.value))

            with ui.row().classes("w-full gap-4 items-center mt-2"):
                name_input = ui.input(
                    label="✏️ Button name",
                    value=s.get("suggested_title", ""),
                ).classes("min-w-[300px] flex-grow")
                name_input.on_value_change(lambda e, i=idx: _update_suggestion(i, "suggested_title", e.value))

                credit_select = ui.select(
                    label="💳 Credits",
                    options=_build_credit_options(),
                    value=_get_matching_credit(s.get("suggested_credits", "")),
                ).classes("min-w-[200px]")
                credit_select.on_value_change(lambda e, i=idx: _update_suggestion(i, "suggested_credits", e.value if e.value else ""))

            with ui.row().classes("w-full mt-2"):
                if is_approved:
                    ui.button("✅ Approved", icon="check", color="gray").props("flat disabled")
                else:
                    async def approve(i=idx):
                        await _approve_suggestion(i)
                    ui.button("Approve", icon="check", color="green", on_click=approve)

@ui.refreshable
def stats_bar():
    total = len(state["suggestions"])
    approved = len(state["approved"])
    ui.label(f"📊 {total} suggestions · {approved} approved · {total - approved} pending").classes("text-sm")

def _get_parent_name(parent_id):
    if parent_id is None:
        return "Main Menu (root)"
    for b in state.get("_menu_buttons", []):
        if b.id == parent_id:
            return b.title
    return "Unknown"

def _build_folder_options():
    options = [(None, "📂 Main Menu (root)")]
    buttons = state.get("_menu_buttons", [])
    for b in buttons:
        if b.button_type == "menu":
            indent = "  " * _depth(b.id, buttons)
            options.append((b.id, f"{indent}📂 {b.title}"))
    return options

def _depth(btn_id, all_buttons, visited=None):
    if visited is None:
        visited = set()
    if btn_id in visited:
        return 0
    visited.add(btn_id)
    for b in all_buttons:
        if b.id == btn_id and b.parent_id is not None:
            return 1 + _depth(b.parent_id, all_buttons, visited)
    return 0

def _build_credit_options():
    templates = state.get("_credit_templates", [])
    options = [("", "💳 None")]
    for t in templates:
        label = f"{t.name}: {t.text[:30]}" if t.text else f"{t.name}: (empty)"
        options.append((t.text, label))
    return options

def _get_matching_credit(credit_text):
    if not credit_text:
        return ""
    templates = state.get("_credit_templates", [])
    for t in templates:
        if t.text == credit_text:
            return credit_text
    return credit_text

def _update_suggestion(idx, key, value):
    if 0 <= idx < len(state["suggestions"]):
        state["suggestions"][idx][key] = value

async def _approve_suggestion(idx):
    if idx in state["approved"]:
        return
    s = state["suggestions"][idx]
    filepath = s.get("filepath")
    parent_id = s.get("suggested_parent_id")
    title = s.get("suggested_title", "").strip()
    color = s.get("suggested_color", "primary")
    credit_text = s.get("suggested_credits", "")

    if not title:
        ui.notify("❌ Button name cannot be empty", type="negative")
        return
    if not filepath or not os.path.exists(filepath):
        ui.notify(f"❌ File not found: {filepath}", type="negative")
        return

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(func.max(DraftMenuButton.order_index)).where(
                    DraftMenuButton.parent_id == parent_id
                )
            )
            max_order = result.scalar() or 0
            new_order = max_order + 1

        source_chat_id = None
        source_message_id = None

        if STORAGE_CHANNEL_ID:
            try:
                from telegram import Bot
                bot = Bot(token=BOT_TOKEN)
                with open(filepath, "rb") as f:
                    sent = await bot.send_document(chat_id=STORAGE_CHANNEL_ID, document=f)
                source_chat_id = STORAGE_CHANNEL_ID
                source_message_id = sent.message_id
                ui.notify(f"📤 Uploaded to channel (msg #{sent.message_id})", type="info")
            except Exception as e:
                ui.notify(f"⚠️ Upload failed: {e}", type="warning")
                logger.error(f"Upload failed for {filepath}: {e}")

        async with AsyncSessionLocal() as session:
            btn = DraftMenuButton(
                parent_id=parent_id,
                title=title,
                button_type="link",
                order_index=new_order,
                button_style=color,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                credit_text=credit_text if credit_text else None,
                extra_sources=None,
            )
            session.add(btn)
            await session.commit()

        state["approved"].add(idx)
        suggestion_cards.refresh()
        stats_bar.refresh()
        ui.notify(f"✅ '{title}' approved and saved to database!", type="positive")
    except Exception as e:
        ui.notify(f"❌ Error: {e}", type="negative")
        logger.exception(f"Failed to approve suggestion {idx}")

async def _load_suggestions():
    if not os.path.exists(SUGGESTIONS_FILE):
        ui.notify("❌ suggestions.json not found. Ask the AI to analyze files first.", type="negative")
        return
    try:
        with open(SUGGESTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        state["suggestions"] = data
        state["approved"] = set()
        state["synced"] = False
        suggestion_cards.refresh()
        stats_bar.refresh()
        tree_panel.refresh()
        ui.notify(f"📥 Loaded {len(data)} suggestions!", type="positive")
    except Exception as e:
        ui.notify(f"❌ Failed to load suggestions: {e}", type="negative")

@ui.refreshable
def tree_panel():
    ui.label("📂 Menu Structure").classes("text-lg font-bold mb-2")
    buttons = state.get("_menu_buttons", [])
    if not buttons:
        ui.label("No menu data loaded.").classes("text-gray-400")
        return
    roots = [b for b in buttons if b.parent_id is None]
    for root in sorted(roots, key=lambda x: x.order_index):
        _render_tree_node(root, buttons, 0)

def _render_tree_node(btn, all_buttons, depth):
    indent = "&nbsp;&nbsp;" * depth
    label = f"{indent}{btn.title}"
    if btn.button_type == "menu":
        label = f"📁 {label}"
    elif btn.button_type == "link":
        label = f"📎 {label}"
    elif btn.button_type == "fb":
        label = f"🔗 {label}"

    is_highlighted = any(
        s.get("suggested_parent_id") == btn.id for s in state["suggestions"]
        if s.get("suggested_parent_id") is not None
    )
    extra = " 🎯" if is_highlighted else ""
    color = "text-blue-600 font-bold" if is_highlighted else ""

    ui.html(f"<div class='{color}'>{label}{extra}</div>").classes("py-0.5 text-sm")

    children = [b for b in all_buttons if b.parent_id == btn.id]
    for child in sorted(children, key=lambda x: x.order_index):
        _render_tree_node(child, all_buttons, depth + 1)

async def refresh_data(silent=False):
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(DraftMenuButton).order_by(DraftMenuButton.order_index)
            )
            state["_menu_buttons"] = result.scalars().all()
            result2 = await session.execute(
                select(CreditTemplate).order_by(CreditTemplate.id)
            )
            state["_credit_templates"] = result2.scalars().all()
        tree_panel.refresh()
        suggestion_cards.refresh()
        stats_bar.refresh()
        if not silent:
            ui.notify("🔄 Data refreshed from database", type="positive")
    except Exception as e:
        logger.exception("Refresh failed")
        if not silent:
            ui.notify(f"❌ Refresh failed: {e}", type="negative")

def open_template_manager():
    with ui.dialog() as dialog, ui.card().classes("min-w-[500px] p-6"):
        ui.label("💳 Credit Templates").classes("text-xl font-bold mb-4")
        templates = state.get("_credit_templates", [])

        for t in templates:
            with ui.row().classes("w-full items-center gap-2 py-1"):
                ui.label(f"{t.name}:").classes("font-bold min-w-[130px]")
                ui.label(f"{t.text[:40]}").classes("text-gray-600 flex-grow")
                async def del_t(tid=t.id, d=dialog):
                    await _delete_template(tid, d)
                ui.button("🗑", color="red", on_click=del_t)

        ui.separator().classes("my-3")
        with ui.row().classes("w-full gap-2 items-end"):
            new_name = ui.input("Template name").classes("flex-grow")
            new_text = ui.input("Credit text").classes("flex-grow")
            async def add_template():
                name = new_name.value.strip()
                text = new_text.value.strip()
                if not name:
                    ui.notify("Name required", type="warning")
                    return
                async with AsyncSessionLocal() as session:
                    session.add(CreditTemplate(name=name, text=text))
                    await session.commit()
                await refresh_data()
                dialog.close()
                open_template_manager()
                ui.notify(f"✅ Template '{name}' added", type="positive")
            ui.button("+ Add", color="green", on_click=add_template)

        ui.separator().classes("my-3")
        ui.button("Close", on_click=dialog.close).props("flat")
    dialog.open()

async def _delete_template(template_id, dialog):
    async with AsyncSessionLocal() as session:
        await session.execute(delete(CreditTemplate).where(CreditTemplate.id == template_id))
        await session.commit()
    await refresh_data()
    dialog.close()
    open_template_manager()
    ui.notify("🗑 Template deleted", type="info")

@ui.page("/")
def main_page():
    ui.page_title("Fast Forward Content Manager")
    if (Path(__file__).parent / "static").exists():
        app.add_static_files("/static", Path(__file__).parent / "static")

    with ui.header(elevated=True).classes("items-center justify-between bg-gray-900 text-white p-3"):
        ui.label("📁 Fast Forward Content Manager").classes("text-xl font-bold")
        async def refresh():
            await refresh_data()
        ui.button("🔄 Refresh Data", on_click=refresh).props("flat color=white")

    with ui.row().classes("w-full h-[calc(100vh-120px)]"):
        with ui.column().classes("w-1/3 h-full overflow-y-auto border-r border-gray-200 p-4"):
            tree_panel()
            ui.button("📂 Create New Folder", icon="add", color="blue", on_click=lambda: ui.notify("Coming soon — use /admin for now", type="info")).classes("mt-4")

        with ui.column().classes("w-2/3 h-full overflow-y-auto p-4"):
            stats_bar()
            ui.separator().classes("my-2")
            suggestion_cards()

    with ui.footer().classes("bg-gray-100 p-3 items-center justify-between"):
        with ui.row().classes("w-full items-center gap-4"):
            async def load_suggestions():
                await _load_suggestions()
            ui.button("🤖 Load AI Suggestions", icon="file_upload", color="blue", on_click=load_suggestions)
            ui.button("💳 Manage Credit Templates", icon="credit_card", color="gray", on_click=open_template_manager)
            ui.space()
            ui.label("Backup: bot_local_backup exists ✓").classes("text-xs text-green-600 italic")

async def startup():
    await init_db()
    await refresh_data(silent=True)
    logger.info("Content Manager ready — database initialized")

app.on_startup(startup)

ui.run(host="localhost", port=8080, title="Fast Forward Content Manager", reload=False)
