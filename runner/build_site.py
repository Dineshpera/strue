#!/usr/bin/env python3
"""Turn results/<date>/<os>.json into the public record under docs/.

Writes:
  docs/api/latest.json          everything, one fetch (what the MCP server reads)
  docs/api/recipes/<id>.json    one recipe
  docs/badges/<id>.svg          README badge
  docs/index.html               status page

    python runner/build_site.py --repo OWNER/strue
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
from pathlib import Path

import yaml

OS_ORDER = ["ubuntu-latest", "macos-latest", "windows-latest"]
OS_NAME = {"ubuntu-latest": "Linux", "macos-latest": "macOS", "windows-latest": "Windows"}
HISTORY_DAYS = 30
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_days(results_dir: Path) -> dict[str, dict[str, dict]]:
    """{date: {os_label: report}} for every dated folder, oldest first."""
    days: dict[str, dict[str, dict]] = {}
    if not results_dir.exists():
        return days
    for folder in sorted(p for p in results_dir.iterdir() if p.is_dir() and DATE_RE.match(p.name)):
        reports = {}
        for path in sorted(folder.glob("*.json")):
            reports[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        if reports:
            days[folder.name] = reports
    return days


def day_status(recipe_id: str, reports: dict[str, dict]) -> tuple[str, dict]:
    """Overall status for one recipe on one day, plus the per-OS detail."""
    per_os = {}
    for label, report in reports.items():
        for item in report["results"]:
            if item["id"] == recipe_id and item["status"] != "skip":
                per_os[label] = {**item, "run_url": report.get("run_url"),
                                 "finished_at": report.get("finished_at")}
    if not per_os:
        return "unknown", per_os
    failing = [label for label, item in per_os.items() if item["status"] != "pass"]
    return ("failing" if failing else "verified"), per_os


def badge_svg(status: str, date: str) -> str:
    colour = {"verified": "#2e7d32", "failing": "#c62828"}.get(status, "#757575")
    right = f"{status} {date}" if status != "unknown" else "not yet inspected"
    left = "strue"
    lw, rw = 8 * len(left) + 10, int(6.6 * len(right)) + 14
    width = lw + rw
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="20" role="img" '
        f'aria-label="{left}: {right}"><title>{left}: {right}</title>'
        f'<rect width="{width}" height="20" rx="3" fill="#424242"/>'
        f'<rect x="{lw}" width="{rw}" height="20" rx="3" fill="{colour}"/>'
        f'<rect x="{lw}" width="6" height="20" fill="{colour}"/>'
        f'<g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11" '
        f'text-anchor="middle"><text x="{lw / 2}" y="14">{left}</text>'
        f'<text x="{lw + rw / 2}" y="14">{right}</text></g></svg>\n'
    )


def build(recipes_dir: Path, results_dir: Path, docs: Path, repo: str) -> dict:
    days = load_days(results_dir)
    latest_date = max(days) if days else None
    blob = f"https://github.com/{repo}/blob/main"
    recipes_out = []

    for path in sorted(recipes_dir.glob("*.yaml")):
        recipe = yaml.safe_load(path.read_text(encoding="utf-8"))
        rid = recipe["id"]
        status, per_os = day_status(rid, days[latest_date]) if latest_date else ("unknown", {})

        history = []
        for date in list(days)[-HISTORY_DAYS:]:
            history.append({"date": date, "status": day_status(rid, days[date])[0]})
        streak = 0
        for entry in reversed(history):
            if entry["status"] != "verified":
                break
            streak += 1

        # Stale commands: report "still fails" only if it failed everywhere it ran.
        avoid = []
        for index, item in enumerate(recipe.get("avoid") or []):
            seen = [r["avoid"][index]["still_fails"] for r in per_os.values()
                    if len(r.get("avoid", [])) > index]
            avoid.append({"run": item["run"], "why": item.get("why", ""),
                          "still_fails": all(seen) if seen else None})

        recipes_out.append({
            "id": rid,
            "title": recipe["title"],
            "intent": recipe["intent"],
            "source": recipe["source"],
            "runtime": recipe["runtime"],
            "tags": recipe.get("tags", []),
            "notes": (recipe.get("notes") or "").strip(),
            "status": status,
            "inspected_on": latest_date,
            "verified_streak_days": streak,
            "files": recipe.get("files", {}),
            "steps": recipe["steps"],
            "check": recipe["check"],
            "avoid": avoid,
            "os": {
                label: {
                    "status": item["status"],
                    "versions": item.get("versions", {}),
                    "duration_s": item.get("duration_s"),
                    "failed_step": item.get("failed_step"),
                    "log_tail": item.get("log_tail", "") if item["status"] != "pass" else "",
                    "run_url": item.get("run_url"),
                    "finished_at": item.get("finished_at"),
                    "result_file": f"{blob}/results/{latest_date}/{label}.json",
                    "verify": (f"gh attestation verify results/{latest_date}/{label}.json "
                               f"--repo {repo}"),
                }
                for label, item in per_os.items()
            },
            "history": history,
            "recipe_file": f"{blob}/recipes/{rid}.yaml",
        })

    summary = {
        "schema": "strue.record/v1",
        "repo": repo,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inspected_on": latest_date,
        "counts": {s: sum(r["status"] == s for r in recipes_out)
                   for s in ("verified", "failing", "unknown")},
        "recipes": recipes_out,
    }

    (docs / "api" / "recipes").mkdir(parents=True, exist_ok=True)
    (docs / "badges").mkdir(parents=True, exist_ok=True)
    dump = dict(indent=2, ensure_ascii=False)
    (docs / "api" / "latest.json").write_text(json.dumps(summary, **dump) + "\n", encoding="utf-8")
    for recipe in recipes_out:
        (docs / "api" / "recipes" / f"{recipe['id']}.json").write_text(
            json.dumps(recipe, **dump) + "\n", encoding="utf-8")
        (docs / "badges" / f"{recipe['id']}.svg").write_text(
            badge_svg(recipe["status"], recipe["inspected_on"] or ""), encoding="utf-8")
    (docs / "index.html").write_text(render_page(summary), encoding="utf-8")
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    return summary


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def render_page(summary: dict) -> str:
    repo, date = summary["repo"], summary["inspected_on"]
    counts = summary["counts"]
    rows = []
    for r in summary["recipes"]:
        cells = []
        for label in OS_ORDER:
            item = r["os"].get(label)
            if not item:
                cells.append('<td class="os na" title="not run">–</td>')
                continue
            ok = item["status"] == "pass"
            mark = "✓" if ok else "✗"
            text = f'{mark}<span class="sr"> {"pass" if ok else "fail"}</span>'
            link = f'<a href="{esc(item["run_url"])}">{text}</a>' if item["run_url"] else text
            cells.append(f'<td class="os {"ok" if ok else "bad"}">{link}</td>')

        versions = {}
        for item in r["os"].values():
            versions.update(item["versions"])
        version_text = ", ".join(f"{k} {v}" for k, v in versions.items()) or "–"

        step_lines = "\n".join(s if isinstance(s, str) else
                               f'(in {s.get("cwd", ".")}/) {s["run"]}' for s in r["steps"])
        files = "".join(
            f'<p class="fname">{esc(name)}</p><pre>{esc(body.rstrip())}</pre>'
            for name, body in r["files"].items() if not name.startswith(("stale.", ".eslintrc")))
        avoid = "".join(
            f'<li><code>{esc(a["run"])}</code> — {esc(a["why"])} '
            f'<span class="tag {"bad" if a["still_fails"] else "na"}">'
            f'{"confirmed broken" if a["still_fails"] else "not confirmed" if a["still_fails"] is None else "now works"}'
            f'</span></li>' for a in r["avoid"])
        failures = "".join(
            f'<p class="fname">{esc(OS_NAME.get(label, label))}: {esc(item["failed_step"])}</p>'
            f'<pre>{esc(item["log_tail"])}</pre>'
            for label, item in r["os"].items() if item["status"] != "pass")
        verify = next((item["verify"] for item in r["os"].values()), "")

        rows.append(f"""
