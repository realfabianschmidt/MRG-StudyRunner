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
readonly MACOS_MINIMUM_MINOR=6
readonly XCODE_REQUIRED_MAJOR=26
readonly XCODE_REFERENCE_VERSION="26.3"
readonly XCODE_REFERENCE_DEVELOPER_DIR="/Applications/Xcode-26.3.app/Contents/Developer"
readonly XCODE_DOWNLOAD_URL="https://developer.apple.com/download/all/?q=Xcode%2026.3"

usage() {
  cat <<'EOF'
Usage: bash tools/install-macos.sh [options]

Options:
  --install-system-dependencies  Install missing Python 3.12 from python.org.
                                 Xcode 26 must already be installed and initialized
                                 when the recording core is enabled.
  --skip-recording-core          Skip CMake, Xcode, and the native
                                 XDF core (non-recording use only).
  -h, --help                     Show this help.
EOF
}

fail() {
  printf 'Study Runner setup failed: %s\n' "$1" >&2
  exit 1
}

is_full_xcode_developer_dir() {
  local candidate="$1"
  case "$candidate" in
    *.app/Contents/Developer)
      [[ -d "$candidate" && -x "$candidate/usr/bin/xcodebuild" ]]
      ;;
    *)
      return 1
      ;;
  esac
}

resolve_full_xcode_developer_dir() {
  local candidate=""

  if [[ -n "${DEVELOPER_DIR:-}" ]] && is_full_xcode_developer_dir "$DEVELOPER_DIR"; then
    printf '%s\n' "$DEVELOPER_DIR"
    return 0
  fi

  if is_full_xcode_developer_dir "$XCODE_REFERENCE_DEVELOPER_DIR"; then
    printf '%s\n' "$XCODE_REFERENCE_DEVELOPER_DIR"
    return 0
  fi

  if command -v xcode-select >/dev/null 2>&1; then
    candidate="$(xcode-select --print-path 2>/dev/null || true)"
    if [[ -n "$candidate" ]] && is_full_xcode_developer_dir "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  candidate="/Applications/Xcode.app/Contents/Developer"
  if is_full_xcode_developer_dir "$candidate"; then
    printf '%s\n' "$candidate"
    return 0
  fi
  return 1
}

validate_xcode_toolchain() {
  local developer_dir=""
  local xcode_output=""
  local xcode_first_line=""
  local xcode_major=""
  local compiler=""
  local sdk_path=""
  local probe_output=""

  developer_dir="$(resolve_full_xcode_developer_dir || true)"
  if [[ -z "$developer_dir" ]]; then
    fail "a complete Xcode ${XCODE_REQUIRED_MAJOR} installation is required for XDF recording. Open ${XCODE_DOWNLOAD_URL}, sign in with an Apple Account, download Xcode ${XCODE_REFERENCE_VERSION} Universal, and install it as /Applications/Xcode-${XCODE_REFERENCE_VERSION}.app. The standalone Command Line Tools at /Library/Developer/CommandLineTools are not sufficient. Then initialize it with: sudo env DEVELOPER_DIR=${XCODE_REFERENCE_DEVELOPER_DIR} /usr/bin/xcodebuild -runFirstLaunch"
  fi

  export DEVELOPER_DIR="$developer_dir"
  if ! xcode_output="$(xcodebuild -version 2>&1)"; then
    fail "Xcode could not be initialized from $DEVELOPER_DIR. Run: sudo env DEVELOPER_DIR=$DEVELOPER_DIR /usr/bin/xcodebuild -runFirstLaunch. Details: $xcode_output"
  fi
  xcode_first_line="$(printf '%s\n' "$xcode_output" | head -n 1)"
  xcode_major="$(printf '%s\n' "$xcode_first_line" | sed -n 's/^Xcode \([0-9][0-9]*\).*/\1/p')"
  [[ "$xcode_major" == "$XCODE_REQUIRED_MAJOR" ]] || fail "Xcode ${XCODE_REQUIRED_MAJOR} is required for XDF recording, but ${xcode_first_line:-an unknown Xcode version} is active at $DEVELOPER_DIR. Install and initialize Xcode ${XCODE_REQUIRED_MAJOR}, then rerun this installer."

  compiler="$(xcrun --sdk macosx --find clang++ 2>/dev/null || true)"
  sdk_path="$(xcrun --sdk macosx --show-sdk-path 2>/dev/null || true)"
  if [[ -z "$compiler" || ! -x "$compiler" || -z "$sdk_path" || ! -d "$sdk_path" ]]; then
    fail "Xcode ${XCODE_REQUIRED_MAJOR} is present but its compiler or macOS SDK is unavailable. Run: sudo env DEVELOPER_DIR=$DEVELOPER_DIR /usr/bin/xcodebuild -runFirstLaunch. Then rerun this installer."
  fi

  if ! probe_output="$(printf '#include <cstdint>\nint main() { std::uint32_t value = 0; return static_cast<int>(value); }\n' | "$compiler" -std=c++20 -arch "$host_arch" -isysroot "$sdk_path" -x c++ -fsyntax-only - 2>&1)"; then
    fail "the Xcode ${XCODE_REQUIRED_MAJOR} C++20 toolchain failed its preflight check (including <cstdint>) before any project dependencies were downloaded. Run: sudo env DEVELOPER_DIR=$DEVELOPER_DIR /usr/bin/xcodebuild -runFirstLaunch. Then rerun this installer. Details: $probe_output"
  fi

  printf 'Using %s from %s.\n' "$xcode_first_line" "$DEVELOPER_DIR"
  printf 'Validated native C++20 compiler and SDK: %s / %s\n' "$compiler" "$sdk_path"
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
IFS='.' read -r macos_major macos_minor _ <<< "$macos_version"
macos_minor="${macos_minor:-0}"
[[ "$macos_major" =~ ^[0-9]+$ && "$macos_minor" =~ ^[0-9]+$ ]] || fail "could not determine the macOS version (found $macos_version)"
if ((macos_major < MACOS_MINIMUM_MAJOR || (macos_major == MACOS_MINIMUM_MAJOR && macos_minor < MACOS_MINIMUM_MINOR))); then
  fail "Study Runner requires macOS $MACOS_MINIMUM_MAJOR.$MACOS_MINIMUM_MINOR or newer (found $macos_version)"
fi

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

if ((skip_recording_core == 0)); then
  validate_xcode_toolchain
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
