import aiosqlite
import asyncio
import logging
from typing import Any, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("data/telemetry/observability.db")

_db: Optional[aiosqlite.Connection] = None
_write_queue: asyncio.Queue = asyncio.Queue()
_writer_task: Optional[asyncio.Task] = None

async def init_db(app_root: Path):
    global _db, _writer_task
    db_file = app_root / DB_PATH
    db_file.parent.mkdir(parents=True, exist_ok=True)
    
    _db = await aiosqlite.connect(db_file)
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    
    # Create tables
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS llm_requests (
            correlation_id TEXT PRIMARY KEY,
            route TEXT,
            backend TEXT,
            model TEXT,
            privacy TEXT,
            status TEXT,
            timestamp_start TEXT,
            timestamp_end TEXT,
            duration_ms INTEGER,
            time_to_first_token_ms INTEGER,
            tokens_in INTEGER,
            tokens_out INTEGER,
            fallback_used BOOLEAN,
            error_code TEXT,
            prompt_hash TEXT,
            response_hash TEXT,
            is_stream BOOLEAN
        )
    """)
    await _db.execute("""
        CREATE TABLE IF NOT EXISTS llm_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            correlation_id TEXT,
            timestamp TEXT,
            event_type TEXT,
            message TEXT,
            metadata TEXT
        )
    """)
    # Add indexes
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_requests_time ON llm_requests(timestamp_start DESC)")
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_requests_status ON llm_requests(status)")
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_requests_backend ON llm_requests(backend)")
    await _db.execute("CREATE INDEX IF NOT EXISTS idx_events_corr ON llm_events(correlation_id)")
    await _db.commit()

    _writer_task = asyncio.create_task(_db_writer_loop())

async def close_db():
    global _writer_task, _db
    if _writer_task:
        _writer_task.cancel()
        try:
            await _writer_task
        except asyncio.CancelledError:
            pass
    if _db:
        await _db.close()
        _db = None

async def _db_writer_loop():
    while True:
        try:
            items = []
            # Block for the first item
            item = await _write_queue.get()
            items.append(item)
            
            # Non-blocking gather up to 50 more
            while len(items) < 50:
                try:
                    items.append(_write_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            
            if _db:
                for table, data in items:
                    try:
                        columns = ', '.join(data.keys())
                        placeholders = ', '.join(['?'] * len(data))
                        values = tuple(data.values())
                        await _db.execute(f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})", values)
                    except Exception as e:
                        logger.error(f"Failed to insert into {table}: {e}")
                
                await _db.commit()
            
            for _ in items:
                _write_queue.task_done()
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Database writer loop error: {e}")
            await asyncio.sleep(1)

def queue_insert(table: str, data: Dict[str, Any]):
    try:
        _write_queue.put_nowait((table, data))
    except Exception as e:
        logger.error(f"Failed to queue db insert: {e}")

async def get_db() -> Optional[aiosqlite.Connection]:
    return _db
