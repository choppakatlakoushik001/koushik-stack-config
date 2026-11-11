#!/usr/bin/env python3
"""Run one tightly scoped Qwen code task in an isolated git archive."""
from __future__ import annotations
import argparse, ast, datetime as dt, difflib, hashlib, json, os, re, shlex, shutil, subprocess, sys, tarfile, tempfile, time, urllib.request
from pathlib import Path, PurePosixPath
from driver_support import TYPE_INSTRUCTION, checker_command, typecheck, load_tunables, get_failure_kind, VALID_FAILURE_KINDS
from explanations import reason_line

SERVER = "http://127.0.0.1:8080"
MAX_INPUT = 3000
MAX_OUTPUT = 1200
REQUIRED_FIELDS = {"schema_version", "repo", "base_commit", "allowed_paths", "line_ranges", "test_command", "acceptance", "token_estimate"}
OPTIONAL_FIELDS = {"transport", "typecheck_scope"}
FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

class Refusal(Exception): pass

def run(args, cwd=None, env=None, check=True, capture=True):
    p = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE if capture else None,
                       stderr=subprocess.PIPE if capture else None)
    if check and p.returncode:
        raise Refusal(f"command failed ({p.returncode}): {shlex.join(args)}\n{(p.stderr or '')[-2000:]}")
    return p

