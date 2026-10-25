#!/bin/bash
# Launcher script — runs the stock screener CLI
# Usage: ./run_screener.sh -c longterm -u sp500
#        ./run_screener.sh --dashboard

DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 -m screener.cli "$@"
