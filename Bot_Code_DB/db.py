import asyncio
import datetime
import json
import os
import socket
import time
from datetime import timedelta
import psycopg
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, Integer, BigInteger, String, Boolean, ForeignKey, DateTime, Text, select, delete, event
from config import DATABASE_URL, ADMIN_IDS

# ─── Failover constants ────────────────────────────────────────────────
LOCK_STALE_SECONDS = 30
LOCK_INSTANCE_NAME = os.getenv("BOT_INSTANCE_NAME") or socket.gethostname()

# ─── Raw PostgreSQL DSN (for sync operations) ─────────────────────────
# Strip +asyncpg and sslmode params for psycopg
_RAW_PG_DSN = (DATABASE_URL or "").strip()
if "+asyncpg" in _RAW_PG_DSN:
    _RAW_PG_DSN = _RAW_PG_DSN.replace("+asyncpg", "")

# ─── PostgreSQL engine (async) ────────────────────────────────────────
_pg_url = (DATABASE_URL or "").strip()
if _pg_url and ("postgres://" in _pg_url or "postgresql://" in _pg_url):
    if _pg_url.startswith("postgres://"):
        _pg_url = _pg_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif _pg_url.startswith("postgresql://") and "+asyncpg" not in _pg_url and "+psycopg" not in _pg_url:
        _pg_url = _pg_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    _connect_args = {}
    if "sslmode=require" in _pg_url:
        _pg_url = _pg_url.replace("?sslmode=require", "").replace("&sslmode=require", "")
        _connect_args["ssl"] = "require"

    engine_pg = create_async_engine(_pg_url, echo=False, connect_args=_connect_args or None)
    AsyncSessionPG = async_sessionmaker(bind=engine_pg, class_=AsyncSession, expire_on_commit=False)
else:
    engine_pg = None
    AsyncSessionPG = None

