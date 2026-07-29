#!/usr/bin/env bash
set -euo pipefail

INSTALL_ONLY=0
FORCE_INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --install-only) INSTALL_ONLY=1 ;;
    --force-install) FORCE_INSTALL=1 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT_DIR/wandao_electron"
RUNTIME_DIR="$ROOT_DIR/.dev-runtime"
NODE_DIR="$RUNTIME_DIR/node"
NODE_VERSION="v22.12.0"
RUST_VERSION="1.88.0"
RUST_TOOLCHAIN="1.88.0"

step() {
  printf '\n==> %s\n' "$1"
}

ok() {
  printf '[OK] %s\n' "$1"
}

test_url_ms() {
  local url="$1"
  local method="${2:-get}"
  local result
  if [[ "$method" == "head" ]]; then
    result="$(curl -L -I --connect-timeout 5 --max-time 8 -o /dev/null -s -w '%{http_code} %{time_total}' "$url" || true)"
  else
    result="$(curl -L --connect-timeout 5 --max-time 8 -o /dev/null -s -w '%{http_code} %{time_total}' "$url" || true)"
  fi
  local code="${result%% *}"
  local seconds="${result##* }"
  if [[ "$code" =~ ^[23] ]]; then
    awk -v s="$seconds" 'BEGIN { printf "%d", s * 1000 }'
  else
    printf '999999'
  fi
}

add_local_node_to_path() {
  if [[ -x "$NODE_DIR/bin/node" ]]; then
    export PATH="$NODE_DIR/bin:$PATH"
  fi
}

node_package_name() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os:$arch" in
    Darwin:arm64) printf "node-%s-darwin-arm64.tar.gz" "$NODE_VERSION" ;;
    Darwin:x86_64) printf "node-%s-darwin-x64.tar.gz" "$NODE_VERSION" ;;
    Linux:aarch64|Linux:arm64) printf "node-%s-linux-arm64.tar.xz" "$NODE_VERSION" ;;
    Linux:x86_64) printf "node-%s-linux-x64.tar.xz" "$NODE_VERSION" ;;
    *) printf "UNSUPPORTED" ;;
  esac
}

node_package_sha256() {
  case "$1" in
    node-v22.12.0-darwin-arm64.tar.gz) printf "293dcc6c2408da21562d135b0412525e381bb6fe150d688edb58fe850d0f3e13" ;;
    node-v22.12.0-darwin-x64.tar.gz) printf "52bc25dd026db7247c3c00439afdb83e95087248267f02d6c1a7250d1f896173" ;;
    node-v22.12.0-linux-arm64.tar.xz) printf "8cfd5a8b9afae5a2e0bd86b0148ca31d2589c0ea669c2d0b11c132e35d90ed68" ;;
    node-v22.12.0-linux-x64.tar.xz) printf "22982235e1b71fa8850f82edd09cdae7e3f32df1764a9ec298c72d25ef2c164f" ;;
    *) return 1 ;;
  esac
}

verify_sha256() {
  local file="$1" expected="$2" actual
  if command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$file" | awk '{print $1}')"
  elif command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$file" | awk '{print $1}')"
  else
    echo "Neither shasum nor sha256sum is available; refusing unverified Node.js download." >&2
    return 1
  fi
  [[ "$actual" == "$expected" ]]
}

install_local_node() {
  step "Node.js/npm not found. Downloading local portable Node.js"
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required to download the pinned Node.js runtime. Install curl and retry." >&2
    exit 1
  fi
  mkdir -p "$RUNTIME_DIR"

  local package_name
  package_name="$(node_package_name)"
  if [[ "$package_name" == "UNSUPPORTED" ]]; then
    echo "This system is not supported for automatic Node.js install. Please install Node.js 22 LTS manually and retry."
    exit 1
  fi
  local expected_hash
  expected_hash="$(node_package_sha256 "$package_name")"

  local mirror_url="https://npmmirror.com/mirrors/node/$NODE_VERSION/$package_name"
  local official_url="https://nodejs.org/dist/$NODE_VERSION/$package_name"
  local mirror_ms official_ms download_url
  mirror_ms="$(test_url_ms "$mirror_url" "head")"
  official_ms="$(test_url_ms "$official_url" "head")"
  download_url="$mirror_url"
  if [[ "$official_ms" -lt "$mirror_ms" ]]; then
    download_url="$official_url"
  fi

  local archive_path="$RUNTIME_DIR/$package_name"
  local extract_dir="$RUNTIME_DIR/node-extract"
  rm -rf "$archive_path" "$extract_dir" "$NODE_DIR"
  mkdir -p "$extract_dir"

  echo "Download URL: $download_url"
  curl -L "$download_url" -o "$archive_path"
  if ! verify_sha256 "$archive_path" "$expected_hash"; then
    rm -f "$archive_path"
    echo "Node.js SHA-256 verification failed for $package_name." >&2
    exit 1
  fi
  ok "Node.js SHA-256 verified"
  case "$archive_path" in
    *.tar.gz) tar -xzf "$archive_path" -C "$extract_dir" ;;
    *.tar.xz) tar -xJf "$archive_path" -C "$extract_dir" ;;
    *) echo "Unsupported Node.js archive: $archive_path" >&2; exit 1 ;;
  esac
  local expanded
  expanded="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  if [[ -z "$expanded" ]]; then
    echo "Node.js extraction failed: extracted folder not found."
    exit 1
  fi
  mv "$expanded" "$NODE_DIR"
  rm -rf "$archive_path" "$extract_dir"
  add_local_node_to_path
  ok "Local Node.js installed: $NODE_DIR"
}

