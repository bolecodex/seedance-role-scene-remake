#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${ROOT}/.venv"

python3 -m venv "${VENV}"
# shellcheck disable=SC1091
source "${VENV}/bin/activate"
python -m pip install --upgrade pip
python -m pip install -e "${ROOT}[dev]"

SKILL_HOME="${CODEX_HOME:-${HOME}/.codex}/skills/seedance-role-scene-remake"
mkdir -p "$(dirname "${SKILL_HOME}")"
rm -rf "${SKILL_HOME}"
cp -R "${ROOT}/skills/seedance-role-scene-remake" "${SKILL_HOME}"

echo "Installed seedance-role-scene-remake CLI and skill."
echo "Activate with: source ${VENV}/bin/activate"

