import subprocess
import sys
import tempfile
from pathlib import Path


def run_mutation_test(module_path, target_pattern, replacement, test_module_or_args):
    """Flip target_pattern to replacement in module_path in a temporary workspace,
    and run test_module_or_args with unittest.
    Returns True if the mutant was killed (test failed as expected).
    Raises AssertionError if the mutant survived (test unexpectedly passed).
    """
    source_path = Path(module_path).resolve()
    content = source_path.read_text()
    if target_pattern not in content:
        raise ValueError(f"target_pattern {target_pattern!r} not found in source")

    mutated_content = content.replace(target_pattern, replacement, 1)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        mutated_file = tmp_path / source_path.name
        mutated_file.write_text(mutated_content)

        # Symlink siblings (benchmarks, settings, tests, etc.) into the temp workspace
        for item in source_path.parent.iterdir():
            if item.name not in (source_path.name, '__pycache__', '.git', '.labour-results'):
                dest = tmp_path / item.name
                try:
                    dest.symlink_to(item)
                except OSError:
                    pass

        if isinstance(test_module_or_args, str):
            args = [test_module_or_args]
        else:
            args = list(test_module_or_args)

        cmd = [sys.executable, '-m', 'unittest', *args]
        proc = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)
        if proc.returncode == 0:
            raise AssertionError(
                f"Mutant survived! Replacing {target_pattern!r} with {replacement!r} "
                f"did not cause test failure.\nOutput:\n{proc.stdout}\n{proc.stderr}"
            )
        return True