ensure_node_and_npm() {
  step "Checking Node.js/npm"
  add_local_node_to_path
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    if node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) ? 0 : 1)'; then
      ok "Node.js found: $(node --version)"
      ok "npm found: $(npm --version)"
      return
    fi
    echo "Installed Node.js is older than 22.12.0. Switching to the pinned local runtime." >&2
  fi

  install_local_node
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "Node.js/npm auto install failed. Please install Node.js 22 LTS manually and retry."
    exit 1
  fi
}

ensure_rust_toolchain() {
  step "Checking Rust $RUST_VERSION toolchain"
  local rustc_output cargo_output
  if command -v rustup >/dev/null 2>&1; then
    if ! rustc_output="$(rustup run "$RUST_TOOLCHAIN" rustc --version 2>&1)" || [[ "$rustc_output" != "rustc $RUST_VERSION "* ]]; then
      echo "Rust $RUST_VERSION is required but the '$RUST_TOOLCHAIN' toolchain is unavailable." >&2
      echo "Run 'rustup toolchain install $RUST_TOOLCHAIN' and retry." >&2
      exit 1
    fi
    if ! cargo_output="$(rustup run "$RUST_TOOLCHAIN" cargo --version 2>&1)"; then
      echo "Cargo for Rust $RUST_VERSION is unavailable. Run 'rustup toolchain install $RUST_TOOLCHAIN' and retry." >&2
      exit 1
    fi
    export RUSTUP_TOOLCHAIN="$RUST_TOOLCHAIN"
    ok "$rustc_output"
    ok "$cargo_output"
    return
  fi

  if ! command -v rustc >/dev/null 2>&1 || ! command -v cargo >/dev/null 2>&1; then
    echo "Rust $RUST_VERSION and Cargo are required for Tauri development. Install rustup from https://rustup.rs/ and retry." >&2
    exit 1
  fi
  rustc_output="$(rustc --version)"
  if [[ "$rustc_output" != "rustc $RUST_VERSION "* ]]; then
    echo "Rust $RUST_VERSION is required, but '$rustc_output' is active. Install or activate Rust $RUST_VERSION and retry." >&2
    exit 1
  fi
  ok "$rustc_output"
  ok "$(cargo --version)"
}

ensure_platform_prerequisites() {
  step "Checking Tauri platform prerequisites"
  case "$(uname -s)" in
    Darwin)
      if ! command -v xcode-select >/dev/null 2>&1 || ! xcode-select -p >/dev/null 2>&1 || ! xcrun --find clang >/dev/null 2>&1; then
        echo "Xcode Command Line Tools are required for Tauri on macOS. Run 'xcode-select --install' and retry." >&2
        exit 1
      fi
      ok "Xcode Command Line Tools found"
      ;;
    Linux)
      local missing=()
      command -v cc >/dev/null 2>&1 || missing+=("C/C++ build tools")
      command -v curl >/dev/null 2>&1 || missing+=("curl")
      command -v wget >/dev/null 2>&1 || missing+=("wget")
      command -v file >/dev/null 2>&1 || missing+=("file")
      if ! command -v pkg-config >/dev/null 2>&1; then
        missing+=("pkg-config")
      else
        pkg-config --exists webkit2gtk-4.1 || missing+=("webkit2gtk-4.1 development files")
        pkg-config --exists gtk+-3.0 || missing+=("GTK 3 development files")
        pkg-config --exists openssl || missing+=("OpenSSL development files")
        pkg-config --exists librsvg-2.0 || missing+=("librsvg development files")
        pkg-config --exists xdo || missing+=("libxdo development files")
      fi
      if [[ "${#missing[@]}" -gt 0 ]]; then
        printf 'Missing Linux Tauri prerequisites: %s\n' "$(IFS=', '; printf '%s' "${missing[*]}")" >&2
        echo "Install the matching development packages for your distribution (for Debian/Ubuntu: build-essential, libwebkit2gtk-4.1-dev, libgtk-3-dev, libssl-dev, librsvg2-dev, libxdo-dev, pkg-config, curl, wget, and file)." >&2
        exit 1
      fi
      ok "Linux compiler and WebKit/GTK development packages found"
      ;;
    *)
      echo "Unsupported platform: $(uname -s). Wandao Tauri development supports macOS and Linux through this launcher." >&2
      exit 1
      ;;
  esac
}

