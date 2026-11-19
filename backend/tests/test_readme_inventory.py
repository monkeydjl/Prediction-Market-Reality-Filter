"""The README's inventory numbers, checked against the tree that produced them.

§53.103 found four of them drifted at once (17 route files / 157 services / 127
frontend test files, against 16 / 156 / 131) and nothing noticed, because a
number in prose has no owner: it is correct on the day it is written and rots
silently after. This file is the owner. It counts the way the README means:

* route files     -- modules directly under ``app/api/routes/``
* services        -- modules directly under ``app/services/``
* backend tests   -- ``tests/test_*.py``
* frontend tests  -- ``frontend/src/**/*.test.ts(x)``
* mutation sets   -- ``len(SETS)`` / total mutations in ``scripts/mutation_verify``

Every pattern is asserted to have matched. Renaming the sentence is fine; making
it unparseable is not, because a guard that quietly finds nothing would pass
forever -- the same failure mode as the README it replaces.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts import mutation_verify  # noqa: E402

REPO_ROOT = _BACKEND.parent
README = REPO_ROOT / "README.md"


def _count_route_modules() -> int:
    return len(list((_BACKEND / "app" / "api" / "routes").glob("*.py")))


def _count_service_modules() -> int:
    return len(list((_BACKEND / "app" / "services").glob("*.py")))


def _count_backend_test_files() -> int:
    return len(list((_BACKEND / "tests").glob("test_*.py")))


def _count_frontend_test_files() -> int:
    src = REPO_ROOT / "frontend" / "src"
    return len({*src.rglob("*.test.ts"), *src.rglob("*.test.tsx")})


class ReadmeInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = README.read_text(encoding="utf-8")

    def _number(self, pattern: str, name: str) -> int:
        match = re.search(pattern, self.text)
        self.assertIsNotNone(
            match,
            f"README no longer contains a parseable {name} (pattern {pattern!r}). "
            "Update this test together with the wording -- do not delete the check.",
        )
        assert match is not None  # for type checkers; assertIsNotNone already gated
        return int(match.group(1))

    def test_route_file_count_matches_the_directory(self):
        self.assertEqual(self._number(r"(\d+) 个路由文件", "route-file count"), _count_route_modules())

    def test_service_count_matches_the_directory(self):
        self.assertEqual(self._number(r"（(\d+) 个服务）", "service count"), _count_service_modules())

    def test_backend_test_file_count_matches_the_directory(self):
        self.assertEqual(
            self._number(r"# 后端（(\d+) 个测试文件）", "backend test-file count"),
            _count_backend_test_files(),
        )

    def test_frontend_test_file_count_matches_the_tree(self):
        self.assertEqual(
            self._number(r"# 前端（(\d+) 个测试文件", "frontend test-file count"),
            _count_frontend_test_files(),
        )

    def test_mutation_inventory_matches_the_harness(self):
        """Both halves: the set count and the mutation count.

        The README sentence is `17 套 / 308 条变异`; adding a set means editing
        the README, which this test then re-checks against ``SETS`` itself.
        """
        match = re.search(r"（(\d+) 套 / (\d+) 条变异）", self.text)
        self.assertIsNotNone(match, "README no longer states the mutation inventory")
        assert match is not None
        self.assertEqual(int(match.group(1)), len(mutation_verify.SETS))
        self.assertEqual(
            int(match.group(2)),
            sum(len(mutation_set.mutations) for mutation_set in mutation_verify.SETS),
        )
