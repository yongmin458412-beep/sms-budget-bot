"""Notion API 연동 — 날짜별 가계부 캘린더 DB 동기화."""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from notion_client import Client

from config import NOTION_DB_ID, NOTION_TOKEN

log = logging.getLogger(__name__)

_client: Optional[Client] = None


def _get_client() -> Optional[Client]:
    global _client
    if not NOTION_TOKEN:
        return None
    if _client is None:
        _client = Client(auth=NOTION_TOKEN)
    return _client


# ---- 날짜 페이지 찾기/생성 ----

async def _find_page_for_date(date_str: str) -> Optional[str]:
    """해당 날짜의 페이지 ID 반환. 없으면 None."""
    client = _get_client()
    if not client:
        return None
    resp = client.databases.query(
        database_id=NOTION_DB_ID,
        filter={"property": "날짜", "date": {"equals": date_str}},
    )
    results = resp.get("results", [])
    return results[0]["id"] if results else None


async def _create_date_page(date_str: str) -> str:
    """날짜 페이지 생성 후 ID 반환."""
    client = _get_client()
    page = client.pages.create(
        parent={"database_id": NOTION_DB_ID},
        properties={
            "날짜": {"date": {"start": date_str}},
            "입금합계": {"number": 0},
            "출금합계": {"number": 0},
            "순이익": {"number": 0},
        },
    )
    return page["id"]


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
    """거래를 노션 캘린더 DB에 추가. 성공 시 True."""
    client = _get_client()
    if not client:
        return False

    try:
        page_id = await _get_or_create_page(tx_date)

        # 상세내역을 페이지 본문(block)으로 추가
        icon = "💰" if tx_type == "입금" else "💸"
        desc_text = description or ""
        bank_text = f"({bank})" if bank else ""
        cat_text = f"[{category}]" if category else ""
        bal_text = f" / 잔액 {balance:,}원" if balance else ""

        block_text = f"{icon} {tx_type} {amount:,}원 {bank_text} {desc_text} {cat_text}{bal_text}"

        # 색상: 입금=green, 출금=red
        color = "green" if tx_type == "입금" else "red"

        client.blocks.children.append(
            block_id=page_id,
            children=[
                {
                    "object": "block",
                    "type": "callout",
                    "callout": {
                        "rich_text": [{"type": "text", "text": {"content": block_text}}],
                        "icon": {"type": "emoji", "emoji": icon},
                        "color": f"{color}_background",
                    },
                }
            ],
        )

        # 날짜 페이지 속성 업데이트 (합계 재계산)
        await _update_daily_totals(page_id, tx_date)

        log.info(f"Notion 동기화 완료: {tx_date} {tx_type} {amount:,}원")
        return True

    except Exception as e:
        log.error(f"Notion 동기화 실패: {e}")
        return False


async def _update_daily_totals(page_id: str, date_str: str) -> None:
    """페이지 본문의 callout 블록을 파싱해서 합계 업데이트."""
    client = _get_client()
    if not client:
        return

    # 본문 블록 조회
    blocks = client.blocks.children.list(block_id=page_id)
    income_total = 0
    expense_total = 0

    for block in blocks.get("results", []):
        if block["type"] != "callout":
            continue
        texts = block["callout"].get("rich_text", [])
        if not texts:
            continue
        content = texts[0].get("text", {}).get("content", "")
        if "입금" in content:
            # "💰 입금 3,000,000원 ..." 에서 금액 추출
            amount = _extract_amount(content)
            income_total += amount
        elif "출금" in content:
            amount = _extract_amount(content)
            expense_total += amount

    # 속성 업데이트
    client.pages.update(
        page_id=page_id,
        properties={
            "입금합계": {"number": income_total},
            "출금합계": {"number": expense_total},
            "순이익": {"number": income_total - expense_total},
        },
    )


def _extract_amount(text: str) -> int:
    """'입금 3,000,000원' 또는 '출금 12,500원' 에서 금액 추출."""
    import re
    m = re.search(r"(?:입금|출금)\s*([\d,]+)\s*원", text)
    if m:
        return int(m.group(1).replace(",", ""))
    return 0


# ---- DB 초기 설정 가이드 ----

SETUP_GUIDE = """
📋 Notion 캘린더 DB 설정 방법:

1. Notion에서 새 데이터베이스 생성 (캘린더 뷰)
2. 속성 추가:
   - 날짜 (Date) — 기본 속성
   - 입금합계 (Number)
   - 출금합계 (Number)
   - 순이익 (Number)
3. 데이터베이스 우측 상단 ··· → 연결 → 내 Integration 연결
4. 데이터베이스 URL에서 ID 복사:
   https://notion.so/xxxxx?v=yyyyy
   → xxxxx 부분이 NOTION_DB_ID
"""
