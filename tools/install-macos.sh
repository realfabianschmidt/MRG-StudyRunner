#!/usr/bin/env bash

set -Eeuo pipefail

skip_recording_core=0
build_core_from_source=0

readonly MACOS_MINIMUM_MAJOR=13

usage() {
  cat <<'EOF'
Usage: bash tools/install-macos.sh [options]

Installs or repairs Study Runner in this folder. Everything it downloads stays
inside this folder (.tools and .venv); no administrator password and no Xcode
are needed. Safe to run again after an update or an interrupted run.

Options:
  --skip-recording-core     Do not install the XDF recording core
                            (studies that require recording stay blocked).
  --build-core-from-source  Developers only: compile the XDF core locally
                            instead of downloading the tested release build.
                            Needs Apple's Command Line Tools (xcode-select --install).
  -h, --help                Show this help.
EOF
}

fail() {
  printf 'Study Runner setup failed: %s\n' "$1" >&2
  exit 1
}

download_directory=""

cleanup_downloads() {
  if [[ -n "$download_directory" && -d "$download_directory" ]]; then
    /bin/rm -rf -- "$download_directory"
  fi
}
trap cleanup_downloads EXIT

download() {
  local url="$1"
  local output="$2"
  curl --fail --location --silent --show-error --proto '=https' --tlsv1.2 \
    --retry 3 --output "$output" "$url"
}

pin_value() {
  sed -n "s/^$1=//p" "$uv_bootstrap"
}

while (($#)); do
  case "$1" in
    --skip-recording-core) skip_recording_core=1 ;;
    --build-core-from-source) build_core_from_source=1 ;;
    --install-system-dependencies)
      printf '%s\n' 'NOTE: --install-system-dependencies is no longer needed and is ignored.'
      ;;
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
  arm64) platform_arch="macos-arm64" ;;
  x86_64) platform_arch="macos-x64" ;;
  *) fail "Study Runner supports macOS on Apple Silicon and Intel only (found $host_arch)" ;;
esac

macos_version="$(sw_vers -productVersion)"
macos_major="${macos_version%%.*}"
[[ "$macos_major" =~ ^[0-9]+$ ]] || fail "could not determine the macOS version (found $macos_version)"
if ((macos_major < MACOS_MINIMUM_MAJOR)); then
  fail "Study Runner requires macOS $MACOS_MINIMUM_MAJOR or newer (found $macos_version)"
fi
command -v curl >/dev/null 2>&1 || fail "curl is required but missing"
command -v shasum >/dev/null 2>&1 || fail "shasum is required but missing"

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
requirements_path="$repository_root/software/requirements.txt"
uv_bootstrap="$repository_root/software/constraints/uv-bootstrap.txt"
bootstrap_constraints="$repository_root/software/constraints/py312-bootstrap.txt"
common_constraints="$repository_root/software/constraints/py312-common.txt"
local_emotion_constraints="$repository_root/software/constraints/py312-local-emotion.txt"
build_tools_constraints="$repository_root/software/constraints/py312-build-tools.txt"
setup_script="$repository_root/tools/setup_recording_worker.py"
tools_path="$repository_root/.tools"
venv_path="$repository_root/.venv"
venv_python="$venv_path/bin/python"

for required_install_file in \
  "$requirements_path" \
  "$uv_bootstrap" \
  "$bootstrap_constraints" \
  "$common_constraints" \
  "$local_emotion_constraints" \
  "$build_tools_constraints" \
  "$setup_script"; do
  [[ -f "$required_install_file" ]] || fail "run this from a complete Study Runner folder; missing $required_install_file"
done

