# tmux-pane-regex

Search tmux scrollback with the words you remember, see the exact range in the
original pane, then paste it at the cursor without submitting it.

The picker stays out of the pane. tmux paints the live match with its native
search and copy-mode styles, including soft-wrapped and multiline text.

## Requirements

- tmux 3.7b or newer
- Python 3.10 or newer
- fzf

`xdotool` is only needed for the optional global `;;^` launcher.

## Install

Clone the repository, then load it near the end of `.tmux.conf`:

```sh
git clone https://github.com/Piotr1215/tmux-pane-regex \
  ~/.tmux/plugins/tmux-pane-regex
```

```tmux
run-shell '~/.tmux/plugins/tmux-pane-regex/tmux-pane-regex.tmux'
```

With TPM:

```tmux
set -g @plugin 'Piotr1215/tmux-pane-regex'
```

Reload tmux, then press `prefix + R`.

## Use

The selectors are regex-based, with a few shortcuts for prose and terminal
output:

| Query | Selection |
| --- | --- |
| `^word$` | Latest logical line containing `word` |
| `^start.*end` | From `start` downward through the first `end` |
| `^start$$` | From `start` through that logical line's last visible character |
| `^start.*end$$` | Through the last visible character on the ending line |
| `^start\ss` | Through the first `.`, `?`, or `!` |
| `^price\$` | A literal final dollar sign |

The first `.*` in a landmark range stops at the first viable ending locator.
Matches are case-insensitive and preserve the original text when pasted.
A straight apostrophe also matches a smart apostrophe.

Add `\C` before the locator to make a query case-sensitive: `^\Cpython$$`
selects from lowercase `python` through its line end, while `\Cpython`
selects just the word. Uppercase letters alone do not change the default.

- Up selects an older occurrence.
- Down selects a newer occurrence.
- Enter or Tab pastes the current selection.
- Space accepts when the query ends in an unescaped `$`.
- Esc closes the picker without pasting and removes the inline `;;^` trigger,
  including any query text typed into the source prompt before the popup opened.

Navigation has no match-count limit and searches all history retained by tmux.
The tmux `history-limit` setting still controls how much scrollback exists.

Pasted text uses tmux bracketed paste. It never submits a command or message.

## Configure

Set the key before loading the plugin:

```tmux
set -g @pane-regex-key 'P'
run-shell '~/.tmux/plugins/tmux-pane-regex/tmux-pane-regex.tmux'
```

The plugin publishes its executable path as `@pane-regex-script`. An external
text expander can use that path and pass its trigger length:

```sh
"$(tmux show-option -gqv @pane-regex-script)" 3
```

The included direct tmux binding passes `--pane '#{pane_id}'` and needs no X11
focus detection.

## How it works

The script captures all retained history from the target pane and joins tmux soft
wraps. Python regex matching chooses the exact source range. The visible
feedback is then rendered in the source pane with tmux's native search and
copy-mode selection primitives. Accepting writes the match to a named tmux
buffer and defers a bracketed paste until the popup has closed.

## Test

```sh
python3 -m unittest discover -s tests
bats tests/plugin_test.bats
ruff check .
ruff format --check .
```
