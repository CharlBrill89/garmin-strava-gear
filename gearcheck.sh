#!/usr/bin/env bash
# gearcheck            -> compare using the last-loaded Strava export
# gearcheck <zip>      -> load that export zip first, then compare
# Any other arguments are passed straight through to compare_gear.py.
#
# To use from anywhere, add to ~/.bashrc:
#   alias gearcheck='/c/projects/garmin-strava-gear/gearcheck.sh'

set -euo pipefail
cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

if [[ ! -x .venv/Scripts/python.exe && ! -x .venv/bin/python ]]; then
    echo "No virtualenv found. From $(pwd) run:" >&2
    echo "  python -m venv .venv && .venv/Scripts/pip install -r requirements.txt" >&2
    exit 1
fi
python=.venv/Scripts/python.exe
[[ -x $python ]] || python=.venv/bin/python

export PYTHONIOENCODING=utf-8

if [[ $# -gt 0 && $1 == *.zip ]]; then
    zip=$1; shift
    exec "$python" compare_gear.py --strava-csv "$zip" --open "$@"
fi
exec "$python" compare_gear.py --open "$@"
