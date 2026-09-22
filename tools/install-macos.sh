#!/usr/bin/env bash

set -Eeuo pipefail

install_system_dependencies=0
skip_recording_core=0

readonly PYTHON_VERSION="3.12.10"
readonly PYTHON_PACKAGE_NAME="python-${PYTHON_VERSION}-macos11.pkg"
readonly PYTHON_PACKAGE_URL="https://www.python.org/ftp/python/${PYTHON_VERSION}/${PYTHON_PACKAGE_NAME}"
readonly PYTHON_PACKAGE_SHA256="8373e58da4ea146b3eb1c1f9834f19a319440b6b679b06050b1f9ee3237aa8e4"
readonly PYTHON_INSTALLER_IDENTITY="Developer ID Installer: Python Software Foundation (BMM5U3QVKW)"
readonly MACOS_MINIMUM_MAJOR=15

usage() {
  cat <<'EOF'
Usage: bash tools/install-macos.sh [options]

Options:
  --install-system-dependencies  Install missing Python 3.12 from python.org.
  --skip-recording-core          Skip CMake, Command Line Tools, and the native
                                 XDF core (non-recording use only).
  -h, --help                     Show this help.
EOF
}

fail() {
  printf 'Study Runner setup failed: %s\n' "$1" >&2
  exit 1
}

python_matches_host() {
  local candidate="$1"
  local details
  [[ -x "$candidate" ]] || return 1
  details="$("$candidate" -c 'import platform, sys; print(f"{sys.version_info.major}.{sys.version_info.minor}|{platform.machine()}")' 2>/dev/null)" || return 1
  [[ "$details" == "3.12|$host_arch" ]]
}

resolve_python312() {
  local candidate
  local path_candidate=""
  if command -v python3.12 >/dev/null 2>&1; then
    path_candidate="$(command -v python3.12)"
  fi
  for candidate in \
    "$path_candidate" \
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12" \
    "/usr/local/bin/python3.12"; do
    if [[ -n "$candidate" ]] && python_matches_host "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

download_directory=""
python_package_path=""

cleanup_python_download() {
  if [[ -n "$python_package_path" && -f "$python_package_path" ]]; then
    /bin/rm -f -- "$python_package_path"
  fi
  if [[ -n "$download_directory" && -d "$download_directory" ]]; then
    /bin/rmdir -- "$download_directory" 2>/dev/null || true
  fi
}

install_official_python() {
  local actual_sha256
  local signature_output

  command -v curl >/dev/null 2>&1 || fail "curl is required to download the official Python installer"
  command -v shasum >/dev/null 2>&1 || fail "shasum is required to verify the official Python installer"
  [[ -x /usr/sbin/pkgutil ]] || fail "pkgutil is required to verify the official Python installer signature"
  [[ -x /usr/sbin/installer ]] || fail "the macOS installer command is unavailable"

  download_directory="$(mktemp -d "${TMPDIR:-/tmp}/study-runner-python.XXXXXX")" || fail "could not create a temporary download directory"
  python_package_path="$download_directory/$PYTHON_PACKAGE_NAME"
  trap cleanup_python_download EXIT

  printf 'Downloading official Python %s universal2 installer...\n' "$PYTHON_VERSION"
  if ! curl --fail --location --proto '=https' --tlsv1.2 \
    --output "$python_package_path" "$PYTHON_PACKAGE_URL"; then
    fail "could not download $PYTHON_PACKAGE_URL"
  fi

  actual_sha256="$(shasum -a 256 "$python_package_path" | awk '{print $1}')"
  [[ "$actual_sha256" == "$PYTHON_PACKAGE_SHA256" ]] || fail "the Python installer checksum is invalid (expected $PYTHON_PACKAGE_SHA256, found $actual_sha256)"

  if ! signature_output="$(/usr/sbin/pkgutil --check-signature "$python_package_path" 2>&1)"; then
    fail "the Python installer does not have a valid macOS package signature"
  fi
  if ! printf '%s\n' "$signature_output" | grep -Fq "$PYTHON_INSTALLER_IDENTITY"; then
    fail "the Python installer was not signed by $PYTHON_INSTALLER_IDENTITY"
  fi

  printf '%s\n' 'The verified Python Software Foundation installer needs administrator permission.'
  if ! sudo /usr/sbin/installer -pkg "$python_package_path" -target /; then
    fail "Python installation was cancelled or failed"
  fi

  cleanup_python_download
  trap - EXIT
  download_directory=""
  python_package_path=""
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

[[ "$(uname -s)" == "Darwin" ]] || fail "this script supports macOS only; on Windows use tools/install-windows.cmd"
if [[ "$(/usr/sbin/sysctl -in sysctl.proc_translated 2>/dev/null || true)" == "1" ]]; then
  fail "this shell is running under Rosetta. Open a native Terminal, or run 'arch -arm64 bash tools/install-macos.sh', and retry"
fi

host_arch="$(uname -m)"
case "$host_arch" in
  arm64|x86_64) ;;
  *) fail "recording is supported on macOS Apple Silicon and Intel only (found $host_arch)" ;;
