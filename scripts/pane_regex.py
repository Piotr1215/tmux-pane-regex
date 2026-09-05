#!/usr/bin/env python3
"""Live multiline regex expansion from the focused tmux pane."""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

SCRIPT = Path(__file__).resolve()
DELIVER = SCRIPT.with_name("pane_deliver.sh")
INITIAL_QUERY = "^"
INLINE_TRIGGER = ";;^"
SENTENCE_SUFFIX = r"\ss"
SELECTION_START = r"\zs"
SELECTION_END = r"\ze"
SELECTION_GROUP = "sel"
LINE_SUFFIX = r"\L"
PARAGRAPH_SUFFIX = r"\P"


@dataclass(frozen=True)
class Match:
    text: str
    start_line: int
    end_line: int
    start_offset: int = 0
    end_offset: int = 0


def query_options(query: str) -> tuple[str, re.RegexFlag]:
    """Recognize a case-sensitive override before the first locator."""
    marker = 1 if query.startswith("^") else 0
    if query[marker : marker + 2] == r"\C":
        return query[:marker] + query[marker + 2 :], re.RegexFlag(0)
    return query, re.IGNORECASE


def has_selection_markers(pattern: str) -> bool:
    return SELECTION_START in pattern or SELECTION_END in pattern


def strip_selection_markers(pattern: str) -> str:
    """Return the pattern as typed, without the selection markers."""
    return pattern.replace(SELECTION_START, "").replace(SELECTION_END, "")


def selection_group_pattern(pattern: str) -> str:
    r"""Translate ``\zs`` and ``\ze`` into one named selection group."""
    if not has_selection_markers(pattern):
        return pattern
    head, _, rest = pattern.partition(SELECTION_START)
    if SELECTION_START not in pattern:
        head, rest = "", pattern
    body, _, tail = rest.partition(SELECTION_END)
    return f"{head}(?P<{SELECTION_GROUP}>{body}){tail}"


def selection_span(found: re.Match[str]) -> tuple[int, int]:
    """Return the marked selection range, or the whole match without markers."""
    if SELECTION_GROUP in found.re.groupindex:
        start, end = found.span(SELECTION_GROUP)
        if start >= 0:
            return start, end
    return found.start(), found.end()


MARGIN = re.compile(r"^([^\S\n]*)([•›][^\S\n]+)?")
PRIVATE_USE = re.compile(r"[\ue000-\uf8ff\U000f0000-\U000ffffd\U00100000-\U0010fffd] ?")


def scrollback_lines(text: str) -> tuple[list[str], list[str]]:
    """Split scrollback into match-ready lines and the indent each one lost.

    Matching runs without terminal UI margins so a locator never has to spell
    out indentation. Keeping each indent lets a pasted block carry the shape it
    had on screen. A bulleted line is picker chrome rather than structure, so
    its leading space is dropped for good.
    """
    lines: list[str] = []
    indents: list[str] = []
    for line in text.splitlines():
        margin = MARGIN.match(line)
        indents.append("" if margin.group(2) else margin.group(1))
        lines.append(PRIVATE_USE.sub("", line[margin.end() :]).rstrip())
    return lines, indents


def clean_scrollback(text: str) -> str:
    """Remove terminal UI margins while retaining hard line boundaries."""
    return "\n".join(scrollback_lines(text)[0])


def restore_indent(match: Match, indents: list[str], whole_line: bool) -> Match:
    """Give a match back the indentation it is shown selected with.

    Every line after the first sits inside the highlighted range whole, so its
    indent belongs to the paste. The first line only keeps its own indent when
    the selection is drawn from the start of the line.
    """
    if match.end_line >= len(indents):
        return match
    covered = list(indents[match.start_line : match.end_line + 1])
    if not whole_line:
        covered[0] = ""
    if not any(covered):
        return match
    parts = match.text.split("\n")
    if len(parts) != len(covered):
        return match
    return replace(
        match, text="\n".join(indent + part for indent, part in zip(covered, parts))
    )


def prose_friendly_pattern(pattern: str) -> str:
    """Let a keyboard apostrophe match either straight or smart prose."""
    out: list[str] = []
    escaped = False
    in_class = False
    for char in pattern:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            out.append(char)
            escaped = True
        elif char == "[":
            out.append(char)
            in_class = True
        elif char == "]" and in_class:
            out.append(char)
            in_class = False
        elif char == "'" and not in_class:
            out.append("['’]")
        else:
            out.append(char)
    return "".join(out)


def anchored_line_prefix(pattern: str) -> str | None:
    """Return the literal locator in a complete or unfinished ``^words$`` form."""
    if not pattern.startswith("^"):
        return None
    body = pattern[1:-1] if has_terminal_anchor(pattern) else pattern[1:]
    if not body or re.search(r"[\\.*+?()[\]{}|^$]", body):
        return None
    return body


