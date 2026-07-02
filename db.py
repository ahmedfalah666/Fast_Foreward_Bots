import datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, Integer, BigInteger, String, Boolean, ForeignKey, DateTime, Text, select, delete, event
from config import DATABASE_URL, ADMIN_IDS

# Adjust DATABASE_URL for async SQLAlchemy driver
_db_url = (DATABASE_URL or "").strip()
if not _db_url:
    _db_url = "sqlite+aiosqlite:///bot_test.db"
elif _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql+asyncpg://", 1)
elif _db_url.startswith("postgresql://") and "+asyncpg" not in _db_url:
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Strip query parameters (e.g. ?pgbouncer=true) that asyncpg doesn't support
if "?" in _db_url and _db_url.count("?") == 1:
    _db_url = _db_url.split("?")[0]

# Create async engine and session factory
engine = create_async_engine(_db_url, echo=False)
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
                    credit_text=db.credit_text
                )
                session.add(pb)
