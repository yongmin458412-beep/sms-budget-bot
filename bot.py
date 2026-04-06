"""텔레그램 봇 — SMS 수신, 커맨드, 인라인 키보드."""
from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db
import excel
import notion
from config import ALLOWED_CHAT_IDS
from parser import auto_categorize, parse_sms

log = logging.getLogger(__name__)


# ---- 포맷 헬퍼 ----

def _fmt(n: int) -> str:
    return f"{n:,}"


def _summary_text(title: str, data: dict) -> str:
    lines = [
        f"📊 {title}",
        "━" * 16,
        f"💰 입금: {_fmt(data['income'])}원 ({data['income_count']}건)",
        f"💸 출금: {_fmt(data['expense'])}원 ({data['expense_count']}건)",
        "━" * 16,
    ]
    net = data["net"]
    sign = "+" if net >= 0 else ""
    lines.append(f"📈 합계: {sign}{_fmt(net)}원")
    return "\n".join(lines)


def _nav_buttons(current_year: int, current_month: int) -> list[list[InlineKeyboardButton]]:
    prev_m = current_month - 1
    prev_y = current_year
    if prev_m < 1:
        prev_m = 12
        prev_y -= 1
    next_m = current_month + 1
    next_y = current_year
    if next_m > 12:
        next_m = 1
        next_y += 1
    return [
        [
            InlineKeyboardButton("◀ 이전달", callback_data=f"sum:{prev_y}-{prev_m:02d}"),
            InlineKeyboardButton("다음달 ▶", callback_data=f"sum:{next_y}-{next_m:02d}"),
        ],
        [
            InlineKeyboardButton("📥 엑셀 내보내기", callback_data=f"excel:{current_year}-{current_month:02d}"),
        ],
    ]


def _quick_buttons() -> InlineKeyboardMarkup:
    today = date.today()
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("오늘", callback_data="sum:today"),
            InlineKeyboardButton("이번주", callback_data="sum:week"),
            InlineKeyboardButton("이번달", callback_data=f"sum:{today.year}-{today.month:02d}"),
        ],
    ])


# ---- 핸들러 ----

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "💰 SMS 가계부 봇\n\n"
        "문자 알림을 전달하면 자동으로 입출금을 기록합니다.\n\n"
        "📋 명령어:\n"
        "/today — 오늘 요약\n"
        "/week — 이번 주 요약\n"
        "/month — 이번 달 요약\n"
        "/excel — 엑셀 내보내기\n"
        "/help — 도움말",
        reply_markup=_quick_buttons(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📋 사용법\n\n"
        "1. 휴대폰 문자 알림 앱에서 이 봇으로 SMS를 전달하세요\n"
        "2. 봇이 자동으로 입금/출금을 인식하고 저장합니다\n"
        "3. 아래 명령어로 요약을 확인하세요\n\n"
        "/today — 오늘 입출금 요약\n"
        "/week — 이번 주 요약\n"
        "/month — 이번 달 요약 (버튼으로 월 이동 가능)\n"
        "/excel — 이번 달 엑셀 파일 다운로드",
    )


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = date.today()
    data = await db.get_daily_summary(today.isoformat())
    text = _summary_text(f"오늘 ({today.isoformat()})", data)
    await update.message.reply_text(text, reply_markup=_quick_buttons())


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = date.today()
    start = today - timedelta(days=today.weekday())  # 월요일
    data = await db.get_range_summary(start.isoformat(), today.isoformat())
    text = _summary_text(f"이번 주 ({start.isoformat()} ~ {today.isoformat()})", data)
    await update.message.reply_text(text, reply_markup=_quick_buttons())