def open_ended_line_pattern(pattern: str) -> str | None:
    """Return the start expression in the shorthand ``^start.*$`` form."""
    if not pattern.startswith("^"):
        return None
    body = pattern[1:-1] if has_terminal_anchor(pattern) else pattern[1:]
    if not body.endswith(".*") or body.endswith(r"\.*"):
        return None
    start = body[:-2]
    return start or None


def line_tail_shorthand(pattern: str) -> str | None:
    """Return the start expression in the shorthand ``^start$$`` form."""
    if not pattern.startswith("^") or not pattern.endswith("$$"):
        return None
    marker = len(pattern) - 2
    slashes = 0
    for char in reversed(pattern[:marker]):
        if char != "\\":
            break
        slashes += 1
    if slashes % 2:
        return None
    start = pattern[1:marker]
    return start or None


def line_tail_start_pattern(pattern: str) -> str | None:
    return line_tail_shorthand(pattern) or open_ended_line_pattern(pattern)


def block_start_pattern(pattern: str, suffix: str) -> str | None:
    """Return the locator in a ``^start\\l`` or ``^start\\p`` form.

    Either case reads the same, so the shift key is optional.
    """
    if not pattern.startswith("^"):
        return None
    for form in (suffix.lower(), suffix.upper()):
        if pattern.endswith(form):
            start = pattern[1 : -len(form)]
            return start or None
    return None


def line_suffix_start(pattern: str) -> str | None:
    return block_start_pattern(pattern, LINE_SUFFIX)


def paragraph_suffix_start(pattern: str) -> str | None:
    return block_start_pattern(pattern, PARAGRAPH_SUFFIX)


def paragraph_bounds(clean: str, start: int, end: int) -> tuple[int, int]:
    """Grow an offset range to the blank-line boundaries around it."""
    head = clean.rfind("\n\n", 0, start)
    top = 0 if head < 0 else head + 2
    tail = clean.find("\n\n", max(start, end - 1))
    bottom = len(clean) if tail < 0 else tail
    while bottom > top and clean[bottom - 1] == "\n":
        bottom -= 1
    return top, bottom


def line_bounds(clean: str, start: int, end: int) -> tuple[int, int]:
    """Grow an offset range to the logical lines that hold it."""
    top = clean.rfind("\n", 0, start) + 1
    tail = clean.find("\n", max(start, end - 1))
    return top, len(clean) if tail < 0 else tail


def sentence_start_pattern(pattern: str) -> str | None:
    r"""Return the locator in the shorthand ``^start\ss`` form."""
    if not pattern.startswith("^") or not pattern.endswith(SENTENCE_SUFFIX):
        return None
    start = pattern[1 : -len(SENTENCE_SUFFIX)]
    return start or None


def first_landmark_pattern(pattern: str) -> str:
    """Make the first unescaped dot-star stop at its nearest ending locator."""
    return re.sub(r"(?<!\\)\.\*(?!\?)", ".*?", pattern, count=1)


def landmark_line_tail_pattern(pattern: str) -> str | None:
    """Return a multiline landmark range followed by the ``$$`` line tail."""
    body = line_tail_shorthand(pattern)
    if body is None or re.search(r"(?<!\\)\.\*", body) is None:
        return None
    return first_landmark_pattern(body)


def leading_literal(pattern: str) -> str:
    """Return the plain-text prefix before the first regex operator."""
    literal: list[str] = []
    escaped = False
    escapable = set(r".\^$*+?{}[]|()")
    for char in pattern:
        if escaped:
            if char not in escapable:
                break
            literal.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in ".^$*+?{}[]|()":
            break
        else:
            literal.append(char)
    return "".join(literal)


def viable_match(found: re.Match[str]) -> bool:
    start, end = selection_span(found)
    return end > start


def regex_matches_latest(
    text: str,
    pattern: str,
    *,
    locator_pattern: str | None = None,
    flags: re.RegexFlag = re.IGNORECASE,
) -> list[re.Match[str]]:
    """Return viable matches from newest start to oldest start."""
    compiled = re.compile(
        prose_friendly_pattern(selection_group_pattern(pattern)),
        re.MULTILINE | re.DOTALL | flags,
    )
    prefix = leading_literal(
        strip_selection_markers(
            locator_pattern if locator_pattern is not None else pattern
        )
    )
    if prefix:
        prefix_pattern = re.compile(prose_friendly_pattern(re.escape(prefix)), flags)
        starts = [candidate.start() for candidate in prefix_pattern.finditer(text)]
        matches = []
        for start in reversed(starts):
            candidate = compiled.match(text, start)
            if candidate is not None and viable_match(candidate):
                matches.append(candidate)
        return matches

    return [
        candidate
        for candidate in reversed(list(compiled.finditer(text)))
        if viable_match(candidate)
    ]


