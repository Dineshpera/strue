#!/usr/bin/env python3
"""Strue MCP server: lets a coding agent ask what works today.

Reads the public record (docs/api/latest.json) published by the daily
inspection. Set STRUE_RECORD to your GitHub Pages URL or to a local file:

    STRUE_RECORD=https://OWNER.github.io/strue/api/latest.json python mcp-server/server.py

Requires: pip install "mcp>=2"
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

from mcp.server.mcpserver import MCPServer

RECORD = os.environ.get("STRUE_RECORD", "docs/api/latest.json")
CACHE_SECONDS = 600
_cache: dict = {"at": 0.0, "data": None}

server = MCPServer(
    "strue",
    instructions=(
        "Strue holds developer setup recipes that are executed daily on Linux, macOS "
        "and Windows. Before writing install or setup commands from memory, search here "
        "and prefer a recipe whose status is 'verified'. Report the inspection date and "
        "the versions that ran. Treat 'avoid' entries as commands confirmed broken."
    ),
)


def record() -> dict:
    if _cache["data"] is None or time.time() - _cache["at"] > CACHE_SECONDS:
        if RECORD.startswith(("http://", "https://")):
            request = urllib.request.Request(RECORD, headers={"User-Agent": "strue-mcp"})
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
        else:
            data = json.loads(Path(RECORD).read_text(encoding="utf-8"))
        _cache.update(at=time.time(), data=data)
    return _cache["data"]


def brief(recipe: dict) -> dict:
    return {
        "id": recipe["id"],
        "title": recipe["title"],
        "intent": recipe["intent"],
        "status": recipe["status"],
        "inspected_on": recipe["inspected_on"],
        "tags": recipe["tags"],
    }


@server.tool()
def search_recipes(query: str = "") -> list[dict]:
    """Find recipes by keyword (tool, framework or task). Empty query lists all.

    Returns id, title, status ('verified', 'failing' or 'unknown') and the date
    of the last inspection. Call get_recipe with an id for the full steps.
    """
    words = [w for w in query.lower().split() if w]
    scored = []
    for recipe in record()["recipes"]:
        haystack = " ".join(
            [recipe["id"], recipe["title"], recipe["intent"], " ".join(recipe["tags"])]
        ).lower()
        score = sum(word in haystack for word in words)
        if score or not words:
            scored.append((score, recipe))
    scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    return [brief(recipe) for _, recipe in scored]


@server.tool()
def get_recipe(recipe_id: str) -> dict:
    """Return one recipe: files to create, commands to run, the check, the versions
    that ran on each operating system, outdated commands to avoid, and a link to the
    signed result. If status is not 'verified', the failure detail is included."""
    for recipe in record()["recipes"]:
        if recipe["id"] == recipe_id:
            return recipe
    return {
        "error": f"No recipe with id '{recipe_id}'.",
        "known_ids": [r["id"] for r in record()["recipes"]],
    }


@server.tool()
def failing_today() -> list[dict]:
    """List recipes whose latest inspection failed, with the failing step per system."""
    out = []
    for recipe in record()["recipes"]:
        if recipe["status"] == "failing":
            out.append({
                **brief(recipe),
                "failures": {
                    label: item["failed_step"]
                    for label, item in recipe["os"].items()
                    if item["status"] != "pass"
                },
            })
    return out


if __name__ == "__main__":
    server.run()
