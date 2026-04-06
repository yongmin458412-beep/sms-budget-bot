"""aiosqlite 기반 비동기 DB 레이어 — 가계부 거래 내역."""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import aiosqlite

from config import DB_PATH

# ---------------- 연결 헬퍼 ----------------

@asynccontextmanager
async def _conn() -> AsyncIterator[aiosqlite.Connection]:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    try:
        await db.execute("PRAGMA journal_mode = WAL")
        yield db
    finally:
        await db.close()


# ---------------- 초기화 ----------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    time TEXT,
    type TEXT NOT NULL,
    amount INTEGER NOT NULL,
    description TEXT,
    bank TEXT,
    balance INTEGER,
    category TEXT,
    raw_message TEXT NOT NULL UNIQUE,
    chat_id INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_type ON transactions(type);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


async def init_db() -> None:
    async with _conn() as db:
        await db.executescript(SCHEMA)
        await db.commit()


# ---------------- settings ----------------

async def get_setting(key: str) -> Optional[str]:
    async with _conn() as db:
        row = await (await db.execute("SELECT value FROM settings WHERE key=?", (key,))).fetchone()
        return row["value"] if row else None


async def set_setting(key: str, value: str) -> None:
    async with _conn() as db:
        await db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await db.commit()


# ---------------- 거래 추가 ----------------

async def insert_transaction(
    *,
    date: str,
    time_str: Optional[str],
    tx_type: str,
    amount: int,
    description: Optional[str],
    bank: Optional[str],
    balance: Optional[int],
    category: Optional[str],
    raw_message: str,
    chat_id: Optional[int],
) -> Optional[int]:
    """거래 저장. 중복(raw_message 동일)이면 None 반환."""
    async with _conn() as db:
        try:
            cur = await db.execute(
                """INSERT INTO transactions
                   (date, time, type, amount, description, bank, balance, category, raw_message, chat_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (date, time_str, tx_type, amount, description, bank, balance, category, raw_message, chat_id, int(time.time())),
            )
            await db.commit()
            return cur.lastrowid
        except aiosqlite.IntegrityError:
            return None


# ---------------- 조회 ----------------

async def get_transactions_for_range(start: str, end: str) -> list[dict]:
    async with _conn() as db:
        rows = await (await db.execute(
            "SELECT * FROM transactions WHERE date BETWEEN ? AND ? ORDER BY date, time",
            (start, end),
        )).fetchall()
        return [dict(r) for r in rows]


async def get_daily_summary(date_str: str) -> dict:
    async with _conn() as db:
        income = (await (await db.execute(
            "SELECT COALESCE(SUM(amount),0) as t, COUNT(*) as c FROM transactions WHERE type='입금' AND date=?",
            (date_str,),
        )).fetchone())
        expense = (await (await db.execute(
            "SELECT COALESCE(SUM(amount),0) as t, COUNT(*) as c FROM transactions WHERE type='출금' AND date=?",
            (date_str,),
        )).fetchone())
        return {
            "date": date_str,
            "income": income["t"], "income_count": income["c"],
            "expense": expense["t"], "expense_count": expense["c"],
            "net": income["t"] - expense["t"],
        }


async def get_range_summary(start: str, end: str) -> dict:
    async with _conn() as db:
        income = (await (await db.execute(
            "SELECT COALESCE(SUM(amount),0) as t, COUNT(*) as c FROM transactions WHERE type='입금' AND date BETWEEN ? AND ?",
            (start, end),
        )).fetchone())
        expense = (await (await db.execute(
            "SELECT COALESCE(SUM(amount),0) as t, COUNT(*) as c FROM transactions WHERE type='출금' AND date BETWEEN ? AND ?",
            (start, end),
        )).fetchone())
        return {
            "start": start, "end": end,
            "income": income["t"], "income_count": income["c"],
            "expense": expense["t"], "expense_count": expense["c"],
            "net": income["t"] - expense["t"],
        }


async def get_monthly_summary(year: int, month: int) -> dict:
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-31"
    s = await get_range_summary(start, end)
    s["year"] = year
    s["month"] = month
    return s


async def get_category_breakdown(year: int, month: int) -> list[dict]:
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-31"
    async with _conn() as db:
        rows = await (await db.execute(
            """SELECT COALESCE(category,'기타') as cat, type,
                      SUM(amount) as total, COUNT(*) as cnt
               FROM transactions
               WHERE date BETWEEN ? AND ?
               GROUP BY cat, type
               ORDER BY total DESC""",
            (start, end),
        )).fetchall()
        return [dict(r) for r in rows]


async def get_yearly_chart_data(year: int) -> dict:
    """월별 수입/지출 12개월 데이터."""
    incomes = []
    expenses = []
    for m in range(1, 13):
        s = await get_monthly_summary(year, m)
        incomes.append(s["income"])
        expenses.append(s["expense"])
    return {"months": list(range(1, 13)), "incomes": incomes, "expenses": expenses}


async def get_calendar_data(year: int, month: int) -> dict[str, dict]:
    """일별 입출금 합계 딕셔너리. key = 'DD'."""
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-31"
    async with _conn() as db:
        rows = await (await db.execute(
            """SELECT SUBSTR(date,9,2) as day, type, SUM(amount) as total
               FROM transactions
               WHERE date BETWEEN ? AND ?
               GROUP BY day, type""",
            (start, end),
        )).fetchall()
    cal: dict[str, dict] = {}
    for r in rows:
        d = r["day"]
        if d not in cal:
            cal[d] = {"income": 0, "expense": 0}
        if r["type"] == "입금":
            cal[d]["income"] = r["total"]
        else:
            cal[d]["expense"] = r["total"]
    return cal


async def get_recent_transactions(limit: int = 20) -> list[dict]:
    async with _conn() as db:
        rows = await (await db.execute(
            "SELECT * FROM transactions ORDER BY date DESC, time DESC, id DESC LIMIT ?",
            (limit,),
        )).fetchall()
        return [dict(r) for r in rows]


async def count_transactions_for_month(year: int, month: int) -> int:
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month:02d}-31"
    async with _conn() as db:
        row = await (await db.execute(
            "SELECT COUNT(*) as c FROM transactions WHERE date BETWEEN ? AND ?",
            (start, end),
        )).fetchone()
        return row["c"]
