# Repository instructions

## Purpose

This repository is a standalone tmux plugin for selecting text from pane
scrollback with live regex-based feedback, then pasting it without submitting
it. Keep the interaction fast, local, and safe for shells and agent prompts.

## Repository map

- `tmux-pane-regex.tmux` is the plugin entrypoint. It publishes the executable
  path and installs the configurable prefix binding.
- `scripts/pane_regex.py` owns capture, matching, popup state, native tmux
  highlighting, occurrence navigation, and acceptance.
- `scripts/pane_deliver.sh` owns deferred bracketed paste into the source pane.
- `tests/test_pane_regex.py` covers matching and Python orchestration.
- `tests/plugin_test.bats` covers plugin loading and pane delivery.
- `README.md` is the public interface and installation guide.

## Stable contracts

- `prefix + R` is the default launcher. `@pane-regex-key` changes it.
- `@pane-regex-script` exposes the executable to external text expanders.
- Direct tmux launches pass `--pane '#{pane_id}'` and start with a fresh `^`.
- External inline launchers pass their trigger length as the positional argument.
- Enter and Tab paste the current match. Esc cancels. Nothing submits input.
- Pane delivery uses a named tmux buffer, bracketed paste, and a deferred
  `run-shell` so multiline text cannot execute while the popup closes.
- Python chooses the exact source range. Native tmux search and copy-mode
  selection must show that same range. Never add a second synthetic highlighter.
- The native tmux search follows the selection start, so a `\zs` query highlights
  the selected text and not its context.
- Up selects an older occurrence. Down selects a newer one. Query refinement
  keeps the selected source position instead of resetting to the newest match.
- Soft-wrapped text stays logical text. Multiline ranges start at the newest
  viable start locator and resolve downward through the first viable end.
- Strip terminal UI margins, trailing display padding, and private-use prompt
  glyphs before matches are pasted.

The documented selector forms are public behavior:

- `^word$` selects the latest logical line containing `word`.
- `^start.*end` selects downward through the first `end`.
- `^start$$` selects through the logical line end.
- `^start.*end$$` includes the rest of the ending logical line.
- `^start\ss` stops after the first sentence-ending `.`, `?`, or `!`.
- A final `\$` matches a literal dollar sign.
- `\zs` and `\ze` mark where the selection starts and ends. Text outside them
  must match and stays out of the selection. They translate to one named group,
  never to a lookaround, so the context carries no fixed-width limit.

Matching is case-insensitive by default. `\C` before the first locator makes
the query case-sensitive. A straight apostrophe also matches a smart apostrophe.

## Changes

- Read the matcher and its focused tests before editing behavior.
- Write a failing behavior test first, then make the smallest implementation
  change that passes it.
- Keep matching helpers pure where possible. Keep tmux and filesystem effects
  at the orchestration boundary.
- Support Python 3.10 or newer. Avoid new runtime dependencies unless the user
  benefit clearly justifies them.
- Keep shell scripts compatible with Bash. Quote pane ids, paths, and query
  fragments at process boundaries.
- Do not hard-code a home directory, checkout path, tmux pane id, or server
  socket.
- Update `README.md` in the same change when a key, option, selector, dependency,
  or visible interaction changes.
- Never log or send captured pane content outside the local tmux server.

## Verification

Run the complete local gate:

```sh
python3 -m unittest discover -s tests
bats tests/plugin_test.bats
ruff check .
ruff format --check .
bash -n tmux-pane-regex.tmux scripts/pane_deliver.sh
```

Run `shellcheck tmux-pane-regex.tmux scripts/pane_deliver.sh` when shellcheck is
available.

For popup, highlighting, navigation, wrapping, or paste changes, also verify in
a real attached tmux client. Prefer an isolated tmux socket. Confirm the visible
selection and the exact text at the destination prompt, and confirm no command
or message was submitted.

## Git

- Use a feature branch and a pull request. Do not push directly to `main`.
- Use lowercase conventional commits with a scope, for example
  `fix(tmux): preserve the selected occurrence`.
- Keep commits focused. Explain why in the commit body.
- Preserve unrelated local changes.