esac

macos_version="$(sw_vers -productVersion)"
macos_major="${macos_version%%.*}"
[[ "$macos_major" =~ ^[0-9]+$ ]] || fail "could not determine the macOS version (found $macos_version)"
((macos_major >= MACOS_MINIMUM_MAJOR)) || fail "Study Runner requires macOS $MACOS_MINIMUM_MAJOR or newer (found $macos_version)"

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
requirements_path="$repository_root/software/requirements.txt"
bootstrap_constraints="$repository_root/software/constraints/py312-bootstrap.txt"
common_constraints="$repository_root/software/constraints/py312-common.txt"
local_emotion_constraints="$repository_root/software/constraints/py312-local-emotion.txt"
build_tools_constraints="$repository_root/software/constraints/py312-build-tools.txt"
setup_script="$repository_root/tools/setup_recording_worker.py"
venv_path="$repository_root/.venv"
venv_python="$venv_path/bin/python"

for required_install_file in \
  "$requirements_path" \
  "$bootstrap_constraints" \
  "$common_constraints" \
  "$local_emotion_constraints" \
  "$build_tools_constraints"; do
  [[ -f "$required_install_file" ]] || fail "run this from a complete Study Runner checkout; missing $required_install_file"
done

expected_cmake_version="$(sed -n 's/^cmake==//p' "$build_tools_constraints")"
[[ "$expected_cmake_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "the CMake build-tool constraint must contain exactly one cmake==X.Y.Z pin"

printf 'Study Runner first-install/repair (macOS %s, %s)\n' "$macos_version" "$host_arch"
printf 'Repository: %s\n' "$repository_root"

if ((skip_recording_core == 0)) && ! xcode-select -p >/dev/null 2>&1; then
  if ((install_system_dependencies)); then
    printf '%s\n' 'Xcode Command Line Tools are required. macOS will now open its installer.'
    xcode-select --install || true
    fail "finish the Apple installer, then run this command again"
  fi
  fail "Xcode Command Line Tools are missing. Rerun with 'bash tools/install-macos.sh --install-system-dependencies', finish the Apple dialog, and retry."
fi

if [[ -e "$venv_path" ]]; then
  [[ -x "$venv_python" ]] || fail "$venv_path exists but is not a valid macOS virtual environment; move it aside manually and rerun"
  venv_details="$($venv_python -c 'import platform, sys; print(f"{sys.version_info.major}.{sys.version_info.minor}|{platform.machine()}")')"
  [[ "$venv_details" == "3.12|$host_arch" ]] || fail "$venv_path uses Python $venv_details, but Study Runner requires Python 3.12 for $host_arch; move it aside manually and rerun"
  printf 'Reusing %s\n' "$venv_path"
else
  python312="$(resolve_python312 || true)"
  if [[ -z "$python312" ]] && ((install_system_dependencies)); then
    install_official_python
    python312="$(resolve_python312 || true)"
  fi
  [[ -n "$python312" ]] || fail "native Python 3.12 was not found. Rerun with 'bash tools/install-macos.sh --install-system-dependencies'."
  printf 'Creating %s with %s...\n' "$venv_path" "$python312"
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
  printf '%s\n' 'Installing the project-local CMake build tools...'
  "$venv_python" -m pip install --constraint "$build_tools_constraints" cmake
  export PATH="$venv_path/bin:$PATH"
  [[ "$(command -v cmake || true)" == "$venv_path/bin/cmake" ]] || fail "the project-local CMake executable is unavailable"
  [[ -x "$venv_path/bin/ctest" ]] || fail "the project-local CTest executable is unavailable"
  cmake_version="$(cmake --version | awk 'NR == 1 {print $3}')"
  [[ "$cmake_version" == "$expected_cmake_version" ]] || fail "the project-local CMake version is $cmake_version; expected $expected_cmake_version"

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
