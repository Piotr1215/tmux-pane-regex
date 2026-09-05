#!/usr/bin/env bats

setup() {
	REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
	BIN="$BATS_TEST_TMPDIR/bin"
	LOG="$BATS_TEST_TMPDIR/tmux.log"
	mkdir -p "$BIN"

	cat > "$BIN/tmux" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$TMUX_TEST_LOG"
if [[ $1 == show-option ]]; then
	printf '%s' "${TMUX_TEST_KEY-}"
elif [[ $1 == display-message ]]; then
	printf '%s\n' "${TMUX_TEST_PANE-}"
fi
EOF
	chmod +x "$BIN/tmux"
}

@test "plugin binds prefix R and publishes its script path" {
	run env PATH="$BIN:$PATH" TMUX_TEST_LOG="$LOG" \
		bash "$REPO_ROOT/tmux-pane-regex.tmux"

	[ "$status" -eq 0 ]
	grep -Fx "set-option -gq @pane-regex-script $REPO_ROOT/scripts/pane_regex.py" "$LOG"
	grep -F "bind-key R run-shell" "$LOG"
	grep -F -- "--pane '#{pane_id}'" "$LOG"
}

@test "plugin honors a custom prefix key" {
	run env PATH="$BIN:$PATH" TMUX_TEST_LOG="$LOG" TMUX_TEST_KEY=P \
		bash "$REPO_ROOT/tmux-pane-regex.tmux"

	[ "$status" -eq 0 ]
	grep -F "bind-key P run-shell" "$LOG"
}

@test "delivery uses bracketed paste for a live pane" {
	payload="$BATS_TEST_TMPDIR/payload"
	printf 'first line\nsecond line' > "$payload"

	run env PATH="$BIN:$PATH" TMUX_TEST_LOG="$LOG" TMUX_TEST_PANE=%7 \
		bash "$REPO_ROOT/scripts/pane_deliver.sh" --file %7 "$payload"

	[ "$status" -eq 0 ]
	grep -F "paste-buffer -p" "$LOG"
}
