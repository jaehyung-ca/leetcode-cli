"""Keep the tool's state (``recent.json``) in sync across machines.

Two backends:

* **drive** -- a Google Drive folder via rclone.  Configure ``drive_folder_id``
  (the ID from the folder's share link) and ``rclone_remote`` (an rclone remote
  of type ``drive`` that can write to that folder).  On startup the remote
  ``recent.json`` is merged into the local one; on quit the merged list is
  uploaded.  Only the recent list is synced: solved status comes from LeetCode
  and the problem index is regenerated locally.
* **git** -- if the data directory is a git repository, commit and push it on
  quit (fallback when no Drive folder is configured).

``data_sync`` in the config forces a mode: ``"drive"``, ``"git"`` or ``"none"``.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from rich.console import Console

import config

RECENT = "recent.json"
RECENT_LIMIT = 200


# ---------------------------------------------------------------------------
# Recent list
# ---------------------------------------------------------------------------


def read_recent(path: Path) -> list[dict]:
    """``[{"slug": ..., "ts": ISO-8601}, ...]`` newest first, or []."""
    try:
        entries = json.loads(path.read_text())
    except Exception:
        return []
    out = []
    for e in entries:
        if isinstance(e, dict) and e.get("slug"):
            out.append({"slug": e["slug"], "ts": e.get("ts") or ""})
    return out


def write_recent(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps(entries[:RECENT_LIMIT], indent=1))


def merge_recent(a: list[dict], b: list[dict]) -> list[dict]:
    """Union by slug keeping the latest timestamp, newest first."""
    best: dict[str, str] = {}
    for e in a + b:
        if e["ts"] > best.get(e["slug"], ""):
            best[e["slug"]] = e["ts"]
    merged = [{"slug": s, "ts": ts} for s, ts in best.items()]
    merged.sort(key=lambda e: e["ts"], reverse=True)
    return merged[:RECENT_LIMIT]


def touch_recent(path: Path, slug: str) -> list[str]:
    """Move ``slug`` to the front with the current time; returns the slug list."""
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    entries = [e for e in read_recent(path) if e["slug"] != slug]
    entries.insert(0, {"slug": slug, "ts": now})
    write_recent(path, entries)
    return [e["slug"] for e in entries]


# ---------------------------------------------------------------------------
# Mode / backends
# ---------------------------------------------------------------------------


def sync_mode(data_dir: Path) -> str:
    mode = config.get_config("data_sync")
    if mode in ("drive", "git", "none"):
        return mode
    if config.get_config("tui_git_sync", True) is False:
        return "none"
    if config.get_config("drive_folder_id"):
        return "drive"
    if (data_dir / ".git").exists():
        return "git"
    return "none"


def _rclone_target() -> str | None:
    folder = config.get_config("drive_folder_id")
    remote = config.get_config("rclone_remote")
    if not folder or not remote:
        return None
    return f"{remote},root_folder_id={folder}:"


def _rclone(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["rclone", "--quiet", "--retries", "2", "--low-level-retries", "3", *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout,
    )


def _drive_ready(out: Console) -> str | None:
    target = _rclone_target()
    if not target:
        out.print("[yellow]Drive sync needs drive_folder_id and rclone_remote in the config.[/yellow]")
        return None
    if not shutil.which("rclone"):
        out.print("[yellow]rclone not found; Drive sync skipped.[/yellow]")
        return None
    return target


# Google Drive's by-name lookup (used by ``rclone copyto remote:file``) runs on a
# search index that lags behind writes, so a file that exists is sometimes
# reported missing -- and an upload could then create a duplicate.  Directory
# listings are consistent, so both directions use ``rclone copy`` between
# directories with an include filter.


def drive_pull(data_dir: Path, out: Console) -> bool:
    """Merge the remote recent list into the local file."""
    target = _drive_ready(out)
    if not target:
        return False
    with tempfile.TemporaryDirectory(prefix="lc-sync-") as tmp:
        r = _rclone("copy", target, tmp, "--include", RECENT)
        if r.returncode != 0:
            out.print(f"[red]Drive pull failed:[/red] {r.stdout.strip()}")
            return False
        remote_copy = Path(tmp) / RECENT
        if not remote_copy.exists():
            out.print("[dim]No recent list on Drive yet.[/dim]")
            return True
        remote = read_recent(remote_copy)
    local_path = data_dir / RECENT
    merged = merge_recent(read_recent(local_path), remote)
    write_recent(local_path, merged)
    out.print(f"[dim]Drive: merged {len(remote)} remote entries.[/dim]")
    return True


def drive_push(data_dir: Path, out: Console) -> bool:
    target = _drive_ready(out)
    if not target:
        return False
    if not (data_dir / RECENT).exists():
        out.print("[dim]Nothing to upload.[/dim]")
        return True
    # Merge once more so a list changed elsewhere meanwhile is not clobbered.
    if not drive_pull(data_dir, out):
        return False
    r = _rclone("copy", str(data_dir), target, "--include", RECENT)
    if r.returncode != 0:
        out.print(f"[red]Drive push failed:[/red] {r.stdout.strip()}")
        return False
    out.print("[green]Uploaded recent list to Drive.[/green]")
    return True


def git_sync(directory: Path, out: Console) -> bool:
    """Commit and push ``directory``. Returns False when something failed."""

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(directory), *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        out.print(f"[yellow]{directory} is not a git repository; skipping sync.[/yellow]")
        return True

    git("add", "-A")
    changes = git("status", "--porcelain").stdout.strip()
    if changes:
        n = len(changes.splitlines())
        msg = f"lc sync {dt.datetime.now():%Y-%m-%d %H:%M} ({n} file{'s' if n != 1 else ''})"
        r = git("commit", "-q", "-m", msg)
        if r.returncode != 0:
            out.print(f"[red]git commit failed:[/red]\n{r.stdout.strip()}")
            return False
        out.print(f"[green]Committed:[/green] {msg}")
    else:
        out.print("[dim]Nothing to commit.[/dim]")

    if not git("remote").stdout.strip():
        out.print("[dim]No git remote; skipping push.[/dim]")
        return True
    out.print("[dim]Pushing…[/dim]")
    r = git("push", "-q")
    if r.returncode != 0:
        out.print(f"[red]git push failed:[/red]\n{r.stdout.strip()}")
        return False
    out.print("[green]Pushed.[/green]")
    return True


# ---------------------------------------------------------------------------
# Entry points used by the TUI
# ---------------------------------------------------------------------------


def startup_sync(data_dir: Path, out: Console) -> bool:
    if sync_mode(data_dir) == "drive":
        return drive_pull(data_dir, out)
    return True


def quit_sync(data_dir: Path, out: Console) -> bool:
    mode = sync_mode(data_dir)
    if mode == "drive":
        return drive_push(data_dir, out)
    if mode == "git":
        return git_sync(data_dir, out)
    return True
