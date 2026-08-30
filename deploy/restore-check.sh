#!/usr/bin/env bash
# Prove the newest dump in R2 actually restores: load it into a throwaway postgres
# container and read back the migration stamp and account count.
# Never touches the live database. Run as root: bash /opt/anchor/restore-check.sh
set -euo pipefail

latest="$(rclone lsf r2:anchor-backups --files-only | sort | tail -1)"
[ -n "$latest" ] || { echo "no dumps in the bucket" >&2; exit 1; }
echo "restoring $latest into a throwaway container..."
rclone copyto "r2:anchor-backups/$latest" /tmp/restore-check.dump

docker run -d --name anchor-restore-check -e POSTGRES_PASSWORD=restore-check postgres:17 >/dev/null
trap 'docker rm -f anchor-restore-check >/dev/null; rm -f /tmp/restore-check.dump' EXIT
until docker exec anchor-restore-check pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done

docker exec anchor-restore-check createdb -U postgres anchor
# --no-owner: the throwaway container has no "anchor" role to hand ownership to.
docker exec -i anchor-restore-check pg_restore -U postgres --no-owner --dbname anchor \
  < /tmp/restore-check.dump

echo "restored. sanity checks:"
docker exec anchor-restore-check psql -U postgres -d anchor -tA \
  -c "select 'migration stamp: ' || version_num from alembic_version" \
  -c "select 'accounts: ' || count(*) from accounts"