# ─── SQLite engine (fast local reads/writes) ──────────────────────────
engine = create_async_engine("sqlite+aiosqlite:///bot_local.db", echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


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
    button_type = Column(String, nullable=False)
    order_index = Column(Integer, default=0)
    button_style = Column(String, nullable=True)
    source_chat_id = Column(BigInteger, nullable=True)
    source_message_id = Column(Integer, nullable=True)
    credit_text = Column(String, nullable=True)
    extra_sources = Column(Text, nullable=True)

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

    id = Column(Integer, primary_key=True)
    parent_id = Column(Integer, ForeignKey("production_menu_buttons.id", ondelete="CASCADE"), nullable=True)
    title = Column(String, nullable=False)
    button_type = Column(String, nullable=False)
    order_index = Column(Integer, default=0)
    button_style = Column(String, nullable=True)
    source_chat_id = Column(BigInteger, nullable=True)
    source_message_id = Column(Integer, nullable=True)
    credit_text = Column(String, nullable=True)
    extra_sources = Column(Text, nullable=True)

class BotLock(Base):
    __tablename__ = "bot_lock"

    id = Column(Integer, primary_key=True)
    instance_name = Column(String, nullable=False)
    last_heartbeat = Column(DateTime, nullable=False)

# ─── All tables that get synced between SQLite and PostgreSQL ─────────
SYNC_TABLES = [User, CreditTemplate, DraftMenuButton, Broadcast, BroadcastRecipient, ProductionMenuButton]


async def init_db():
    """Create tables in both SQLite and PostgreSQL."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if engine_pg is not None:
        async with engine_pg.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # Bootstrap admins + credit templates (SQLite only; PG gets them via sync)
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


# ─── Sync helpers ─────────────────────────────────────────────────────

async def pull_from_postgres():
    """Pull all data from PostgreSQL → replace local SQLite (called on startup)."""
    if engine_pg is None:
        return

    async with AsyncSessionPG() as pg_session:
        for table_class in SYNC_TABLES:
            rows = (await pg_session.execute(select(table_class))).scalars().all()
            if not rows:
                continue

            async with AsyncSessionLocal() as local_session:
                await local_session.execute(delete(table_class))
                # Sort by id so parents are inserted before children (self-referencing FK)
                for row in sorted(rows, key=lambda r: getattr(r, 'id', 0)):
                    state = {k: v for k, v in row.__dict__.items() if not k.startswith('_')}
                    new_row = table_class(**state)
                    local_session.add(new_row)
                await local_session.commit()

            print(f"  Synced {table_class.__tablename__}: {len(rows)} rows")


async def push_table_to_postgres(table_class):
    """Push one table from SQLite → PostgreSQL (non-blocking, fire-and-forget)."""
    if engine_pg is None:
        return
    asyncio.create_task(_push_table_impl(table_class))

async def _push_table_impl(table_class):
    try:
        async with AsyncSessionLocal() as local_session:
            rows = (await local_session.execute(select(table_class))).scalars().all()

        async with AsyncSessionPG() as pg_session:
            await pg_session.execute(delete(table_class))
            for row in sorted(rows, key=lambda r: getattr(r, 'id', 0)):
                state = {k: v for k, v in row.__dict__.items() if not k.startswith('_')}
                new_row = table_class(**state)
                pg_session.add(new_row)
            await pg_session.commit()
    except Exception as e:
        print(f"Push to PostgreSQL failed for {table_class.__tablename__}: {e}")


# ─── Synchronous lock operations (called BEFORE any async code) ──────

def _sync_lock_dsn() -> str | None:
    """Return a psycopg-compatible DSN from DATABASE_URL."""
    raw = (DATABASE_URL or "").strip()
    if not raw or ("postgres" not in raw and "postgresql" not in raw):
        return None
    raw = raw.replace("+asyncpg", "")
    return raw


def sync_ensure_lock_table():
    """Create the bot_lock table if it doesn't exist (synchronous)."""
    dsn = _sync_lock_dsn()
    if not dsn:
        return
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_lock (
                    id INTEGER PRIMARY KEY,
                    instance_name TEXT NOT NULL,
                    last_heartbeat TIMESTAMP NOT NULL
                )
            """)
        conn.commit()


def sync_acquire_lock() -> bool:
    """Atomically acquire the failover lock. Returns True if acquired.

    Uses a single UPDATE with a WHERE guard — PostgreSQL serializes
    this so two concurrent calls cannot both return True.
    """
    dsn = _sync_lock_dsn()
    if not dsn:
        return True  # No PG configured, always "acquired"

    now = datetime.datetime.utcnow()
    stale_before = now - timedelta(seconds=LOCK_STALE_SECONDS)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_lock (id, instance_name, last_heartbeat) "
                "VALUES (1, %s, %s) ON CONFLICT DO NOTHING",
                (LOCK_INSTANCE_NAME, now)
            )
            if cur.rowcount > 0:
                conn.commit()
                return True

            cur.execute(
                "UPDATE bot_lock SET instance_name = %s, last_heartbeat = %s "
                "WHERE id = 1 AND (instance_name = %s OR last_heartbeat < %s)",
                (LOCK_INSTANCE_NAME, now, LOCK_INSTANCE_NAME, stale_before)
            )
            conn.commit()
            return cur.rowcount > 0


def sync_release_lock():
    """Mark the lock as stale so another instance can claim it."""
    dsn = _sync_lock_dsn()
    if not dsn:
        return

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bot_lock SET last_heartbeat = %s "
                "WHERE id = 1 AND instance_name = %s",
                (datetime.datetime.utcnow() - timedelta(seconds=3600), LOCK_INSTANCE_NAME)
            )
        conn.commit()


# ─── Draft → Production sync ──────────────────────────────────────────

async def sync_draft_to_production():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(delete(ProductionMenuButton))

            draft_result = await session.execute(select(DraftMenuButton))
            draft_buttons = draft_result.scalars().all()

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
