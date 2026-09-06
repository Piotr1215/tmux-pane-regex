import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RenovateExtractionTests(unittest.TestCase):
    def test_custom_managers_find_each_ci_tool_once(self):
        config = json.loads((ROOT / "renovate.json").read_text())
        dependencies = []
        for workflow in (ROOT / ".github/workflows").glob("*.y*ml"):
            filename = workflow.relative_to(ROOT).as_posix()
            for manager in config["customManagers"]:
                if not any(
                    re.search(pattern[1:-1], filename)
                    for pattern in manager["managerFilePatterns"]
                ):
                    continue
                for pattern in manager["matchStrings"]:
                    pattern = pattern.replace("(?<", "(?P<")
                    for match in re.finditer(pattern, workflow.read_text()):
                        dependency = match.groupdict()
                        self.assertTrue(dependency["currentValue"])
                        dependencies.append(
                            dependency.get("depName", manager.get("depNameTemplate"))
                        )

        self.assertCountEqual(dependencies, ["ruff", "tmux/tmux", "junegunn/fzf"])


if __name__ == "__main__":
    unittest.main()
