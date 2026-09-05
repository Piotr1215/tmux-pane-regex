import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "pane_regex.py"


def load_module():
    spec = importlib.util.spec_from_file_location("pane_regex_expand", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PaneRegexMatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_exact_line_uses_latest_match(self):
        match = self.mod.find_latest_match("site\nother\nsite\n", r"^site$")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "site")
        self.assertEqual(match.start_line, 2)
        self.assertEqual(match.end_line, 2)

    def test_occurrence_offset_selects_an_older_highlight(self):
        text = "first screenshot line\nother\nnewest screenshot line\n"

        match = self.mod.find_latest_match(text, r"^screen$", occurrence=1)

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "first screenshot line")

    def test_short_anchored_phrase_selects_the_whole_latest_line(self):
        text = (
            "The stable old result\n"
            "other\n"
            "The stable build now passes the isolated Tab path.\n"
        )

        match = self.mod.find_latest_match(text, r"^The stable$")

        self.assertIsNotNone(match)
        self.assertEqual(
            match.text, "The stable build now passes the isolated Tab path."
        )
        self.assertEqual(match.start_line, 2)

    def test_short_anchored_word_may_be_anywhere_in_the_line(self):
        text = "before\nThe stable build now passes.\nafter\n"

        match = self.mod.find_latest_match(text, r"^stable$")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "The stable build now passes.")

    def test_unfinished_short_form_already_highlights_the_containing_line(self):
        text = "before\nI’m checking the exact live popup state.\nafter\n"

        match = self.mod.find_latest_match(text, r"^checking")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "I’m checking the exact live popup state.")

    def test_codex_bullet_is_not_part_of_a_selected_line(self):
        text = "• The stable build now passes the isolated Tab path.\n"

        match = self.mod.find_latest_match(text, r"^The stable$")

        self.assertIsNotNone(match)
        self.assertEqual(
            match.text, "The stable build now passes the isolated Tab path."
        )

    def test_private_use_prompt_icon_is_not_pasted_as_hidden_bytes(self):
        text = "dotfiles [\ue0a0 master][⇡]\n"

        match = self.mod.find_latest_match(text, r"^dotfiles$")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "dotfiles [master][⇡]")

    def test_dot_matches_newlines_and_keeps_boundaries(self):
        text = "before\nthe first line\nmiddle line\nlast line too\nafter\n"

        match = self.mod.find_latest_match(text, r"^the.*too$")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "the first line\nmiddle line\nlast line too")
        self.assertEqual(match.start_line, 1)
        self.assertEqual(match.end_line, 3)

    def test_range_words_are_inclusive_locators_not_line_anchors(self):
        text = "prefix checking one\nmiddle\nending selector suffix\n"

        match = self.mod.find_latest_match(text, r"^checking.*selector$")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "checking one\nmiddle\nending selector")

    def test_landmark_range_stops_at_the_first_ending_locator(self):
        text = "with first match and later match\nafter\n"

        match = self.mod.find_latest_match(text, r"^with.*match")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "with first match")

    def test_escaped_final_dollar_is_a_literal_ending_locator(self):
        text = "before\nsession output $ and more\nafter\n"

        match = self.mod.find_latest_match(text, r"^session.*\$")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "session output $")

    def test_open_ended_range_starts_at_match_and_stops_at_logical_line_end(self):
        text = "prefix screenshot text to reuse\nlater output\n"

        match = self.mod.find_latest_match(text, r"^screen.*$")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "screenshot text to reuse")

    def test_double_dollar_is_short_for_match_through_line_end(self):
        text = "prefix screenshot text to reuse\nlater output\n"

        match = self.mod.find_latest_match(text, r"^screen$$")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "screenshot text to reuse")

    def test_selectors_ignore_case_and_keep_original_text(self):
        text = "prefix PYTHON starts here. More details.\nEND of the range\nafter\n"
        for query, expected in (
            ("^python$", "prefix PYTHON starts here. More details."),
            ("^python$$", "PYTHON starts here. More details."),
            (r"^python\ss", "PYTHON starts here."),
            ("^python.*end", "PYTHON starts here. More details.\nEND"),
            ("^python.*end$$", "PYTHON starts here. More details.\nEND of the range"),
            ("python", "PYTHON"),
        ):
            with self.subTest(query=query):
                self.assertEqual(self.mod.find_latest_match(text, query).text, expected)

    def test_selection_end_counts_all_case_variants_of_the_final_word(self):
        match = self.mod.Match("start END and end", 0, 0)

        self.assertEqual(self.mod.selection_end_fragment(match), ("end", 2))

    def test_capital_c_requests_case_sensitive_matching(self):
        text = "python old\nPython mixed\nPYTHON newest\n"
        for query, expected in (
            (r"^\Cpython$$", "python old"),
            (r"^\CPython$", "Python mixed"),
            (r"\Cpython", "python"),
        ):
            with self.subTest(query=query):
                self.assertEqual(self.mod.find_latest_match(text, query).text, expected)

    def test_double_dollar_extends_a_landmark_range_through_its_last_line(self):
        text = (
            "prefix master change\n"
            "details\n"
            "2 files changed, 3 insertions, 4 deletions(-)\n"
            "after\n"
        )

        match = self.mod.find_latest_match(text, r"^master.*deletions$$")

        self.assertIsNotNone(match)
        self.assertEqual(
            match.text,
            "master change\ndetails\n2 files changed, 3 insertions, 4 deletions(-)",
        )

    def test_sentence_shorthand_stops_after_the_first_sentence_end(self):
        text = "before\nThen this wraps\nonto the next line. Keep this out!\nafter\n"

        match = self.mod.find_latest_match(text, r"^Then\ss")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "Then this wraps\nonto the next line.")

    def test_sentence_shorthand_accepts_question_and_exclamation_marks(self):
        question = self.mod.find_latest_match("Can this work? Yes!", r"^Can\ss")
        exclamation = self.mod.find_latest_match("It can work! Yes.", r"^It\ss")

        self.assertEqual(question.text, "Can this work?")
        self.assertEqual(exclamation.text, "It can work!")

    def test_range_search_uses_the_latest_viable_start_locator(self):
        text = "checking old selector\nchecking newest selector\n"

        match = self.mod.find_latest_match(text, r"^checking.*selector$")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "checking newest selector")

    def test_native_highlight_uses_the_line_locator(self):
        self.assertEqual(self.mod.native_highlight_pattern(r"^checking"), "checking")
        self.assertEqual(self.mod.native_highlight_pattern(r"^checking$"), "checking")

    def test_native_highlight_uses_the_start_of_a_multiline_range(self):
        self.assertEqual(
            self.mod.native_highlight_pattern(r"^checking.*selector$"),
            "checking.*selector",
        )

    def test_native_highlight_covers_an_open_ended_line_from_the_match(self):
        self.assertEqual(
            self.mod.native_highlight_pattern(r"^screen.*$"),
            r"(screen.*[^[:space:]]|screen)",
        )

    def test_native_highlight_expands_double_dollar_shorthand(self):
        self.assertEqual(
            self.mod.native_highlight_pattern(r"^screen$$"),
            r"(screen.*[^[:space:]]|screen)",
        )

    def test_multiline_native_highlight_covers_the_first_line_tail(self):
        match = self.mod.Match("checking one\nmiddle\nending selector", 1, 3)

        self.assertEqual(
            self.mod.native_highlight_pattern(r"^checking.*selector$", match),
            r"(checking.*[^[:space:]]|checking)",
        )

    def test_native_highlight_accepts_smart_prose_apostrophes(self):
        self.assertEqual(self.mod.native_highlight_pattern(r"^I'm$"), "I['’]m")

    def test_native_selection_spans_the_exact_wrapped_match(self):
        match = self.mod.Match(
            "I’m switching the native highlight through navigation", 1, 1
        )

        with mock.patch.object(self.mod, "tmux") as tmux:
            self.mod.show_match("%1", r"^I'm.*navigation", match)

        command = tmux.call_args.args
        self.assertIn("begin-selection", command)
        self.assertIn("search-forward-text", command)
        self.assertIn("navigation", command)
        self.assertIn("stop-selection", command)

    def test_literal_line_locator_keeps_search_highlight_without_selection(self):
        match = self.mod.Match("prefix checking the popup", 1, 1)

        with mock.patch.object(self.mod, "tmux") as tmux:
            self.mod.show_match("%1", r"^checking$", match)

        self.assertNotIn("begin-selection", tmux.call_args.args)

    def test_search_starts_at_latest_output_and_moves_up(self):
        text = "the old\nold too\nthe newest\nnewest too\nafter\n"

        match = self.mod.find_latest_match(text, r"^the.*too$")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, "the newest\nnewest too")

    def test_straight_apostrophe_matches_smart_prose_apostrophe(self):
        text = "I’m setting aside the memory.\nTab inserts it back into.\n"

        match = self.mod.find_latest_match(text, r"^I'm.*into\.$")

        self.assertIsNotNone(match)
        self.assertEqual(match.text, text.rstrip("\n"))

    def test_codex_display_margin_is_not_part_of_expansion(self):
        text = "  I’m setting aside the memory.\n    Tab inserts it too.\n"

        match = self.mod.find_latest_match(text, r"^I'm.*too\.$")

        self.assertIsNotNone(match)
        self.assertEqual(
            match.text, "I’m setting aside the memory.\nTab inserts it too."
        )

    def test_accept_erases_only_the_matcher_already_in_the_app(self):
        self.assertEqual(self.mod.inline_erase_count(3, "^"), 3)
        self.assertEqual(
            self.mod.inline_erase_count(3, "^the.*too$"), len(";;^the.*too$")
        )

    def test_query_typed_during_popup_start_is_recovered(self):
        captured = "text> ;;^the.*too$" + (" " * 60)

        query = self.mod.inline_query_from_capture(captured, len("text> ;;^the.*too$"))

        self.assertEqual(query, "^the.*too$")

    def test_recovered_query_uses_the_latest_trigger(self):
        captured = "old ;;^site$ then ;;^I'm.*to\\.$"

        query = self.mod.inline_query_from_capture(captured, len(captured))

        self.assertEqual(query, "^I'm.*to\\.$")

    def test_fzf_starts_with_the_recovered_query(self):
        command = self.mod.fzf_command(
            "%1", Path("/tmp/pane-regex-expand-test"), "^the.*too$"
        )

        self.assertIn("--query=^the.*too$", command)

    def test_fzf_records_live_updates_without_running_tmux_inline(self):
        command = self.mod.fzf_command("%1", Path("/tmp/pane-regex-expand-test"), "^")
        rendered = " ".join(command)

        self.assertIn("desired.new", rendered)
        self.assertIn("desired", rendered)
        self.assertNotIn("--update", rendered)

    def test_fzf_arrows_move_between_native_tmux_highlights(self):
        command = self.mod.fzf_command("%1", Path("/tmp/pane-regex-expand-test"), "^")
        rendered = " ".join(command)

        self.assertIn("up:execute-silent", rendered)
        self.assertIn("down:execute-silent", rendered)
        self.assertIn("--move %1 /tmp/pane-regex-expand-test older", rendered)
        self.assertIn("--move %1 /tmp/pane-regex-expand-test newer", rendered)

    def test_enter_keeps_the_match_selected_with_the_arrows(self):
        query = r"^screen$$"
        with tempfile.TemporaryDirectory(prefix="pane-regex-expand-test-") as name:
            state = Path(name)
            (state / "query").write_text(query)
            self.mod.write_match(state, query, self.mod.Match("older screen", 2, 2))

            with mock.patch.object(self.mod, "update") as update:
                action = self.mod.action_for_accept("%1", name, query, space=False)

        self.assertEqual(action, "accept")
        update.assert_not_called()

    def test_watcher_refresh_keeps_the_arrow_selected_occurrence(self):
        query = r"^screen$"
        captured = "older screenshot line\nnewer screenshot line\n"
        with tempfile.TemporaryDirectory(prefix="pane-regex-expand-test-") as name:
            state = Path(name)
            (state / "query").write_text(query)
            (state / "occurrence").write_text("1")
            completed = self.mod.subprocess.CompletedProcess([], 0, stdout=captured)

            with (
                mock.patch.object(self.mod, "tmux", return_value=completed),
                mock.patch.object(self.mod, "show_match") as show_match,
            ):
                match = self.mod.update("%1", name, query)

            stored = self.mod.read_match(state, query)

        self.assertEqual(match.text, "older screenshot line")
        self.assertEqual(stored, match)
        show_match.assert_called_once_with(
            "%1", query, match, occurrence=1, search_occurrence=None
        )

    def test_query_refinement_keeps_the_arrow_selected_occurrence(self):
        old_query = r"^screen"
        new_query = r"^screen$$"
        captured = "older screenshot line\nnewer screenshot line\n"
        with tempfile.TemporaryDirectory(prefix="pane-regex-expand-test-") as name:
            state = Path(name)
            (state / "query").write_text(old_query)
            (state / "occurrence").write_text("1")
            completed = self.mod.subprocess.CompletedProcess([], 0, stdout=captured)

            with (
                mock.patch.object(self.mod, "tmux", return_value=completed),
                mock.patch.object(self.mod, "show_match") as show_match,
            ):
                match = self.mod.update("%1", name, new_query)

            occurrence = self.mod.read_occurrence(state)

        self.assertEqual(match.text, "screenshot line")
        self.assertEqual(occurrence, 1)
        show_match.assert_called_once_with(
            "%1", new_query, match, occurrence=1, search_occurrence=1
        )

    def test_query_refinement_tracks_source_when_match_order_changes(self):
        old_query = r"^this"
        new_query = r"^this p"
        captured = "this point is selected\nnewer this other line\n"
        old_match = self.mod.find_latest_match(captured, old_query, occurrence=1)
        with tempfile.TemporaryDirectory(prefix="pane-regex-expand-test-") as name:
            state = Path(name)
            (state / "query").write_text(old_query)
            (state / "occurrence").write_text("1")
            self.mod.write_match(state, old_query, old_match)
            completed = self.mod.subprocess.CompletedProcess([], 0, stdout=captured)

            with (
                mock.patch.object(self.mod, "tmux", return_value=completed),
                mock.patch.object(self.mod, "show_match") as show_match,
            ):
                match = self.mod.update("%1", name, new_query)

            occurrence = self.mod.read_occurrence(state)

        self.assertEqual(match.text, "this point is selected")
        self.assertEqual(occurrence, 0)
        show_match.assert_called_once_with(
            "%1", new_query, match, occurrence=0, search_occurrence=None
        )

    def test_trailing_anchor_space_accepts_only_a_current_match(self):
        self.assertEqual(self.mod.space_action(r"^site$", has_match=True), "accept")
        self.assertEqual(self.mod.space_action(r"^site$", has_match=False), "no-match")
        self.assertEqual(self.mod.space_action(r"^kind of", has_match=False), "put( )")
        self.assertEqual(self.mod.space_action(r"^price\$", has_match=True), "put( )")


class PaneRegexPluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = load_module()

    def test_explicit_pane_launch_does_not_resolve_the_focused_x11_client(self):
        with (
            mock.patch.object(self.mod, "active_tmux_target") as active_target,
            mock.patch.object(self.mod, "tmux") as tmux,
        ):
            self.mod.start(0, pane="%7")

        active_target.assert_not_called()
        popup = next(
            call for call in tmux.call_args_list if call.args[0] == "display-popup"
        )
        self.assertIn("--prompt %7", popup.args[-1])
        self.assertTrue(popup.args[-1].endswith(" 0"))

    def test_direct_tmux_launch_starts_with_a_fresh_query(self):
        with tempfile.TemporaryDirectory(prefix="pane-regex-expand-test-") as name:
            state = Path(name)
            watcher = mock.Mock()
            completed = self.mod.subprocess.CompletedProcess([], 1, stdout="")
            with (
                mock.patch.object(self.mod, "recover_inline_query") as recover,
                mock.patch.object(self.mod.subprocess, "Popen", return_value=watcher),
                mock.patch.object(self.mod, "run", return_value=completed),
                mock.patch.object(self.mod, "tmux"),
            ):
                self.mod.prompt("%7", name, 0)

            recover.assert_not_called()
            self.assertEqual((state / "query").read_text(), "^")

    def test_explicit_pane_must_be_a_tmux_pane_id(self):
        with self.assertRaisesRegex(ValueError, "invalid tmux pane"):
            self.mod.start(0, pane="shell")

    def test_direct_pane_cli_defaults_to_no_inline_trigger(self):
        with (
            mock.patch.object(sys, "argv", ["pane_regex.py", "--pane", "%7"]),
            mock.patch.object(self.mod, "start", return_value=0) as start,
        ):
            self.mod.main()

        start.assert_called_once_with(0, pane="%7")


if __name__ == "__main__":
    unittest.main()
