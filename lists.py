"""Your own problem lists, each problem with a note (``lists.json`` in the data dir).

    {
      "redo": {
        "two-sum": {"id": "1", "title": "Two Sum", "note": "hash map, one pass",
                    "added": "2026-10-02T10:00:00+00:00", "ts": "2026-10-02T10:00:00+00:00"},
        "lru-cache": {"removed": true, "ts": "..."}
      }
    }

``ts`` is the time of the last change.  Removing a problem keeps a tombstone
(``removed``) so the removal survives a merge with another machine's copy:
``merge_lists`` keeps the newest entry per (list, slug).  The file is synced
through Google Drive or git together with the recent list (see sync.py).

Every change re-reads the file, so the TUI and ``lc save`` can run at once.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

LISTS = "lists.json"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def read_lists(path: Path) -> dict[str, dict[str, dict]]:
    """Raw contents including tombstones, or {}."""
    try:
        raw = json.loads(path.read_text())
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, dict]] = {}
    for name, items in raw.items():
        if isinstance(items, dict):
            out[str(name)] = {
                str(slug): e for slug, e in items.items() if isinstance(e, dict) and e.get("ts")
            }
    return out


def write_lists(path: Path, data: dict[str, dict[str, dict]]) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False))


def merge_lists(a: dict, b: dict) -> dict:
    """Union of both, keeping the newest entry per (list, slug)."""
    merged: dict[str, dict[str, dict]] = {}
    for data in (a, b):
        for name, items in data.items():
            dest = merged.setdefault(name, {})
            for slug, e in items.items():
                if e.get("ts", "") > dest.get(slug, {}).get("ts", ""):
                    dest[slug] = e
    return merged


def live_lists(data: dict) -> dict[str, list[dict]]:
    """``{name: [entry with "slug", ...]}`` without tombstones, oldest first.

    Lists whose problems were all removed are kept (empty) so the name stays
    available.
    """
    out = {}
    for name in sorted(data, key=str.lower):
        items = [
            {**e, "slug": slug} for slug, e in data[name].items() if not e.get("removed")
        ]
        items.sort(key=lambda e: e.get("added") or e["ts"])
        out[name] = items
    return out


def lists_for(data: dict, slug: str) -> list[tuple[str, str]]:
    """``[(list name, note), ...]`` for the lists that contain ``slug``."""
    return [
        (name, e.get("note", ""))
        for name, items in sorted(data.items(), key=lambda kv: kv[0].lower())
        if (e := items.get(slug)) and not e.get("removed")
    ]


def save(path: Path, name: str, slug: str, note: str, pid: str = "", title: str = "") -> bool:
    """Add ``slug`` to list ``name`` (creating it) or update its note.

    Returns True when the problem was newly added.
    """
    name = name.strip()
    if not name:
        raise ValueError("list name is empty")
    data = read_lists(path)
    items = data.setdefault(name, {})
    old = items.get(slug)
    added = not old or old.get("removed")
    now = _now()
    items[slug] = {
        "id": pid or (old or {}).get("id", ""),
        "title": title or (old or {}).get("title", ""),
        "note": note.strip(),
        "added": now if added else old.get("added", now),
        "ts": now,
    }
    write_lists(path, data)
    return bool(added)


def remove(path: Path, name: str, slug: str) -> bool:
    """Remove ``slug`` from list ``name``; False if it was not there."""
    data = read_lists(path)
    e = data.get(name, {}).get(slug)
    if not e or e.get("removed"):
        return False
    data[name][slug] = {"removed": True, "ts": _now()}
    write_lists(path, data)
    return True


def find_list(data: dict, name: str) -> str | None:
    """Exact name (case-insensitive), then a unique prefix."""
    key = name.strip().lower()
    names = list(data)
    exact = [n for n in names if n.lower() == key]
    if exact:
        return exact[0]
    prefix = [n for n in names if n.lower().startswith(key)]
    return prefix[0] if len(prefix) == 1 else None
