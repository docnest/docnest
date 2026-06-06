"""One-time corrective backfill of monthly invoices on a live DocNest DB.

Expands every *seed* receipt (pass_type IS NULL and note 'Initial membership')
into a chronological trail of one ₹RATE invoice per month over its own period,
while preserving any *real* receipts (signups/renewals/re-activations) exactly.
All receipts are renumbered DN-0001.. in payment-date order. Member
expiry/start dates are NOT touched.

Usage: DOCNEST_DB=/path/to/docnest.db python scripts/backfill_monthly.py
"""

import os
import sqlite3
from datetime import date, timedelta

DB = os.environ.get("DOCNEST_DB", "/data/docnest.db")
RATE = float(os.environ.get("DOCNEST_DEFAULT_FEE", "2000"))


def add_months(d, n):
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    last = (date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(d.day, last))


def month_end(s):
    return add_months(s, 1) - timedelta(days=1)


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    pays = conn.execute("SELECT * FROM payments ORDER BY id").fetchall()

    events = []
    for p in pays:
        is_seed_lump = (p["pass_type"] is None) and (
            (p["note"] or "") == "Initial membership"
        )
        parsed = None
        if is_seed_lump:
            try:
                parsed = (
                    date.fromisoformat(p["period_start"]),
                    date.fromisoformat(p["period_end"]),
                )
            except (TypeError, ValueError):
                parsed = None

        if is_seed_lump and parsed:
            cur, end = parsed
            while cur <= end:
                pe = month_end(cur)
                if pe > end:
                    pe = end
                events.append(
                    dict(
                        member_id=p["member_id"], txn_id=None, mode="cash",
                        amount=RATE, period_start=cur.isoformat(),
                        period_end=pe.isoformat(), paid_on=cur.isoformat(),
                        note="Monthly pass", caution=0, pass_type="monthly",
                    )
                )
                cur = pe + timedelta(days=1)
        else:
            # Preserve a real receipt exactly as it is.
            events.append(
                dict(
                    member_id=p["member_id"], txn_id=p["txn_id"], mode=p["mode"],
                    amount=p["amount"], period_start=p["period_start"],
                    period_end=p["period_end"], paid_on=p["paid_on"],
                    note=p["note"], caution=p["caution"] or 0,
                    pass_type=p["pass_type"],
                )
            )

    def sort_key(ev):
        try:
            d = date.fromisoformat(ev["paid_on"])
        except (TypeError, ValueError):
            d = date(2000, 1, 1)
        return (d, ev["member_id"])

    events.sort(key=sort_key)

    conn.execute("DELETE FROM payments")
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
    ).fetchone():
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'payments'")

    for i, ev in enumerate(events, start=1):
        conn.execute(
            """
            INSERT INTO payments
                (member_id, receipt_no, txn_id, mode, amount,
                 period_start, period_end, paid_on, note, caution, pass_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ev["member_id"], f"DN-{i:04d}", ev["txn_id"], ev["mode"],
             ev["amount"], ev["period_start"], ev["period_end"], ev["paid_on"],
             ev["note"], ev["caution"], ev["pass_type"]),
        )

    conn.execute("UPDATE members SET fee = ? WHERE fee = 1000", (RATE,))
    conn.commit()
    print(f"Rebuilt {len(events)} invoices from {len(pays)} source receipts.")
    conn.close()


if __name__ == "__main__":
    main()
