"""Definitions for 10 synthetic tasks in the A/B evaluation suite.

Tasks 1-5: Annotated/typed tasks testing whole-function vs diff transport.
Tasks 6-10: Legacy untyped modules with pre-existing typing errors outside
the target function, testing scoped vs whole-file static checking.
"""
from __future__ import annotations

from typing import TypedDict


class TaskDef(TypedDict):
    name: str
    file: str
    test_file: str
    test_module: str
    func_name: str
    initial_code: str
    test_code: str
    acceptance: str
    token_estimate: int
    is_legacy: bool


TASKS: list[TaskDef] = [
    # --- Group 1: Typed tasks (Transport measurement) ---
    {
        "name": "task_01_add_typed",
        "file": "calc.py",
        "test_file": "tests/test_calc.py",
        "test_module": "tests.test_calc",
        "func_name": "add_two",
        "initial_code": (
            "def add_two(a: int, b: int) -> int:\n"
            '    """Return the sum of integers a and b."""\n'
            "    raise NotImplementedError\n"
        ),
        "test_code": (
            "import unittest\nfrom calc import add_two\n\n"
            "class TestAdd(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add_two(2, 3), 5)\n"
            "        self.assertEqual(add_two(-1, 1), 0)\n"
        ),
        "acceptance": "Implement add_two so it returns the sum of a and b with strict int typing.",
        "token_estimate": 800,
        "is_legacy": False,
    },
    {
        "name": "task_02_multiply_typed",
        "file": "calc_mult.py",
        "test_file": "tests/test_calc_mult.py",
        "test_module": "tests.test_calc_mult",
        "func_name": "multiply",
        "initial_code": (
            "def multiply(a: int, b: int) -> int:\n"
            '    """Return the product of integers a and b."""\n'
            "    raise NotImplementedError\n"
        ),
        "test_code": (
            "import unittest\nfrom calc_mult import multiply\n\n"
            "class TestMultiply(unittest.TestCase):\n"
            "    def test_mult(self):\n"
            "        self.assertEqual(multiply(3, 4), 12)\n"
            "        self.assertEqual(multiply(0, 5), 0)\n"
        ),
        "acceptance": "Implement multiply so it returns the product of a and b with strict int typing.",
        "token_estimate": 800,
        "is_legacy": False,
    },
    {
        "name": "task_03_str_upper_typed",
        "file": "text_ops.py",
        "test_file": "tests/test_text_ops.py",
        "test_module": "tests.test_text_ops",
        "func_name": "to_upper",
        "initial_code": (
            "def to_upper(s: str) -> str:\n"
            '    """Return string s converted to uppercase."""\n'
            "    raise NotImplementedError\n"
        ),
        "test_code": (
            "import unittest\nfrom text_ops import to_upper\n\n"
            "class TestText(unittest.TestCase):\n"
            "    def test_upper(self):\n"
            "        self.assertEqual(to_upper('hello'), 'HELLO')\n"
        ),
        "acceptance": "Implement to_upper so it returns the uppercase string.",
        "token_estimate": 800,
        "is_legacy": False,
    },
    {
        "name": "task_04_list_sum_typed",
        "file": "list_ops.py",
        "test_file": "tests/test_list_ops.py",
        "test_module": "tests.test_list_ops",
        "func_name": "sum_positive",
        "initial_code": (
            "def sum_positive(nums: list[int]) -> int:\n"
            '    """Return the sum of positive integers in nums."""\n'
            "    raise NotImplementedError\n"
        ),
        "test_code": (
            "import unittest\nfrom list_ops import sum_positive\n\n"
            "class TestListOps(unittest.TestCase):\n"
            "    def test_sum(self):\n"
            "        self.assertEqual(sum_positive([1, -2, 3, 4, -5]), 8)\n"
        ),
        "acceptance": "Implement sum_positive returning sum of positive numbers.",
        "token_estimate": 800,
        "is_legacy": False,
    },
    {
        "name": "task_05_reverse_typed",
        "file": "list_rev.py",
        "test_file": "tests/test_list_rev.py",
        "test_module": "tests.test_list_rev",
        "func_name": "reverse_items",
        "initial_code": (
            "def reverse_items(items: list[int]) -> list[int]:\n"
            '    """Return a new list with items reversed."""\n'
            "    raise NotImplementedError\n"
        ),
        "test_code": (
            "import unittest\nfrom list_rev import reverse_items\n\n"
            "class TestRev(unittest.TestCase):\n"
            "    def test_rev(self):\n"
            "        self.assertEqual(reverse_items([1, 2, 3]), [3, 2, 1])\n"
        ),
        "acceptance": "Implement reverse_items returning the reversed list of integers.",
        "token_estimate": 800,
        "is_legacy": False,
    },

    # --- Group 2: Legacy files with pre-existing typing contradictions (Scope measurement) ---
    {
        "name": "task_06_legacy_sum",
        "file": "legacy_calc.py",
        "test_file": "tests/test_legacy_calc.py",
        "test_module": "tests.test_legacy_calc",
        "func_name": "compute_sum",
        "initial_code": (
            "# Legacy module with pre-existing type conflict in unrelated function\n"
            "def legacy_broken_helper() -> int:\n"
            "    return 'not an integer'\n\n"
            "def compute_sum(a: int, b: int) -> int:\n"
            '    """Compute a + b."""\n'
            "    raise NotImplementedError\n"
        ),
        "test_code": (
            "import unittest\nfrom legacy_calc import compute_sum\n\n"
            "class TestLegacyCalc(unittest.TestCase):\n"
            "    def test_sum(self):\n"
            "        self.assertEqual(compute_sum(10, 20), 30)\n"
        ),
        "acceptance": "Implement compute_sum returning a + b. Do not modify legacy_broken_helper.",
        "token_estimate": 900,
        "is_legacy": True,
    },
    {
        "name": "task_07_legacy_clamp",
        "file": "legacy_math.py",
        "test_file": "tests/test_legacy_math.py",
        "test_module": "tests.test_legacy_math",
        "func_name": "clamp_val",
        "initial_code": (
            "# Legacy math module\n"
            "def unannotated_broken_fn() -> float:\n"
            "    return None\n\n"
            "def clamp_val(val: int, low: int, high: int) -> int:\n"
            '    """Clamp val to [low, high]."""\n'
            "    raise NotImplementedError\n"
        ),
        "test_code": (
            "import unittest\nfrom legacy_math import clamp_val\n\n"
            "class TestLegacyClamp(unittest.TestCase):\n"
            "    def test_clamp(self):\n"
            "        self.assertEqual(clamp_val(5, 1, 10), 5)\n"
            "        self.assertEqual(clamp_val(0, 1, 10), 1)\n"
            "        self.assertEqual(clamp_val(15, 1, 10), 10)\n"
        ),
        "acceptance": "Implement clamp_val so it clamps val to the [low, high] interval.",
        "token_estimate": 900,
        "is_legacy": True,
    },
    {
        "name": "task_08_legacy_parity",
        "file": "legacy_logic.py",
        "test_file": "tests/test_legacy_logic.py",
        "test_module": "tests.test_legacy_logic",
        "func_name": "is_even",
        "initial_code": (
            "# Legacy logic functions\n"
            "def legacy_misconfigured_logic() -> bool:\n"
            "    return 42\n\n"
            "def is_even(n: int) -> bool:\n"
            '    """Return True if n is even, False otherwise."""\n'
            "    raise NotImplementedError\n"
        ),
        "test_code": (
            "import unittest\nfrom legacy_logic import is_even\n\n"
            "class TestParity(unittest.TestCase):\n"
            "    def test_even(self):\n"
            "        self.assertTrue(is_even(4))\n"
            "        self.assertFalse(is_even(5))\n"
        ),
        "acceptance": "Implement is_even returning True if n is even, False otherwise.",
        "token_estimate": 900,
        "is_legacy": True,
    },
    {
        "name": "task_09_legacy_find",
        "file": "legacy_search.py",
        "test_file": "tests/test_legacy_search.py",
        "test_module": "tests.test_legacy_search",
        "func_name": "first_positive",
        "initial_code": (
            "# Legacy search module\n"
            "def legacy_incompatible() -> int:\n"
            "    return [10, 20]\n\n"
            "def first_positive(items: list[int]) -> int:\n"
            '    """Return the first positive integer in items, or 0 if none exist."""\n'
            "    raise NotImplementedError\n"
        ),
        "test_code": (
            "import unittest\nfrom legacy_search import first_positive\n\n"
            "class TestSearch(unittest.TestCase):\n"
            "    def test_first(self):\n"
            "        self.assertEqual(first_positive([-1, 0, 7, 2]), 7)\n"
            "        self.assertEqual(first_positive([-2, -3]), 0)\n"
        ),
        "acceptance": "Implement first_positive returning first positive integer or 0.",
        "token_estimate": 900,
        "is_legacy": True,
    },
    {
        "name": "task_10_legacy_concat",
        "file": "legacy_str.py",
        "test_file": "tests/test_legacy_str.py",
        "test_module": "tests.test_legacy_str",
        "func_name": "join_pair",
        "initial_code": (
            "# Legacy string formatting\n"
            "def legacy_str_fail() -> str:\n"
            "    return 12345\n\n"
            "def join_pair(a: str, b: str) -> str:\n"
            '    """Join strings a and b separated by a single space."""\n'
            "    raise NotImplementedError\n"
        ),
        "test_code": (
            "import unittest\nfrom legacy_str import join_pair\n\n"
            "class TestStr(unittest.TestCase):\n"
            "    def test_join(self):\n"
            "        self.assertEqual(join_pair('hello', 'world'), 'hello world')\n"
        ),
        "acceptance": "Implement join_pair returning strings a and b joined with a space.",
        "token_estimate": 900,
        "is_legacy": True,
    },
]