<tr class="{esc(r['status'])}">
  <td class="what">
    <details>
      <summary><strong>{esc(r['title'])}</strong><br><span class="muted">{esc(r['id'])}</span></summary>
      <p>{esc(r['intent'])} <a href="{esc(r['source'])}">Official docs</a> ·
         <a href="{esc(r['recipe_file'])}">recipe file</a></p>
      {f'<p class="muted">{esc(r["notes"])}</p>' if r['notes'] else ''}
      {f'<h4>Failure</h4>{failures}' if failures else ''}
      <h4>Steps, run from an empty folder</h4><pre>{esc(step_lines)}</pre>
      {files}
      <h4>Check</h4><pre>{esc(r['check']['run'])}</pre>
      {f'<h4>Outdated commands to avoid</h4><ul>{avoid}</ul>' if avoid else ''}
      {f'<h4>Check the signature yourself</h4><pre>{esc(verify)}</pre>' if verify else ''}
      <h4>Badge</h4><pre>[![strue](https://{esc(repo.split('/')[0])}.github.io/{esc(repo.split('/')[1])}/badges/{esc(r['id'])}.svg)](https://{esc(repo.split('/')[0])}.github.io/{esc(repo.split('/')[1])}/)</pre>
    </details>
  </td>
  <td><span class="tag {esc(r['status'])}">{esc(r['status'])}</span></td>
  {''.join(cells)}
  <td class="muted ver">{esc(version_text)}</td>
  <td class="num">{r['verified_streak_days']}</td>
</tr>""")

    headline = (f"{counts['verified']} verified, {counts['failing']} failing"
                + (f", {counts['unknown']} not yet inspected" if counts["unknown"] else ""))
    when = f"Last inspection: {esc(date)} (UTC)" if date else "No inspection has run yet."
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strue — what works today</title>
<style>
  :root {{ --bg:#fbfaf7; --fg:#1d1c1a; --muted:#6b6862; --line:#e3e0d8; --card:#fff;
          --ok:#2e7d32; --bad:#c62828; --na:#8a867e; --code:#f3f1ea; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#161614; --fg:#ecebe6; --muted:#a09c93; --line:#33322e; --card:#1e1e1b;
            --ok:#7bc47f; --bad:#ef8a80; --na:#8a867e; --code:#262622; }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--fg);
         font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
  main {{ max-width:1080px; margin:0 auto; padding:40px 20px 80px; }}
  h1 {{ font-size:34px; margin:0 0 4px; letter-spacing:-.01em; }}
  h4 {{ margin:18px 0 6px; font-size:13px; text-transform:uppercase; letter-spacing:.06em;
       color:var(--muted); }}
  .lede {{ font-size:18px; max-width:62ch; margin:0 0 20px; }}
  .muted {{ color:var(--muted); font-size:14px; }}
  .stats {{ display:flex; gap:24px; flex-wrap:wrap; padding:14px 0; border-top:1px solid var(--line);
           border-bottom:1px solid var(--line); margin-bottom:24px; }}
  .scroll {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:10px; }}
  table {{ border-collapse:collapse; width:100%; min-width:760px; }}
  th, td {{ padding:12px 14px; text-align:left; vertical-align:top; border-bottom:1px solid var(--line); }}
  th {{ font-size:12px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }}
  tr:last-child td {{ border-bottom:0; }}
  td.os, th.os, td.num, th.num {{ text-align:center; width:70px; }}
  td.os a {{ color:inherit; text-decoration:none; }}
  .ok {{ color:var(--ok); }} .bad {{ color:var(--bad); }} .na {{ color:var(--na); }}
  .tag {{ display:inline-block; padding:1px 9px; border-radius:99px; font-size:13px;
         border:1px solid currentColor; white-space:nowrap; }}
  .tag.verified {{ color:var(--ok); }} .tag.failing {{ color:var(--bad); }} .tag.unknown {{ color:var(--na); }}
  .ver {{ max-width:210px; }}
  summary {{ cursor:pointer; }}
  pre {{ background:var(--code); padding:10px 12px; border-radius:6px; overflow-x:auto;
        font:13px/1.45 ui-monospace,Menlo,Consolas,monospace; margin:0 0 8px; white-space:pre-wrap;
        word-break:break-word; }}
  code {{ background:var(--code); padding:1px 5px; border-radius:4px; font-size:13px; }}
  .fname {{ margin:10px 0 4px; font:13px ui-monospace,Menlo,monospace; color:var(--muted); }}
  .sr {{ position:absolute; left:-9999px; }}
  a {{ color:inherit; }}
  footer {{ margin-top:28px; }}
</style>
</head>
<body>
<main>
  <h1>Strue</h1>
  <p class="lede">Developer setup recipes, run from an empty folder on Linux, macOS and Windows
  every day. Each result is signed by the machine that produced it.</p>
  <div class="stats">
    <span><strong>{esc(headline)}</strong></span>
    <span class="muted">{when}</span>
    <span class="muted"><a href="api/latest.json">JSON</a> ·
      <a href="https://github.com/{esc(repo)}">Source and full history</a></span>
  </div>
  <div class="scroll">
  <table>
    <thead><tr><th>Recipe</th><th>Status</th><th class="os">Linux</th><th class="os">macOS</th>
      <th class="os">Windows</th><th>Versions that ran</th><th class="num">Streak, days</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
  </div>
  <footer class="muted">
    <p>A tick links to the run that produced it. Nobody can pay for a pass: a recipe is verified
    only when its check exits cleanly on every listed system.</p>
  </footer>
</main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--recipes", default="recipes")
    parser.add_argument("--results", default="results")
    parser.add_argument("--docs", default="docs")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "OWNER/strue"))
    args = parser.parse_args()
    summary = build(Path(args.recipes), Path(args.results), Path(args.docs), args.repo)
    print(f"[strue] site built: {summary['counts']} for {summary['inspected_on']}")


if __name__ == "__main__":
    main()