async def cmd_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = date.today()
    data = await db.get_monthly_summary(today.year, today.month)
    text = _summary_text(f"{today.year}년 {today.month}월", data)
    buttons = _nav_buttons(today.year, today.month)
    buttons.insert(0, [
        InlineKeyboardButton("오늘", callback_data="sum:today"),
        InlineKeyboardButton("이번주", callback_data="sum:week"),
    ])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def cmd_excel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = date.today()
    msg = await update.message.reply_text("📊 엑셀 파일 생성 중...")
    path = await excel.generate_excel(today.year, today.month)
    with open(path, "rb") as f:
        await update.message.reply_document(
            document=f,
            filename=f"가계부_{today.year}_{today.month:02d}.xlsx",
            caption=f"📊 {today.year}년 {today.month}월 가계부",
        )
    await msg.delete()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """텍스트 메시지 수신 — SMS 파싱 시도."""
    if not update.message or not update.message.text:
        return

    chat_id = update.message.chat_id
    if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
        return

    text = update.message.text.strip()
    result = parse_sms(text)

    if not result:
        # 인식 못하는 메시지는 무시 (노이즈 방지)
        return

    today = date.today()
    tx_date = result.date_str or today.isoformat()
    category = auto_categorize(result.description)

    row_id = await db.insert_transaction(
        date=tx_date,
        time_str=result.time_str,
        tx_type=result.tx_type,
        amount=result.amount,
        description=result.description,
        bank=result.bank,
        balance=result.balance,
        category=category,
        raw_message=text,
        chat_id=chat_id,
    )

    if row_id is None:
        await update.message.reply_text("⚠️ 이미 기록된 메시지입니다.")
        return

    # Notion 동기화 (비동기, 실패해도 무시)
    await notion.add_transaction(
        tx_date=tx_date,
        tx_type=result.tx_type,
        amount=result.amount,
        description=result.description,
        bank=result.bank,
        category=category,
        balance=result.balance,
    )

    icon = "💰" if result.tx_type == "입금" else "💸"
    cat_text = f" [{category}]" if category else ""
    desc_text = f" {result.description}" if result.description else ""
    bal_text = f"\n잔액: {_fmt(result.balance)}원" if result.balance else ""

    await update.message.reply_text(
        f"{icon} {result.tx_type} {_fmt(result.amount)}원 ({result.bank}){desc_text}{cat_text}{bal_text}\n\n✅ 저장 완료",
        reply_markup=_quick_buttons(),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """인라인 버튼 콜백."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "sum:today":
        today = date.today()
        s = await db.get_daily_summary(today.isoformat())
        text = _summary_text(f"오늘 ({today.isoformat()})", s)
        await query.edit_message_text(text, reply_markup=_quick_buttons())

    elif data == "sum:week":
        today = date.today()
        start = today - timedelta(days=today.weekday())
        s = await db.get_range_summary(start.isoformat(), today.isoformat())
        text = _summary_text(f"이번 주 ({start.isoformat()} ~ {today.isoformat()})", s)
        await query.edit_message_text(text, reply_markup=_quick_buttons())

    elif data.startswith("sum:"):
        # sum:YYYY-MM
        ym = data[4:]
        parts = ym.split("-")
        year, month = int(parts[0]), int(parts[1])
        s = await db.get_monthly_summary(year, month)
        text = _summary_text(f"{year}년 {month}월", s)
        buttons = _nav_buttons(year, month)
        buttons.insert(0, [
            InlineKeyboardButton("오늘", callback_data="sum:today"),
            InlineKeyboardButton("이번주", callback_data="sum:week"),
        ])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    elif data.startswith("excel:"):
        ym = data[6:]
        parts = ym.split("-")
        year, month = int(parts[0]), int(parts[1])
        await query.edit_message_text("📊 엑셀 파일 생성 중...")
        path = await excel.generate_excel(year, month)
        with open(path, "rb") as f:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=f,
                filename=f"가계부_{year}_{month:02d}.xlsx",
                caption=f"📊 {year}년 {month}월 가계부",
            )


# ---- 앱 빌드 ----

def build_application(token: str) -> Application:
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("month", cmd_month))
    app.add_handler(CommandHandler("excel", cmd_excel))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))

    return app
