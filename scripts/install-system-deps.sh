#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'EOF'
Usage: ./scripts/install-system-deps.sh

Install RPL system packages on Debian/Ubuntu. The script uses sudo when needed.
Set POSTGRESQL_MAJOR to override automatic PostgreSQL version detection.
EOF
    exit 0
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "This script supports Debian/Ubuntu systems with apt-get." >&2
    exit 1
fi

if [[ "${EUID}" -eq 0 ]]; then
    APT=(apt-get)
else
    if ! command -v sudo >/dev/null 2>&1; then
        echo "sudo is required when the script is not run as root." >&2
        exit 1
    fi
    APT=(sudo apt-get)
fi

export DEBIAN_FRONTEND=noninteractive

"${APT[@]}" update
"${APT[@]}" install -y \
    build-essential \
    ca-certificates \
    curl \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    postgis \
    postgresql \
    postgresql-contrib \
    python3 \
    python3-dev \
    python3-venv

if [[ -n "${POSTGRESQL_MAJOR:-}" ]]; then
    postgresql_major="${POSTGRESQL_MAJOR}"
elif command -v pg_config >/dev/null 2>&1; then
    postgresql_major="$(pg_config --version | sed -E 's/^PostgreSQL ([0-9]+).*/\1/')"
else
    echo "Could not determine the installed PostgreSQL major version." >&2
    echo "Re-run with POSTGRESQL_MAJOR=<version>, for example POSTGRESQL_MAJOR=14." >&2
    exit 1
fi

"${APT[@]}" install -y "postgresql-${postgresql_major}-postgis-3"

echo
echo "RPL system dependencies are installed."
echo "PostgreSQL major version: ${postgresql_major}"
echo
echo "Database initialization is intentionally separate from package installation."
echo "See README.md for CREATE EXTENSION and database setup commands."
