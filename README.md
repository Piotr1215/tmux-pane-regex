# tmux-pane-regex

Search tmux scrollback with the words you remember, see the exact range in the
original pane, then paste it at the cursor without submitting it or copy it to
the clipboard.

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
| `^start\3f,` | Through the third `,`, vim `v3f,`. The target is literal text |
| `^start\3t,` | Up to the third `,`, vim `v3t,`. The target is literal text |
| `^start\2fout` | Through the second `out`. The target is literal text |
| `^start.*stop\2` | Through the second `stop`, `\2t` up to it. `stop` is regex, so write `\.` for a period |
| `^word$` | Latest logical line containing `word` |
| `^start.*end` | From `start` downward through the first `end` |
| `^start$$` | From `start` through that logical line's last visible character |
| `^start.*end$$` | Through the last visible character on the ending line |
| `^start\ss` | Through the first `.`, `?`, or `!` |
| `^start\zsword` | Selects from `word`, with `start` matched as context |
| `^word\zeend` | Selects `word`, with `end` matched but left out |
| `^word\l` | The whole logical line holding `word`, vim `V` |
| `^word\p` | The whole paragraph holding `word`, vim `vip` |
| `^\u` | The newest URL, Up for older ones |
| `^word\u` | The newest URL holding `word` |
| `^price\$` | A literal final dollar sign |

The first `.*` in a landmark range stops at the first viable ending locator.
Matches are case-insensitive and preserve the original text when pasted or
copied. A straight apostrophe also matches a smart apostrophe.

A paste or copy carries the indentation you can see selected, so an indented
YAML block keeps its shape. Leading whitespace before the selection start is not
part of the range, and terminal chrome never is: picker bullets such as `•` and `›`,
and the `●` and `⎿` gutter an agent CLI draws down the left of its own output.

`\l` and `\p` name what they take, so `^word\l` reads the same as `^word$` and
`^word\p` runs from the blank line above the locator to the blank line below it.
A paragraph is reported once however many of its lines hold the locator.
Either case works, `\L` and `\P` read the same as the lowercase pair.

`\zs` and `\ze` move the selection boundaries the way they do in vim. The text
outside them still has to match, it is just not selected. The context before
`\zs` has no width limit, so `^branch.*\zsunlim` works where a Python lookbehind
cannot compile at all. Both markers compose with `$$` and `\ss`, which keep
owning the end of the range.

`\f` and `\t` take a count and a target, the way vim's `f` and `t` motions do.
`^start\f,` selects through the first comma, `^start\3t,` stops just before the
third. The target is the rest of the query, so it can be a whole word:
`^change\2fout` selects through the second `out`, which `^change.*(out){2}`
cannot, since `{2}` asks for `outout`. Every hop is lazy, so the range ends at
the nearest target and never runs on to a later one. The target is taken
literally: `\2f.` means the second period. `\t` also leaves out the space
before its target, so the selection ends on a visible character.

A count can also come last. `^start.*stop` already ends at the first `stop`, so
`^start.*stop\2` ends at the second and `^start.*stop\2t` just before it.
Changing the count is one backspace. The count repeats only the hop after the
last `.*` or `.+`, and that landmark stays a regex, so `^start.*(stop|end)\3`
works. A bare `.+` keeps its greedy regex meaning; `^start.+stop\1` is the
lazy form. A count never replaces a real backreference: in a query with two
groups, `\2` still refers to the second one.

`\u` selects a URL. `^\u` visits every URL in scrollback, newest first, and
`^github\u` keeps only the ones containing `github`. A URL is any `scheme://`
run up to whitespace, a quote, a backtick, or an angle bracket. Sentence
punctuation after it and a closing bracket it never opened are left out, so
`(see https://example.com/a_(b)).` selects `https://example.com/a_(b)`.
Every URL the query visits is highlighted, with the current one marked, so you
see where Up and Down will land. Press Ctrl+Y to copy it or Enter to paste it.
`\U` reads the same.

Add `\C` before the locator to make a query case-sensitive: `^\Cpython$$`
selects from lowercase `python` through its line end, while `\Cpython`
selects just the word. Uppercase letters alone do not change the default.

The picker header lists every shortcut, so the table above is always one
keypress away.

- Up selects an older occurrence.
- Down selects a newer occurrence.
- Enter or Tab pastes the current selection.
- Ctrl+Y copies the current selection to the clipboard and closes the picker
  without inserting it. It also removes the inline `;;^` trigger and recovered
  query text. With no match, the picker stays open.
- Space accepts when the query ends in an unescaped `$`.
- Esc closes the picker without pasting and removes the inline `;;^` trigger,
  including any query text typed into the source prompt before the popup opened.

Navigation has no match-count limit and searches all history retained by tmux.
The tmux `history-limit` setting still controls how much scrollback exists.

Pasted text uses tmux bracketed paste. It never submits a command or message.
Clipboard copying uses tmux's native clipboard escape sequence (OSC 52), so the
attached terminal must allow clipboard writes. No clipboard helper is required.

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
buffer and defers a bracketed paste until the popup has closed. Ctrl+Y sends the
same text to the attached client's clipboard and removes the temporary buffer.

## Test

```sh
python3 -m unittest discover -s tests
bats tests/plugin_test.bats
ruff check .
ruff format --check .
```
