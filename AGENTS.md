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

- Extend Python regex, never change it. The picker exists to harpoon one string
  out of scrollback, so shortcuts are new atoms for that job. A new shortcut
  claims only syntax Python rejects, and a valid regex keeps the meaning Python
  gives it. `\ss`, `$$`, `\f`, `\t` and the lazy first `.*` predate this rule.
- Every shortcut appears in the picker legend.
- `prefix + R` is the default launcher. `@pane-regex-key` changes it.
- `@pane-regex-script` exposes the executable to external text expanders.
- Direct tmux launches pass `--pane '#{pane_id}'` and start with a fresh `^`.
- External inline launchers pass their trigger length as the positional argument.
- Enter and Tab paste the current match. Esc cancels. Nothing submits input.
- Ctrl+Y copies the same match to the attached client's clipboard and closes the
  picker without inserting text. It removes the inline trigger like paste does.
  With no match, the picker stays open.
- Pane delivery uses a named tmux buffer, bracketed paste, and a deferred
  `run-shell` so multiline text cannot execute while the popup closes.
- Python chooses the exact source range. Native tmux search and copy-mode
  selection must show that same range. Never add a second synthetic highlighter.
- The native tmux search follows the selection start, so the selection of a
  `\zs` query covers the selected text and not its context.
- The committed `^word$` line form draws a whole-line copy selection, while an
  unfinished `^word` keeps the search highlight so typing does not flash a line.
- Every form shows where Up and Down will land. A form that selects runs one
  more search after the selection stops, naming the first line of every match
  as a literal, since tmux cannot run the query and never matches across a
  line. `\zs` and `\ze` context stays in those literals, or a short match would
  paint every copy of itself, so context shows in the match colour around the
  selection. The search names the matches nearest the current one, since a tmux
  command holds about 16 KB.
- A match of any length gets a selection: one word, one character, or a
  selection that opens on a regex operator, which anchors on the first word
  of the text Python chose.
- Up selects an older occurrence. Down selects a newer one. Query refinement
  keeps the selected source position instead of resetting to the newest match.
- Soft-wrapped text stays logical text. Multiline ranges start at the newest
  viable start locator and resolve downward through the first viable end.
- Match against text with terminal UI margins, trailing display padding, and
  private-use prompt glyphs removed, so a locator never spells out indentation.
- Paste what the selection shows. Every line after the first sits inside the
  highlighted range whole, so it keeps its indent. The first line keeps its own
  indent only in the whole-line forms, which start the selection at column 0.
  Picker bullets and agent gutter glyphs are chrome, so their line's leading
  space stays off.

The documented selector forms are public behavior:

- `^word$` selects the latest logical line containing `word`.
- `^start.*end` selects downward through the first `end`.
- `^start$$` selects through the logical line end.
- `^start.*end$$` includes the rest of the ending logical line.
- `^start\ss` stops after the first sentence-ending `.`, `?`, or `!`.
- A final `\$` matches a literal dollar sign.
- `^word\l` selects the logical line holding the locator, `^word\p` the
  paragraph between the blank lines around it, deduplicated per block. The
  committed `^word$` line form is deduplicated the same way, while the
  unfinished `^word` keeps one stop per hit so arrows can walk them.
  Both accept either case.
- `^start\3f,` selects through the third `,` and `^start\3t,` stops before it,
  vim `v3f,` and `v3t,`. The count defaults to one. The target is the literal
  rest of the query, one character or a word, so `^start\2fout` reaches the
  second `out`. `\t` also stops before the whitespace ahead of its target.
- `^start.*stop\2` repeats the hop after the last `.*` or `.+` twice, and `\2t` stops
  before the second `stop`. The bare range is the implicit `\1`. The landmark
  stays a regex, wrapped in a group. A count needs a digit and applies only
  where Python would reject it as a group reference, so a query with two
  groups keeps its `\2` backreference.
  The form expands to the marker form and shares its highlight and navigation.
- `^\u` visits every URL newest first and `^word\u` only the URLs containing
  `word`. Trailing sentence punctuation and unbalanced closers stay out. tmux
  searches for the exact URLs Python chose, so it highlights every one and
  marks the current one, as a plain search does. The form draws no selection,
  and a URL is one search hit, so soft wraps hold in both key modes. The search
  names the URLs nearest the current one, since a tmux command holds about
  16 KB. Either case works.
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
- Never log captured pane content or send it to a remote service. Only the
  selected text may leave tmux through an explicit paste or clipboard action.

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
