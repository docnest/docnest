#!/usr/bin/env bash
set -e

# Always operate from the repo root so app.main:app and the relative
# app/static and app/templates directories resolve correctly.
cd "$(dirname "$0")"

VENV_DIR=".venv"

# Create the virtualenv on first run, reuse it afterwards.
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating virtual environment in $VENV_DIR ..."
  python -m venv "$VENV_DIR"
fi

# Activate the venv.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Install/refresh dependencies.
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

# Seed the SQLite database (idempotent: does nothing if already seeded).
python -c "from app.database import get_conn, init_db; from app.seed import seed; c = get_conn(); init_db(c); seed(c); c.commit(); c.close(); print('Database ready.')"

PORT="${PORT:-8000}"

echo
echo "docnest is starting..."
echo "  Student seat map : http://localhost:${PORT}/"
echo "  Admin login      : http://localhost:${PORT}/admin/login"
echo "  Default admin     : user=${DOCNEST_ADMIN_USER:-admin}  pass=${DOCNEST_ADMIN_PASS:-admin123}"
echo "  (Change DOCNEST_ADMIN_USER / DOCNEST_ADMIN_PASS / DOCNEST_SECRET in production.)"
echo

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --reload
