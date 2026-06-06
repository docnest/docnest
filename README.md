# DocNest

**DocNest** is a lightweight study-space **membership & seat management** app.
The public page (hosted at **students.docnest.co.in**) shows a live seat map so
students can see what's free. Behind an admin login, staff manage members,
record renewals (transaction id + mode of payment), and generate printable
**cash receipts**. It's a single-process FastAPI app backed by SQLite, shipped
as a Docker container and exposed publicly through a Cloudflare tunnel.

## Seat layout

There are **33 seats** across two rooms:

| Room   | Seats                           | Count |
|--------|---------------------------------|-------|
| Room 1 | `A1`–`A7`, `B1`–`B8`, `C1`–`C8` | 23    |
| Room 2 | `D1`–`D10`                      | 10    |

Seats are seeded automatically on first startup, along with the current member
roster (see `app/seed.py`).

## Features

- **Student view** (`/`): a live, names-free seat map grouped by room — green is
  available, red is taken. Auto-refreshes from `GET /api/seats`.
- **Admin dashboard** (`/admin`): at-a-glance stats (occupied / free / expiring
  ≤14 days / expired) and a members table. Assign a new member to any free seat,
  or free a seat.
- **Member page** (`/admin/member/{id}`): membership details, full receipt
  history, and a **Record renewal** form (amount, mode of payment, transaction
  id, period, payment date). Recording a renewal rolls the member's expiry date
  forward and issues a receipt.
- **Cash receipts** (`/admin/receipt/{receipt_no}`): a clean, printable receipt
  (browser print → PDF) with a sequential receipt number (`DN-0001`, …).

## Data model

| Table      | Purpose                                                                 |
|------------|-------------------------------------------------------------------------|
| `seats`    | Physical seat inventory (`code`, `room`).                               |
| `members`  | One active member per seat: name, phone, fee, payment/start/expiry.     |
| `payments` | Every payment = one receipt: `receipt_no`, `txn_id`, `mode`, `amount`, period, `paid_on`. |

A seat's status (available / booked / expired) is **derived** by comparing the
member's `expiration_date` to today — never stored.

## Run with Docker (production)

This is how it runs on the host, tunneled to `students.docnest.co.in`.

1. **Configure secrets**

   ```bash
   cp .env.example .env
   # edit .env: set DOCNEST_ADMIN_PASS, DOCNEST_SECRET, CLOUDFLARE_TUNNEL_TOKEN
   python -c "import secrets; print(secrets.token_hex(32))"   # for DOCNEST_SECRET
   ```

2. **Create the Cloudflare tunnel** (one-time, in the dashboard)

   - Cloudflare **Zero Trust → Networks → Tunnels → Create a tunnel**
     (type: *Cloudflared*).
   - Copy the **tunnel token** (the long string after `--token` in the install
     command) into `CLOUDFLARE_TUNNEL_TOKEN` in `.env`.
   - Under the tunnel's **Public Hostnames**, add:
     - **Subdomain** `students`, **Domain** `docnest.co.in`
     - **Service** `HTTP` → `app:8000`
   - (`docnest.co.in` must already be a zone in this Cloudflare account; the
     tunnel creates the `students` DNS record for you.)

3. **Start everything**

   ```bash
   docker compose up -d --build
   ```

   This runs two containers: `app` (the FastAPI server, also published on
   `127.0.0.1:8000` for local admin) and `cloudflared` (the tunnel). The
   SQLite DB persists in the `docnest-data` volume across rebuilds.

   - Public student map: **https://students.docnest.co.in/**
   - Admin: **https://students.docnest.co.in/admin/login** (or
     `http://127.0.0.1:8000/admin/login` on the host)

Useful commands:

```bash
docker compose logs -f app          # app logs
docker compose logs -f cloudflared  # tunnel status
docker compose down                 # stop (data volume kept)
```

## Run locally (development)

```bash
./run.sh                 # creates .venv, installs deps, seeds DB, starts uvicorn
PORT=9000 ./run.sh       # custom port
```

Reset the roster to the seeded baseline at any time:

```bash
python -m app.seed --reset
```

## Default admin credentials (development only)

| Field    | Value      |
|----------|------------|
| Username | `admin`    |
| Password | `admin123` |

> **Always override these in production** via `.env` (`DOCNEST_ADMIN_USER`,
> `DOCNEST_ADMIN_PASS`) and set a strong `DOCNEST_SECRET`.

## Configuration (environment variables)

| Setting           | Env var                   | Default                  |
|-------------------|---------------------------|--------------------------|
| Admin username    | `DOCNEST_ADMIN_USER`      | `admin`                  |
| Admin password    | `DOCNEST_ADMIN_PASS`      | `admin123`               |
| Session secret    | `DOCNEST_SECRET`          | `dev-secret-change-me`   |
| Default seat fee  | `DOCNEST_DEFAULT_FEE`     | `1000`                   |
| Receipt header    | `DOCNEST_BUSINESS_NAME`   | `DocNest Study Space`    |
| Currency symbol   | `DOCNEST_CURRENCY`        | `₹`                      |
| Database path     | `DOCNEST_DB`              | `<repo_root>/docnest.db` |
| Tunnel token      | `CLOUDFLARE_TUNNEL_TOKEN` | *(required for tunnel)*  |

## Routes

| Method | Path                          | Auth    | Description                                  |
|--------|-------------------------------|---------|----------------------------------------------|
| GET    | `/`                           | none    | Student seat map (availability only)         |
| GET    | `/api/seats`                  | none    | Live seat list (JSON, no names)              |
| GET    | `/healthz`                    | none    | Health check                                 |
| GET    | `/admin/login`                | none    | Admin login form                             |
| POST   | `/admin/login`                | none    | Authenticate admin                           |
| GET    | `/admin/logout`               | session | Log out                                      |
| GET    | `/admin`                      | session | Dashboard: stats, members, free seats        |
| GET    | `/admin/member/{id}`          | session | Member detail, receipts, renewal form        |
| POST   | `/admin/member/{id}/renew`    | session | Record a renewal → issue receipt             |
| POST   | `/admin/member/{id}/free`     | session | Remove member, free the seat                 |
| POST   | `/admin/seat/{code}/assign`   | session | Assign a new member to a free seat           |
| GET    | `/admin/receipt/{receipt_no}` | session | Printable cash receipt                       |

## Project structure

```
docnest/
├── app/
│   ├── main.py            # FastAPI app and all routes
│   ├── database.py        # sqlite3 schema + queries (seats, members, payments)
│   ├── seed.py            # seat inventory + current member roster
│   ├── templates/         # index, admin, member, receipt, admin_login
│   └── static/            # admin.css, receipt.css, *.js, student.css
├── Dockerfile
├── docker-compose.yml     # app + cloudflared tunnel
├── .env.example
├── requirements.txt
├── run.sh
└── README.md
```
