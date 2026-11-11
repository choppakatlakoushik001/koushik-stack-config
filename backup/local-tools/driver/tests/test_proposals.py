"""Unit tests for proposals 9, 10, and 12."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import driver_support
import qwen_task


class FailureKindAndVerdictsTests(unittest.TestCase):
    def test_get_failure_kind_with_older_verdicts(self):
        # Passed verdict without failure_kind
        self.assertEqual(driver_support.get_failure_kind({"verdict": "pass"}), "none")

        # Failed verdict with tests failure
        self.assertEqual(
            driver_support.get_failure_kind({"verdict": "fail", "test_exit_code": 1}),
            "tests",
        )

        # Failed verdict with timeout exit code
        self.assertEqual(
            driver_support.get_failure_kind({"verdict": "fail", "test_exit_code": 124}),
            "timeout",
        )

        # Failed verdict with typecheck failure
        self.assertEqual(
            driver_support.get_failure_kind({"verdict": "fail", "test_exit_code": 0, "typecheck_exit_code": 1}),
            "typecheck",
        )

        # Failed verdict with typecheck timeout
        self.assertEqual(
            driver_support.get_failure_kind({"verdict": "fail", "test_exit_code": 0, "typecheck_exit_code": 124}),
            "timeout",
        )

        # Refusal due to patch malformation
        self.assertEqual(
            driver_support.get_failure_kind({"verdict": "fail", "reason": "malformed unified diff hunk"}),
            "malformed_patch",
        )

        # Refusal due to patch apply failure
        self.assertEqual(
            driver_support.get_failure_kind({"verdict": "fail", "reason": "git apply failed (1)"}),
            "patch_apply",
        )

        # Server failure
        self.assertEqual(
            driver_support.get_failure_kind({"verdict": "fail", "reason": "cannot start qwen-server"}),
            "server",
        )

        # Spec refusal
        self.assertEqual(
            driver_support.get_failure_kind({"verdict": "fail", "reason": "schema_version must be 1"}),
            "refused_spec",
        )

        # Explicit failure_kind takes precedence
        self.assertEqual(
            driver_support.get_failure_kind({"verdict": "fail", "failure_kind": "timeout"}),
            "timeout",
        )


class TransportFunctionTests(unittest.TestCase):
    def test_extract_replacement_functions(self):
        # Code fence with REASON line (unprefixed)
        reply = "```python\ndef add_one(x: int) -> int:\n    return x + 1\nREASON: adds 1\n```"
        funcs = qwen_task.extract_replacement_functions(reply)
        self.assertIn("add_one", funcs)
        self.assertEqual(funcs["add_one"], "def add_one(x: int) -> int:\n    return x + 1")

        # Plain code without fences (unprefixed)
        reply_plain = "def calc(a, b):\n    return a * b\nREASON: multiplication"
        funcs_plain = qwen_task.extract_replacement_functions(reply_plain)
        self.assertIn("calc", funcs_plain)

        # Prefixed reply with line numbers (e.g. echoed from numbered context)
        reply_prefixed = (
            "```python\n"
            "10: def add_one(x: int) -> int:\n"
            "11:     return x + 1\n"
            "REASON: adds 1 with line numbers\n"
            "```"
        )
        funcs_prefixed = qwen_task.extract_replacement_functions(reply_prefixed)
        self.assertIn("add_one", funcs_prefixed)
        self.assertEqual(funcs_prefixed["add_one"], "def add_one(x: int) -> int:\n    return x + 1")

        # Prefixed reply with varying line number lengths and blank lines
        reply_prefixed_blank = (
            "```python\n"
            " 9: def calculate(val: int) -> int:\n"
            "\n"
            "10:     return val * 2\n"
            "```"
        )
        funcs_prefixed_blank = qwen_task.extract_replacement_functions(reply_prefixed_blank)
        self.assertIn("calculate", funcs_prefixed_blank)
        self.assertEqual(funcs_prefixed_blank["calculate"], "def calculate(val: int) -> int:\n\n    return val * 2")

        # Prefixed reply with unprefixed leading comment line and numbered lines including blank numbered line
        reply_prefixed_with_comment = (
            "```python\n"
            "# file.py\n"
            "1: def process_data(val: int) -> int:\n"
            "2:     # inline comment\n"
            "3:     res = val * 2\n"
            "4: \n"
            "5:     return res\n"
            "```"
        )
        funcs_prefixed_comment = qwen_task.extract_replacement_functions(reply_prefixed_with_comment)
        self.assertIn("process_data", funcs_prefixed_comment)
        self.assertEqual(
            funcs_prefixed_comment["process_data"],
            "def process_data(val: int) -> int:\n    # inline comment\n    res = val * 2\n\n    return res"
        )

        # Code that legitimately contains such text on only some lines (not all non-empty lines carry it)
        # Should NOT strip prefix and should parse successfully as valid python
        reply_legit_partial = (
            "```python\n"
            "def format_log(code: int) -> str:\n"
            "    # 1: note\n"
            "    msg = '10: sample prefix'\n"
            "    return msg\n"
            "```"
        )
        funcs_partial = qwen_task.extract_replacement_functions(reply_legit_partial)
        self.assertIn("format_log", funcs_partial)
        self.assertEqual(
            funcs_partial["format_log"],
            "def format_log(code: int) -> str:\n    # 1: note\n    msg = '10: sample prefix'\n    return msg"
        )

    def test_build_diff_from_functions_top_level(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            (work / "calc.py").write_text("def add_one(x):\n    return x\n")
            funcs = {"add_one": "def add_one(x: int) -> int:\n    return x + 1"}
            diff = qwen_task.build_diff_from_functions(work, ["calc.py"], funcs)
            self.assertIn("diff --git a/calc.py b/calc.py", diff)
            self.assertIn("-def add_one(x):", diff)
            self.assertIn("+def add_one(x: int) -> int:", diff)

    def test_build_diff_from_functions_class_method_preserves_indent(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            (work / "calc.py").write_text(
                "class Calculator:\n    def compute(self, x):\n        return x\n"
            )
            funcs = {"compute": "def compute(self, x: int) -> int:\n    return x + 42"}
            diff = qwen_task.build_diff_from_functions(work, ["calc.py"], funcs)
            self.assertIn("diff --git a/calc.py b/calc.py", diff)
            self.assertIn("+    def compute(self, x: int) -> int:", diff)

    def test_build_diff_refuses_when_function_not_in_allowed_files(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            (work / "calc.py").write_text("def add_one(x):\n    return x\n")
            funcs = {"unknown_func": "def unknown_func():\n    pass"}
            with self.assertRaises(qwen_task.Refusal) as ctx:
                qwen_task.build_diff_from_functions(work, ["calc.py"], funcs)
            self.assertIn("not found in allowed files", str(ctx.exception))


class TypecheckScopeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("pyright") or shutil.which("mypy"), "local checker required")
    def test_changed_scope_ignores_legacy_errors_outside_hunk(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            # Legacy file: intentional type error on line 2, target function on line 4
            content_base = (
                "def legacy() -> int:\n"
                "    return 'not an int'\n\n"
                "def target(x: int) -> int:\n"
                "    return x\n"
            )
            (work / "mod.py").write_text(content_base)
            cmd = driver_support.checker_command()

            # Baseline typecheck on base file fails
            base_code, base_output = driver_support.typecheck(work, ["mod.py"], cmd)
            self.assertNotEqual(base_code, 0)
            self.assertIn("not an int", base_output)

            # Patch modifying only target function (lines 4-5) with valid types
            patch = (
                "diff --git a/mod.py b/mod.py\n"
                "--- a/mod.py\n"
                "+++ b/mod.py\n"
                "@@ -4,2 +4,2 @@\n"
                "-def target(x: int) -> int:\n"
                "-    return x\n"
                "+def target(x: int) -> int:\n"
                "+    return x + 1\n"
            )
            content_patched = (
                "def legacy() -> int:\n"
                "    return 'not an int'\n\n"
                "def target(x: int) -> int:\n"
                "    return x + 1\n"
            )
            (work / "mod.py").write_text(content_patched)

            # Whole file scope still fails due to legacy()
            file_code, _ = driver_support.typecheck(work, ["mod.py"], cmd, scope="file")
            self.assertNotEqual(file_code, 0)

            # Changed scope passes because legacy() error is outside changed lines!
            changed_code, changed_output = driver_support.typecheck(
                work, ["mod.py"], cmd, scope="changed", base_output=base_output, patch=patch
            )
            self.assertEqual(changed_code, 0, changed_output)
            self.assertIn("Scoped typecheck passed", changed_output)

    @unittest.skipUnless(shutil.which("pyright") or shutil.which("mypy"), "local checker required")
    def test_changed_scope_catches_new_type_error_inside_hunk(self):
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            content_base = (
                "def legacy() -> int:\n"
                "    return 'not an int'\n\n"
                "def target(x: int) -> int:\n"
                "    return x\n"
            )
            (work / "mod.py").write_text(content_base)
            cmd = driver_support.checker_command()

            base_code, base_output = driver_support.typecheck(work, ["mod.py"], cmd)

            # Patch introduces a type error in target()
            patch = (
                "diff --git a/mod.py b/mod.py\n"
                "--- a/mod.py\n"
                "+++ b/mod.py\n"
                "@@ -4,2 +4,2 @@\n"
                "-def target(x: int) -> int:\n"
                "-    return x\n"
                "+def target(x: int) -> int:\n"
                "+    return 'wrong return'\n"
            )
            content_patched = (
                "def legacy() -> int:\n"
                "    return 'not an int'\n\n"
                "def target(x: int) -> int:\n"
                "    return 'wrong return'\n"
            )
            (work / "mod.py").write_text(content_patched)

            # Changed scope must fail because target() has a new error inside the hunk!
            code, out = driver_support.typecheck(
                work, ["mod.py"], cmd, scope="changed", base_output=base_output, patch=patch
            )
            self.assertNotEqual(code, 0)
            self.assertIn("Scoped typecheck failed", out)


class SpecValidationTests(unittest.TestCase):
    def test_spec_accepts_transport_and_typecheck_scope(self):
        spec_dict = {
            "schema_version": 1,
            "repo": "/home/chopp/local-llm/driver/selftest/pass",
            "base_commit": "d0d8001181f4034e4b8bb11b1dc61a95d459fffe",
            "allowed_paths": ["calc.py"],
            "line_ranges": {"calc.py": [{"start": 1, "end": 2}], "tests/test_calc.py": [{"start": 1, "end": 6}]},
            "test_command": ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_calc.py", "-q"],
            "acceptance": "Change add_one so it returns input plus one.",
            "token_estimate": 1000,
            "transport": "function",
            "typecheck_scope": "changed",
        }
        with tempfile.TemporaryDirectory() as d:
            spec_file = Path(d) / "spec.json"
            spec_file.write_text(json.dumps(spec_dict))
            loaded = qwen_task.load_spec(spec_file)
            self.assertEqual(loaded["transport"], "function")
            self.assertEqual(loaded["typecheck_scope"], "changed")

    def test_spec_defaults_transport_and_scope(self):
        base = {
            "schema_version": 1,
            "repo": "/home/chopp/local-llm/driver/selftest/pass",
            "base_commit": "d0d8001181f4034e4b8bb11b1dc61a95d459fffe",
            "allowed_paths": ["calc.py"],
            "line_ranges": {"calc.py": [{"start": 1, "end": 2}]},
            "test_command": ["python3", "-m", "unittest", "discover", "-s", "tests"],
            "acceptance": "Fix arithmetic.",
            "token_estimate": 500,
        }
        with tempfile.TemporaryDirectory() as d:
            spec_file = Path(d) / "spec.json"
            spec_file.write_text(json.dumps(base))
            loaded = qwen_task.load_spec(spec_file)
            self.assertEqual(loaded.get("transport", "function"), "function")
            self.assertEqual(loaded.get("typecheck_scope", "changed"), "changed")

    def test_spec_honours_explicit_diff_and_file(self):
        spec_dict = {
            "schema_version": 1,
            "repo": "/home/chopp/local-llm/driver/selftest/pass",
            "base_commit": "d0d8001181f4034e4b8bb11b1dc61a95d459fffe",
            "allowed_paths": ["calc.py"],
            "line_ranges": {"calc.py": [{"start": 1, "end": 2}]},
            "test_command": ["python3", "-m", "unittest", "discover", "-s", "tests"],
            "acceptance": "Fix arithmetic.",
            "token_estimate": 500,
            "transport": "diff",
            "typecheck_scope": "file",
        }
        with tempfile.TemporaryDirectory() as d:
            spec_file = Path(d) / "spec.json"
            spec_file.write_text(json.dumps(spec_dict))
            loaded = qwen_task.load_spec(spec_file)
            self.assertEqual(loaded["transport"], "diff")
            self.assertEqual(loaded["typecheck_scope"], "file")

    def test_spec_rejects_invalid_transport_or_scope(self):
        base = {
            "schema_version": 1,
            "repo": "/home/chopp/local-llm/driver/selftest/pass",
            "base_commit": "d0d8001181f4034e4b8bb11b1dc61a95d459fffe",
            "allowed_paths": ["calc.py"],
            "line_ranges": {"calc.py": [{"start": 1, "end": 2}]},
            "test_command": ["python3", "-m", "unittest", "discover", "-s", "tests"],
            "acceptance": "Fix arithmetic.",
            "token_estimate": 500,
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "spec.json"

            p.write_text(json.dumps({**base, "transport": "invalid"}))
            with self.assertRaises(qwen_task.Refusal):
                qwen_task.load_spec(p)

            p.write_text(json.dumps({**base, "typecheck_scope": "invalid"}))
            with self.assertRaises(qwen_task.Refusal):
                qwen_task.load_spec(p)


if __name__ == "__main__":
    unittest.main()
