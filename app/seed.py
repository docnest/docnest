"""Seed the docnest seat inventory and the current member roster.

Seat layout (33 seats):
    Room 1: A1-A7 (7), B1-B8 (8), C1-C8 (8) = 23 seats
    Room 2: D1-D10 (10)                      = 10 seats

The member roster below is the live set of DocNest members. Seeding is
idempotent: seats are inserted only if missing, and members/payments are
inserted only when the members table is empty. To force a full reset, call
``reset_and_seed`` (used by the reset script / fresh container start).

Runnable as a module: ``python -m app.seed``        (idempotent)
                      ``python -m app.seed --reset`` (wipe members + reseed)
"""

import sys
from datetime import date, timedelta

from app.database import (
    DEFAULT_FEE,
    get_conn,
    init_db,
    monthly_period_end,
    next_receipt_no,
    record_payment,
)


def _seat_codes():
    """Yield (code, room) pairs in canonical insertion order."""
    for n in range(1, 8):          # A1..A7
        yield (f"A{n}", "Room 1")
    for n in range(1, 9):          # B1..B8
        yield (f"B{n}", "Room 1")
    for n in range(1, 9):          # C1..C8
        yield (f"C{n}", "Room 1")
    for n in range(1, 11):         # D1..D10
        yield (f"D{n}", "Room 2")


# Current member roster.
# (name, seat, payment_date, start_date, expiration_date) — ISO dates.
# A missing start date is recorded as None.
MEMBERS = [
    ("Aswathy",                       "C6", "2025-12-17", "2025-12-18", "2026-06-17"),
    ("Aneesha",                       "A5", "2025-12-12", "2025-12-12", "2026-06-11"),
    ("Abhishek K",                    "D8", "2025-12-28", "2025-12-28", "2026-06-28"),
    ("NEO",                           "B8", "2025-12-28", "2025-12-29", "2026-06-28"),
    ("Ranjitha",                      "B5", "2025-12-29", "2026-01-01", "2026-06-30"),
    ("Pranav T",                      "C1", "2025-12-30", "2026-01-06", "2026-06-05"),
    ("amal raj",                      "A7", "2025-12-31", "2026-01-16", "2026-06-15"),
    ("kanzul nettoor",                "A1", "2026-01-04", "2026-01-13", "2026-08-12"),
    ("dr. nevin",                     "B7", "2026-01-06", "2026-02-01", "2026-06-30"),
    ("deva priya",                    "B1", "2026-01-17", "2026-01-31", "2026-06-30"),
    ("Aparna S Nair",                 "A4", "2026-02-01", "2026-02-02", "2026-07-01"),
    ("Balram JS",                     "D2", "2026-02-02", None,         "2026-07-01"),
    ("deepak",                        "D7", "2026-02-02", "2026-02-03", "2026-07-02"),
    ("Nithya Railway exam",           "D6", "2026-02-09", "2026-02-10", "2026-06-09"),
    ("nandana kartha",                "D5", "2026-03-02", "2026-03-03", "2026-07-02"),
    ("joy chettan daughter",          "B6", "2026-03-22", "2026-03-22", "2026-06-21"),
    ("Nikhina Noushad",               "C8", "2026-04-01", "2026-04-11", "2026-06-10"),
    ("Ayub A Ali",                    "D1", "2026-04-20", "2026-04-21", "2026-06-20"),
    ("malavika",                      "C3", "2026-04-22", "2026-05-01", "2026-06-30"),
    ("sijy sebastian",                "B4", "2026-04-26", "2026-04-29", "2026-06-28"),
    ("dr Altus",                      "D10", "2026-05-03", "2026-05-03", "2026-07-02"),
    ("Ejaz",                          "B3", "2026-05-06", "2026-05-08", "2026-06-07"),
    ("Naveen KR",                     "D4", "2026-05-06", "2026-05-06", "2026-07-05"),
    ("Varsha",                        "B2", "2026-05-08", "2026-05-08", "2026-06-07"),
    ("aswathy biju kumar",            "D3", "2026-05-09", "2026-05-10", "2026-06-09"),
    ("Niranjana",                     "C4", "2026-05-16", "2026-05-17", "2026-06-16"),
    ("Madhav",                        "A6", "2026-05-18", "2026-05-20", "2026-06-19"),
    ("Sreelakshmi AI Course",         "A3", "2026-05-19", "2026-05-20", "2026-06-19"),
    ("Anuvindha",                     "C7", "2026-05-21", "2026-05-22", "2026-06-21"),
    ("Nikhila ( Nikhina reference)",  "D9", "2026-05-22", "2026-06-01", "2026-06-30"),
    ("Sreelakshmi Gopinathan",        "C2", "2026-05-25", "2026-06-01", "2026-06-30"),
    ("Jovin Jose",                    "C5", "2026-05-29", "2026-06-05", "2026-07-04"),
    ("Joel Benny",                    "A2", "2026-05-31", "2026-06-01", "2026-07-02"),
]


