# DocNest

**DocNest** is a lightweight study-space **membership, seat & billing** app.
The public page shows a live seat map so students can see what's free. Behind an
admin login, staff manage members, sell monthly/daily passes, collect caution
deposits, record renewals, and print **cash receipts**. It's a single-process
**FastAPI + SQLite** app shipped as a Docker container.

- **Student view** (`/`) — live, names-free floor-plan seat map (available /
  booked / expired), auto-refreshing.
- **Admin dashboard** (`/admin`) — seating floor plan, member table, stats,
  expiry reminders, assign/free seats, returning-member re-add.
- **Invoices** (`/admin/invoices`) — every receipt, with inline editing.
- **Printable receipts** — sequential numbers (`DN-0001…`), pass type, caution
  breakdown, dd-mm-yyyy dates.

---

## Quick start on a Raspberry Pi

> Tested target: Raspberry Pi (64-bit Raspberry Pi OS) with Docker. A 64-bit OS
> is strongly recommended so prebuilt ARM wheels are used.

**1. Install Docker** (once):

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"   # then log out/in so 'docker' works without sudo
```

**2. Get the code:**

```bash
git clone https://github.com/docnest/docnest.git
cd docnest
```

**3. Configure secrets:**

```bash
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"   # value for DOCNEST_SECRET
nano .env   # set DOCNEST_ADMIN_PASS and DOCNEST_SECRET (and tunnel token if used)
```

**4. Start it:**

```bash
docker compose up -d --build
```

That's it. On first start the database is created and **seeded automatically**
(seats + the member roster + their monthly invoice history) at
**`./data/docnest.db`** on the host.

- Admin: **http://`<pi-ip>`:8000/admin/login** (or `http://127.0.0.1:8000` on the Pi)
- Student map: **http://`<pi-ip>`:8000/**

> By default the app port is published only on `127.0.0.1`. To reach it from
> other machines, use **Tailscale** or the **Cloudflare tunnel** below (don't
> expose port 8000 to the open internet).

---

## Data & backups

The live SQLite database is **bind-mounted to `./data/docnest.db`** in the repo
directory — so a backup is just a file copy:

```bash
# Backup (a live copy of an idle SQLite DB is safe; stop the app for a guaranteed-quiet snapshot)
cp data/docnest.db data/docnest-backup-$(date +%F).db

# Restore
docker compose stop app
cp data/docnest-backup-YYYY-MM-DD.db data/docnest.db
docker compose start app
```

`data/` and all `*.db` files are gitignored — your database is never committed.

---

## Remote access

### Tailscale (private — recommended for admin)

Expose the app to your tailnet only, with automatic HTTPS, without touching the
port binding:

```bash
sudo tailscale serve --bg 8000
```

Then browse from any device on your tailnet to `https://<machine>.<tailnet>.ts.net/`.
Undo with `sudo tailscale serve reset`.

### Cloudflare tunnel (public student page — optional)

The compose file includes a `cloudflared` service for publishing the student map
at a public hostname (e.g. `students.docnest.co.in`):

1. Cloudflare **Zero Trust → Networks → Tunnels → Create a tunnel** (*Cloudflared*).
2. Copy the **tunnel token** into `CLOUDFLARE_TUNNEL_TOKEN` in `.env`.
3. Add a **Public Hostname**: `students` . `yourdomain` → **Service** `HTTP` → `app:8000`.
4. `docker compose up -d` — the `cloudflared` container connects automatically.

