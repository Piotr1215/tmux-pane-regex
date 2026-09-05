"""Exercise navigation against the native selection in an attached tmux client."""

import fcntl
import os
import pty
import select
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from test_pane_regex import load_module


@unittest.skipUnless(shutil.which("tmux"), "tmux is required")
class TmuxNavigationTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()
        self.tmp = tempfile.TemporaryDirectory(prefix="pane-regex-expand-test-")
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.socket = str(self.state / "tmux.sock")
        config = self.state / "tmux.conf"
        config.write_text(
            "set -g history-limit 20000\nset -g status off\nsetw -g mode-keys vi\n"
        )
        fixture = self.state / "fixture.py"
        fixture.write_text(
            "import readline\n"
            "print('needle oldest')\n"
            "for i in range(10020): print('filler', i)\n"
            "for i in range(12): print('needle', i)\n"
            "print('python old')\n"
            "print('Python mixed')\n"
            "print('PYTHON newest')\n"
            "print('Two different things with the same name, '"
            "'and only one of them is settled.')\n"
            "print('Wrapped ' + 'word ' * 30 + 'tail.')\n"
            "print('tokenVALUE tail')\n"
            "print('token other')\n"
            "print('-option selected -tail')\n"
            "input('DEST> ')\n"
        )
        self.pane = self.tmux(
            "-f",
            str(config),
            "new-session",
            "-d",
            "-x",
            "100",
            "-y",
            "24",
            "-P",
            "-F",
            "#{pane_id}",
            shlex.join([sys.executable, str(fixture)]),
        ).stdout.strip()
        self.addCleanup(self.tmux, "kill-server", check=False)
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.master = master
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
        env = dict(os.environ, TERM="xterm-256color")
        env.pop("TMUX", None)
        self.client = subprocess.Popen(
            ["tmux", "-S", self.socket, "attach-session"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
        )
        os.close(slave)
        self.addCleanup(self.close_client)
        self.stop = threading.Event()
        self.popup_ready = threading.Event()
        self.reader = threading.Thread(target=self.drain, args=(master,), daemon=True)
        self.reader.start()
        self.addCleanup(self.close_reader)
        self.wait_for_prompt()
        self.tmux("copy-mode", "-t", self.pane)
        self.patch = mock.patch.object(self.mod, "tmux", self.tmux)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def tmux(self, *args, check=True, **kwargs):
        kwargs.setdefault("capture_output", True)
        return subprocess.run(
            ["tmux", "-S", self.socket, *args],
            text=True,
            check=check,
            timeout=5,
            **kwargs,
        )

    def drain(self, master):
        rendered = b""
        while not self.stop.is_set():
            if select.select([master], [], [], 0.05)[0]:
                try:
                    rendered = (rendered + os.read(master, 65536))[-16384:]
                    if b"regex>" in rendered:
                        self.popup_ready.set()
                except OSError:
                    return

    def close_reader(self):
        self.stop.set()
        self.reader.join(timeout=1)

    def close_client(self):
        self.client.terminate()
        self.client.wait(timeout=3)

    def wait_for_prompt(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            captured = self.tmux("capture-pane", "-p", "-t", self.pane).stdout
            attached = self.tmux("list-clients", "-F", "#{client_name}").stdout
            if "DEST>" in captured and attached.strip():
                return
            time.sleep(0.02)
        self.fail("attached fixture did not reach its input prompt")

    def selected_text(self):
        self.tmux("send-keys", "-X", "-t", self.pane, "copy-selection")
        return self.tmux("show-buffer").stdout

    def test_arrows_visit_every_match_in_both_directions_including_old_history(self):
        query = "^needle$$"
        self.mod.update(self.pane, str(self.state), query)
        expected = [f"needle {i}" for i in reversed(range(12))] + ["needle oldest"]
        for text in expected:
            self.assertEqual(self.mod.read_match(self.state, query).text, text)
            self.assertEqual(self.selected_text(), text)
            self.mod.move_selection(self.pane, str(self.state), "older", query)
        for text in reversed(expected):
            self.assertEqual(self.mod.read_match(self.state, query).text, text)
            self.assertEqual(self.selected_text(), text)
            self.mod.move_selection(self.pane, str(self.state), "newer", query)

    def test_initial_query_can_find_a_match_beyond_ten_thousand_lines(self):
        query = "^needle oldest$$"
        match = self.mod.update(self.pane, str(self.state), query)

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "needle oldest")
        self.assertEqual(self.selected_text(), "needle oldest")

    def test_arrows_visit_all_case_variants_shown_by_native_search(self):
        for query in ("^python$$", "^PYTHON$$"):
            self.mod.write_match(self.state, query, None)
            (self.state / "occurrence").write_text("0")
            self.mod.update(self.pane, str(self.state), query)
            for text in ("PYTHON newest", "Python mixed", "python old"):
                self.assertEqual(self.mod.read_match(self.state, query).text, text)
                self.assertEqual(self.selected_text(), text)
                self.mod.move_selection(self.pane, str(self.state), "older", query)

    def test_line_end_selection_is_exact_in_both_copy_key_modes(self):
        for mode in ("vi", "emacs"):
            self.tmux("send-keys", "-X", "-t", self.pane, "cancel")
            self.tmux("setw", "mode-keys", mode)
            self.tmux("copy-mode", "-t", self.pane)
            for query in ("^Two$$", "^Wrapped$$", "^Two.*tail$$"):
                match = self.mod.update(self.pane, str(self.state), query)
                self.assertEqual(self.selected_text(), match.text, (mode, query))

    def test_case_sensitive_override_matches_the_native_highlights(self):
        query = r"^\Cpython$"
        match = self.mod.update(self.pane, str(self.state), query)

        self.assertEqual(match.text, "python old")
        count = self.tmux("display", "-p", "-t", self.pane, "#{search_count}").stdout
        self.assertEqual(count.strip(), "1")
        self.mod.update(self.pane, str(self.state), query + "$")
        self.assertEqual(self.selected_text(), "python old")

    def test_case_folding_keeps_negated_regex_escapes(self):
        match = self.mod.update(self.pane, str(self.state), r"^TOKEN\S+$$")

        self.assertEqual(match.text, "tokenVALUE tail")
        self.assertEqual(self.selected_text(), match.text)

    def test_leading_dashes_are_search_text_in_both_locators(self):
        match = self.mod.update(self.pane, str(self.state), "^-option$$")

        self.assertEqual(match.text, "-option selected -tail")
        self.assertEqual(self.selected_text(), match.text)

    @unittest.skipUnless(shutil.which("fzf"), "fzf is required")
    def test_popup_pastes_an_older_case_variant_without_submitting(self):
        self.tmux("send-keys", "-X", "-t", self.pane, "cancel")
        command = shlex.join(
            [
                sys.executable,
                str(self.mod.SCRIPT),
                "--prompt",
                self.pane,
                str(self.state),
                "0",
            ]
        )
        self.tmux(
            "run-shell",
            "-b",
            shlex.join(["tmux", "display-popup", "-E", "-h", "7", command]),
        )
        self.assertTrue(self.popup_ready.wait(timeout=5), "popup did not render")
        os.write(self.master, b"python$$")
        self.wait_for_match("PYTHON newest")
        os.write(self.master, b"\x1b[A")
        self.wait_for_match("Python mixed")
        os.write(self.master, b"\r")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            captured = self.tmux("capture-pane", "-p", "-t", self.pane).stdout
            if "DEST> Python mixed" in captured:
                self.assertEqual(
                    self.tmux("list-panes", "-F", "#{pane_dead}").stdout.strip(), "0"
                )
                return
            time.sleep(0.02)
        self.fail("accepted text did not reach the destination prompt")

    @unittest.skipUnless(shutil.which("fzf"), "fzf is required")
    def test_escape_removes_inline_trigger_and_keeps_the_existing_prompt(self):
        self.tmux("send-keys", "-X", "-t", self.pane, "cancel")
        self.tmux("send-keys", "-l", "-t", self.pane, "keep this ;;^python")
        command = shlex.join(
            [
                sys.executable,
                str(self.mod.SCRIPT),
                "--prompt",
                self.pane,
                str(self.state),
                "3",
            ]
        )
        self.tmux(
            "run-shell",
            "-b",
            shlex.join(
                [
                    "tmux",
                    "display-popup",
                    "-E",
                    "-h",
                    "7",
                    command,
                ]
            ),
        )
        self.assertTrue(self.popup_ready.wait(timeout=5), "popup did not render")
        os.write(self.master, b"more query text\x1b")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            captured = self.tmux("capture-pane", "-p", "-t", self.pane).stdout
            if "DEST> keep this" in captured.splitlines():
                return
            time.sleep(0.02)
        self.fail("Esc did not restore the original destination prompt")

    def wait_for_match(self, text):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            match = self.mod.read_match(self.state, "^python$$")
            if match is not None and match.text == text:
                return
            time.sleep(0.02)
        self.fail(f"popup did not select {text!r}")
