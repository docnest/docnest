"""SQLite data layer for docnest.

Three tables:

* ``seats``    — the physical seat inventory (``code``, ``room``). Static.
* ``members``  — the person currently assigned to a seat, with the membership
                 window (``payment_date``, ``start_date``, ``expiration_date``)
                 and the standard ``fee``. One active member per seat.
* ``payments`` — every payment ever taken (the initial join and each renewal).
                 Each row is a cash/online receipt: ``receipt_no`` (unique),
                 ``txn_id``, ``mode``, ``amount``, the period it covers, and
                 when it was paid.

A seat's live status (available / booked / expired) is *derived* by joining
``seats`` to ``members`` and comparing ``expiration_date`` to today — it is
never stored.
"""

import os
import sqlite3
from datetime import date, timedelta

# Repo root = parent of the `app/` package dir (this file lives in app/).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DOCNEST_DB", os.path.join(BASE_DIR, "docnest.db"))

# Default monthly seat fee (INR). Pre-fills the signup/renewal form; editable.
DEFAULT_FEE = float(os.environ.get("DOCNEST_DEFAULT_FEE", "2000"))
# Per-day rate for daily passes (INR).
DAILY_RATE = float(os.environ.get("DOCNEST_DAILY_RATE", "299"))


def add_months(d, n):
    """Return ``d`` shifted by ``n`` calendar months, clamping the day.

    Jan 31 + 1 month -> Feb 28/29 (not March). Accepts a date or ISO string.
    """
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    # Last valid day of the target month.
    last = (date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


def monthly_period_end(start):
    """End of a one-month pass: +1 month minus a day (2 Jun -> 1 Jul)."""
    if isinstance(start, str):
        start = date.fromisoformat(start[:10])
    return add_months(start, 1) - timedelta(days=1)


def get_conn():
    """Return a new sqlite3 connection with dict-like rows and FKs on."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn, table, column, decl):
    """Add ``column`` to ``table`` if an existing database predates it."""
    existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migrate_members_seatable(conn):
    """Rebuild a legacy ``members`` table whose ``seat_code`` is ``NOT NULL``.

    Early databases declared ``seat_code TEXT NOT NULL UNIQUE`` — which both
    forbids archiving a member (seat must become NULL) and blocks reusing a
    seat once an inactive member ever held it. SQLite can't drop those
    constraints in place, so we recreate the table (preserving ``id`` so the
    ``payments`` foreign keys stay valid) only when the old shape is detected.
    """
    seat = next(
        (c for c in conn.execute("PRAGMA table_info(members)") if c["name"] == "seat_code"),
        None,
    )
    if seat is None or seat["notnull"] == 0:
        return  # fresh/new schema already has a nullable seat_code

    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        BEGIN;
        CREATE TABLE members_rebuilt (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            seat_code       TEXT REFERENCES seats(code),
            phone           TEXT,
            fee             REAL NOT NULL DEFAULT 0,
            payment_date    TEXT,
            start_date      TEXT,
            expiration_date TEXT,
            status          TEXT NOT NULL DEFAULT 'active',
            reminder_sent_for TEXT,
            last_seat_code  TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO members_rebuilt
            (id, name, seat_code, phone, fee, payment_date, start_date,
             expiration_date, status, reminder_sent_for, last_seat_code, created_at)
        SELECT id, name, seat_code, phone, fee, payment_date, start_date,
               expiration_date, status, reminder_sent_for, last_seat_code, created_at
          FROM members;
        DROP TABLE members;
        ALTER TABLE members_rebuilt RENAME TO members;
        COMMIT;
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def init_db(conn):
    """Create all tables if they do not already exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seats (
            code TEXT PRIMARY KEY,
            room TEXT NOT NULL
        )
        """
    )
    # seat_code is nullable: an archived (inactive) member keeps their history
    # but holds no seat. "One active member per seat" is enforced by the
    # partial unique index below rather than a column UNIQUE constraint.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS members (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            seat_code       TEXT REFERENCES seats(code),
            phone           TEXT,
            fee             REAL NOT NULL DEFAULT 0,
            payment_date    TEXT,
            start_date      TEXT,
            expiration_date TEXT,
            status          TEXT NOT NULL DEFAULT 'active',
            reminder_sent_for TEXT,
            last_seat_code  TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    _ensure_column(conn, "members", "reminder_sent_for", "TEXT")
    _ensure_column(conn, "members", "last_seat_code", "TEXT")
    _migrate_members_seatable(conn)
    # Optional profile / accounting fields (added after the rebuild migration so
    # legacy databases pick them up too).
    _ensure_column(conn, "members", "address", "TEXT")
    _ensure_column(conn, "members", "aadhaar", "TEXT")
    _ensure_column(conn, "members", "caution_deposit", "REAL NOT NULL DEFAULT 0")
    # At most one *active* member per seat; archived rows (seat_code NULL) and
    # historical seat reuse are unaffected.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_active_seat "
        "ON members(seat_code) WHERE status = 'active'"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id    INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            receipt_no   TEXT NOT NULL UNIQUE,
            txn_id       TEXT,
            mode         TEXT NOT NULL DEFAULT 'cash',
            amount       REAL NOT NULL DEFAULT 0,
            period_start TEXT,
            period_end   TEXT,
            paid_on      TEXT NOT NULL,
            note         TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    # Caution (refundable security) deposit collected on this receipt — usually
    # only on the first signup; 0 for renewals.
    _ensure_column(conn, "payments", "caution", "REAL NOT NULL DEFAULT 0")
    # 'monthly' or 'daily' — the kind of pass this receipt covers.
    _ensure_column(conn, "payments", "pass_type", "TEXT")
    conn.commit()


# --- Seat map (derived status) ----------------------------------------------

def _seat_status(expiration_date):
    """Derive a seat's status from its member's expiration date."""
    if not expiration_date:
        return "booked"
    try:
        exp = date.fromisoformat(expiration_date)
    except ValueError:
        return "booked"
    return "expired" if exp < date.today() else "booked"


def _seat_sort_key(seat):
    """Canonical order: room, row letter, numeric part ascending."""
    code = seat["code"]
    return (seat["room"], code[0], int(code[1:]))


def list_seats(conn):
    """Return every seat joined to its active member (admin view).

    Each dict: ``code``, ``room``, ``status`` (available/booked/expired),
    ``booked_by`` (member name or None), ``member_id``, ``expiration_date``.
    """
    rows = conn.execute(
        """
        SELECT s.code AS code,
               s.room AS room,
               m.id   AS member_id,
               m.name AS booked_by,
               m.expiration_date AS expiration_date
          FROM seats s
          LEFT JOIN members m
            ON m.seat_code = s.code AND m.status = 'active'
        """
    ).fetchall()
    seats = []
    for r in rows:
        booked = r["member_id"] is not None
        seats.append(
            {
                "code": r["code"],
                "room": r["room"],
                "status": _seat_status(r["expiration_date"]) if booked else "available",
                "booked_by": r["booked_by"],
                "member_id": r["member_id"],
                "expiration_date": r["expiration_date"],
            }
        )
    seats.sort(key=_seat_sort_key)
    return seats


def public_seats(conn):
    """Seat map for the public student view — status only, no names.

    Carries the same derived status as the admin view (available / booked /
    expired) so the student map reflects expiry identically; only the member
    name/id are withheld.
    """
    return [
        {"code": s["code"], "room": s["room"], "status": s["status"]}
        for s in list_seats(conn)
    ]


def list_rooms(conn):
    """Distinct room names in canonical order."""
    rows = conn.execute("SELECT DISTINCT room FROM seats ORDER BY room").fetchall()
    return [r["room"] for r in rows]


def available_seat_codes(conn):
    """Seat codes with no active member, in canonical order."""
    return [s["code"] for s in list_seats(conn) if s["status"] == "available"]


# --- Members ----------------------------------------------------------------

def _member_dict(row):
    if row is None:
        return None
    d = dict(row)
    if d.get("status") and d["status"] != "active":
        d["status_label"] = "inactive"
    elif d.get("seat_code"):
        d["status_label"] = _seat_status(d.get("expiration_date"))
    else:
        d["status_label"] = "active"
    return d


def list_members(conn):
    """All active members joined with their seat's room, seat-ordered."""
    rows = conn.execute(
        """
        SELECT m.*, s.room AS room
          FROM members m
          JOIN seats s ON s.code = m.seat_code
         WHERE m.status = 'active'
        """
    ).fetchall()
    members = [_member_dict(r) for r in rows]
    members.sort(key=lambda m: (m["room"], m["seat_code"][0], int(m["seat_code"][1:])))
    return members


def get_member(conn, member_id):
    # LEFT JOIN so archived members (seat_code NULL) are still returned.
    row = conn.execute(
        """
        SELECT m.*, s.room AS room
          FROM members m
          LEFT JOIN seats s ON s.code = m.seat_code
         WHERE m.id = ?
        """,
        (member_id,),
    ).fetchone()
    return _member_dict(row)


def list_archived(conn):
    """Inactive (archived) members — kept for re-activation onto a free seat."""
    rows = conn.execute(
        "SELECT * FROM members WHERE status != 'active' ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return [_member_dict(r) for r in rows]


def add_member(conn, name, seat_code, fee, payment_date, start_date,
               expiration_date, phone=None, address=None, aadhaar=None,
               caution_deposit=0):
    """Assign a new member to an empty seat. Returns the new member dict.

    Raises sqlite3.IntegrityError if the seat is already taken (UNIQUE).
    """
    cur = conn.execute(
        """
        INSERT INTO members
            (name, seat_code, phone, fee, payment_date, start_date, expiration_date,
             address, aadhaar, caution_deposit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, seat_code, phone, fee, payment_date, start_date, expiration_date,
         address, aadhaar, caution_deposit),
    )
    conn.commit()
    return get_member(conn, cur.lastrowid)


def update_member(conn, member_id, name, phone, seat_code,
                  start_date, expiration_date, address=None, aadhaar=None,
                  caution_deposit=0):
    """Edit a member's core details in place (no receipt is created).

    Used to correct typos, backfill a phone/address/Aadhaar, adjust the caution
    deposit on file, move the member to a different free seat, or fix a wrong
    start/expiry date. Raises sqlite3.IntegrityError if ``seat_code`` is already
    held by another active member.
    """
    conn.execute(
        """
        UPDATE members
           SET name = ?,
               phone = ?,
               seat_code = ?,
               start_date = ?,
               expiration_date = ?,
               address = ?,
               aadhaar = ?,
               caution_deposit = ?
         WHERE id = ?
        """,
        (name, phone, seat_code, start_date, expiration_date,
         address, aadhaar, caution_deposit, member_id),
    )
    conn.commit()
    return get_member(conn, member_id)


def mark_reminded(conn, member_id):
    """Record that an expiry reminder was sent for the member's *current* cycle.

    Stamps ``reminder_sent_for`` with the member's present ``expiration_date``;
    once they renew (and the expiry rolls forward) the stamp no longer matches,
    so they become eligible for a fresh reminder next cycle.
    """
    conn.execute(
        "UPDATE members SET reminder_sent_for = expiration_date WHERE id = ?",
        (member_id,),
    )
    conn.commit()


def archive_member(conn, member_id):
    """Free a member's seat but keep their record (and receipts) for re-use.

    Sets the member inactive, remembers their seat in ``last_seat_code``, and
    vacates ``seat_code`` so the seat is immediately available again. They can
    later be re-activated onto any free seat via :func:`reactivate_member`.
    """
    cur = conn.execute(
        """
        UPDATE members
           SET status = 'inactive',
               last_seat_code = seat_code,
               seat_code = NULL,
               reminder_sent_for = NULL
         WHERE id = ?
        """,
        (member_id,),
    )
    conn.commit()
    return cur.rowcount == 1


def reactivate_member(conn, member_id, seat_code, fee, payment_date,
                      start_date, expiration_date, phone=None,
                      caution_deposit=None):
    """Re-assign an archived member to a free seat. Returns the member dict.

    Raises sqlite3.IntegrityError if the seat is already held by an active
    member (the partial unique index ``ux_active_seat``).
    """
    conn.execute(
        """
        UPDATE members
           SET status = 'active',
               seat_code = ?,
               last_seat_code = NULL,
               fee = ?,
               payment_date = ?,
               start_date = ?,
               expiration_date = ?,
               phone = COALESCE(?, phone),
               caution_deposit = COALESCE(?, caution_deposit),
               reminder_sent_for = NULL
         WHERE id = ?
        """,
        (seat_code, fee, payment_date, start_date, expiration_date,
         phone, caution_deposit, member_id),
    )
    conn.commit()
    return get_member(conn, member_id)


# --- Payments / receipts ----------------------------------------------------

def next_receipt_no(conn):
    """Return the next sequential receipt number, e.g. ``DN-0042``.

    Based on the highest existing number, not the row count, so freeing a
    member (which cascade-deletes their receipts) can never produce a number
    that collides with a surviving receipt.
    """
    rows = conn.execute("SELECT receipt_no FROM payments").fetchall()
    highest = 0
    for r in rows:
        try:
            highest = max(highest, int(str(r[0]).rsplit("-", 1)[-1]))
        except (ValueError, IndexError):
            continue
    return f"DN-{highest + 1:04d}"


def record_payment(conn, member_id, amount, mode, txn_id, period_start,
                   period_end, paid_on, note=None, receipt_no=None, caution=0,
                   pass_type=None):
    """Record a payment/renewal for a member and roll their membership forward.

    Creates a payments row (the receipt), then updates the member's
    ``expiration_date`` to ``period_end`` and ``payment_date`` to ``paid_on``.
    ``caution`` is the refundable deposit collected on this receipt (typically
    only at first signup; 0 for renewals). ``pass_type`` is 'monthly'/'daily'.
    Returns the new payment dict.
    """
    if receipt_no is None:
        receipt_no = next_receipt_no(conn)
    cur = conn.execute(
        """
        INSERT INTO payments
            (member_id, receipt_no, txn_id, mode, amount,
             period_start, period_end, paid_on, note, caution, pass_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (member_id, receipt_no, txn_id, mode, amount,
         period_start, period_end, paid_on, note, caution or 0, pass_type),
    )
    conn.execute(
        """
        UPDATE members
           SET expiration_date = ?,
               payment_date = ?
         WHERE id = ?
        """,
        (period_end, paid_on, member_id),
    )
    conn.commit()
    return get_payment_by_id(conn, cur.lastrowid)


def get_payment_by_id(conn, payment_id):
    row = conn.execute(
        "SELECT * FROM payments WHERE id = ?", (payment_id,)
    ).fetchone()
    return dict(row) if row else None


def get_receipt(conn, receipt_no):
    """Return a receipt joined with member + seat info, or None.

    LEFT JOIN so receipts of archived members (no current seat) still resolve;
    the seat falls back to the member's last seat for display.
    """
    row = conn.execute(
        """
        SELECT p.*,
               m.name AS member_name,
               COALESCE(m.seat_code, m.last_seat_code) AS seat_code,
               s.room AS room
          FROM payments p
          JOIN members m ON m.id = p.member_id
          LEFT JOIN seats s ON s.code = COALESCE(m.seat_code, m.last_seat_code)
         WHERE p.receipt_no = ?
        """,
        (receipt_no,),
    ).fetchone()
    return dict(row) if row else None


def list_payments(conn, member_id):
    """All payments for a member, newest first."""
    rows = conn.execute(
        """
        SELECT * FROM payments
         WHERE member_id = ?
         ORDER BY date(paid_on) DESC, id DESC
        """,
        (member_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_all_payments(conn):
    """Every receipt/invoice ever issued, with member + seat, newest first."""
    rows = conn.execute(
        """
        SELECT p.*,
               m.id   AS member_id,
               m.name AS member_name,
               m.status AS member_status,
               COALESCE(m.seat_code, m.last_seat_code) AS seat_code
          FROM payments p
          JOIN members m ON m.id = p.member_id
         ORDER BY date(p.paid_on) DESC, p.id DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def update_payment(conn, receipt_no, amount, caution, mode, txn_id,
                   period_start, period_end, paid_on, note):
    """Edit an already-issued receipt's fields in place. Returns True if found.

    Edits only the receipt record itself — it does not re-roll the member's
    current expiry (use the member's Edit form for that).
    """
    cur = conn.execute(
        """
        UPDATE payments
           SET amount = ?, caution = ?, mode = ?, txn_id = ?,
               period_start = ?, period_end = ?, paid_on = ?, note = ?
         WHERE receipt_no = ?
        """,
        (amount, caution or 0, mode, txn_id, period_start, period_end,
         paid_on, note, receipt_no),
    )
    conn.commit()
    return cur.rowcount == 1