If you don't use the tunnel, you can leave the token as a placeholder; the `app`
service runs fine on its own (the `cloudflared` container just won't connect).

---

## Configuration (`.env`)

| Setting               | Env var                   | Default                |
|-----------------------|---------------------------|------------------------|
| Admin username        | `DOCNEST_ADMIN_USER`      | `admin`                |
| Admin password        | `DOCNEST_ADMIN_PASS`      | `admin123` **— change**|
| Session secret        | `DOCNEST_SECRET`          | `dev-secret-change-me` **— change** |
| Monthly pass fee      | `DOCNEST_DEFAULT_FEE`     | `2000`                 |
| Daily pass rate (/day)| `DOCNEST_DAILY_RATE`      | `299`                  |
| Default caution dep.  | `DOCNEST_DEFAULT_CAUTION` | `0`                    |
| Reminder window (days)| `DOCNEST_REMINDER_DAYS`   | `10`                   |
| Phone country code    | `DOCNEST_COUNTRY_CODE`    | `91`                   |
| Receipt business name | `DOCNEST_BUSINESS_NAME`   | `DocNest Study Space`  |
| Currency symbol       | `DOCNEST_CURRENCY`        | `₹`                    |
| Database path         | `DOCNEST_DB`              | `/data/docnest.db` (container) |
| Cloudflare token      | `CLOUDFLARE_TUNNEL_TOKEN` | *(only for the tunnel)*|

> **Always** set a strong `DOCNEST_ADMIN_PASS` and a random `DOCNEST_SECRET`
> before exposing the app to anyone.

---

## Seat layout

**33 seats** across two rooms (seeded on first start):

| Room   | Seats                           | Count |
|--------|---------------------------------|-------|
| Room 1 | `A1`–`A7`, `B1`–`B8`, `C1`–`C8` | 23    |
| Room 2 | `D1`–`D10`                      | 10    |

The admin floor plan reproduces the physical seating chart; edit the layout in
`FLOOR_PLAN` (`app/main.py`) and the roster in `app/seed.py`.

---

## Features

- **Passes & pricing** — Monthly (₹2000) and Daily (₹299/day) passes. The
  signup/renewal form auto-fills the amount and expiry from the pass type
  (monthly = +1 month − 1 day; daily = rate × days); both stay editable.
- **Caution deposit** — collected at first signup as a separate refundable line
  on the receipt; not charged on renewals; editable any time.
- **Members** — add to a free seat; edit name / phone / address / Aadhaar /
  seat / dates / caution; **archive** (the "Free" button keeps the record &
  receipts and frees the seat); **re-add** returning members onto a free seat
  with their history intact.
- **Renewals** — roll the expiry forward (start date never changes) and issue a
  receipt.
- **Invoices** — a dashboard of every receipt with totals and **inline editing**
  for corrections.
- **Expiry reminders** — members expiring within `DOCNEST_REMINDER_DAYS` get a
  one-click **WhatsApp** message (pre-filled), tracked per cycle.
- **Receipts** — printable (browser → PDF), sequential numbers, pass type,
  caution breakdown, dd-mm-yyyy dates, print-margin-safe.

---

## Operations

```bash
docker compose logs -f app          # app logs
docker compose logs -f cloudflared  # tunnel status
docker compose up -d --build        # apply code changes / update
docker compose restart app          # restart after editing .env
docker compose down                 # stop (data in ./data is kept)
```

**Reset the roster** to the seeded baseline (wipes members + payments):

```bash
docker compose exec app python -m app.seed --reset
```

**Backfill monthly invoices** on an already-running DB (one-time; preserves real
receipts) — see `scripts/backfill_monthly.py`:

```bash
docker compose exec app env DOCNEST_DB=/data/docnest.db python /app/scripts/backfill_monthly.py
```

---

## Routes

| Method | Path                              | Auth    | Description                          |
|--------|-----------------------------------|---------|--------------------------------------|
| GET    | `/`                               | none    | Student seat map                     |
| GET    | `/api/seats`                      | none    | Live seat list (JSON, no names)      |
| GET    | `/healthz`                        | none    | Health check                         |
| GET/POST | `/admin/login`                  | none    | Admin login                          |
| GET    | `/admin/logout`                   | session | Log out                              |
| GET    | `/admin`                          | session | Dashboard                            |
| GET    | `/admin/invoices`                 | session | All invoices (+ inline edit)         |
| GET    | `/admin/member/{id}`              | session | Member detail / edit / renewal       |
| POST   | `/admin/member/{id}/renew`        | session | Record renewal → receipt             |
| POST   | `/admin/member/{id}/edit`         | session | Edit member details                  |
| POST   | `/admin/member/{id}/free`         | session | Archive member, free the seat        |
| POST   | `/admin/member/{id}/reassign`     | session | Re-add archived member → receipt     |
| POST   | `/admin/member/{id}/remind`       | session | Log WhatsApp expiry reminder         |
| POST   | `/admin/seat/{code}/assign`       | session | Assign new member → receipt          |
| GET    | `/admin/receipt/{receipt_no}`     | session | Printable receipt                    |
| POST   | `/admin/receipt/{receipt_no}/edit`| session | Edit an issued receipt               |

---

## Data model

| Table      | Purpose                                                                              |
|------------|--------------------------------------------------------------------------------------|
| `seats`    | Physical seat inventory (`code`, `room`).                                             |
| `members`  | One active member per seat (partial unique index); name, phone, address, aadhaar, fee, caution_deposit, payment/start/expiry, status (`active`/`inactive`), last_seat_code. |
| `payments` | Every payment = one receipt: `receipt_no`, `txn_id`, `mode`, `amount`, `caution`, `pass_type`, period, `paid_on`. |

A seat's status (available / booked / expired) is **derived** from the member's
`expiration_date` vs today — never stored. Archived members keep `seat_code`
NULL so the seat frees up while their history is preserved.

---

## Local development (without Docker)

```bash
./run.sh                 # creates .venv, installs deps, seeds DB, starts uvicorn
PORT=9000 ./run.sh       # custom port
python -m app.seed --reset   # reset roster to the seeded baseline
```

Locally the DB defaults to `<repo>/docnest.db` (override with `DOCNEST_DB`).

---

## Project structure

```
docnest/
├── app/
│   ├── main.py            # FastAPI app, routes, floor-plan & pricing config
│   ├── database.py        # sqlite3 schema, migrations, queries
│   ├── seed.py            # seat inventory, roster, monthly-invoice backfill
│   ├── templates/         # index, admin, member, invoices, receipt, admin_login
│   └── static/            # *.css, *.js
├── scripts/
│   └── backfill_monthly.py  # one-time monthly-invoice backfill (preserves real receipts)
├── Dockerfile
├── docker-compose.yml     # app + cloudflared tunnel; DB bind-mounted to ./data
├── .env.example
├── requirements.txt
├── run.sh
└── README.md
```
