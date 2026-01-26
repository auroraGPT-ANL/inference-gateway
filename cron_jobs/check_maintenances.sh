#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON_BIN="${PYTHON_BIN:-/home/webportal/miniconda3/envs/python3.12.6-env/bin/python}"

exec "$PYTHON_BIN" "$SCRIPT_DIR/check_maintenances.py" "$@"
