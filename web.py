"""FastAPI 웹 대시보드 — 가계부."""
from __future__ import annotations

import calendar
import hmac
import os
from datetime import date
from typing import Optional

from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

import database as db
from config import ADMIN_PASSWORD, SESSION_SECRET

COOKIE_NAME = "session"
serializer = URLSafeSerializer(SESSION_SECRET, salt="auth")


def _make_cookie() -> str:
    return serializer.dumps({"auth": True})


def _verify_cookie(value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        data = serializer.loads(value)
        return bool(data.get("auth"))
    except BadSignature:
        return False


async def require_login(session: Optional[str] = Cookie(default=None, alias=COOKIE_NAME)):
    if not _verify_cookie(session):
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return True


def _month_nav(year: int, month: int):
    pm = month - 1
    py = year
    if pm < 1:
        pm, py = 12, year - 1
    nm = month + 1
    ny = year
    if nm > 12:
        nm, ny = 1, year + 1
    return py, pm, ny, nm


def create_app(ptb_app=None) -> FastAPI:
    app = FastAPI(title="SMS 가계부 대시보드")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates = Jinja2Templates(directory=os.path.join(base_dir, "templates"))
    static_dir = os.path.join(base_dir, "static")
    if os.path.isdir(static_dir):
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # ---- Auth ----

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, error: Optional[str] = None):
        return templates.TemplateResponse(request, "login.html", {"error": error})

    @app.post("/login")
    async def login_submit(password: str = Form(...)):
        if hmac.compare_digest(password, ADMIN_PASSWORD):
            resp = RedirectResponse(url="/", status_code=303)
            resp.set_cookie(COOKIE_NAME, _make_cookie(), httponly=True, samesite="lax", max_age=7 * 24 * 3600)
            return resp
        return RedirectResponse(url="/login?error=1", status_code=303)

    @app.get("/logout")
    async def logout():
        resp = RedirectResponse(url="/login", status_code=303)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    # ---- Dashboard ----

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request, _=Depends(require_login),
                        year: Optional[int] = None, month: Optional[int] = None):
        today = date.today()
        y = year or today.year
        m = month or today.month
        py, pm, ny, nm = _month_nav(y, m)

        summary = await db.get_monthly_summary(y, m)
        tx_count = await db.count_transactions_for_month(y, m)
        chart = await db.get_yearly_chart_data(y)
        cats = await db.get_category_breakdown(y, m)
        recent = await db.get_recent_transactions(20)

        # 카테고리 파이 데이터 (출금만)
        expense_cats = [c for c in cats if c["type"] == "출금"]
        pie_data = {
            "labels": [c["cat"] for c in expense_cats],
            "values": [c["total"] for c in expense_cats],
        }

        chart_labels = [f"{i}월" for i in chart["months"]]

        return templates.TemplateResponse(request, "dashboard.html", {
            "nav": "dashboard",
            "year": y, "month": m,
            "prev_year": py, "prev_month": pm,
            "next_year": ny, "next_month": nm,
            "summary": summary,
            "tx_count": tx_count,
            "chart_labels": chart_labels,
            "chart_incomes": chart["incomes"],
            "chart_expenses": chart["expenses"],
            "pie_data": pie_data,
            "recent": recent,
        })

    # ---- Calendar ----

    @app.get("/calendar", response_class=HTMLResponse)
    async def calendar_page(request: Request, _=Depends(require_login),
                            year: Optional[int] = None, month: Optional[int] = None):
        today = date.today()
        y = year or today.year
        m = month or today.month
        py, pm, ny, nm = _month_nav(y, m)

        cal_data = await db.get_calendar_data(y, m)
        summary = await db.get_monthly_summary(y, m)

        # 주 단위 배열 생성
        first_weekday, num_days = calendar.monthrange(y, m)
        weeks: list[list[int]] = []
        week: list[int] = [0] * first_weekday
        for d in range(1, num_days + 1):
            week.append(d)
            if len(week) == 7:
                weeks.append(week)
                week = []
        if week:
            week.extend([0] * (7 - len(week)))
            weeks.append(week)

        return templates.TemplateResponse(request, "calendar.html", {
            "nav": "calendar",
            "year": y, "month": m,
            "prev_year": py, "prev_month": pm,
            "next_year": ny, "next_month": nm,
            "weeks": weeks,
            "cal_data": cal_data,
            "summary": summary,
        })

    # ---- Transactions ----

    @app.get("/transactions", response_class=HTMLResponse)
    async def transactions_page(request: Request, _=Depends(require_login),
                                start: Optional[str] = None, end: Optional[str] = None,
                                type: Optional[str] = None, category: Optional[str] = None):
        today = date.today()
        s = start or f"{today.year}-{today.month:02d}-01"
        e = end or today.isoformat()

        txns = await db.get_transactions_for_range(s, e)

        # 필터 적용
        if type:
            txns = [t for t in txns if t["type"] == type]
        if category:
            txns = [t for t in txns if t.get("category") == category]

        total_income = sum(t["amount"] for t in txns if t["type"] == "입금")
        total_expense = sum(t["amount"] for t in txns if t["type"] == "출금")

        # 카테고리 목록 (필터 드롭다운용)
        all_txns = await db.get_transactions_for_range(s, e)
        categories = sorted(set(t.get("category") or "기타" for t in all_txns))

        return templates.TemplateResponse(request, "transactions.html", {
            "nav": "transactions",
            "start": s, "end": e,
            "filter_type": type or "",
            "filter_cat": category or "",
            "transactions": txns,
            "total_income": total_income,
            "total_expense": total_expense,
            "categories": categories,
        })

    return app
