#!/usr/bin/env bash
# PHAROS single-entry Ubuntu x86-64 bootstrap.
# Use from a copy of this script or from the repository itself.
# No root execution, Docker, forced Git reset or replacement of existing data.
set -Eeuo pipefail
if [[ "${EUID}" -eq 0 ]]; then
  echo "PHAROS must be installed as a regular user, not root." >&2; exit 1
fi
if [[ ! -r /etc/os-release ]]; then
  echo "Ubuntu release information is unavailable." >&2; exit 1
fi
# shellcheck source=/etc/os-release
source /etc/os-release
if [[ "${ID:-}" != ubuntu || "$(uname -m)" != x86_64 ]]; then
  echo "Only Ubuntu x86-64 is currently supported by this installer." >&2; exit 1
fi
if ! command -v git >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
  echo "PHAROS requires Git and Python. Ubuntu can install these prerequisites."
  if ! command -v sudo >/dev/null 2>&1; then
    echo "Prerequisites missing and sudo unavailable. Install Git and Python first." >&2
    exit 1
  fi
  # curl | bash owns stdin: read interactive confirmations from the
  # controlling terminal, never from the source-code pipe.
  if [[ ! -r /dev/tty ]]; then
    echo "A terminal is required to authorize Ubuntu package installation." >&2
    exit 1
  fi
  read -r -p "Install Git, Python 3 and python3-venv via official Ubuntu apt? [y/N] " answer </dev/tty
  case "$answer" in y|Y|yes|YES)
    sudo apt-get update
    sudo apt-get install -y git python3 python3-venv
    ;; *) echo "PHAROS setup stopped: prerequisites were not installed." >&2; exit 1;;
  esac
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' || {
  echo "Python >= 3.11 is required." >&2; exit 1;
}
TARGET="${PHAROS_INSTALL_DIR:-$HOME/pharos}"
# Paths must not accidentally be interpreted as git switches.
case "$TARGET" in -*) echo "Invalid install path" >&2; exit 1;; esac
TRUSTED_SSH='git@github.com:mascalx/pharos.git'
TRUSTED_HTTPS='https://github.com/mascalx/pharos.git'
if [[ -e "$TARGET" && ! -d "$TARGET/.git" ]]; then
  echo "Target already exists but is not a Git checkout: $TARGET" >&2; exit 1
fi
if [[ -d "$TARGET/.git" ]]; then
  CURRENT="$(git -C "$TARGET" remote get-url origin)"
  case "$CURRENT" in
    "$TRUSTED_SSH"|git@github.com:mascalx/pharos|"$TRUSTED_HTTPS"|https://github.com/mascalx/pharos)
      ;;
    *) echo "Existing checkout has an untrusted origin; refusing to continue." >&2; exit 1;;
  esac
  # Reuse established conservative updater (fast-forward and config backups).
  echo "Existing PHAROS checkout found. Applying conservative update."
  exec bash "$TARGET/scripts/update-ubuntu.sh" "$@"
fi
# Public repositories clone anonymously over HTTPS. SSH remains an explicit
# opt-in for private checkouts with already-configured credentials.
# Never request or store a GitHub token here.
if [[ -n "${PHAROS_REPO_URL:-}" && "${PHAROS_REPO_URL}" != "$TRUSTED_SSH" &&
      "${PHAROS_REPO_URL}" != "$TRUSTED_HTTPS" ]]; then
  echo "Only the official repository is permitted." >&2; exit 1
fi
REPO="${PHAROS_REPO_URL:-$TRUSTED_HTTPS}"
echo "Cloning official PHAROS into $TARGET..."
git clone -- "$REPO" "$TARGET"
if [[ ! -f "$TARGET/scripts/install-ubuntu.sh" ]]; then
  echo "Unexpected checkout: installer not found." >&2; exit 1
fi
# New installations use English by default; an explicit later --language it
# still overrides this value. Existing installations retain their TOML.
# In piped execution, stdin contains source code rather than keyboard input.
# Preserve terminal prompts in the existing installer (venv/NGINX consent).
if [[ -r /dev/tty ]]; then
  exec bash "$TARGET/scripts/install-ubuntu.sh" --language en "$@" </dev/tty
fi
exec bash "$TARGET/scripts/install-ubuntu.sh" --language en "$@"