select_npm_install_mode() {
  step "Checking npm network" >&2
  local official_ms mirror_ms
  official_ms="$(test_url_ms "https://registry.npmjs.org/@tauri-apps%2fcli")"
  mirror_ms="$(test_url_ms "https://registry.npmmirror.com/@tauri-apps%2fcli")"

  if [[ "$official_ms" -lt 999999 && "$mirror_ms" -lt 999999 ]]; then
    if [[ "$official_ms" -le $((mirror_ms * 13 / 10)) ]]; then
      ok "Using official npm registry, about ${official_ms}ms" >&2
      printf "official"
      return
    fi
    ok "Using China npmmirror registry, about ${mirror_ms}ms" >&2
    printf "cn"
    return
  fi

  if [[ "$official_ms" -lt 999999 ]]; then
    ok "Using official npm registry" >&2
    printf "official"
    return
  fi

  if [[ "$mirror_ms" -lt 999999 ]]; then
    ok "Using China npmmirror registry" >&2
    printf "cn"
    return
  fi

  echo "Network probe failed. Falling back to China npmmirror registry." >&2
  printf "cn"
}

tauri_lock_version() {
  node -e '
    const fs = require("fs");
    const lock = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
    const entry = lock.packages && lock.packages["node_modules/@tauri-apps/cli"];
    if (!entry || typeof entry.version !== "string" || !entry.version) process.exit(1);
    process.stdout.write(entry.version);
  ' "$1"
}

tauri_manifest_version() {
  node -e '
    const fs = require("fs");
    const manifest = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
    const version = manifest.devDependencies && manifest.devDependencies["@tauri-apps/cli"];
    if (typeof version !== "string" || !version) process.exit(1);
    process.stdout.write(version);
  ' "$1"
}

tauri_dependencies_ready() {
  local app_manifest="$APP_DIR/package.json"
  local project_lock="$APP_DIR/package-lock.json"
  local installed_lock="$APP_DIR/node_modules/.package-lock.json"
  local tauri_package="$APP_DIR/node_modules/@tauri-apps/cli/package.json"
  local tauri_script="$APP_DIR/node_modules/@tauri-apps/cli/tauri.js"
  local tauri_shim="$APP_DIR/node_modules/.bin/tauri"
  [[ -f "$app_manifest" && -f "$project_lock" && -f "$installed_lock" && -f "$tauri_package" && -f "$tauri_script" && -x "$tauri_shim" ]] || return 1

  local declared_version locked_version installed_lock_version installed_version
  declared_version="$(tauri_manifest_version "$app_manifest" 2>/dev/null)" || return 1
  locked_version="$(tauri_lock_version "$project_lock" 2>/dev/null)" || return 1
  installed_lock_version="$(tauri_lock_version "$installed_lock" 2>/dev/null)" || return 1
  installed_version="$(node -e '
    const manifest = require(process.argv[1]);
    if (typeof manifest.version !== "string" || !manifest.version) process.exit(1);
    process.stdout.write(manifest.version);
  ' "$tauri_package" 2>/dev/null)" || return 1
  [[ "$declared_version" == "$locked_version" && "$locked_version" == "$installed_lock_version" && "$locked_version" == "$installed_version" ]] || return 1
  node "$tauri_script" --version >/dev/null 2>&1
}

install_dependencies() {
  local declared_version locked_version
  declared_version="$(tauri_manifest_version "$APP_DIR/package.json" 2>/dev/null)" || true
  locked_version="$(tauri_lock_version "$APP_DIR/package-lock.json" 2>/dev/null)" || true
  if [[ -z "$declared_version" || -z "$locked_version" || "$declared_version" != "$locked_version" ]]; then
    echo "package.json and package-lock.json must declare the same pinned @tauri-apps/cli version. Refusing an unlocked desktop dependency install." >&2
    exit 1
  fi
  if [[ "$FORCE_INSTALL" -eq 0 ]] && tauri_dependencies_ready; then
    ok "Tauri CLI matches package-lock.json. Skipping npm install"
    return
  fi

  local mode
  mode="$(select_npm_install_mode)"
  local registry
  if [[ "$mode" == "cn" ]]; then
    registry="https://registry.npmmirror.com/"
  else
    registry="https://registry.npmjs.org/"
  fi
  step "Installing Tauri desktop dependencies"
  pushd "$APP_DIR" >/dev/null
  npm ci --registry="$registry" --replace-registry-host=always --no-audit --no-fund
  popd >/dev/null
  if ! tauri_dependencies_ready; then
    echo "npm completed, but @tauri-apps/cli does not match package-lock.json or cannot run on this platform." >&2
    exit 1
  fi
}

start_wandao() {
  step "Starting Wandao with Tauri"
  pushd "$APP_DIR" >/dev/null
  npm run dev
  popd >/dev/null
}

if [[ ! -d "$APP_DIR" ]]; then
  echo "wandao_electron folder not found. Please run this script from the Wandao project root."
  exit 1
fi

ensure_node_and_npm
ensure_rust_toolchain
ensure_platform_prerequisites
install_dependencies

if [[ "$INSTALL_ONLY" -eq 1 ]]; then
  ok "Node.js, Tauri CLI, Rust, and platform prerequisite checks completed. Desktop app was not started."
  exit 0
fi

start_wandao
