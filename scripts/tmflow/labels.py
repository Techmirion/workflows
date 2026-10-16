"""The one label set of git.md 13, applied to a repo with `gh api`."""
from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

LABELS_FILE = Path(__file__).resolve().parents[2] / "labels.json"


def parse_pages(text: str) -> list[dict]:
    """The items of `gh api --paginate` output, one JSON array per page, concatenated.

    No `--slurp`: gh 2.45, the boxes' version, does not have it (#4). An array of
    pages, as `--slurp` prints it, is read too.
    """
    items, dec, i, text = [], json.JSONDecoder(), 0, text or ""
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i == len(text):
            return items
        page, i = dec.raw_decode(text, i)
        if page and all(isinstance(x, list) for x in page):
            items.extend(x for sub in page for x in sub)
        else:
            items.extend(page)


def load_labels(path: str | Path | None = None) -> list[dict]:
    data = json.loads(Path(path or LABELS_FILE).read_text(encoding="utf-8"))
    for lab in data:
        lab["color"] = lab["color"].lstrip("#").lower()
    return data


def plan_labels(desired: list[dict], current: list[dict], prune: bool = False) -> list[tuple]:
    """Operations that make `current` equal to `desired`. Names compare case-insensitively."""
    cur = {c["name"].lower(): c for c in current}
    want = {d["name"].lower() for d in desired}
    ops = []
    for d in desired:
        c = cur.get(d["name"].lower())
        if c is None:
            ops.append(("create", d))
        elif (c["name"] != d["name"] or c.get("color", "").lower() != d["color"]
              or (c.get("description") or "") != d.get("description", "")):
            ops.append(("update", c["name"], d))
    if prune:
        for c in current:
            if c["name"].lower() not in want:
                ops.append(("delete", c["name"]))
    return ops


def gh_commands(repo: str, ops: list[tuple]) -> list[list[str]]:
    cmds = []
    for op in ops:
        if op[0] == "create":
            d = op[1]
            cmds.append(["gh", "api", "-X", "POST", f"repos/{repo}/labels", "-f", f"name={d['name']}",
                         "-f", f"color={d['color']}", "-f", f"description={d.get('description', '')}"])
        elif op[0] == "update":
            name, d = op[1], op[2]
            cmds.append(["gh", "api", "-X", "PATCH", f"repos/{repo}/labels/{urllib.parse.quote(name, safe='')}",
                         "-f", f"new_name={d['name']}", "-f", f"color={d['color']}",
                         "-f", f"description={d.get('description', '')}"])
        elif op[0] == "delete":
            cmds.append(["gh", "api", "-X", "DELETE", f"repos/{repo}/labels/{urllib.parse.quote(op[1], safe='')}"])
    return cmds
