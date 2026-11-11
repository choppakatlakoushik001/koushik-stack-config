#!/usr/bin/env python3
"""Local no-server checks for spec and patch refusal rules."""
import importlib.util, sys
from pathlib import Path
script=Path(__file__).parents[1]/"qwen_task.py"
sys.path.insert(0, str(script.parent))
spec=importlib.util.spec_from_file_location("qwen_task",script)
module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)
outside="diff --git a/secret.py b/secret.py\n--- a/secret.py\n+++ b/secret.py\n@@ -1 +1 @@\n-old\n+new\n"
for patch in (outside, outside.replace("secret.py","../calc.py")):
    try: module.validate_patch(patch,{"calc.py"})
    except module.Refusal as exc: print("rejected unsafe patch:",exc)
    else: raise SystemExit("unsafe patch unexpectedly accepted")
print("safety checks passed")
