"""Notion API 연동 — httpx 직접 호출."""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from config import NOTION_DB_ID, NOTION_TOKEN

log = logging.getLogger(__name__)

_BASE = "https://api.notion.com/v1"
_HEADERS = {}


def _get_headers() -> dict:
    global _HEADERS
    if not _HEADERS and NOTION_TOKEN:
        _HEADERS = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
    return _HEADERS


def _is_configured() -> bool:
    return bool(NOTION_TOKEN and NOTION_DB_ID)


# ---- 날짜 페이지 찾기/생성 ----

async def _find_page_for_date(date_str: str) -> Optional[str]:
    if not _is_configured():
        return None
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_BASE}/databases/{NOTION_DB_ID}/query",
            headers=_get_headers(),
            json={"filter": {"property": "날짜", "date": {"equals": date_str}}},
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return results[0]["id"] if results else None


async def _create_date_page(date_str: str) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_BASE}/pages",
            headers=_get_headers(),
            json={
                "parent": {"database_id": NOTION_DB_ID},
                "properties": {
                    "Name": {"title": [{"text": {"content": date_str}}]},
                    "날짜": {"date": {"start": date_str}},
                    "입금합계": {"number": 0},
                    "출금합계": {"number": 0},
                    "순이익": {"number": 0},
                },
            },
        )
        resp.raise_for_status()
        return resp.json()["id"]


async def _get_or_create_page(date_str: str) -> str:
    page_id = await _find_page_for_date(date_str)
    if page_id:
        return page_id
    return await _create_date_page(date_str)


# ---- 거래 추가 ----

async def add_transaction(
    *,
    tx_date: str,
    tx_type: str,
    amount: int,
    description: Optional[str],
    bank: Optional[str],
    category: Optional[str],
    balance: Optional[int],
) -> bool:
    if not _is_configured():
        return False

    try:
        page_id = await _get_or_create_page(tx_date)

        icon = "💰" if tx_type == "입금" else "💸"
        desc_text = description or ""
        bank_text = f"({bank})" if bank else ""
        cat_text = f"[{category}]" if category else ""
        bal_text = f" / 잔액 {balance:,}원" if balance else ""
        block_text = f"{icon} {tx_type} {amount:,}원 {bank_text} {desc_text} {cat_text}{bal_text}"

        color = "green_background" if tx_type == "입금" else "red_background"

        async with httpx.AsyncClient() as client:
            # 블록 추가
            await client.patch(
                f"{_BASE}/blocks/{page_id}/children",
                headers=_get_headers(),
                json={
                    "children": [{
                        "object": "block",
                        "type": "callout",
                        "callout": {
                            "rich_text": [{"type": "text", "text": {"content": block_text}}],
                            "icon": {"type": "emoji", "emoji": icon},
                            "color": color,
                        },
                    }],
                },
            )

        await _update_daily_totals(page_id)
        log.info(f"Notion 동기화 완료: {tx_date} {tx_type} {amount:,}원")
        return True

    except Exception as e:
        log.error(f"Notion 동기화 실패: {e}")
        return False


async def _update_daily_totals(page_id: str) -> None:
    async with httpx.AsyncClient() as client:
        # 블록 조회
        resp = await client.get(
            f"{_BASE}/blocks/{page_id}/children",
            headers=_get_headers(),
        )
        resp.raise_for_status()
        blocks = resp.json().get("results", [])

        income_total = 0
        expense_total = 0

        for block in blocks:
            if block["type"] != "callout":
                continue
            texts = block["callout"].get("rich_text", [])
            if not texts:
                continue
            content = texts[0].get("text", {}).get("content", "")
            if "입금" in content:
                income_total += _extract_amount(content)
            elif "출금" in content:
                expense_total += _extract_amount(content)

        # 속성 업데이트
        await client.patch(
            f"{_BASE}/pages/{page_id}",
            headers=_get_headers(),
            json={
                "properties": {
                    "입금합계": {"number": income_total},
                    "출금합계": {"number": expense_total},
                    "순이익": {"number": income_total - expense_total},
                },
            },
        )


def _extract_amount(text: str) -> int:
    m = re.search(r"(?:입금|출금)\s*([\d,]+)\s*원", text)
    if m:
        return int(m.group(1).replace(",", ""))
    return 0
