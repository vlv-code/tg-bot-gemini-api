#!/bin/sh
set -e

mkdir -p /app/data
chown -R bot:bot /app/data 2>/dev/null || true
chmod 775 /app/data 2>/dev/null || true

exec gosu bot "$@"
