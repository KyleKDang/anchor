#!/usr/bin/env bash
# Is the box already serving this commit, or something newer than it?
#
# The deploy job asks twice, because a yes means something different each time.
# Before shipping, yes means a newer commit overtook this one while it waited in
# the deploy queue, and shipping now would roll production backwards. After
# shipping, yes is the proof that this commit is what actually landed - the
# question that used to be answerable only by curling production by hand (#107).
#
# Reads the box over ssh and changes nothing. Exit 0 for yes, 1 for no; every no
# says why, because a no before the ship is routine and a no after it is a deploy
# that did not take.
set -uo pipefail

target=$1
sha=$2

# Only the three services deploy/compose.yml pins to ANCHOR_IMAGE_TAG: postgres is
# upstream and carries no commit tag, and migrate has exited by now so `ps -q`
# leaves it out. xargs -r keeps a stack that is down from erroring inside inspect,
# so an empty box reaches the count below instead of dying here.
if ! images=$(ssh -i ~/.ssh/anchor_deploy "$target" \
  'cd /opt/anchor && docker compose ps -q web worker caddy | xargs -r docker inspect --format "{{.Config.Image}}"'); then
  echo "could not read the running images off the box" >&2
  exit 1
fi

ours=$(printf '%s\n' "$images" | grep '^ghcr\.io/kylekdang/')
if [ "$(printf '%s\n' "$ours" | grep -c .)" -ne 3 ]; then
  echo "the box is not running web, worker and caddy: [$(printf '%s' "$images" | tr '\n' ' ')]" >&2
  exit 1
fi

tag=$(printf '%s\n' "$ours" | sed 's/.*://' | sort -u)
if [ "$(printf '%s\n' "$tag" | grep -c .)" -ne 1 ]; then
  echo "the box is running mixed image tags: $(printf '%s' "$tag" | tr '\n' ' ')" >&2
  exit 1
fi

if [ "$tag" = "$sha" ]; then
  echo "the box is serving $sha"
  exit 0
fi

# A commit that merged while this deploy sat in the queue is not in a checkout
# taken before it existed, and an unresolvable tag would read as "not newer".
if ! git cat-file -e "$tag^{commit}" 2>/dev/null; then
  git fetch --quiet origin main 2>/dev/null || true
fi

if git merge-base --is-ancestor "$sha" "$tag" 2>/dev/null; then
  echo "the box is serving $tag, which is newer than $sha"
  exit 0
fi

echo "the box is serving $tag, which is neither $sha nor newer than it" >&2
exit 1
