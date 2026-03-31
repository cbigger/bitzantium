#!/bin/bash
# install.sh — bootstrap install for Bitzantium.
# Run as root:  sudo bash install.sh [--realm-path <relative-path>]

set -euo pipefail

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Parse arguments ─────────────────────────────────────────────────────────

REALM_PATH=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --realm-path)
            REALM_PATH="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Usage: sudo bash install.sh [--realm-path <relative-path>]" >&2
            exit 1
            ;;
    esac
done

if [[ -z "${REALM_PATH}" ]]; then
    echo "No --realm-path provided."
    read -rp "Enter the path to the realm directory (relative to ${SRC_DIR}): " REALM_PATH
    if [[ -z "${REALM_PATH}" ]]; then
        echo "Error: realm path is required." >&2
        exit 1
    fi
fi

REALM_FULL_PATH="${SRC_DIR}/${REALM_PATH}"
if [[ ! -d "${REALM_FULL_PATH}" ]]; then
    echo "Error: realm directory not found: ${REALM_FULL_PATH}" >&2
    exit 1
fi

# ── Preflight ────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    echo "Error: install.sh must be run as root." >&2
    exit 1
fi

echo "=== Bitzantium Installation ==="
echo "Source:     ${SRC_DIR}"
echo "Realm data: ${REALM_FULL_PATH}"
echo ""

# ── 1. Install system packages ──────────────────────────────────────────────

echo "[1/6] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq postgresql postgresql-client python3 python3-pip \
    python3-venv python3-dev gcc libpq-dev

# ── 2. Initialize and start PostgreSQL ───────────────────────────────────────

echo "[2/6] Setting up PostgreSQL..."
systemctl enable --quiet postgresql
systemctl start postgresql

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='bitzantium'" \
    | grep -q 1 || \
    sudo -u postgres psql -c "CREATE USER bitzantium WITH PASSWORD 'bitzantium';"

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='bitzantium'" \
    | grep -q 1 || \
    sudo -u postgres psql -c "CREATE DATABASE bitzantium OWNER bitzantium;"

echo "       Database 'bitzantium' ready."

# ── 3. Create Python virtual environment ────────────────────────────────────

echo "[3/6] Setting up Python virtual environment..."
python3 -m venv "${SRC_DIR}/.venv"
echo "       $("${SRC_DIR}/.venv/bin/python3" --version)"

# ── 4. Install Python dependencies ──────────────────────────────────────────

echo "[4/6] Installing Python dependencies..."
"${SRC_DIR}/.venv/bin/pip" install --upgrade pip --quiet
"${SRC_DIR}/.venv/bin/pip" install -r "${SRC_DIR}/requirements.txt" --quiet
"${SRC_DIR}/.venv/bin/pip" install \
    sqlalchemy psycopg2-binary uvicorn starlette aiohttp openai --quiet
"${SRC_DIR}/.venv/bin/pip" install -e "${SRC_DIR}/bitzantium_schemas/" --quiet

# ── 5. Configure bitzantium.toml ────────────────────────────────────────────

echo "[5/6] Checking configuration..."
if grep -q "bitzantium_temp" "${SRC_DIR}/bitzantium.toml" 2>/dev/null; then
    sed -i 's|bitzantium_temp|bitzantium|g' "${SRC_DIR}/bitzantium.toml"
fi
echo "       Config: ${SRC_DIR}/bitzantium.toml"

# ── 6. Load realm data into database ────────────────────────────────────────

echo "[6/6] Loading realm data..."
"${SRC_DIR}/.venv/bin/python3" "${SRC_DIR}/load_realm.py" "${REALM_FULL_PATH}"

# ── Done ─────────────────────────────────────────────────────────────────────

echo ""
echo "=== Installation complete ==="
echo ""
echo "Start the auth server:"
echo "  ${SRC_DIR}/.venv/bin/python3 ${SRC_DIR}/auth_wrapper.py"
echo ""
echo "Start the game server (separate terminal):"
echo "  ${SRC_DIR}/.venv/bin/python3 ${SRC_DIR}/game_server.py"
echo ""
echo "Quick test:"
echo "  curl -s -X POST http://localhost:8080/api/register"
