#!/usr/bin/env bash
set -euo pipefail

plugin_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
script="$plugin_dir/scripts/pane_regex.py"
key="$(tmux show-option -gqv @pane-regex-key)"
key="${key:-R}"

tmux set-option -gq @pane-regex-script "$script"
tmux bind-key "$key" run-shell "$script --pane '#{pane_id}'"
