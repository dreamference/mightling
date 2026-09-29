#!/usr/bin/env bash
# Runs one Goose task against the local model server, through `puffin-admin`, which writes Goose's
# config for the served model and waits for the server. (This script used to export a fixed,
# no-longer-served model name and call `goose` directly.)
#
#   ./scripts/run_goose.sh "task prompt"
set -euo pipefail

exec puffin-admin run --agent goose "$@"
