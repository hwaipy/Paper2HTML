#!/usr/bin/env bash

set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly LOCAL_DIR="${PAPER2HTML_TESTDATA_DIR:-${SCRIPT_DIR}/testdata}"
readonly REMOTE_DIR="${PAPER2HTML_TESTDATA_REMOTE:-paper2html-testdata:sync/pdf2html/testdata}"

usage() {
  cat <<'EOF'
Usage:
  ./sync_testdata.sh pull [--dry-run]
  ./sync_testdata.sh push [--dry-run]

Modes:
  pull      Copy new and changed files from WebDAV to local testdata/.
  push      Copy new and changed files from local testdata/ to WebDAV.

Options:
  --dry-run Show the planned operations without transferring files.

Environment overrides:
  PAPER2HTML_TESTDATA_DIR     Local directory (default: <repo>/testdata)
  PAPER2HTML_TESTDATA_REMOTE  rclone path
                              (default: paper2html-testdata:sync/pdf2html/testdata)

Neither mode deletes files from its destination.
EOF
}

if ! command -v rclone >/dev/null 2>&1; then
  echo "Error: rclone is required. Install it with: brew install rclone" >&2
  exit 1
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage >&2
  exit 2
fi

mode="$1"
dry_run=()

if [[ $# -eq 2 ]]; then
  if [[ "$2" != "--dry-run" ]]; then
    usage >&2
    exit 2
  fi
  dry_run=(--dry-run)
fi

common_options=(
  --create-empty-src-dirs
  --progress
  --human-readable
)

case "$mode" in
  pull)
    mkdir -p -- "$LOCAL_DIR"
    echo "Pulling ${REMOTE_DIR}/ -> ${LOCAL_DIR}/"
    rclone copy "$REMOTE_DIR" "$LOCAL_DIR" "${common_options[@]}" "${dry_run[@]}"
    ;;
  push)
    if [[ ! -d "$LOCAL_DIR" ]]; then
      echo "Error: local test data directory does not exist: $LOCAL_DIR" >&2
      exit 1
    fi
    echo "Pushing ${LOCAL_DIR}/ -> ${REMOTE_DIR}/"
    rclone copy "$LOCAL_DIR" "$REMOTE_DIR" "${common_options[@]}" "${dry_run[@]}"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