def latest_regex_match(text: str, pattern: str) -> re.Match[str] | None:
    """Prefer the latest viable start, including overlapping greedy ranges."""
    matches = regex_matches_latest(text, pattern)
    return matches[0] if matches else None


def offsets_result(clean: str, start: int, end: int) -> Match:
    start_line = clean.count("\n", 0, start)
    end_line = clean.count("\n", 0, max(start, end - 1))
    return Match(
        clean[start:end],
        start_line,
        end_line,
        start_offset=start,
        end_offset=end,
    )


def regex_match_result(clean: str, found: re.Match[str]) -> Match:
    start, end = selection_span(found)
    return offsets_result(clean, start, end)


def line_start_offsets(lines: list[str]) -> list[int]:
    offsets = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line) + 1
    return offsets


def block_matches(clean: str, locator: str, bounds, flags: re.RegexFlag) -> list[Match]:
    """Expand every locator hit to its surrounding block, without repeats."""
    seen: set[tuple[int, int]] = set()
    matches = []
    for found in regex_matches_latest(clean, locator, flags=flags):
        span = bounds(clean, *selection_span(found))
        if span in seen:
            continue
        seen.add(span)
        matches.append(offsets_result(clean, *span))
    return matches


def find_matches_latest(text: str, pattern: str) -> list[Match]:
    """Return every viable expansion from newest source position to oldest."""
    lines, indents = scrollback_lines(text)
    whole_line = selects_whole_line(pattern)
    return [
        restore_indent(match, indents, whole_line)
        for match in clean_matches_latest("\n".join(lines), pattern)
    ]


def clean_matches_latest(clean: str, pattern: str) -> list[Match]:
    """Choose the selector shape and return its matches, newest first."""
    pattern, flags = query_options(pattern)
    if not pattern:
        return []

    paragraph_start = paragraph_suffix_start(pattern)
    if paragraph_start is not None:
        return block_matches(clean, paragraph_start, paragraph_bounds, flags)

    line_start = line_suffix_start(pattern)
    if line_start is not None:
        return block_matches(clean, line_start, line_bounds, flags)

    landmark_tail = landmark_line_tail_pattern(pattern)
    if landmark_tail is not None:
        found_matches = regex_matches_latest(clean, landmark_tail, flags=flags)
        matches = []
        for found in found_matches:
            start, end = selection_span(found)
            line_end = clean.find("\n", end)
            if line_end < 0:
                line_end = len(clean)
            matches.append(offsets_result(clean, start, line_end))
        return matches

    sentence_start = sentence_start_pattern(pattern)
    if sentence_start is not None:
        sentence_pattern = f"(?:{sentence_start})[^.!?]*[.!?]"
        found_matches = regex_matches_latest(
            clean, sentence_pattern, locator_pattern=sentence_start, flags=flags
        )
        return [
            offsets_result(clean, selection_span(found)[0], found.end())
            for found in found_matches
        ]

    line_prefix = anchored_line_prefix(pattern)
    if line_prefix is not None:
        compiled_prefix = re.compile(
            prose_friendly_pattern(re.escape(line_prefix)), flags
        )
        lines = clean.splitlines()
        offsets = line_start_offsets(lines)
        matches = []
        for index in range(len(lines) - 1, -1, -1):
            occurrences = list(compiled_prefix.finditer(lines[index]))
            for found in reversed(occurrences):
                start = offsets[index] + found.start()
                matches.append(
                    Match(
                        lines[index],
                        index,
                        index,
                        start_offset=start,
                        end_offset=start + len(found.group(0)),
                    )
                )
        return matches

    line_start_pattern = line_tail_start_pattern(pattern)
    if line_start_pattern is not None:
        lines = clean.splitlines()
        offsets = line_start_offsets(lines)
        matches = []
        for index in range(len(lines) - 1, -1, -1):
            for found in regex_matches_latest(
                lines[index], line_start_pattern, flags=flags
            ):
                column = selection_span(found)[0]
                start = offsets[index] + column
                matches.append(
                    Match(
                        lines[index][column:],
                        index,
                        index,
                        start_offset=start,
                        end_offset=offsets[index] + len(lines[index]),
                    )
                )
        return matches

    if pattern.startswith("^"):
        range_pattern = pattern[1:-1] if has_terminal_anchor(pattern) else pattern[1:]
        if not range_pattern:
            return []
        range_pattern = first_landmark_pattern(range_pattern)
        found_matches = regex_matches_latest(clean, range_pattern, flags=flags)
    else:
        found_matches = regex_matches_latest(clean, pattern, flags=flags)
    return [regex_match_result(clean, found) for found in found_matches]


