#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck source=private-beta-toolchain.sh
source "$ROOT_DIR/scripts/private-beta-toolchain.sh"

cd "$ROOT_DIR"
activate_private_beta_toolchain check
bash "$ROOT_DIR/scripts/check-private-beta.sh"
exec bash "$ROOT_DIR/scripts/dev-macos.sh"
