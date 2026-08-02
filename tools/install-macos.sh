#!/usr/bin/env bash

set -Eeuo pipefail

install_system_dependencies=0
skip_recording_core=0

usage() {
  cat <<'EOF'
Usage: bash tools/install-macos.sh [options]

Options:
  --install-system-dependencies  Install Python 3.12 and CMake with Homebrew.
  --skip-recording-core          Skip the native XDF core (non-recording use only).
  -h, --help                     Show this help.
EOF
}

fail() {
  printf 'Study Runner setup failed: %s\n' "$1" >&2
  exit 1
}

find_brew() {
  local candidate
  if command -v brew >/dev/null 2>&1; then
    command -v brew
    return 0
  fi
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

while (($#)); do
  case "$1" in
    --install-system-dependencies) install_system_dependencies=1 ;;
    --skip-recording-core) skip_recording_core=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "unknown option: $1" ;;
  esac
  shift
done

[[ "$(uname -s)" == "Darwin" ]] || fail "this script supports macOS only; on Windows use tools/install-windows.ps1"
host_arch="$(uname -m)"
case "$host_arch" in
  arm64|x86_64) ;;
  *) fail "recording is supported on macOS Apple Silicon and Intel only (found $host_arch)" ;;
esac

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
requirements_path="$repository_root/software/requirements.txt"
bootstrap_constraints="$repository_root/software/constraints/py312-bootstrap.txt"
common_constraints="$repository_root/software/constraints/py312-common.txt"
local_emotion_constraints="$repository_root/software/constraints/py312-local-emotion.txt"
setup_script="$repository_root/tools/setup_recording_worker.py"
venv_path="$repository_root/.venv"
venv_python="$venv_path/bin/python"

for required_install_file in \
  "$requirements_path" \
  "$bootstrap_constraints" \
  "$common_constraints" \
  "$local_emotion_constraints"; do
  [[ -f "$required_install_file" ]] || fail "run this from a complete Study Runner checkout; missing $required_install_file"
done

printf 'Study Runner first-install/repair (macOS %s)\n' "$host_arch"
printf 'Repository: %s\n' "$repository_root"

brew_command="$(find_brew || true)"

if ((install_system_dependencies)); then
  if ! xcode-select -p >/dev/null 2>&1; then
    printf '%s\n' 'Xcode Command Line Tools are required. macOS will now open its installer.'
    xcode-select --install || true
    fail "finish the Apple installer, then run this command again"
  fi
  [[ -n "$brew_command" ]] || fail 'Homebrew is not installed. Install it from https://brew.sh, then run this command again.'
  brew_prefix="$("$brew_command" --prefix)"
  export PATH="$brew_prefix/bin:$PATH"
  HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ENV_HINTS=1 "$brew_command" install python@3.12 cmake
fi

if ! xcode-select -p >/dev/null 2>&1 && ((skip_recording_core == 0)); then
  fail "Xcode Command Line Tools are missing. Run 'xcode-select --install', finish the dialog, and retry."
fi

if [[ -e "$venv_path" ]]; then
  [[ -x "$venv_python" ]] || fail "$venv_path exists but is not a valid macOS virtual environment; move it aside manually and rerun"
  venv_version="$($venv_python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  [[ "$venv_version" == "3.12" ]] || fail "$venv_path uses Python $venv_version; move it aside manually and rerun with Python 3.12"
  venv_arch="$($venv_python -c 'import platform; print(platform.machine())')"
  [[ "$venv_arch" == "$host_arch" ]] || fail "$venv_path uses architecture $venv_arch, but this shell uses $host_arch; move it aside manually and rerun from the intended native shell"
  printf 'Reusing %s\n' "$venv_path"
else
  python312=""
  if [[ -n "$brew_command" ]]; then
    brew_python="$("$brew_command" --prefix python@3.12 2>/dev/null)/bin/python3.12" || true
    if [[ -x "$brew_python" ]]; then
      python312="$brew_python"
    fi
  fi
  if [[ -z "$python312" ]] && command -v python3.12 >/dev/null 2>&1; then
    python312="$(command -v python3.12)"
  fi
  [[ -n "$python312" ]] || fail "Python 3.12 was not found. Install Homebrew from https://brew.sh and rerun with --install-system-dependencies."
  [[ "$($python312 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" == "3.12" ]] || fail "the selected Python is not version 3.12: $python312"
  [[ "$($python312 -c 'import platform; print(platform.machine())')" == "$host_arch" ]] || fail "the selected Python architecture does not match this shell ($host_arch): $python312"
  printf 'Creating %s with Python 3.12...\n' "$venv_path"
  "$python312" -m venv "$venv_path"
fi

printf '%s\n' 'Installing Study Runner Python dependencies...'
"$venv_python" -m pip install --upgrade --constraint "$bootstrap_constraints" pip
dependency_constraints=(--constraint "$common_constraints")
if [[ "$host_arch" == "arm64" ]]; then
  dependency_constraints+=(--constraint "$local_emotion_constraints")
fi
"$venv_python" -m pip install "${dependency_constraints[@]}" --requirement "$requirements_path"

if ((skip_recording_core == 0)); then
  command -v cmake >/dev/null 2>&1 || fail "CMake is missing. Run this script with --install-system-dependencies or run 'brew install cmake'."
  printf '%s\n' 'Checking the canonical XDF recording core...'
  if ! "$venv_python" "$setup_script" --probe-only --require-canonical --json >/dev/null 2>&1; then
    printf '%s\n' 'No current verified core was found; building and testing it now...'
    "$venv_python" "$setup_script" --require-canonical
  else
    printf '%s\n' 'Reusing the current verified XDF recording core.'
  fi
else
  printf '%s\n' 'WARNING: Recording-core setup was skipped. Required XDF recording studies will remain blocked.' >&2
fi

if [[ "$host_arch" == "x86_64" ]]; then
  printf '%s\n' 'NOTE: macOS Intel supports the server and XDF recording, but camera_emotion must use remote_worker; local DeepFace is unavailable on this platform.'
fi

printf '\nStudy Runner is ready. Later starts need only:\n  bash tools/start-macos.sh\n'