uv_version="$(pin_value uv_version)"
python_version="$(pin_value python_version)"
uv_pin="$(pin_value "$platform_arch")"
uv_asset="${uv_pin%% *}"
uv_sha256="${uv_pin##* }"
[[ -n "$uv_version" && -n "$python_version" && -n "$uv_asset" && ${#uv_sha256} -eq 64 ]] \
  || fail "software/constraints/uv-bootstrap.txt is incomplete for $platform_arch"

# uv splits --constraint values at spaces, so constraints are passed relative to
# the repository root and never as absolute paths (the folder may contain spaces).
cd "$repository_root"
relative_constraints="software/constraints"

printf 'Study Runner first-install/repair (macOS %s, %s)\n' "$macos_version" "$host_arch"
printf 'Folder: %s\n' "$repository_root"

# Keep uv, Python, and every cache inside this folder and ignore user-wide uv settings.
export UV_CACHE_DIR="$tools_path/uv-cache"
export UV_PYTHON_INSTALL_DIR="$tools_path/python"
export UV_NO_CONFIG=1
unset UV_PYTHON UV_INDEX_URL UV_EXTRA_INDEX_URL VIRTUAL_ENV

download_directory="$(mktemp -d "${TMPDIR:-/tmp}/study-runner-setup.XXXXXX")" \
  || fail "could not create a temporary download folder"

uv_path="$tools_path/uv/$uv_version/uv"
if [[ -x "$uv_path" ]] && "$uv_path" --version 2>/dev/null | grep -Fq "uv $uv_version"; then
  printf 'Reusing uv %s.\n' "$uv_version"
else
  printf 'Downloading uv %s...\n' "$uv_version"
  download "https://github.com/astral-sh/uv/releases/download/$uv_version/$uv_asset" \
    "$download_directory/$uv_asset" || fail "could not download uv. Check the internet connection and retry"
  actual_sha256="$(shasum -a 256 "$download_directory/$uv_asset" | awk '{print $1}')"
  [[ "$actual_sha256" == "$uv_sha256" ]] \
    || fail "the uv download has the wrong checksum (expected $uv_sha256, found $actual_sha256)"
  tar -xzf "$download_directory/$uv_asset" -C "$download_directory" \
    || fail "could not unpack $uv_asset"
  mkdir -p "$tools_path/uv/$uv_version"
  mv -f "$download_directory/${uv_asset%.tar.gz}/uv" "$uv_path"
  chmod 755 "$uv_path"
fi

if [[ -e "$venv_path" ]]; then
  [[ -x "$venv_python" ]] || fail "$venv_path exists but is not a valid macOS virtual environment; move it aside manually and rerun"
  venv_details="$("$venv_python" -c 'import platform, sys; print(f"{sys.version_info.major}.{sys.version_info.minor}|{platform.machine()}")')"
  [[ "$venv_details" == "3.12|$host_arch" ]] || fail "$venv_path uses Python $venv_details, but Study Runner requires Python 3.12 for $host_arch; move it aside manually and rerun"
  printf 'Reusing %s\n' "$venv_path"
else
  printf 'Installing Python %s into %s...\n' "$python_version" "$UV_PYTHON_INSTALL_DIR"
  "$uv_path" python install "$python_version" --no-bin \
    || fail "could not install Python $python_version. Check the internet connection and retry"
  printf 'Creating %s...\n' "$venv_path"
  "$uv_path" venv --seed --managed-python --python "$python_version" "$venv_path" \
    || fail "could not create $venv_path"
fi

printf '%s\n' 'Installing Study Runner Python dependencies...'
"$uv_path" pip install --python "$venv_python" --constraint "$relative_constraints/py312-bootstrap.txt" pip \
  || fail "could not install pip into $venv_path"
dependency_constraints=(--constraint "$relative_constraints/py312-common.txt")
if [[ "$host_arch" == "arm64" ]]; then
  dependency_constraints+=(--constraint "$relative_constraints/py312-local-emotion.txt")
fi
"$uv_path" pip install --python "$venv_python" "${dependency_constraints[@]}" --requirement "$requirements_path" \
  || fail "could not install software/requirements.txt. Check the internet connection and retry"

install_prebuilt_core() {
  local core_source core_asset core_url checksums_url
  local checksum_arguments=()

  core_source="$("$venv_python" "$setup_script" --prebuilt-source)" \
    || fail "could not determine which XDF recording core to download"
  core_asset="$(printf '%s\n' "$core_source" | sed -n 1p)"
  core_url="$(printf '%s\n' "$core_source" | sed -n 2p)"
  checksums_url="$(printf '%s\n' "$core_source" | sed -n 3p)"

  if [[ -n "${STUDY_RUNNER_CORE_ASSET_DIR:-}" ]]; then
    printf 'Using the XDF recording core from %s...\n' "$STUDY_RUNNER_CORE_ASSET_DIR"
    cp "$STUDY_RUNNER_CORE_ASSET_DIR/$core_asset" "$download_directory/$core_asset" \
      || fail "$core_asset is missing from $STUDY_RUNNER_CORE_ASSET_DIR"
    if [[ -f "$STUDY_RUNNER_CORE_ASSET_DIR/SHA256SUMS" ]]; then
      cp "$STUDY_RUNNER_CORE_ASSET_DIR/SHA256SUMS" "$download_directory/SHA256SUMS"
    fi
  else
    printf 'Downloading the tested XDF recording core (%s)...\n' "$core_asset"
    download "$core_url" "$download_directory/$core_asset" \
      || fail "could not download $core_url. Check the internet connection and retry"
    download "$checksums_url" "$download_directory/SHA256SUMS" || true
  fi
  if [[ -f "$download_directory/SHA256SUMS" ]]; then
    checksum_arguments=(--checksums "$download_directory/SHA256SUMS")
  fi

  printf '%s\n' 'Verifying and testing the XDF recording core on this Mac...'
  "$venv_python" "$setup_script" --install-prebuilt "$download_directory/$core_asset" \
    ${checksum_arguments[@]+"${checksum_arguments[@]}"} --require-canonical \
    || fail "the downloaded XDF recording core could not be verified (see above). Developers with changed native sources can use --build-core-from-source"
}

build_core_locally() {
  printf '%s\n' 'Installing the project-local CMake build tools...'
  "$uv_path" pip install --python "$venv_python" --constraint "$relative_constraints/py312-build-tools.txt" cmake \
    || fail "could not install CMake into $venv_path"
  export PATH="$venv_path/bin:$PATH"
  printf '%s\n' 'Building and testing the XDF recording core from source...'
  "$venv_python" "$setup_script" --require-canonical \
    || fail "the local XDF core build failed (see above)"
}

if ((skip_recording_core == 0)); then
  printf '%s\n' 'Checking the XDF recording core...'
  if "$venv_python" "$setup_script" --probe-only --require-canonical --json >/dev/null 2>&1; then
    printf '%s\n' 'Reusing the current verified XDF recording core.'
  elif ((build_core_from_source)); then
    build_core_locally
  else
    install_prebuilt_core
  fi
else
  printf '%s\n' 'WARNING: Recording-core setup was skipped. Required XDF recording studies will remain blocked.' >&2
fi

if [[ "$host_arch" == "x86_64" ]]; then
  printf '%s\n' 'NOTE: macOS Intel supports the server and XDF recording, but camera_emotion must use remote_worker; local DeepFace is unavailable on this platform.'
fi

printf '\nStudy Runner is ready. Later starts need only:\n  bash tools/start-macos.sh\n'
