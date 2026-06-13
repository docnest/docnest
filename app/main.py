"""FastAPI application for docnest — seat map, membership admin, receipts."""

import os
import re
import sqlite3
import urllib.parse
from datetime import date, timedelta

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.database import (
    DAILY_RATE,
    DEFAULT_FEE,
    add_member,
    archive_member,
    get_conn,
    get_member,
    get_receipt,
    init_db,
    list_all_payments,
    list_archived,
    list_members,
    list_payments,
    list_seats,
    mark_reminded,
    monthly_period_end,
    public_seats,
    reactivate_member,
    record_payment,
    swap_seats,
    update_member,
    update_payment,
)
from app.seed import seed

# --- Config (env with sensible defaults) ------------------------------------
ADMIN_USER = os.environ.get("DOCNEST_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("DOCNEST_ADMIN_PASS", "admin123")
SESSION_SECRET = os.environ.get("DOCNEST_SECRET", "dev-secret-change-me")
BUSINESS_NAME = os.environ.get("DOCNEST_BUSINESS_NAME", "DocNest Study Space")
BUSINESS_SUB = os.environ.get("DOCNEST_BUSINESS_SUB", "Membership Receipt")
CURRENCY = os.environ.get("DOCNEST_CURRENCY", "₹")  # ₹
# Default refundable caution deposit, pre-filled on the new-signup form.
DEFAULT_CAUTION = float(os.environ.get("DOCNEST_DEFAULT_CAUTION", "0"))

PAYMENT_MODES = ["cash", "upi", "card", "bank transfer", "other"]

# Reminders: how many days before expiry a membership shows up as "due", and
# the default country code prepended to bare local phone numbers for wa.me.
REMINDER_DAYS = int(os.environ.get("DOCNEST_REMINDER_DAYS", "10"))
COUNTRY_CODE = os.environ.get("DOCNEST_COUNTRY_CODE", "91")  # India by default

# Physical seat layout for the admin floor plan. Each seat maps to a
# (column, row) cell in its room's grid, reproducing the printed seating
# chart (two clusters + aisle in Room 1, the L-shape in Room 2). "doors"
# are decorative cells marking the entrance. Empty grid cells are aisles.
FLOOR_PLAN = {
    "Room 1": {
        "cols": 9,
        "rows": 4,
        "seats": {
            # left cluster
            "C1": (1, 1), "C2": (2, 1), "C3": (3, 1),
            "B1": (1, 2), "B2": (2, 2), "B3": (3, 2),
            "A1": (1, 4), "A2": (2, 4), "A3": (3, 4),
            # right cluster
            "C4": (5, 1), "C5": (6, 1), "C6": (7, 1), "C7": (8, 1), "C8": (9, 1),
            "B4": (5, 2), "B5": (6, 2), "B6": (7, 2), "B7": (8, 2), "B8": (9, 2),
            "A4": (5, 4), "A5": (6, 4), "A6": (7, 4), "A7": (8, 4),
        },
        "doors": [(4, 4)],
    },
    "Room 2": {
        "cols": 4,
        "rows": 5,
        "seats": {
            "D8": (1, 1), "D7": (2, 1), "D6": (3, 1), "D5": (4, 1),
            "D9": (1, 2), "D10": (2, 2), "D4": (4, 2),
            "D3": (4, 3),
            "D1": (2, 5), "D2": (3, 5),
        },
        "doors": [(1, 5)],
    },
}


def _build_floor_plan(seats, public=False):
    """Merge live seat status onto the static physical layout.

    With ``public=True`` the member name/id are omitted (student view); the
    derived status (available / booked / expired) is kept so the public map
    reflects expiry exactly like the admin view — only who sits where is hidden.
    """
    by_code = {s["code"]: s for s in seats}
    plan = []
    for room, spec in FLOOR_PLAN.items():
        cells = []
        for code, (col, row) in spec["seats"].items():
            s = by_code.get(code, {})
            cell = {"code": code, "col": col, "row": row,
                    "status": s.get("status", "available")}
            if not public:
                cell["booked_by"] = s.get("booked_by")
                cell["member_id"] = s.get("member_id")
            cells.append(cell)
        plan.append(
            {
                "room": room,
                "cols": spec["cols"],
                "rows": spec["rows"],
                "cells": cells,
                "doors": [{"col": d[0], "row": d[1]} for d in spec["doors"]],
            }
        )
    return plan

# --- App setup --------------------------------------------------------------
app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def _money(value):
    """Format a number as a currency string, e.g. ₹1,000."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{CURRENCY}{n:,.0f}" if n == int(n) else f"{CURRENCY}{n:,.2f}"


def _dmy(value):
    """Format an ISO date (YYYY-MM-DD) as DD-MM-YYYY for display."""
    if not value:
        return ""
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d-%m-%Y")
    except (TypeError, ValueError):
        return str(value)


templates.env.filters["money"] = _money
templates.env.filters["dmy"] = _dmy
templates.env.globals["business_name"] = BUSINESS_NAME
templates.env.globals["business_sub"] = BUSINESS_SUB
templates.env.globals["currency"] = CURRENCY


@app.on_event("startup")
def on_startup():
    conn = get_conn()
    try:
        init_db(conn)
        seed(conn)
    finally:
        conn.close()


def _wa_link(phone, message):
    """Build a wa.me click-to-chat URL, or None if there's no usable number."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None
    if len(digits) == 10:  # bare local number — prepend the default country code
        digits = COUNTRY_CODE + digits
    return "https://wa.me/" + digits + "?text=" + urllib.parse.quote(message)


def _reminder_message(name, seat_code, expiry, days_left):
    """The pre-filled WhatsApp text a member receives about their renewal."""
    when = "today" if days_left == 0 else (
        "tomorrow" if days_left == 1 else f"in {days_left} days"
    )
    return (
        f"Hi {name}, a friendly reminder from {BUSINESS_NAME}: your seat "
        f"{seat_code} membership expires on {expiry} ({when}). "
        "Would you like to renew? Reply here and we'll keep your seat reserved."
    )


def _reminders_due(members, today):
    """Members expiring within REMINDER_DAYS, with reminder/WhatsApp metadata.

    A member counts as already reminded for this cycle when their stored
    ``reminder_sent_for`` matches their current ``expiration_date``.
    """
    due = []
    for m in members:
        exp = m.get("expiration_date")
        if not exp:
            continue
        try:
            d = date.fromisoformat(exp)
        except ValueError:
            continue
        days_left = (d - today).days
        if 0 <= days_left <= REMINDER_DAYS:
            message = _reminder_message(m["name"], m["seat_code"], exp, days_left)
            row = dict(m)
            row["days_left"] = days_left
            row["reminded"] = m.get("reminder_sent_for") == exp
            row["wa_link"] = _wa_link(m.get("phone"), message)
            due.append(row)
    due.sort(key=lambda r: r["days_left"])
    return due


def is_admin(request: Request) -> bool:
    return request.session.get("admin") is True


def _today_iso():
    return date.today().isoformat()


# --- Student (public) routes ------------------------------------------------
@app.get("/")
def index(request: Request):
    conn = get_conn()
    try:
        seats = list_seats(conn)
    finally:
        conn.close()
    floor_plan = _build_floor_plan(seats, public=True)
    return templates.TemplateResponse(
        request, "index.html", {"floor_plan": floor_plan}
    )


@app.get("/api/seats")
def api_seats():
    conn = get_conn()
    try:
        seats = public_seats(conn)
    finally:
        conn.close()
    return JSONResponse(content=seats)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# --- Admin auth -------------------------------------------------------------
@app.get("/admin/login")
def admin_login_form(request: Request):
    return templates.TemplateResponse(request, "admin_login.html", {"error": None})


@app.post("/admin/login")
def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if username == ADMIN_USER and password == ADMIN_PASS:
        request.session["admin"] = True
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(
        request, "admin_login.html", {"error": "Invalid credentials"}, status_code=401
    )


@app.get("/admin/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)


# --- Admin dashboard --------------------------------------------------------
@app.get("/admin")
def admin_dashboard(request: Request):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    conn = get_conn()
    try:
        members = list_members(conn)
        seats = list_seats(conn)
        archived = list_archived(conn)
    finally:
        conn.close()

    today = date.today()
    soon = today + timedelta(days=14)
    expired = active = expiring = 0
    for m in members:
        exp = m.get("expiration_date")
        if not exp:
            active += 1
            continue
        try:
            d = date.fromisoformat(exp)
        except ValueError:
            active += 1
            continue
        if d < today:
            expired += 1
        elif d <= soon:
            expiring += 1
            active += 1
        else:
            active += 1

    free_seats = [s for s in seats if s["status"] == "available"]
    stats = {
        "total_seats": len(seats),
        "occupied": len(members),
        "free": len(free_seats),
        "expired": expired,
        "expiring": expiring,
        "active": active,
    }
    return templates.TemplateResponse(
        request,
        "admin.html",
        {
            "members": members,
            "free_seats": free_seats,
            "archived": archived,
            "floor_plan": _build_floor_plan(seats),
            "reminders": _reminders_due(members, today),
            "reminder_days": REMINDER_DAYS,
            "stats": stats,
            "today": today.isoformat(),
            "default_fee": DEFAULT_FEE,
            "default_caution": DEFAULT_CAUTION,
            "daily_rate": DAILY_RATE,
            "modes": PAYMENT_MODES,
        },
    )


@app.get("/admin/member/{member_id}")
def admin_member(request: Request, member_id: int):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    conn = get_conn()
    try:
        member = get_member(conn, member_id)
        if member is None:
            return RedirectResponse(url="/admin", status_code=303)
        payments = list_payments(conn, member_id)
        seats = list_seats(conn)
    finally:
        conn.close()

    # Suggested renewal window: the new month begins the day after the current
    # expiry (start date never changes), running one month minus a day.
    exp = member.get("expiration_date")
    try:
        start = date.fromisoformat(exp) + timedelta(days=1) if exp else date.today()
    except ValueError:
        start = date.today()
    suggested_start = start.isoformat()
    suggested_end = monthly_period_end(start).isoformat()

    # Seats the member can be moved to: their current seat first, then any free
    # seat. (Edit changes a seat in place without archiving/re-adding.)
    current = member.get("seat_code")
    seat_options = []
    if current:
        seat_options.append({"code": current, "room": member.get("room")})
    seat_options += [
        {"code": s["code"], "room": s["room"]}
        for s in seats
        if s["status"] == "available" and s["code"] != current
    ]

    return templates.TemplateResponse(
        request,
        "member.html",
        {
            "member": member,
            "payments": payments,
            "today": _today_iso(),
            "suggested_start": suggested_start,
            "suggested_end": suggested_end,
            "default_fee": member.get("fee") or DEFAULT_FEE,
            "daily_rate": DAILY_RATE,
            "modes": PAYMENT_MODES,
            "seat_options": seat_options,
            "seat_error": request.query_params.get("err") == "seat",
        },
    )


@app.post("/admin/member/{member_id}/renew")
def admin_renew(
    request: Request,
    member_id: int,
    amount: float = Form(...),
    mode: str = Form(...),
    txn_id: str = Form(None),
    period_start: str = Form(...),
    period_end: str = Form(...),
    paid_on: str = Form(...),
    note: str = Form(None),
    pass_type: str = Form("monthly"),
):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    conn = get_conn()
    try:
        member = get_member(conn, member_id)
        if member is None:
            return RedirectResponse(url="/admin", status_code=303)
        txn = (txn_id or "").strip() or None
        payment = record_payment(
            conn,
            member_id=member_id,
            amount=amount,
            mode=(mode or "cash").strip().lower(),
            txn_id=txn,
            period_start=period_start or None,
            period_end=period_end or None,
            paid_on=paid_on or _today_iso(),
            note=(note or "").strip() or None,
            pass_type=(pass_type or "monthly"),
        )
    finally:
        conn.close()
    # Land on the printable receipt for the renewal just recorded.
    return RedirectResponse(
        url=f"/admin/receipt/{payment['receipt_no']}", status_code=303
    )


@app.post("/admin/member/{member_id}/edit")
def admin_edit(
    request: Request,
    member_id: int,
    name: str = Form(...),
    phone: str = Form(None),
    address: str = Form(None),
    aadhaar: str = Form(None),
    caution_deposit: float = Form(0),
    seat_code: str = Form(...),
    start_date: str = Form(None),
    expiration_date: str = Form(...),
):
    """Edit a member's details in place (name, phone, address, Aadhaar, caution,
    seat, dates) — no receipt is created."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    trimmed = (name or "").strip()
    if not trimmed:
        return RedirectResponse(url=f"/admin/member/{member_id}", status_code=303)
    conn = get_conn()
    try:
        member = get_member(conn, member_id)
        if member is None:
            return RedirectResponse(url="/admin", status_code=303)
        try:
            update_member(
                conn,
                member_id=member_id,
                name=trimmed,
                phone=(phone or "").strip() or None,
                seat_code=seat_code,
                start_date=start_date or None,
                expiration_date=expiration_date or None,
                address=(address or "").strip() or None,
                aadhaar=(aadhaar or "").strip() or None,
                caution_deposit=caution_deposit or 0,
            )
        except sqlite3.IntegrityError:
            # Target seat is held by another active member.
            return RedirectResponse(
                url=f"/admin/member/{member_id}?err=seat", status_code=303
            )
    finally:
        conn.close()
    return RedirectResponse(url=f"/admin/member/{member_id}", status_code=303)


@app.post("/admin/seat/{seat_code}/assign")
def admin_assign(
    request: Request,
    seat_code: str,
    name: str = Form(...),
    phone: str = Form(None),
    address: str = Form(None),
    aadhaar: str = Form(None),
    amount: float = Form(...),
    caution: float = Form(0),
    mode: str = Form(...),
    txn_id: str = Form(None),
    period_start: str = Form(...),
    period_end: str = Form(...),
    paid_on: str = Form(...),
    pass_type: str = Form("monthly"),
):
    """Assign a brand-new member to an empty seat and take their first payment.

    The first receipt may also collect a refundable caution deposit.
    """
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    trimmed = (name or "").strip()
    if not trimmed:
        return RedirectResponse(url="/admin", status_code=303)
    conn = get_conn()
    try:
        try:
            member = add_member(
                conn,
                name=trimmed,
                seat_code=seat_code,
                fee=amount,
                payment_date=paid_on or _today_iso(),
                start_date=period_start or None,
                expiration_date=period_end or None,
                phone=(phone or "").strip() or None,
                address=(address or "").strip() or None,
                aadhaar=(aadhaar or "").strip() or None,
                caution_deposit=caution or 0,
            )
        except sqlite3.IntegrityError:
            # Seat already taken (race) — bounce back to the dashboard.
            return RedirectResponse(url="/admin", status_code=303)
        payment = record_payment(
            conn,
            member_id=member["id"],
            amount=amount,
            mode=(mode or "cash").strip().lower(),
            txn_id=(txn_id or "").strip() or None,
            period_start=period_start or None,
            period_end=period_end or None,
            paid_on=paid_on or _today_iso(),
            note="Initial membership",
            caution=caution or 0,
            pass_type=(pass_type or "monthly"),
        )
    finally:
        conn.close()
    return RedirectResponse(
        url=f"/admin/receipt/{payment['receipt_no']}", status_code=303
    )


@app.post("/admin/member/{member_id}/remind")
def admin_remind(request: Request, member_id: int):
    """Log an expiry reminder for a member and bounce to their WhatsApp chat.

    The dashboard normally opens WhatsApp client-side (admin.js) and calls this
    via fetch; the redirect here is the no-JavaScript fallback.
    """
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    conn = get_conn()
    try:
        member = get_member(conn, member_id)
        if member is None:
            return RedirectResponse(url="/admin", status_code=303)
        mark_reminded(conn, member_id)
    finally:
        conn.close()

    exp = member.get("expiration_date")
    wa = None
    if exp:
        try:
            days_left = (date.fromisoformat(exp) - date.today()).days
        except ValueError:
            days_left = 0
        message = _reminder_message(member["name"], member["seat_code"], exp, days_left)
        wa = _wa_link(member.get("phone"), message)
    return RedirectResponse(url=wa or "/admin", status_code=303)


@app.post("/admin/member/{member_id}/free")
def admin_free(request: Request, member_id: int):
    """Vacate a member's seat but keep their record (archive, don't delete)."""
    if not is_admin(request):
        return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    conn = get_conn()
    try:
        archive_member(conn, member_id)
    finally:
        conn.close()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/member/{member_id}/restore")
def admin_restore(request: Request, member_id: int):
    """Undo a Free: put an archived member back on their last seat unchanged.

    Restores the member's own preserved fee/start/expiry — no new receipt is
    created. Used to reverse an accidental Free. If their last seat is already
    taken (or they have none recorded), bounce back without changing anything.
    """
    if not is_admin(request):
        return JSONResponse({"ok": False, "error": "Not authenticated"}, status_code=401)
    conn = get_conn()
    try:
        member = get_member(conn, member_id)
        if member is None or member["status"] != "inactive" or not member["last_seat_code"]:
            return RedirectResponse(url="/admin", status_code=303)
        try:
            reactivate_member(
                conn,
                member_id=member_id,
                seat_code=member["last_seat_code"],
                fee=member["fee"],
                payment_date=member["payment_date"],
                start_date=member["start_date"],
                expiration_date=member["expiration_date"],
            )
        except sqlite3.IntegrityError:
            # Last seat taken since they were freed — leave them archived.
            return RedirectResponse(url="/admin", status_code=303)
    finally:
        conn.close()
    return RedirectResponse(url="/admin", status_code=303)


@app.post("/admin/member/{member_id}/reassign")
def admin_reassign(
    request: Request,
    member_id: int,
    seat_code: str = Form(...),
    amount: float = Form(...),
    caution: float = Form(0),
    mode: str = Form(...),
    txn_id: str = Form(None),
    period_start: str = Form(...),
    period_end: str = Form(...),
    paid_on: str = Form(...),
    phone: str = Form(None),
    pass_type: str = Form("monthly"),
):
    """Re-activate an archived member onto a free seat and take their payment."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    conn = get_conn()
    try:
        member = get_member(conn, member_id)
        if member is None:
            return RedirectResponse(url="/admin", status_code=303)
        try:
            reactivate_member(
                conn,
                member_id=member_id,
                seat_code=seat_code,
                fee=amount,
                payment_date=paid_on or _today_iso(),
                start_date=period_start or None,
                expiration_date=period_end or None,
                phone=(phone or "").strip() or None,
                caution_deposit=caution if caution else None,
            )
        except sqlite3.IntegrityError:
            # Seat taken since the page loaded — bounce back to the dashboard.
            return RedirectResponse(url="/admin", status_code=303)
        payment = record_payment(
            conn,
            member_id=member_id,
            amount=amount,
            mode=(mode or "cash").strip().lower(),
            txn_id=(txn_id or "").strip() or None,
            period_start=period_start or None,
            period_end=period_end or None,
            paid_on=paid_on or _today_iso(),
            note="Re-activated membership",
            caution=caution or 0,
            pass_type=(pass_type or "monthly"),
        )
    finally:
        conn.close()
    return RedirectResponse(
        url=f"/admin/receipt/{payment['receipt_no']}", status_code=303
    )


@app.post("/admin/swap")
def admin_swap(
    request: Request,
    member_a: int = Form(...),
    member_b: int = Form(...),
):
    """Exchange the seats of two active members. No invoices are created."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    conn = get_conn()
    try:
        try:
            swap_seats(conn, member_a, member_b)
        except (ValueError, sqlite3.IntegrityError):
            return RedirectResponse(url="/admin", status_code=303)
    finally:
        conn.close()
    return RedirectResponse(url="/admin", status_code=303)


@app.get("/admin/receipt/{receipt_no}")
def admin_receipt(request: Request, receipt_no: str):
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    conn = get_conn()
    try:
        receipt = get_receipt(conn, receipt_no)
    finally:
        conn.close()
    if receipt is None:
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse(
        request, "receipt.html", {"r": receipt}
    )


@app.get("/admin/invoices")
def admin_invoices(request: Request):
    """List every receipt/invoice issued, with inline edit."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    conn = get_conn()
    try:
        payments = list_all_payments(conn)
    finally:
        conn.close()
    total = sum((p.get("amount") or 0) + (p.get("caution") or 0) for p in payments)
    return templates.TemplateResponse(
        request,
        "invoices.html",
        {
            "payments": payments,
            "total": total,
            "modes": PAYMENT_MODES,
            "today": _today_iso(),
        },
    )


@app.post("/admin/receipt/{receipt_no}/edit")
def admin_receipt_edit(
    request: Request,
    receipt_no: str,
    amount: float = Form(...),
    caution: float = Form(0),
    mode: str = Form(...),
    txn_id: str = Form(None),
    period_start: str = Form(None),
    period_end: str = Form(None),
    paid_on: str = Form(...),
    note: str = Form(None),
):
    """Edit an already-issued receipt (corrections) — receipt record only."""
    if not is_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    conn = get_conn()
    try:
        update_payment(
            conn,
            receipt_no=receipt_no,
            amount=amount,
            caution=caution or 0,
            mode=(mode or "cash").strip().lower(),
            txn_id=(txn_id or "").strip() or None,
            period_start=period_start or None,
            period_end=period_end or None,
            paid_on=paid_on or _today_iso(),
            note=(note or "").strip() or None,
        )
    finally:
        conn.close()
    return RedirectResponse(url=f"/admin/receipt/{receipt_no}", status_code=303)
