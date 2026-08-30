# Deploying Anchor

Everything in this directory lands on the VPS at `/opt/anchor` on every deploy.

## How a deploy happens

Merging to `main` runs the test jobs in `.github/workflows/ci.yml`; if they pass, the `deploy` job builds the `app` and `caddy` images, pushes them to GHCR tagged with the commit SHA, rsyncs this directory (plus a `.env` rendered from the repo's secrets and variables) to the box, and runs `docker compose pull && up -d --wait`.
The job then smokes the live site with the skeleton journey.
A red deploy job is read by its failed step: anything before "Ship to the box" means nothing changed on the server; "Ship" means the new stack did not come up healthy; "Smoke" means it is up but the journey failed.

## Rolling back

Re-run the `deploy` job of the last good commit from the Actions tab.
Images are tagged by SHA and kept on GHCR, so the old build redeploys exactly.

## Backups

`anchor-backup.timer` (systemd, on the box) runs `backup.sh` nightly: a `pg_dump` through the postgres container, shipped to the `anchor-backups` R2 bucket.
The bucket's lifecycle rule deletes dumps older than 30 days.
Check recent runs with `journalctl -u anchor-backup.service`; trigger one by hand with `systemctl start anchor-backup.service`.
To prove a dump restores, run `bash /opt/anchor/restore-check.sh` as root; it loads the newest dump into a throwaway container and prints sanity counts.
To restore for real, stop the stack, restore the dump into the postgres volume the same way, and start the stack again.

## Configuration

All app secrets and variables live in the GitHub repo settings (`gh secret list` / `gh variable list`), and reach the box only as the rendered `.env`.
To change one: update it on GitHub, then re-run the deploy job.
Values are written into `.env` and the database URL verbatim, so keep them free of `$`, quotes, `#`, and newlines (the generated password is plain hex for this reason).
The R2 credentials are the exception: they live only in root's rclone config on the box.

## One-time server setup

`setup.sh` bootstraps a fresh Ubuntu 24.04 box: Docker, the `deploy` user CI logs in as, the firewall, the rclone config, and the backup timer.
The systemd unit files are installed only by `setup.sh`; if they change, deploy first, then run `bash /opt/anchor/setup.sh` as root (no argument needed: it keeps the CI key and rclone config it finds).
