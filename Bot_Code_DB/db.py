import datetime
import json
from datetime import timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, Integer, BigInteger, String, Boolean, ForeignKey, DateTime, Text, select, delete, event
from config import DATABASE_URL, ADMIN_IDS

# Adjust DATABASE_URL for async SQLAlchemy driver
_db_url = (DATABASE_URL or "").strip()
if not _db_url:
    _db_url = "sqlite+aiosqlite:///bot_local.db"
elif _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _db_url.startswith("postgresql://") and "+asyncpg" not in _db_url and "+psycopg" not in _db_url:
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Handle sslmode for asyncpg (which uses 'ssl' param instead of 'sslmode')
_connect_args = {}
if "sslmode=require" in _db_url:
    _db_url = _db_url.replace("?sslmode=require", "").replace("&sslmode=require", "")
    _connect_args["ssl"] = "require"

# Create async engine and session factory
engine_kwargs = dict(echo=False)
if _connect_args:
    engine_kwargs["connect_args"] = _connect_args
engine = create_async_engine(_db_url, **engine_kwargs)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

# Enable foreign key enforcement for SQLite (needed for CASCADE deletes)
# PostgreSQL enforces FK constraints natively.
if "sqlite" in _db_url:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    user_id = Column(BigInteger, primary_key=True)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    is_admin = Column(Boolean, default=False)
    joined_at = Column(DateTime, default=datetime.datetime.utcnow)

class CreditTemplate(Base):
    __tablename__ = "credit_templates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    text = Column(String, nullable=False)

class DraftMenuButton(Base):
    __tablename__ = "draft_menu_buttons"

    id = Column(Integer, primary_key=True, autoincrement=True)
    parent_id = Column(Integer, ForeignKey("draft_menu_buttons.id", ondelete="CASCADE"), nullable=True)
    title = Column(String, nullable=False)
    button_type = Column(String, nullable=False)  # 'menu', 'link', 'feedback'
    order_index = Column(Integer, default=0)
    button_style = Column(String, nullable=True)  # Store native style e.g., 'primary', 'success', 'danger'
    source_chat_id = Column(BigInteger, nullable=True)
    source_message_id = Column(Integer, nullable=True)
    credit_text = Column(String, nullable=True)
    extra_sources = Column(Text, nullable=True)  # JSON list: [{"chat_id":..., "message_id":...}, ...]

class Broadcast(Base):
    __tablename__ = "broadcasts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    text = Column(Text, nullable=False)
    parse_mode = Column(String, default="Markdown")
    sent_at = Column(DateTime, default=datetime.datetime.utcnow)
    sent_count = Column(Integer, default=0)
    fail_count = Column(Integer, default=0)
    recipients = relationship("BroadcastRecipient", backref="broadcast", cascade="all, delete-orphan",
                              passive_deletes=True)

class BroadcastRecipient(Base):
    __tablename__ = "broadcast_recipients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    broadcast_id = Column(Integer, ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(BigInteger, nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(Integer, nullable=False)

class ProductionMenuButton(Base):
    __tablename__ = "production_menu_buttons"

    id = Column(Integer, primary_key=True)  # No autoincrement here because we copy exact IDs from draft
    parent_id = Column(Integer, ForeignKey("production_menu_buttons.id", ondelete="CASCADE"), nullable=True)
    title = Column(String, nullable=False)
    button_type = Column(String, nullable=False)  # 'menu', 'link', 'feedback'
    order_index = Column(Integer, default=0)
    button_style = Column(String, nullable=True)
    source_chat_id = Column(BigInteger, nullable=True)
    source_message_id = Column(Integer, nullable=True)
    credit_text = Column(String, nullable=True)
    extra_sources = Column(Text, nullable=True)  # JSON list

class BotLock(Base):
    __tablename__ = "bot_lock"

    id = Column(Integer, primary_key=True)  # Always 1 (singleton row)
    instance_name = Column(String, nullable=False)
    last_heartbeat = Column(DateTime, nullable=False)

# Helper to initialize DB
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Auto-bootstrap admins from config
    async with AsyncSessionLocal() as session:
        for admin_id in ADMIN_IDS:
            result = await session.execute(select(User).filter_by(user_id=admin_id))
            user = result.scalars().first()
            if not user:
                new_admin = User(user_id=admin_id, first_name="Bootstrap Admin", is_admin=True)
                session.add(new_admin)
            else:
                if not user.is_admin:
                    user.is_admin = True
        await session.commit()
    
    # Prepopulate credit templates if table is empty
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(CreditTemplate).limit(1))
        if not result.scalars().first():
            templates = [
                CreditTemplate(name="بدون", text=""),
                CreditTemplate(name="القناة الرئيسية", text="https://t.me/tasreee3"),
                CreditTemplate(name="قناة الزهراء", text="https://t.me/alzahraa_tasree3"),
                CreditTemplate(name="قناة هولمز", text="https://t.me/holmes1j"),
            ]
            session.add_all(templates)
            await session.commit()

# Helper to sync Draft menu to Production menu
async def sync_draft_to_production():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. Clear production menu buttons
            await session.execute(delete(ProductionMenuButton))
            
            # 2. Fetch all draft menu buttons
            draft_result = await session.execute(select(DraftMenuButton))
            draft_buttons = draft_result.scalars().all()
            
            # 3. Insert into production, preserving the exact same IDs and parent IDs
            sorted_draft = sorted(draft_buttons, key=lambda x: x.id)
            
            for db in sorted_draft:
                pb = ProductionMenuButton(
                    id=db.id,
                    parent_id=db.parent_id,
                    title=db.title,
                    button_type=db.button_type,
                    order_index=db.order_index,
                    button_style=db.button_style,
                    source_chat_id=db.source_chat_id,
                    source_message_id=db.source_message_id,
                    credit_text=db.credit_text,
                    extra_sources=db.extra_sources
                )
                session.add(pb)
