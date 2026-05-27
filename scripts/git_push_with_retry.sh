#!/usr/bin/env bash
set -euo pipefail

remote="${1:-origin}"
branch="${2:-main}"
refspec="${3:-HEAD:${branch}}"
max_attempts="${GIT_PUSH_MAX_ATTEMPTS:-5}"
sleep_base_s="${GIT_PUSH_SLEEP_BASE_S:-15}"

head_sha="$(git rev-parse HEAD)"

for attempt in $(seq 1 "$max_attempts"); do
  echo "git push attempt ${attempt}/${max_attempts}: ${remote} ${refspec}"
  if git push "$remote" "$refspec"; then
    exit 0
  fi

  git fetch "$remote" "$branch" || true
  remote_sha="$(git rev-parse --verify "${remote}/${branch}" 2>/dev/null || true)"
  if [[ "$remote_sha" == "$head_sha" ]]; then
    echo "Remote ${remote}/${branch} already points at ${head_sha}; treating push as successful."
    exit 0
  fi

  if [[ "$attempt" == "$max_attempts" ]]; then
    break
  fi

  sleep_s=$((sleep_base_s * attempt))
  echo "git push failed; retrying in ${sleep_s}s"
  sleep "$sleep_s"
done

echo "git push failed after ${max_attempts} attempts" >&2
exit 1