def find_latest_match(
    text: str,
    pattern: str,
    occurrence: int = 0,
    *,
    anchor_offset: int | None = None,
) -> Match | None:
    """Select one expansion, optionally keeping its prior source position."""
    if occurrence < 0:
        return None
    matches = find_matches_latest(text, pattern)
    if anchor_offset is not None and matches:
        return min(matches, key=lambda match: abs(match.start_offset - anchor_offset))
    return matches[occurrence] if occurrence < len(matches) else None


def occurrence_for_match(text: str, pattern: str, match: Match) -> int:
    matches = find_matches_latest(text, pattern)
    return min(
        range(len(matches)),
        key=lambda index: abs(matches[index].start_offset - match.start_offset),
        default=0,
    )


def inline_erase_count(trigger_length: int, initial_query: str) -> int:
    return max(0, trigger_length + len(initial_query) - len(INITIAL_QUERY))


def inline_query_from_capture(captured: str, cursor_x: int) -> str:
    """Recover a matcher suffix typed before the popup acquired focus."""
    current_row = captured.split("\n", 1)[0]
    before_cursor = current_row[:cursor_x]
    trigger_at = before_cursor.rfind(INLINE_TRIGGER)
    if trigger_at < 0:
        return INITIAL_QUERY
    return before_cursor[trigger_at + len(INLINE_TRIGGER) - len(INITIAL_QUERY) :]


def recover_inline_query(pane: str) -> str:
    cursor = tmux(
        "display-message",
        "-p",
        "-t",
        pane,
        "#{cursor_x}|#{cursor_y}",
        capture_output=True,
    ).stdout.strip()
    cursor_x, separator, cursor_y = cursor.partition("|")
    if not separator or not cursor_x.isdigit() or not cursor_y.isdigit():
        return INITIAL_QUERY
    captured = tmux(
        "capture-pane",
        "-p",
        "-N",
        "-S",
        cursor_y,
        "-E",
        cursor_y,
        "-t",
        pane,
        capture_output=True,
    ).stdout
    return inline_query_from_capture(captured, int(cursor_x))


def has_terminal_anchor(query: str) -> bool:
    if not query.endswith("$"):
        return False
    slashes = 0
    for char in reversed(query[:-1]):
        if char != "\\":
            break
        slashes += 1
    return slashes % 2 == 0


def space_action(query: str, has_match: bool) -> str:
    if not has_terminal_anchor(query):
        return "put( )"
    return "accept" if has_match else "no-match"


def run(
    command: list[str], *, check: bool = True, **kwargs
) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=check, text=True, **kwargs)


def tmux(*args: str, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return run(["tmux", *args], check=check, **kwargs)


def active_tmux_target() -> tuple[str, str]:
    """Resolve the tmux client hosted by the focused X11 terminal."""
    active_window = None
    try:
        active_window = int(
            run(["xdotool", "getactivewindow"], capture_output=True).stdout.strip()
        )
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError):
        pass

    clients = tmux(
        "list-clients", "-F", "#{client_name}|#{client_pid}", capture_output=True
    ).stdout.splitlines()
    chosen = None
    if active_window is not None:
        for row in clients:
            client, _, pid = row.partition("|")
            try:
                environ = Path(f"/proc/{int(pid)}/environ").read_bytes().split(b"\0")
                value = next(
                    part[9:] for part in environ if part.startswith(b"WINDOWID=")
                )
                if int(value) == active_window:
                    chosen = client
                    break
            except (OSError, ValueError, StopIteration):
                continue
    if chosen is None and clients:
        chosen = clients[0].partition("|")[0]
    if not chosen:
        raise RuntimeError("no attached tmux client")

    pane = tmux(
        "display-message", "-p", "-c", chosen, "#{pane_id}", capture_output=True
    ).stdout.strip()
    if re.fullmatch(r"%\d+", pane) is None:
        raise RuntimeError("focused tmux client has no active pane")
    return chosen, pane


def state_path(state: str) -> Path:
    path = Path(state).resolve()
    if path.parent != Path(tempfile.gettempdir()) or not path.name.startswith(
        "pane-regex-expand-"
    ):
        raise ValueError("invalid state directory")
    return path


def write_match(state: Path, query: str, match: Match | None) -> None:
    target = state / "match.json"
    if match is None:
        target.unlink(missing_ok=True)
        return
    temp = state / "match.json.new"
    temp.write_text(json.dumps({"query": query, **asdict(match)}, ensure_ascii=False))
    temp.replace(target)


