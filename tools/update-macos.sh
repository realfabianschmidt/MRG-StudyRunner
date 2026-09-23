#!/usr/bin/env bash
# Update Study Runner from the terminal. Stop Study Runner first (Ctrl+C).
# `exec` hands over to Python at once: the update replaces this tools folder.
set -Eeuo pipefail
repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
[[ -x "$repository_root/.venv/bin/python" ]] || { echo "Study Runner is not installed here. Run 'bash tools/install-macos.sh' first." >&2; exit 1; }
exec "$repository_root/.venv/bin/python" "$repository_root/tools/update_study_runner.py" "$@"
