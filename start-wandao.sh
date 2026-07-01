#!/usr/bin/env bash
set -euo pipefail

INSTALL_ONLY=0
FORCE_INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --install-only) INSTALL_ONLY=1 ;;
    --force-install) FORCE_INSTALL=1 ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ELECTRON_DIR="$ROOT_DIR/wandao_electron"
RUNTIME_DIR="$ROOT_DIR/.dev-runtime"
NODE_DIR="$RUNTIME_DIR/node"
NODE_VERSION="v20.18.1"

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
    Linux:aarch64|Linux:arm64) printf "node-%s-linux-arm64.tar.gz" "$NODE_VERSION" ;;
    Linux:x86_64) printf "node-%s-linux-x64.tar.gz" "$NODE_VERSION" ;;
    *) printf "UNSUPPORTED" ;;
  esac
}

install_local_node() {
  step "Node.js/npm not found. Downloading local portable Node.js"
  mkdir -p "$RUNTIME_DIR"

  local package_name
  package_name="$(node_package_name)"
  if [[ "$package_name" == "UNSUPPORTED" ]]; then
    echo "This system is not supported for automatic Node.js install. Please install Node.js 20 LTS manually and retry."
    exit 1
  fi

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
  tar -xzf "$archive_path" -C "$extract_dir"
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
    ok "Node.js found: $(node --version)"
    ok "npm found: $(npm --version)"
    return
  fi

  install_local_node
  if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo "Node.js/npm auto install failed. Please install Node.js 20 LTS manually and retry."
    exit 1
  fi
}

select_npm_install_mode() {
  step "Checking npm network" >&2
  local official_ms mirror_ms
  official_ms="$(test_url_ms "https://registry.npmjs.org/electron")"
  mirror_ms="$(test_url_ms "https://registry.npmmirror.com/electron")"

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

install_dependencies() {
  local mode="$1"
  if [[ "$FORCE_INSTALL" -eq 0 && -d "$ELECTRON_DIR/node_modules/electron" && -d "$ELECTRON_DIR/node_modules/electron-builder" ]]; then
    ok "Desktop dependencies already exist. Skipping npm install"
    return
  fi

  step "Installing desktop dependencies"
  pushd "$ELECTRON_DIR" >/dev/null
  if [[ "$mode" == "cn" ]]; then
    npm run install:cn
  else
    npm install --no-audit --no-fund
  fi
  popd >/dev/null
}

start_wandao() {
  step "Starting Wandao"
  pushd "$ELECTRON_DIR" >/dev/null
  npm start
  popd >/dev/null
}

if [[ ! -d "$ELECTRON_DIR" ]]; then
  echo "wandao_electron folder not found. Please run this script from the Wandao project root."
  exit 1
fi

ensure_node_and_npm
INSTALL_MODE="$(select_npm_install_mode)"
install_dependencies "$INSTALL_MODE"

if [[ "$INSTALL_ONLY" -eq 1 ]]; then
  ok "Dependency check completed. Desktop app was not started."
  exit 0
fi

start_wandao
