#!/usr/bin/env bash

# Source this file, then call activate_private_beta_toolchain install|check.

activate_private_beta_toolchain() {
  local mode="${1:-check}"
  local root_dir="${ROOT_DIR:?ROOT_DIR must be set before loading the private beta toolchain}"
  local required_node
  local actual_node
  local required_python
  local actual_python
  local python_bin

  required_node="$(tr -d '[:space:]' < "$root_dir/.nvmrc")"
  actual_node="$(node -p 'process.versions.node' 2>/dev/null || true)"
  if [ "$actual_node" != "$required_node" ]; then
    export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
    if [ -s "$NVM_DIR/nvm.sh" ]; then
      # shellcheck source=/dev/null
      source "$NVM_DIR/nvm.sh" --no-use
      unset npm_config_prefix NPM_CONFIG_PREFIX
      if [ "$mode" = "install" ]; then
        nvm install "$required_node"
      else
        nvm use "$required_node" >/dev/null
      fi
    else
      echo "Node $required_node is required. Install nvm, then run the beta command again." >&2
      return 1
    fi
  fi

  actual_node="$(node -p 'process.versions.node' 2>/dev/null || true)"
  if [ "$actual_node" != "$required_node" ]; then
    echo "Node $required_node is required (found ${actual_node:-missing})." >&2
    return 1
  fi

  required_python="$(tr -d '[:space:]' < "$root_dir/.python-version")"
  actual_python="$(python3 -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
  if [ "$actual_python" = "$required_python" ]; then
    return 0
  fi

  if [ -x "$root_dir/.venv/bin/python" ] \
    && [ "$("$root_dir/.venv/bin/python" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)" = "$required_python" ]; then
    export PATH="$root_dir/.venv/bin:$PATH"
    return 0
  fi

  if [ "$mode" != "install" ]; then
    echo "Python $required_python is required. Run npm run beta:setup first." >&2
    return 1
  fi

  if ! command -v uv >/dev/null 2>&1; then
    if ! command -v brew >/dev/null 2>&1; then
      echo "Python $required_python is missing. Install Homebrew, then run the beta setup again." >&2
      return 1
    fi
    echo "==> Installing the Python version manager"
    brew install uv
  fi

  echo "==> Installing Python $required_python"
  uv python install "$required_python"
  python_bin="$(uv python find "$required_python" 2>/dev/null || true)"
  if [ -z "$python_bin" ] || [ ! -x "$python_bin" ]; then
    echo "Python $required_python was installed but could not be selected." >&2
    return 1
  fi

  PRIVATE_BETA_TOOLCHAIN_TMP="$(mktemp -d "${TMPDIR:-/tmp}/electronic-mail-beta-toolchain.XXXXXX")"
  export PRIVATE_BETA_TOOLCHAIN_TMP
  ln -s "$python_bin" "$PRIVATE_BETA_TOOLCHAIN_TMP/python3"
  export PATH="$PRIVATE_BETA_TOOLCHAIN_TMP:$PATH"
}
