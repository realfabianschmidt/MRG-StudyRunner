#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: bash tools/start-macos.sh [options]

Options:
  --host ADDRESS   Override the listening address.
  --port PORT      Override the listening port (1-65535).
  --no-browser     Do not open the Admin page automatically.
  --no-https       Disable HTTPS (camera/browser sources will not be ready).
  --self-check     Check source startup without opening a persistent server.
  -h, --help       Show this help.
EOF
}

fail() {
  printf 'Study Runner start failed: %s\n' "$1" >&2
  exit 1
}

self_check=0

while (($#)); do
  case "$1" in
    --host)
      (($# >= 2)) || fail "--host needs an address"
      export STUDY_RUNNER_HOST="$2"
      shift
      ;;
    --port)
      (($# >= 2)) || fail "--port needs a value"
      [[ "$2" =~ ^[0-9]+$ ]] && ((10#$2 >= 1 && 10#$2 <= 65535)) || fail "--port must be between 1 and 65535"
      export STUDY_RUNNER_PORT="$2"
      shift
      ;;
    --no-browser) export STUDY_RUNNER_NO_BROWSER=1 ;;
    --no-https) export STUDY_RUNNER_HTTPS=0 ;;
    --self-check) self_check=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "unknown option: $1" ;;
  esac
  shift
done

[[ "$(uname -s)" == "Darwin" ]] || fail "this script supports macOS only; on Windows use tools/start-windows.cmd"
repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
software_root="$repository_root/software"
venv_python="$repository_root/.venv/bin/python"
server_script="$software_root/server.py"

[[ -x "$venv_python" ]] || fail "Study Runner is not installed in this checkout. Run 'bash tools/install-macos.sh' first."
[[ -f "$server_script" ]] || fail "incomplete checkout: missing $server_script"

server_arguments=("$server_script")
if ((self_check)); then
  server_arguments+=(--self-check)
  printf '%s\n' 'Running the Study Runner source self-check.'
else
  printf '%s\n' 'Starting Study Runner. Press Ctrl+C to stop it.'
fi
cd "$software_root"
exec "$venv_python" "${server_arguments[@]}"
