#!/usr/bin/env bash
# The nightly backup: pg_dump taken through the postgres container (so the pg_dump
# version always matches the server), shipped to the R2 bucket via rclone.
# Run by anchor-backup.timer as root; the bucket's lifecycle rule prunes old dumps.
set -euo pipefail

stamp="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
dump="/tmp/anchor-${stamp}.dump"
trap 'rm -f "$dump"' EXIT

# -Fc is the compressed format pg_restore reads; see restore-check.sh.
docker compose --project-directory /opt/anchor exec -T postgres \
  pg_dump -U anchor -d anchor -Fc > "$dump"

rclone copyto "$dump" "r2:anchor-backups/anchor-${stamp}.dump"
echo "shipped anchor-${stamp}.dump ($(du -h "$dump" | cut -f1))"