def utc(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
def sha(b): return hashlib.sha256(b).hexdigest()
def safe_path(s):
    p = PurePosixPath(s)
    return bool(s) and not p.is_absolute() and ".." not in p.parts and "\\" not in s and p.as_posix() == s and not s.startswith(".git/")

def load_spec(path):
    try: spec = json.loads(path.read_text())
    except Exception as e: raise Refusal(f"invalid JSON spec: {e}")
    if not isinstance(spec, dict) or not REQUIRED_FIELDS.issubset(set(spec)) or not set(spec).issubset(FIELDS):
        raise Refusal(f"spec keys must contain: {', '.join(sorted(REQUIRED_FIELDS))} and at most optional: {', '.join(sorted(OPTIONAL_FIELDS))}")
    if spec["schema_version"] != 1: raise Refusal("schema_version must be 1")
    if not isinstance(spec["repo"], str) or not Path(spec["repo"]).is_absolute(): raise Refusal("repo must be an absolute path")
    if not isinstance(spec["base_commit"], str) or not re.fullmatch(r"[0-9a-fA-F]{40,64}", spec["base_commit"]): raise Refusal("base_commit must be a full commit hash")
    paths = spec["allowed_paths"]
    if not isinstance(paths, list) or not paths or any(not isinstance(x,str) or not safe_path(x) for x in paths) or len(set(paths)) != len(paths): raise Refusal("allowed_paths must be unique safe repository-relative paths")
    ranges = spec["line_ranges"]
    if not isinstance(ranges, dict) or not set(paths).issubset(set(ranges)) or any(not isinstance(k,str) or not safe_path(k) for k in ranges): raise Refusal("line_ranges must cover every allowed path and contain only safe paths")
    for p, rs in ranges.items():
        if not isinstance(rs, list) or not rs: raise Refusal(f"line_ranges[{p}] must be a nonempty list")
        prev=0
        for r in rs:
            if not isinstance(r, dict) or set(r) != {"start", "end"} or not all(type(r[k]) is int and r[k] > 0 for k in ("start","end")) or r["end"] < r["start"] or r["start"] <= prev: raise Refusal(f"invalid or overlapping ranges for {p}")
            prev=r["end"]
    cmd=spec["test_command"]
    if not isinstance(cmd,list) or len(cmd)<5 or any(not isinstance(x,str) for x in cmd) or cmd[:3] not in (["python3","-m","pytest"],["python","-m","pytest"],["python3","-m","unittest"],["python","-m","unittest"]): raise Refusal("test_command must be an argv list beginning python[3] -m pytest or -m unittest")
    if any(x in " ;|&$`><\n\r" for x in cmd): raise Refusal("shell syntax is forbidden in test_command")
    if cmd[2] == "pytest":
        allowed_args={"-q","--quiet","-x","--disable-warnings","--no-header","--no-summary"}
        i=3; saw_test=False
        while i<len(cmd):
            arg=cmd[i]
            if arg in allowed_args: i+=1; continue
            if arg == "-p" and i+1<len(cmd) and cmd[i+1]=="no:cacheprovider": i+=2; continue
            if (arg.startswith("tests/") or arg.startswith("test_")) and ".." not in PurePosixPath(arg.split("::",1)[0]).parts and not Path(arg).is_absolute(): saw_test=True; i+=1; continue
            raise Refusal(f"unsupported pytest argument: {arg}")
        if not saw_test: raise Refusal("pytest command must name a repository test path")
    else:
        allowed_unittest={"discover","-q"}; i=3; saw_tests=False
        while i<len(cmd):
            arg=cmd[i]
            if arg in allowed_unittest: i+=1; continue
            if arg in ("-s","-p") and i+1<len(cmd):
                value=cmd[i+1]
                if arg=="-s" and (value.startswith("tests") or value==".") and ".." not in PurePosixPath(value).parts and not Path(value).is_absolute(): saw_tests=True; i+=2; continue
                if arg=="-p" and value.startswith("test_") and "/" not in value: i+=2; continue
            if arg.startswith("tests.") and all(part.isidentifier() for part in arg.split(".")): saw_tests=True; i+=1; continue
            raise Refusal(f"unsupported unittest argument: {arg}")
        if not saw_tests: raise Refusal("unittest command must use tests discovery or a tests module")
    if not isinstance(spec["acceptance"], str) or not spec["acceptance"].strip() or len(spec["acceptance"])>2000: raise Refusal("acceptance must be 1-2000 characters")
    if type(spec["token_estimate"]) is not int or not 1 <= spec["token_estimate"] <= MAX_INPUT: raise Refusal("token_estimate must be 1..3000")
    transport = spec.get("transport", "function")
    if transport not in ("diff", "function"): raise Refusal("transport must be 'diff' or 'function'")
    scope = spec.get("typecheck_scope", "changed")
    if scope not in ("file", "changed"): raise Refusal("typecheck_scope must be 'file' or 'changed'")
    return spec

def tokenize_count(prompt):
    req=urllib.request.Request(SERVER+"/tokenize", data=json.dumps({"content":prompt}).encode(), headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r: data=json.loads(r.read())
        toks=data.get("tokens")
        if not isinstance(toks,list): raise ValueError("missing tokens list")
        return len(toks)
    except Exception as e: raise Refusal(f"cannot verify prompt token count at {SERVER}/tokenize: {e}")

def normalize_hunks(patch):
    """Correct model-invented hunk counts; git still verifies every context line."""
    lines=patch.splitlines(); out=[]; i=0
    hunk=re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
    while i < len(lines):
        m=hunk.match(lines[i])
        if not m:
            out.append(lines[i]); i+=1; continue
        j=i+1; old=new=0
        while j<len(lines) and not lines[j].startswith(("@@ ","diff --git ")):
            line=lines[j]
            if line.startswith(" "): old+=1; new+=1
            elif line.startswith("-"): old+=1
            elif line.startswith("+"): new+=1
            elif line.startswith("\\") and line.startswith("\\ No newline"):
                pass
            else: raise Refusal("malformed unified diff hunk")
            j+=1
        out.append(f"@@ -{m.group(1)},{old} +{m.group(3)},{new} @@{m.group(5)}")
        out.extend(lines[i+1:j]); i=j
    return "\n".join(out)

def validate_patch(patch, allowed):
    if not patch.strip(): raise Refusal("model returned an empty patch")
    if "GIT binary patch" in patch or "Binary files " in patch: raise Refusal("binary patch refused")
    files=[]; git_paths=[]
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            parts=line.split()
            if len(parts)!=4: raise Refusal("malformed diff --git header")
            names=[]
            for name in parts[2:]:
                name=name[2:] if name.startswith(("a/","b/")) else name
                if not safe_path(name) or name not in allowed: raise Refusal(f"patch path outside allowlist: {name}")
                names.append(name)
            if names[0]!=names[1]: raise Refusal("renames are refused")
            git_paths.append(names[0])
        if line.startswith("--- ") or line.startswith("+++ "):
            name=line[4:].split("\t",1)[0]
            if name == "/dev/null": raise Refusal("file creation/deletion refused")
            if name.startswith("a/") or name.startswith("b/"): name=name[2:]
            if not safe_path(name) or name not in allowed: raise Refusal(f"patch path outside allowlist: {name}")
            files.append(name)
        if line.startswith(("rename from ","rename to ","new file mode ","deleted file mode ","old mode ","new mode ")): raise Refusal("rename, create, delete, and mode changes are refused")
    touched=set(files)
    if not touched or not touched.issubset(set(allowed)): raise Refusal("patch has no valid allowed paths")
    if git_paths and set(git_paths)!=touched: raise Refusal("diff header and file headers disagree")
    if len(patch.encode()) > 60000: raise Refusal("patch exceeds 60 KB")

def extract_replacement_functions(reply: str) -> dict[str, str]:
    """Extract whole replacement function definitions from model reply."""
    blocks = re.findall(r'```(?:python)?\n(.*?)(?:```|\Z)', reply, re.S) or [reply]
    funcs: dict[str, str] = {}
    for block in blocks:
        raw_lines = [l for l in block.splitlines() if not l.strip().upper().startswith("REASON:")]
        non_empty = [l for l in raw_lines if l.strip()]
        code_lines = [l for l in non_empty if not l.strip().startswith("#")]
        if code_lines and all(re.match(r'^\s*\d+: ', l) for l in code_lines):
            cleaned = "\n".join(re.sub(r'^\s*\d+: ?', '', l, count=1) if re.match(r'^\s*\d+: ?', l) else l for l in raw_lines)
        else:
            cleaned = "\n".join(raw_lines)
        try:
            tree = ast.parse(cleaned)
        except SyntaxError:
            continue
        lines = cleaned.splitlines()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_code = "\n".join(lines[node.lineno - 1 : node.end_lineno])
                funcs[node.name] = func_code
    return funcs

def build_diff_from_functions(work: Path, allowed: list[str], funcs: dict[str, str]) -> str:
    """Locate functions by name (ast), replace them, and build a unified diff."""
    if not funcs:
        raise Refusal("no parsable function definition found in model reply")
    diffs = []
    found_any = False
    for p in allowed:
        file_path = work / p
        if not p.endswith(".py") or not file_path.exists():
            continue
        orig_text = file_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(orig_text)
        except SyntaxError:
            continue
        file_lines = orig_text.splitlines(keepends=True)
        matches = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in funcs:
                matches.append((node, None))
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub.name in funcs:
                        matches.append((sub, node))
        if not matches:
            continue
        found_any = True
        matches.sort(key=lambda m: m[0].lineno, reverse=True)
        new_lines = list(file_lines)
        for node, parent_class in matches:
            code = funcs[node.name]
            indent = " " * node.col_offset
            code_lines = [indent + l if l.strip() else l for l in code.splitlines()]
            replacement = [l + "\n" for l in code_lines]
            new_lines = new_lines[: node.lineno - 1] + replacement + new_lines[node.end_lineno :]
        diff_hunks = list(difflib.unified_diff(file_lines, new_lines, fromfile=f"a/{p}", tofile=f"b/{p}"))
        if diff_hunks:
            diffs.append(f"diff --git a/{p} b/{p}\n" + "".join(diff_hunks))

    if not found_any:
        missing = ", ".join(funcs.keys())
        raise Refusal(f"named function(s) not found in allowed files: {missing}")
    if not diffs:
        raise Refusal("model returned identical function; empty patch")
    return "\n".join(diffs)

def request_model(prompt, seed=None, system_message=None):
    if system_message is None:
        system_message = "Return a unified diff followed by one REASON: line explaining why it works. Do not include other prose, markdown fences, or changes outside the supplied allowlist."
    payload={"messages":[{"role":"system","content":system_message},{"role":"user","content":prompt}],"temperature":0.1,"max_tokens":MAX_OUTPUT,"stream":False}
    if seed is not None: payload["seed"] = seed
    req=urllib.request.Request(SERVER+"/v1/chat/completions",data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
    t=time.monotonic()
    try:
        with urllib.request.urlopen(req,timeout=600) as r: data=json.loads(r.read())
        reply=data["choices"][0]["message"]["content"]
    except Exception as e: raise Refusal(f"Qwen request failed: {e}")
    return reply, time.monotonic()-t

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument("spec",type=Path); ap.add_argument("--results",type=Path,default=Path.home()/"local-llm/driver/results")
    type_flags=ap.add_mutually_exclusive_group()
    type_flags.add_argument("--typecheck", action="store_true", help="Enable annotation prompting and strict static checking (default: off)")
    type_flags.add_argument("--no-typecheck", dest="typecheck", action="store_false", help="Keep checking disabled (default; compatibility flag)")
    ap.set_defaults(typecheck=False)
    ap.add_argument("--seed", type=int, help="Reproducible model seed for paired measurements"); ns=ap.parse_args()
    start_t=time.monotonic()
    started=utc(); server_started=False; tmp=None; result=None
    verdict={"verdict":"fail","failure_kind":"refused_spec","started_at":started,"finished_at":None,"reason":None}
    stage="spec"
    try:
        spec=load_spec(ns.spec.resolve())
        transport=spec.get("transport", "function")
        scope=spec.get("typecheck_scope", "changed")
        tunables=load_tunables()
        if spec["token_estimate"] > tunables["split_prompt_tokens"]:
            verdict["split_advised"] = True
        target=ns.results.expanduser().resolve(); target.mkdir(parents=True,exist_ok=True)
        result=target/(dt.datetime.now().strftime("%Y%m%dT%H%M%S")+"-"+sha(os.urandom(16))[:8]); result.mkdir()
        (result/"spec.json").write_bytes(ns.spec.resolve().read_bytes()); spec_hash=sha((result/"spec.json").read_bytes())
        verdict.update(typecheck_enabled=ns.typecheck, attempt_budget=1, jobs=[])
        checker=checker_command() if ns.typecheck else None
        verdict["checker"]=checker
        repo=Path(spec["repo"]).resolve()
        if not repo.is_dir(): raise Refusal("repo directory does not exist")
        head=run(["git","rev-parse","HEAD"],repo).stdout.strip()
        if head.lower()!=spec["base_commit"].lower(): raise Refusal("checked out HEAD differs from base_commit")
        if run(["git","--no-optional-locks","status","--porcelain"],repo).stdout.strip(): raise Refusal("source repository is dirty; clean base required")
        run(["git","cat-file","-e",spec["base_commit"]+"^{commit}"],repo)
        for p in spec["allowed_paths"]:
            n=int(run(["git","show",spec["base_commit"]+":"+p],repo).returncode==0)
            if not n: raise Refusal(f"allowed path not present at base commit: {p}")
        stage="server"
        server_status=subprocess.run(["qwen-server","status"],text=True,capture_output=True)
        if server_status.returncode or "Qwen server running" not in server_status.stdout:
            run(["qwen-server","start"]); server_started=True
        tmp=tempfile.TemporaryDirectory(prefix="qwen-task-"); work=Path(tmp.name)/"src"; work.mkdir()
        archive=subprocess.Popen(["git","archive","--format=tar",spec["base_commit"]],cwd=repo,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        with tarfile.open(fileobj=archive.stdout,mode="r|") as tf: tf.extractall(work,filter="data")
        _,err=archive.communicate()
        if archive.returncode: raise Refusal("git archive failed: "+err.decode(errors="replace")[-1000:])
        snippets=[]
        for p in spec["line_ranges"]:
            lines=(work/p).read_text(errors="strict").splitlines()
            if any(r["end"]>len(lines) for r in spec["line_ranges"][p]): raise Refusal(f"line range exceeds file length: {p}")
            snippets.append(f"\n### {p}\n"+"\n".join(f"{i}: {lines[i-1]}" for r in spec["line_ranges"][p] for i in range(r["start"],min(r["end"],len(lines))+1)))
        stage="model"
        if transport == "function":
            sys_msg = "Return whole replacement Python function definition(s) followed by one REASON: line explaining why it works. Do not include other prose or changes outside the supplied allowlist."
            instruction = "Modify only the production file(s) in the allowlist; do not edit tests. Return whole replacement Python function definition(s) for the function(s) to modify. Return the complete function in a ```python block, followed by one REASON: line explaining why it works.\n"
        else:
            sys_msg = "Return a unified diff followed by one REASON: line explaining why it works. Do not include other prose, markdown fences, or changes outside the supplied allowlist."
            instruction = "Modify only the production file(s) in the allowlist; do not edit tests. Make the requested line edit only. Return exactly one git unified diff with diff --git, --- a/path, +++ b/path, and valid @@ hunks; no code fences. After the diff, add one REASON: line explaining why it works.\n"
        prompt=((TYPE_INSTRUCTION if checker else "")+"Task: make the smallest change that satisfies the acceptance criteria.\n"
                f"Acceptance: {spec['acceptance']}\nAllowed paths: {', '.join(spec['allowed_paths'])}\n"
                f"Test command: {shlex.join(spec['test_command'])}\n{instruction}Context follows; line numbers are informational.\n"+"\n".join(snippets))
        actual=tokenize_count(prompt)
        if actual>MAX_INPUT: raise Refusal(f"actual prompt is {actual} tokens; limit is {MAX_INPUT}")
        if actual>spec["token_estimate"]: raise Refusal(f"actual prompt ({actual}) exceeds declared token_estimate ({spec['token_estimate']})")
        (result/"prompt.txt").write_text(prompt)
        job={"attempt":1,"seed":ns.seed,"prompt_tokens":actual,"output_lines":0,"verdict":"fail","failure_kind":"refused_spec","test_exit_code":None,"typecheck_exit_code":None}
        verdict["jobs"].append(job)
        (result/"verdict.json").write_text(json.dumps(verdict,indent=2)+"\n")
        if transport == "function":
            try:
                reply,seconds=request_model(prompt,ns.seed,system_message=sys_msg)
            except TypeError:
                reply,seconds=request_model(prompt,ns.seed)
        else:
            reply,seconds=request_model(prompt,ns.seed)
        (result/"model_reply.txt").write_text(reply)
        completion_toks = 0
        try:
            completion_toks = tokenize_count(reply)
        except Exception:
            pass
        job.update(output_lines=len(reply.splitlines()), model_seconds=round(seconds,3), reason=reason_line(reply), completion_tokens=completion_toks)
        stage="patch"
        if transport == "function":
            funcs = extract_replacement_functions(reply)
            patch = build_diff_from_functions(work, spec["allowed_paths"], funcs)
        else:
            patch="\n".join(line for line in reply.strip().splitlines() if not line.strip().startswith("```" ) and not line.strip().upper().startswith("REASON:")).strip()
        patch=normalize_hunks(patch)
        validate_patch(patch,spec["allowed_paths"]); (result/"patch.diff").write_text(patch+"\n")
        stage="apply"
        base_output = None
        if scope == "changed" and checker:
            _, base_output = typecheck(work, spec["allowed_paths"], checker)
        run(["git","apply","--check",str(result/"patch.diff")],work); run(["git","apply",str(result/"patch.diff")],work)
        env=os.environ.copy(); env.update({"TMPDIR":str(Path(tmp.name)),"TMP":str(Path(tmp.name)),"TEMP":str(Path(tmp.name)),"XDG_CONFIG_HOME":str(Path(tmp.name)/"config"),"XDG_DATA_HOME":str(Path(tmp.name)/"data"),"HOME":str(Path(tmp.name)/"home"),"PYTHONDONTWRITEBYTECODE":"1","PYTEST_ADDOPTS":"-p no:cacheprovider"})
        Path(env["HOME"]).mkdir(); (Path(env["XDG_CONFIG_HOME"])).mkdir(); Path(env["XDG_DATA_HOME"]).mkdir()
        stage="tests"
        test=run(spec["test_command"],cwd=work,env=env,check=False); output=f"$ {shlex.join(spec['test_command'])}\nexit_code={test.returncode}\n--- stdout ---\n{test.stdout[-12000:]}\n--- stderr ---\n{test.stderr[-12000:]}\n"
        (result/"test_output.txt").write_text(output)
        stage="typecheck"
        changed=sorted({m.group(1) for m in re.finditer(r"^(?:---|\+\+\+) (?:[ab]/)?([^\t\n]+)",patch,re.M) if m.group(1) != "/dev/null"})
        check_code,check_output=typecheck(work,changed,checker,scope=scope,base_output=base_output,patch=patch) if checker else (None,"Disabled.\n")
        (result/"typecheck_output.txt").write_text(check_output)
        passed=test.returncode==0 and check_code in (None,0)
        if passed:
            kind="none"
        elif test.returncode != 0:
            kind="timeout" if test.returncode == 124 else "tests"
        elif check_code not in (None,0):
            kind="timeout" if check_code == 124 else "typecheck"
        else:
            kind="tests"
        job.update(test_exit_code=test.returncode,typecheck_exit_code=check_code,
                   checker_caught_test_miss=test.returncode==0 and check_code not in (None,0), verdict="pass" if passed else "fail", failure_kind=kind)
        verdict["typecheck_exit_code"]=check_code
        verdict["failure_kind"]=kind
        verdict.update({"verdict":"pass" if passed else "fail","reason":"tests and enabled checks passed" if passed else "tests or static type checker failed","base_commit":head,"spec_sha256":spec_hash,"changed_paths":changed,"test_command":spec["test_command"],"test_exit_code":test.returncode,"prompt_tokens":actual,"completion_tokens":completion_toks,"output_token_limit":MAX_OUTPUT,"model_seconds":round(seconds,3),"started_server":server_started,"result_dir":str(result),"repo":str(repo)})
        tmp.cleanup()
    except Exception as e:
        verdict["reason"]=str(e)
        msg=str(e).lower()
        if "timed out" in msg or "timeout" in msg:
            verdict["failure_kind"]="timeout"
        elif stage=="server" or "server" in msg or "qwen request failed" in msg:
            verdict["failure_kind"]="server"
        elif stage=="patch":
            verdict["failure_kind"]="malformed_patch"
        elif stage=="apply":
            verdict["failure_kind"]="patch_apply"
        elif stage=="tests":
            verdict["failure_kind"]="timeout" if "timed out" in msg else "tests"
        elif stage=="typecheck":
            verdict["failure_kind"]="timeout" if "timed out" in msg else "typecheck"
        else:
            verdict["failure_kind"]="refused_spec"
        if verdict.get("jobs"):
            verdict["jobs"][-1]["failure_kind"]=verdict["failure_kind"]
        if result and not (result/"test_output.txt").exists():
            (result/"test_output.txt").write_text("Not run: "+str(e)+"\n")
    finally:
        verdict["finished_at"]=utc()
        verdict["wall_seconds"]=round(time.monotonic()-start_t,3)
        verdict["attempts"]=len(verdict.get("jobs",[]))
        if tmp is not None: tmp.cleanup()
        if server_started:
            stopped=subprocess.run(["qwen-server","stop"],text=True,capture_output=True)
            if stopped.returncode:
                status=subprocess.run(["qwen-server","status"],text=True,capture_output=True)
                if "stale PID file" in status.stdout:
                    stopped=subprocess.run(["qwen-server","stop"],text=True,capture_output=True)
            if stopped.returncode and result:
                verdict["server_stop_error"]=(stopped.stderr or stopped.stdout).strip()
        if result:
            (result/"verdict.json").write_text(json.dumps(verdict,indent=2)+"\n")
    print(json.dumps(verdict,indent=2))
    return 0 if verdict["verdict"]=="pass" else 1
if __name__=="__main__": sys.exit(main())