# Known member phone numbers (name → phone). Backfilled into any matching row
# whose phone is still blank, so an existing live database is enriched on the
# next start without overwriting a number an admin has since entered/edited.
MEMBER_PHONES = {
    "Aswathy": "62355 56007",
    "Nithya Railway exam": "75609 74319",
    "Nikhina Noushad": "94478 40314",
    "Ejaz": "9061136896",
    "aswathy biju kumar": "94002 21931",
}


def backfill_phones(conn):
    """Fill in known phone numbers for members missing one (idempotent)."""
    for name, phone in MEMBER_PHONES.items():
        conn.execute(
            "UPDATE members SET phone = ? "
            "WHERE name = ? AND (phone IS NULL OR phone = '')",
            (phone, name),
        )
    conn.commit()


def seed_seats(conn):
    """Insert any missing seats (idempotent)."""
    conn.executemany(
        "INSERT OR IGNORE INTO seats (code, room) VALUES (?, ?)",
        list(_seat_codes()),
    )
    conn.commit()


def seed_members(conn):
    """Insert the member roster + an initial receipt each, only if empty."""
    count = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
    if count > 0:
        return

    for name, seat, pay, start, exp in MEMBERS:
        cur = conn.execute(
            """
            INSERT INTO members
                (name, seat_code, fee, payment_date, start_date, expiration_date)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, seat, DEFAULT_FEE, pay, start, exp),
        )
        member_id = cur.lastrowid
        # Initial join recorded as the first receipt (cash, no txn id).
        conn.execute(
            """
            INSERT INTO payments
                (member_id, receipt_no, txn_id, mode, amount,
                 period_start, period_end, paid_on, note)
            VALUES (?, ?, NULL, 'cash', ?, ?, ?, ?, 'Initial membership')
            """,
            (member_id, next_receipt_no(conn), DEFAULT_FEE,
             start or pay, exp, pay),
        )
    conn.commit()


def backfill_monthly_invoices(conn):
    """Replace each member's single placeholder receipt with a monthly trail.

    For an untouched seed (every receipt is the initial lump), wipe the
    payments and regenerate one ₹{DEFAULT_FEE} monthly invoice per month from
    each member's start date through their current expiry — issued in date
    order so receipt numbers run chronologically. One-shot and idempotent:
    once real receipts exist (or it has already run), it leaves data alone.
    """
    rows = conn.execute("SELECT note, pass_type FROM payments").fetchall()
    if not rows:
        return 0

    # Only operate on a pristine seed. Seed lumps carry no pass_type and the
    # 'Initial membership' note; every real receipt (signup/renewal) and our own
    # backfilled invoices set a pass_type — so the moment any typed receipt or
    # non-seed note exists we leave the data untouched (also makes this a no-op
    # on the second run).
    if any(r["pass_type"] for r in rows):
        return 0
    if any((r["note"] or "") != "Initial membership" for r in rows):
        return 0

    conn.execute("DELETE FROM payments")
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone():
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'payments'")
    conn.commit()

    # Collect every monthly period across all members, then sort by date so
    # receipt numbers are issued chronologically.
    periods = []
    for m in conn.execute("SELECT id, start_date, expiration_date FROM members").fetchall():
        try:
            start = date.fromisoformat(m["start_date"]) if m["start_date"] else None
            exp = date.fromisoformat(m["expiration_date"]) if m["expiration_date"] else None
        except (TypeError, ValueError):
            continue
        if not start or not exp or exp < start:
            continue
        cur = start
        while cur <= exp:
            pe = monthly_period_end(cur)
            if pe > exp:
                pe = exp
            periods.append((cur, pe, m["id"]))
            cur = pe + timedelta(days=1)

    periods.sort(key=lambda t: (t[0], t[2]))
    for ps, pe, member_id in periods:
        record_payment(
            conn,
            member_id=member_id,
            amount=DEFAULT_FEE,
            mode="cash",
            txn_id=None,
            period_start=ps.isoformat(),
            period_end=pe.isoformat(),
            paid_on=ps.isoformat(),
            note="Monthly pass",
            pass_type="monthly",
        )
    # Standard fee on file -> monthly rate.
    conn.execute("UPDATE members SET fee = ?", (DEFAULT_FEE,))
    conn.commit()
    return len(periods)


def seed(conn):
    """Idempotent full seed: seats, members, phones, then monthly invoices."""
    seed_seats(conn)
    seed_members(conn)
    backfill_phones(conn)
    backfill_monthly_invoices(conn)


def reset_and_seed(conn):
    """Wipe members + payments, then reseed from the roster above."""
    conn.execute("DELETE FROM payments")
    conn.execute("DELETE FROM members")
    # Restart AUTOINCREMENT counters so a reseed yields stable ids (1..33).
    # sqlite_sequence only exists once an AUTOINCREMENT row has been inserted.
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone():
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN ('members', 'payments')"
        )
    conn.commit()
    seed(conn)


if __name__ == "__main__":
    conn = get_conn()
    try:
        init_db(conn)
        if "--reset" in sys.argv:
            reset_and_seed(conn)
            print("Reset complete.")
        else:
            seed(conn)
        seats = conn.execute("SELECT COUNT(*) FROM seats").fetchone()[0]
        members = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        receipts = conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        print(f"Seed complete. {seats} seats, {members} members, {receipts} receipts.")
    finally:
        conn.close()