def read_match(state: Path, query: str) -> Match | None:
    try:
        payload = json.loads((state / "match.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if payload.pop("query", None) != query:
        return None
    return Match(**payload)


def ere_literal(text: str) -> str:
    return re.sub(r"([\\.\[\]{}()*+?^$|])", r"\\\1", text)


def native_ignore_case_pattern(pattern: str) -> str:
    """Use tmux smart case without changing negated regex character classes."""
    classes = {
        r"\D": "[^[:digit:]]",
        r"\S": "[^[:space:]]",
        r"\W": "[^[:alnum:]_]",
    }
    return "".join(
        classes.get(token, token) if token.startswith("\\") else token.lower()
        for token in re.findall(r"\\.|[^\\]+|\\$", pattern)
    )


def marker_highlight_pattern(query: str, match: Match | None) -> str:
    r"""Build the tmux locator for a query that carries selection markers.

    tmux searches ERE and knows neither marker, and context left inside the
    pattern becomes part of the native selection whenever the ending fragment
    cannot trim it. Both sides go, so the highlight is the selection.
    """
    body = query[1:] if query.startswith("^") else query
    line_tail = False
    if body.endswith("$$"):
        body, line_tail = body[:-2], True
    elif body.endswith(SENTENCE_SUFFIX):
        body, line_tail = body[: -len(SENTENCE_SUFFIX)], True
    if SELECTION_START in body:
        body = body.partition(SELECTION_START)[2]
    body = body.partition(SELECTION_END)[0]
    if line_tail or (match is not None and "\n" in match.text):
        prefix = leading_literal(body)
        if prefix:
            start = prose_friendly_pattern(ere_literal(prefix))
            return f"({start}.*[^[:space:]]|{start})"
    return prose_friendly_pattern(body)


def native_highlight_pattern(query: str, match: Match | None = None) -> str:
    """Choose the stable single-line locator for tmux's native highlighter."""
    query, _ = query_options(query)
    query = block_highlight_query(query) or query
    if has_selection_markers(query):
        return marker_highlight_pattern(query, match)
    landmark_tail = landmark_line_tail_pattern(query)
    if landmark_tail is not None:
        prefix = leading_literal(landmark_tail)
        if prefix:
            start = prose_friendly_pattern(ere_literal(prefix))
            return f"({start}.*[^[:space:]]|{start})"
    sentence_start = sentence_start_pattern(query)
    if sentence_start is not None:
        start = prose_friendly_pattern(sentence_start)
        if match is not None and "\n" in match.text:
            return f"({start}.*[^[:space:]]|{start})"
        return f"({start})[^.!?]*[.!?]"
    locator = anchored_line_prefix(query)
    if locator is not None:
        return prose_friendly_pattern(ere_literal(locator))
    line_start = line_tail_start_pattern(query)
    if line_start is not None:
        start = prose_friendly_pattern(line_start)
        return f"({start}.*[^[:space:]]|{start})"
    if query.startswith("^"):
        body = query[1:-1] if has_terminal_anchor(query) else query[1:]
        if match is not None and "\n" in match.text:
            prefix = leading_literal(body)
            if prefix:
                start = prose_friendly_pattern(ere_literal(prefix))
                return f"({start}.*[^[:space:]]|{start})"
            return prose_friendly_pattern(body)
        return prose_friendly_pattern(body)
    return prose_friendly_pattern(query)


def native_selection_start_pattern(
    query: str, match: Match | None = None
) -> str | None:
    """Return the first locator when the exact match needs a copy selection."""
    query, _ = query_options(query)
    if paragraph_suffix_start(query) is not None:
        # The paragraph opens above the locator, so the search has to start
        # from the text itself. Its first word is the only anchor there is.
        first = re.search(r"\S+", match.text) if match is not None else None
        return prose_friendly_pattern(ere_literal(first.group(0))) if first else None
    if line_suffix_start(query) is not None:
        return None
    if SELECTION_START in query:
        body = strip_selection_markers(query.partition(SELECTION_START)[2])
        start = leading_literal(body)
        return prose_friendly_pattern(ere_literal(start)) if start else None
    if anchored_line_prefix(query) is not None:
        return None
    start = sentence_start_pattern(query)
    if start is None:
        line_tail = line_tail_start_pattern(query)
        if line_tail is not None and landmark_line_tail_pattern(query) is not None:
            start = leading_literal(line_tail)
            if start:
                start = ere_literal(start)
        else:
            start = line_tail
    if start is None and query.startswith("^"):
        body = query[1:-1] if has_terminal_anchor(query) else query[1:]
        start = leading_literal(body)
        if start:
            start = ere_literal(start)
    return prose_friendly_pattern(start) if start else None


def selection_end_fragment(match: Match) -> tuple[str, int] | None:
    """Return a plain-text tail and searches needed to reach the match end."""
    text = match.text.rstrip()
    found = re.search(r"\S+$", text)
    if found is None:
        return None
    fragment = found.group(0)
    searches = sum(
        found.start() > 0
        for found in re.finditer(re.escape(fragment), text, re.IGNORECASE)
    )
    if searches == 0:
        return None
    return fragment, searches


def native_search_occurrence(text: str, query: str, match: Match) -> int | None:
    """Map the stored source offset to tmux's backward-search occurrence."""
    pattern = native_selection_start_pattern(query, match)
    if pattern is None and selects_whole_line(query):
        # A block form reports one entry per line, while tmux walks every hit
        # on it. Counting the hits keeps the two in step.
        pattern = native_highlight_pattern(query, match)
    if pattern is None:
        return None
    clean = clean_scrollback(text)
    _, flags = query_options(query)
    try:
        positions = [found.start() for found in re.finditer(pattern, clean, flags)]
    except re.error:
        return None
    positions.reverse()
    inside = [
        index
        for index, position in enumerate(positions)
        if match.start_offset
        <= position
        < max(match.end_offset, match.start_offset + 1)
    ]
    if inside:
        # The earliest hit inside the range is the one the selection is
        # anchored at. A later hit would start the selection short.
        return inside[-1]
    return min(
        range(len(positions)),
        key=lambda index: abs(positions[index] - match.start_offset),
        default=0,
    )


def block_highlight_query(query: str) -> str | None:
    """Rewrite a block suffix as its bare locator for the native search."""
    locator = line_suffix_start(query) or paragraph_suffix_start(query)
    return None if locator is None else "^" + locator


def selects_whole_line(query: str) -> bool:
    """Report a committed ``^word$`` form, which pastes the logical line.

    Every half-typed literal reads as this form too, so the closing ``$`` is
    what separates a committed range from a query still being typed. Without
    that test the selection would flash a whole line on every keystroke.
    """
    query, _ = query_options(query)
    if line_suffix_start(query) is not None:
        return True
    return has_terminal_anchor(query) and anchored_line_prefix(query) is not None


def show_match(
    pane: str,
    query: str,
    match: Match | None,
    occurrence: int = 0,
    *,
    search_occurrence: int | None = None,
) -> None:
    selection_start = (
        native_selection_start_pattern(query, match) if match is not None else None
    )
    whole_line = match is not None and selects_whole_line(query)
    selection_end = selection_end_fragment(match) if selection_start and match else None
    search_pattern = (
        selection_start
        if selection_end is not None
        else native_highlight_pattern(query, match)
    )
    _, flags = query_options(query)
    if flags & re.IGNORECASE:
        # tmux uses smart case: lowercase searches include all case variants.
        search_pattern = native_ignore_case_pattern(search_pattern)
    else:
        # tmux has no case-sensitive switch. An impossible uppercase branch
        # disables smart case without adding any possible search matches.
        search_pattern = f"({search_pattern})|(^A$)(^B$)"
    command = [
        "send-keys",
        "-X",
        "-t",
        pane,
        "clear-selection",
        ";",
        "send-keys",
        "-X",
        "-t",
        pane,
        "history-bottom",
        ";",
        "send-keys",
        "-X",
        "-t",
        pane,
        "search-backward",
        "--",
        search_pattern,
    ]
    repeats = search_occurrence if search_occurrence is not None else occurrence
    for _ in range(repeats):
        command.extend([";", "send-keys", "-X", "-t", pane, "search-again"])
    if whole_line:
        # The line form has no ending locator to search forward for. Copy mode
        # spans the logical line for us, soft wraps and trailing padding both.
        for movement in ("start-of-line", "begin-selection", "end-of-line"):
            command.extend([";", "send-keys", "-X", "-t", pane, movement])
    elif selection_end is not None:
        fragment, searches = selection_end
        mode_keys = tmux(
            "display-message", "-p", "-t", pane, "#{mode-keys}", capture_output=True
        ).stdout.strip()
        command.extend([";", "send-keys", "-X", "-t", pane, "begin-selection"])
        for _ in range(searches):
            command.extend(
                [
                    ";",
                    "send-keys",
                    "-X",
                    "-t",
                    pane,
                    "search-forward-text",
                    "--",
                    fragment.lower(),
                ]
            )
        if mode_keys == "emacs":
            # Search moves the Emacs cursor past the word after updating the
            # selection. Refresh that endpoint with a round trip of one cell.
            for movement in ("cursor-left", "cursor-right"):
                command.extend([";", "send-keys", "-X", "-t", pane, movement])
        elif len(fragment) > 1:
            command.extend(
                [
                    ";",
                    "send-keys",
                    "-X",
                    "-N",
                    str(len(fragment) - 1),
                    "-t",
                    pane,
                    "cursor-right",
                ]
            )
        command.extend([";", "send-keys", "-X", "-t", pane, "stop-selection"])
        command.extend(
            [
                ";",
                "send-keys",
                "-X",
                "-t",
                pane,
                "search-forward-text",
                "--",
                f"pane-regex-clear-{time.monotonic_ns()}",
            ]
        )
    tmux(*command, check=False)


def read_occurrence(state: Path) -> int:
    try:
        return max(0, int((state / "occurrence").read_text()))
    except (FileNotFoundError, ValueError):
        return 0


def update(pane: str, state_name: str, query: str) -> Match | None:
    state = state_path(state_name)
    with (state / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous_query = (state / "query").read_text()
        except FileNotFoundError:
            previous_query = ""
        previous_match = read_match(state, previous_query)
        (state / "query").write_text(query)
        if not (state / "occurrence").exists():
            (state / "occurrence").write_text("0")
        occurrence = read_occurrence(state)

        captured = tmux(
            "capture-pane", "-p", "-J", "-S", "-", "-t", pane, capture_output=True
        ).stdout
        try:
            anchor_offset = (
                previous_match.start_offset
                if previous_match is not None and previous_match.end_offset > 0
                else None
            )
            match = find_latest_match(
                captured,
                query,
                occurrence=occurrence,
                anchor_offset=anchor_offset,
            )
        except re.error:
            match = None
        if match is not None and anchor_offset is not None:
            occurrence = occurrence_for_match(captured, query, match)
            (state / "occurrence").write_text(str(occurrence))
        write_match(state, query, match)
        search_occurrence = (
            native_search_occurrence(captured, query, match)
            if match is not None
            else None
        )
        show_match(
            pane,
            query,
            match,
            occurrence=occurrence,
            search_occurrence=search_occurrence,
        )
        return match


def move_selection(pane: str, state_name: str, direction: str, query: str) -> None:
    if direction not in {"older", "newer"}:
        raise ValueError("invalid selection direction")

    state = state_path(state_name)
    with (state / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        occurrence = read_occurrence(state)
        candidate = occurrence + 1 if direction == "older" else max(0, occurrence - 1)

        captured = tmux(
            "capture-pane",
            "-p",
            "-J",
            "-S",
            "-",
            "-t",
            pane,
            capture_output=True,
        ).stdout
        try:
            match = find_latest_match(captured, query, occurrence=candidate)
        except re.error:
            match = None
        if match is None:
            return

        (state / "query").write_text(query)
        (state / "occurrence").write_text(str(candidate))
        write_match(state, query, match)
        search_occurrence = native_search_occurrence(captured, query, match)
        show_match(
            pane,
            query,
            match,
            occurrence=candidate,
            search_occurrence=search_occurrence,
        )


def action_for_accept(pane: str, state_name: str, query: str, *, space: bool) -> str:
    state = state_path(state_name)
    if space and not has_terminal_anchor(query):
        return "put( )"
    try:
        current_query = (state / "query").read_text()
    except FileNotFoundError:
        current_query = ""
    match = read_match(state, query) if current_query == query else None
    found = match is not None or update(pane, state_name, query) is not None
    action = (
        space_action(query, found) if space else ("accept" if found else "no-match")
    )
    if action == "no-match":
        return "change-header(No match. Keep typing or press Esc)"
    return action


def cancel(pane: str) -> None:
    tmux("send-keys", "-X", "-t", pane, "cancel", check=False)


def erase_inline_trigger(pane: str, state: Path) -> None:
    try:
        erase = int((state / "inline-length").read_text())
    except (FileNotFoundError, ValueError):
        erase = 0
    if erase > 0:
        tmux("send-keys", "-t", pane, "-N", str(erase), "BSpace")


def accept(pane: str, state: Path, query: str) -> None:
    match = read_match(state, query)
    if match is None:
        cancel(pane)
        return
    match_file = state / "match.txt"
    match_file.write_text(match.text)
    cancel(pane)
    erase_inline_trigger(pane, state)
    run(["bash", str(DELIVER), "--file", pane, str(match_file)])


def watch_queries(pane: str, state_name: str) -> int:
    state = state_path(state_name)
    last = None
    while state.is_dir() and not (state / "stop").exists():
        try:
            desired = (state / "desired").read_text()
        except FileNotFoundError:
            time.sleep(0.02)
            continue
        if desired != last:
            update(pane, state_name, desired)
            last = desired
        time.sleep(0.02)
    return 0


def fzf_command(pane: str, state: Path, initial_query: str) -> list[str]:
    base = [sys.executable, str(SCRIPT)]
    desired_new = shlex.quote(str(state / "desired.new"))
    desired = shlex.quote(str(state / "desired"))
    record_query = (
        f"printf %s {{q}} > {desired_new} && mv -f -- {desired_new} {desired}"
    )
    accept_command = shlex.join([*base, "--accept-action", pane, str(state)]) + " {q}"
    space_command = shlex.join([*base, "--space-action", pane, str(state)]) + " {q}"
    older_command = shlex.join([*base, "--move", pane, str(state), "older"]) + " {q}"
    newer_command = shlex.join([*base, "--move", pane, str(state), "newer"]) + " {q}"
    return [
        "fzf",
        "--disabled",
        "--layout=reverse",
        "--no-info",
        "--no-separator",
        "--no-scrollbar",
        "--pointer=",
        "--marker=",
        "--prompt=regex> ",
        r"--header=Up older. Down newer. \ss sentence. Tab or Enter expands.",
        f"--query={initial_query}",
        "--print-query",
        "--bind",
        f"start,change:execute-silent({record_query})",
        "--bind",
        f"tab,enter:transform({accept_command})",
        "--bind",
        f"space:transform({space_command})",
        "--bind",
        f"up:execute-silent({older_command})",
        "--bind",
        f"down:execute-silent({newer_command})",
    ]


def prompt(pane: str, state_name: str, trigger_length: int) -> int:
    state = state_path(state_name)
    initial_query = recover_inline_query(pane) if trigger_length else INITIAL_QUERY
    (state / "query").write_text(initial_query)
    (state / "desired").write_text(initial_query)
    (state / "inline-length").write_text(
        str(inline_erase_count(trigger_length, initial_query))
    )
    tmux("copy-mode", "-t", pane)
    watcher = subprocess.Popen(
        [sys.executable, str(SCRIPT), "--watch", pane, str(state)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        result = run(
            fzf_command(pane, state, initial_query),
            input="\n",
            capture_output=True,
            check=False,
        )
    finally:
        (state / "stop").touch()
        try:
            watcher.wait(timeout=1)
        except subprocess.TimeoutExpired:
            watcher.terminate()
            watcher.wait(timeout=1)
    if result.returncode != 0:
        cancel(pane)
        erase_inline_trigger(pane, state)
        return 0
    query = result.stdout.splitlines()[0] if result.stdout else ""
    accept(pane, state, query)
    return 0


def start(trigger_length: int, *, pane: str | None = None) -> int:
    if pane is None and trigger_length <= 0:
        return 0
    if pane is None:
        _, pane = active_tmux_target()
    elif re.fullmatch(r"%\d+", pane) is None:
        raise ValueError("invalid tmux pane id")
    state = Path(tempfile.mkdtemp(prefix="pane-regex-expand-"))
    (state / "query").write_text(INITIAL_QUERY)
    try:
        command = shlex.join(
            [
                sys.executable,
                str(SCRIPT),
                "--prompt",
                pane,
                str(state),
                str(trigger_length),
            ]
        )
        tmux(
            "display-popup",
            "-E",
            "-t",
            pane,
            "-x",
            "C",
            "-y",
            "0",
            "-w",
            "90%",
            "-h",
            "7",
            "-T",
            " pane regex ",
            command,
        )
    finally:
        cancel(pane)
        shutil.rmtree(state, ignore_errors=True)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("trigger_length", nargs="?", type=int)
    parser.add_argument("--pane", help="tmux pane id for a direct plugin launch")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prompt", nargs=3, metavar=("PANE", "STATE", "TRIGGER_LENGTH"))
    modes.add_argument("--watch", nargs=2, metavar=("PANE", "STATE"))
    modes.add_argument("--accept-action", nargs=3, metavar=("PANE", "STATE", "QUERY"))
    modes.add_argument("--space-action", nargs=3, metavar=("PANE", "STATE", "QUERY"))
    modes.add_argument(
        "--move", nargs=4, metavar=("PANE", "STATE", "DIRECTION", "QUERY")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.prompt:
        pane, state, trigger_length = args.prompt
        return prompt(pane, state, int(trigger_length))
    if args.watch:
        pane, state = args.watch
        return watch_queries(pane, state)
    if args.accept_action:
        pane, state, query = args.accept_action
        print(action_for_accept(pane, state, query, space=False))
        return 0
    if args.space_action:
        pane, state, query = args.space_action
        print(action_for_accept(pane, state, query, space=True))
        return 0
    if args.move:
        pane, state, direction, query = args.move
        move_selection(pane, state, direction, query)
        return 0
    trigger_length = args.trigger_length
    if trigger_length is None:
        trigger_length = 0 if args.pane else len(INLINE_TRIGGER)
    return start(trigger_length, pane=args.pane)


if __name__ == "__main__":
    raise SystemExit(main())
