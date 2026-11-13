#!/usr/bin/env python3
"""Strue inspection runner.

Runs every recipe in recipes/ inside a fresh temporary directory and writes one
JSON result file. A failing recipe is data, not a crash: the runner exits 0
unless --strict is passed or the runner itself breaks.

    python runner/run.py --os ubuntu-latest --out out/ubuntu-latest.json
    python runner/run.py --only fastapi-hello,express5-hello --strict
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

SCHEMA = "strue.result/v1"
ALL_OS = ["ubuntu-latest", "macos-latest", "windows-latest"]
IS_WINDOWS = os.name == "nt"
LOG_TAIL_CHARS = 2500
REQUIRED = ("id", "title", "intent", "source", "runtime", "steps", "check")


def find_bash() -> str:
    """Return a real bash.

    On Windows, a bare "bash" handed to CreateProcess resolves to the WSL
    launcher in System32 before PATH is consulted, so resolve it explicitly.
    """
    if IS_WINDOWS:
        for candidate in (
            os.environ.get("STRUE_BASH"),
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
        ):
            if candidate and Path(candidate).exists():
                return candidate
    found = shutil.which("bash")
    if not found:
        sys.exit("strue: bash not found on PATH")
    return found


BASH = find_bash()


def base_env() -> dict:
    env = dict(os.environ)
    env.update(
        CI="1",
        NO_COLOR="1",
        FORCE_COLOR="0",
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        PIP_NO_INPUT="1",
        npm_config_yes="true",
        npm_config_fund="false",
        npm_config_audit="false",
        npm_config_update_notifier="false",
        NEXT_TELEMETRY_DISABLED="1",
    )
    # Never leak the CI token into third-party install scripts.
    for secret in ("GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN",
                   "ACTIONS_RUNTIME_URL", "ACTIONS_CACHE_URL", "ACTIONS_RESULTS_URL",
                   "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL"):
        env.pop(secret, None)
    return env


def sh(cmd: str, cwd: Path, env: dict, timeout: int) -> tuple[int, str]:
    """Run one bash command. Returns (exit_code, combined_output)."""
    kwargs = dict(
        cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([BASH, "-eo", "pipefail", "-c", cmd], **kwargs)
    try:
        out, _ = proc.communicate(timeout=timeout)
        return proc.returncode, out or ""
    except subprocess.TimeoutExpired:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        out, _ = proc.communicate()
        return 124, (out or "") + f"\n[strue] timed out after {timeout}s"


def tail(text: str) -> str:
    text = text.strip()
    return text if len(text) <= LOG_TAIL_CHARS else "…" + text[-LOG_TAIL_CHARS:]


def load_recipes(folder: Path) -> list[dict]:
    recipes = []
    for path in sorted(folder.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED if k not in data]
        if missing:
            sys.exit(f"strue: {path.name} is missing {missing}")
        if data["id"] != path.stem:
            sys.exit(f"strue: {path.name} id must equal the file name")
        if data["runtime"] not in ("python", "node"):
            sys.exit(f"strue: {path.name} runtime must be python or node")
        data.setdefault("os", ALL_OS)
        recipes.append(data)
    return recipes


def setup_python(work: Path, env: dict) -> None:
    subprocess.run([sys.executable, "-m", "venv", str(work / ".venv")], check=True)
    bindir = work / ".venv" / ("Scripts" if IS_WINDOWS else "bin")
    env["VIRTUAL_ENV"] = str(work / ".venv")
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    env.pop("PYTHONHOME", None)


def tracked_versions(recipe: dict, work: Path, env: dict) -> dict:
    wanted = recipe.get("track") or []
    if not wanted:
        return {}
    found: dict[str, str] = {}
    try:
        if recipe["runtime"] == "python":
            code, out = sh("python -m pip list --format=json 2>/dev/null", work, env, 120)
            if code == 0:
                for pkg in json.loads(out[out.index("["):]):
                    found[pkg["name"].lower().replace("_", "-")] = pkg["version"]
        else:
            cwd = work / recipe.get("track_in", ".")
            _, out = sh("npm ls --json --depth=0 2>/dev/null || true", cwd, env, 120)
            deps = json.loads(out[out.index("{"):]).get("dependencies", {})
            found = {name: meta.get("version", "?") for name, meta in deps.items()}
    except (ValueError, KeyError):
        return {}
    return {name: found[name.lower()] if name.lower() in found else found.get(name, "not found")
            for name in wanted}


def inspect(recipe: dict) -> dict:
    """Run one recipe from an empty directory and report what happened."""
    started = time.time()
    result = {"id": recipe["id"], "status": "pass", "failed_step": None,
              "log_tail": "", "versions": {}, "avoid": []}
    work = Path(tempfile.mkdtemp(prefix=f"strue-{recipe['id']}-"))
    env = base_env()
    try:
        if recipe["runtime"] == "python":
            setup_python(work, env)
        for rel, content in (recipe.get("files") or {}).items():
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")

        step_timeout = int(recipe.get("timeout", 600))
        for index, step in enumerate(recipe["steps"], start=1):
            cmd, cwd = (step, work) if isinstance(step, str) else (
                step["run"], work / step.get("cwd", "."))
            code, out = sh(cmd, cwd, env, step_timeout)
            if code != 0:
                result.update(status="fail", failed_step=f"step {index}: {cmd}",
                              log_tail=tail(out))
                break

        if result["status"] == "pass":
            check = recipe["check"]
            cwd = work / check.get("cwd", ".")
            code, out = sh(check["run"], cwd, env, step_timeout)
            expect = check.get("expect")
            if code != 0:
                result.update(status="fail", failed_step=f"check: {check['run']}",
                              log_tail=tail(out))
            elif expect and expect not in out:
                result.update(status="fail",
                              failed_step=f"check output did not contain {expect!r}",
                              log_tail=tail(out))

        result["versions"] = tracked_versions(recipe, work, env)

        # "avoid" entries are outdated commands we expect to fail. Only meaningful
        # once the recipe itself passed.
        if result["status"] == "pass":
            for item in recipe.get("avoid") or []:
                cwd = work / item.get("cwd", ".")
                code, out = sh(item["run"], cwd, env, int(item.get("timeout", 120)))
                result["avoid"].append({
                    "run": item["run"], "why": item.get("why", ""),
                    "still_fails": code != 0,
                    "evidence": tail(out)[-400:],
                })
    except Exception as exc:  # runner-side problem, still record it
        result.update(status="fail", failed_step="runner error", log_tail=repr(exc))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    result["duration_s"] = round(time.time() - started, 1)
    return result


def tool_version(cmd: str) -> str:
    code, out = sh(cmd, Path.cwd(), base_env(), 30)
    return out.strip().splitlines()[0] if code == 0 and out.strip() else "unavailable"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--recipes", default="recipes")
    parser.add_argument("--os", dest="os_label", default="local")
    parser.add_argument("--out", default="out/local.json")
    parser.add_argument("--only", default="", help="comma-separated recipe ids")
    parser.add_argument("--strict", action="store_true", help="exit 1 if anything fails")
    args = parser.parse_args()

    recipes = load_recipes(Path(args.recipes))
    only = {x for x in args.only.split(",") if x}
    if only:
        unknown = only - {r["id"] for r in recipes}
        if unknown:
            sys.exit(f"strue: unknown recipe ids {sorted(unknown)}")
        recipes = [r for r in recipes if r["id"] in only]

    results = []
    for recipe in recipes:
        if args.os_label != "local" and args.os_label not in recipe["os"]:
            results.append({"id": recipe["id"], "status": "skip"})
            continue
        print(f"[strue] {recipe['id']} ...", flush=True)
        outcome = inspect(recipe)
        print(f"[strue] {recipe['id']}: {outcome['status']} "
              f"({outcome['duration_s']}s)", flush=True)
        if outcome["status"] == "fail":
            print(f"         {outcome['failed_step']}\n{outcome['log_tail']}", flush=True)
        results.append(outcome)

    env = os.environ
    run_url = None
    if env.get("GITHUB_RUN_ID"):
        run_url = (f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                   f"{env.get('GITHUB_REPOSITORY')}/actions/runs/{env['GITHUB_RUN_ID']}")
    report = {
        "schema": SCHEMA,
        "os": args.os_label,
        "finished_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_url": run_url,
        "commit": env.get("GITHUB_SHA"),
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "node": tool_version("node --version"),
            "npm": tool_version("npm --version"),
        },
        "results": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8", newline="\n")
    failed = [r["id"] for r in results if r["status"] == "fail"]
    print(f"[strue] wrote {out}: {len(results) - len(failed)} ok, {len(failed)} failed")
    return 1 if (failed and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
