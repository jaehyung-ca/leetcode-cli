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

``setup_drive`` writes the Drive settings interactively; the TUI runs it once on
first launch when no mode is configured (declining stores ``data_sync: "none"``).
"""

from __future__ import annotations

import datetime as dt
import json
import re
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
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=timeout,
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
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
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
# First-run setup
# ---------------------------------------------------------------------------

DEFAULT_FOLDER = "leetcode-cli"


def needs_setup(data_dir: Path) -> bool:
    """True when no sync mode was chosen or implied by the config/data dir."""
    if config.get_config("data_sync") in ("drive", "git", "none"):
        return False
    if config.get_config("tui_git_sync", True) is False:
        return False
    return sync_mode(data_dir) == "none"


def _drive_remotes() -> list[str]:
    r = _rclone("listremotes", "--long", timeout=15)
    if r.returncode != 0:
        return []
    remotes = []
    for line in r.stdout.splitlines():
        name, _, kind = line.partition(":")
        if kind.strip() == "drive":
            remotes.append(name.strip())
    return remotes


def _parse_folder_id(text: str) -> str:
    """Folder ID from a share link (``.../folders/<id>?usp=...``) or a bare ID."""
    m = re.search(r"/folders/([\w-]+)", text) or re.search(r"[?&]id=([\w-]+)", text)
    return m.group(1) if m else text.strip()


def _default_folder_id(remote: str, out: Console) -> str | None:
    """ID of ``DEFAULT_FOLDER`` at the remote's root, creating it if needed."""

    def lookup() -> str | None:
        r = _rclone("lsjson", f"{remote}:", "--dirs-only", "--include", f"/{DEFAULT_FOLDER}/")
        if r.returncode != 0:
            out.print(f"[red]Listing {remote}: failed:[/red] {r.stdout.strip()}")
            return None
        try:
            dirs = json.loads(r.stdout)
        except ValueError:
            return None
        return next((d.get("ID") for d in dirs if d.get("Name") == DEFAULT_FOLDER), None)

    folder = lookup()
    if folder:
        out.print(f"Using existing folder [bold]{DEFAULT_FOLDER}[/bold] on {remote}:")
        return folder
    r = _rclone("mkdir", f"{remote}:{DEFAULT_FOLDER}")
    if r.returncode != 0:
        out.print(f"[red]Creating {remote}:{DEFAULT_FOLDER} failed:[/red] {r.stdout.strip()}")
        return None
    out.print(f"Created folder [bold]{DEFAULT_FOLDER}[/bold] on {remote}:")
    return lookup()


def setup_drive(data_dir: Path, out: Console, auto: bool = False) -> bool:
    """Ask for an rclone remote and Drive folder and save them to the config.

    Returns True when Drive sync was configured.  Declining stores
    ``data_sync: "none"`` so the question is not asked again.  With ``auto``
    (first launch) a missing rclone or Drive remote is skipped silently.
    """
    from rich.prompt import Confirm, Prompt

    if not shutil.which("rclone"):
        if auto:
            return False
        out.print("[yellow]rclone not found; install it to sync the recent list through Google Drive.[/yellow]")
        return False
    remotes = _drive_remotes()
    if not remotes:
        if auto:
            return False
        out.print("[yellow]No rclone remote of type 'drive' found; create one with `rclone config`.[/yellow]")
        return False

    out.print("[bold]Sync the recent list across machines through Google Drive?[/bold]")
    if not Confirm.ask("Set up Drive sync", default=True, console=out):
        config.update_config("data_sync", "none")
        out.print("[dim]Skipped. Set data_sync in the config or run `lc sync-setup` later.[/dim]")
        return False

    if len(remotes) == 1:
        remote = remotes[0]
        out.print(f"rclone remote: [bold]{remote}[/bold]")
    else:
        remote = Prompt.ask("rclone remote", choices=remotes, default=remotes[0], console=out)

    answer = Prompt.ask(
        f"Drive folder share link or ID (empty: use/create '{DEFAULT_FOLDER}' in {remote}:)",
        default="", show_default=False, console=out,
    )
    folder = _parse_folder_id(answer) if answer.strip() else _default_folder_id(remote, out)
    if not folder:
        return False

    r = _rclone("lsjson", f"{remote},root_folder_id={folder}:", "--max-depth", "1", timeout=30)
    if r.returncode != 0:
        out.print(f"[red]Cannot access folder {folder} via {remote}:[/red] {r.stdout.strip()}")
        return False

    cfg = config.load_config()
    cfg.update({"rclone_remote": remote, "drive_folder_id": folder, "data_sync": "drive"})
    config.save_config(cfg)
    out.print(f"[green]Drive sync configured[/green] (folder {folder}). Use this ID on your other machines.")
    return drive_pull(data_dir, out)


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
