#!/usr/bin/env bash
# Prove the newest dump in R2 actually restores: load it into a throwaway postgres
# container and read back the migration stamp and account count.
# Never touches the live database. Run as root: bash /opt/anchor/restore-check.sh
set -euo pipefail

# Filtered to dumps, not just newest-by-name: anything else in the bucket sorts
# alongside them and would be picked instead.
latest="$(rclone lsf r2:anchor-backups --files-only --include "anchor-*.dump" | sort | tail -1)"
[ -n "$latest" ] || { echo "no dumps in the bucket" >&2; exit 1; }
echo "restoring $latest into a throwaway container..."
rclone copyto "r2:anchor-backups/$latest" /tmp/restore-check.dump

docker run -d --name anchor-restore-check -e POSTGRES_PASSWORD=restore-check postgres:17 >/dev/null
trap 'docker rm -f anchor-restore-check >/dev/null; rm -f /tmp/restore-check.dump' EXIT
# Over TCP, not the default unix socket: the postgres image runs its initialisation
# against a temporary socket-only server, so a socket probe reports ready before the
# real server exists and the next command finds nothing listening.
for _ in $(seq 1 60); do
  if docker exec anchor-restore-check pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec anchor-restore-check pg_isready -h 127.0.0.1 -U postgres >/dev/null

docker exec anchor-restore-check createdb -U postgres anchor
# --no-owner: the throwaway container has no "anchor" role to hand ownership to.
docker exec -i anchor-restore-check pg_restore -U postgres --no-owner --dbname anchor \
  < /tmp/restore-check.dump

echo "restored. sanity checks:"
docker exec anchor-restore-check psql -U postgres -d anchor -tA \
  -c "select 'migration stamp: ' || version_num from alembic_version" \
  -c "select 'accounts: ' || count(*) from accounts"
