import io
import os
import subprocess
import shutil
import hashlib
import typer
from rich.console import Console
from rich.table import Table
from rich.markdown import Markdown
from pathlib import Path
from urllib.parse import urlparse
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("leetcode-cli")
except PackageNotFoundError:
    __version__ = "unknown"

from auth import extract_cookies
import api
import core
from core import resolve_slug
from sets import (
    get_all_sets,
    find_set,
    level_label,
    resolve_set_problems,
    set_progress,
    DIFF_NAME,
)

app = typer.Typer(help="CLI tool for LeetCode")
console = Console()


def is_wezterm_session() -> bool:
    return (
        os.environ.get("TERM_PROGRAM") == "WezTerm"
        or bool(os.environ.get("WEZTERM_PANE"))
        or bool(os.environ.get("WEZTERM_EXECUTABLE"))
    )


def get_image_dimensions(image_path: str) -> tuple[int, int] | None:
    """Get (width, height) of an image using 'identify'."""
    if not shutil.which("identify"):
        return None
    try:
        result = subprocess.run(
            ["identify", "-format", "%w %h", image_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.split()
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None


def render_image_with_wezterm(image_path: str, width: str | None = None) -> str | None:
    """Render an image inline when running inside WezTerm."""
    if not shutil.which("wezterm"):
        return None

    # Disable images in TMUX as they are unstable
    if os.environ.get("TMUX"):
        return None

    if not is_wezterm_session():
        return None

    try:
        # width can be a number (cells) or a string like "100px"
        w_arg = width if width else "auto"
        args = ["wezterm", "imgcat", "--width", w_arg, image_path]

        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="ignore")
    except Exception:
        pass
    return None


def get_image_rendering(image_path: str, prefer_text: bool = False) -> str | None:
    """Try to render an image using wezterm imgcat."""
    if prefer_text:
        return None

    # Constants for estimation
    CELL_W_PX = 10
    MAX_W_CELLS = max(20, console.width - 4)
    MAX_W_PX = MAX_W_CELLS * CELL_W_PX

    dims = get_image_dimensions(image_path)
    
    width_arg = str(MAX_W_CELLS)

    if dims:
        img_w, _ = dims
        if img_w < MAX_W_PX:
            width_arg = f"{img_w}px"
        else:
            width_arg = str(MAX_W_CELLS)
    
    return render_image_with_wezterm(image_path, width=width_arg)


def render_image(image_path: str) -> bool:
    """Try various methods to render an image in the terminal."""
    res = get_image_rendering(image_path)
    if res:
        console.file.write(res)
        console.file.flush()
        return True
    return False


def download_image_to_tempfile(url: str) -> tuple[str | None, dict]:
    import requests
    import tempfile

    diagnostics = {
        "url": url,
        "status_code": None,
        "content_type": None,
        "content_length_header": None,
        "downloaded_bytes": 0,
        "final_url": None,
        "sha256": None,
        "temp_path": None,
    }

    resp = requests.get(url, timeout=10)
    diagnostics["status_code"] = resp.status_code
    diagnostics["content_type"] = resp.headers.get("content-type")
    diagnostics["content_length_header"] = resp.headers.get("content-length")
    diagnostics["final_url"] = resp.url

    if resp.status_code != 200:
        return None, diagnostics

    suffix = Path(urlparse(resp.url).path).suffix or ".img"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
        tf.write(resp.content)
        tf.flush()
        diagnostics["downloaded_bytes"] = len(resp.content)
        diagnostics["sha256"] = hashlib.sha256(resp.content).hexdigest()
        diagnostics["temp_path"] = tf.name
        return tf.name, diagnostics


def debug_render_image(url: str):
    console.print(f"[bold]Image debug[/bold] {url}")
    console.print(f"TERM_PROGRAM={os.environ.get('TERM_PROGRAM')}")
    console.print(f"TERM={os.environ.get('TERM')}")
    console.print(f"WEZTERM_PANE={os.environ.get('WEZTERM_PANE')}")
    console.print(f"WEZTERM_EXECUTABLE={os.environ.get('WEZTERM_EXECUTABLE')}")
    console.print(f"TMUX={os.environ.get('TMUX')}")
    console.print(f"which wezterm={shutil.which('wezterm')}")
    console.print(f"is_wezterm_session={is_wezterm_session()}")

    image_path = None
    try:
        image_path, diagnostics = download_image_to_tempfile(url)
        for key in [
            "status_code",
            "content_type",
            "content_length_header",
            "downloaded_bytes",
            "final_url",
            "sha256",
            "temp_path",
        ]:
            console.print(f"{key}={diagnostics[key]}")

        if not image_path:
            console.print("[red]Download failed before rendering.[/red]")
            return

        console.print("[bold]Testing render_image()...[/bold]")
        success = render_image(image_path)
        console.print(f"render_image_success={success}")

        # Test the direct wezterm rendering component
        res = render_image_with_wezterm(image_path)
        if res:
            console.print("[bold]Testing render_image_with_wezterm() output...[/bold]")
            console.file.write(res)
            console.file.flush()
            console.print("\n[bold]Image rendered above.[/bold]")
        else:
            console.print("[red]render_image_with_wezterm() returned None[/red]")
            
    except subprocess.TimeoutExpired as exc:
        console.print(
            f"[red]wezterm_imgcat_timeout={
                exc.timeout}s after command start[/red]"
        )
    except Exception as exc:
        console.print(f"[red]debug_exception={
                      type(exc).__name__}: {exc}[/red]")
    finally:
        if image_path and os.path.exists(image_path):
            os.unlink(image_path)


def version_callback(value: bool):
    if value:
        console.print(f"leetcode-cli version {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """CLI tool for LeetCode. Run with no command to open the TUI."""
    if ctx.invoked_subcommand is None:
        from tui import launch_tui

        launch_tui()


@app.command()
@app.command("v", hidden=True)
def version():
    """Show the version of leetcode-cli."""
    console.print(f"leetcode-cli version {__version__}")


@app.command()
@app.command("a", hidden=True)
def auth():
    """Extract browser cookies for LeetCode authentication."""
    extract_cookies()


@app.command()
@app.command("tg", hidden=True)
def tags():
    """List available problem tags."""
    tags_data = api.get_tags()
    if not tags_data:
        console.print("[red]Failed to fetch tags.[/red]")
        return
    table = Table(title="LeetCode Tags")
    table.add_column("Name", style="cyan")
    table.add_column("Slug", style="magenta")
    for t in tags_data:
        table.add_row(t.get("name"), t.get("slug"))
    console.print(table)


# ---------------------------------------------------------------------------
# Problem sets
# ---------------------------------------------------------------------------


def _fetch_problem_index() -> tuple[dict, dict] | None:
    """Return (by_slug, by_id) maps of every LeetCode problem, or None if offline."""
    try:
        questions = api.get_all_questions()
    except Exception as e:
        console.print(
            f"[yellow]Could not fetch problem status from LeetCode ({e}). "
            "Showing offline data.[/yellow]"
        )
        return None
    by_slug = {q["titleSlug"]: q for q in questions if q.get("titleSlug")}
    by_id = {
        str(q["frontendQuestionId"]): q
        for q in questions
        if q.get("frontendQuestionId")
    }
    return by_slug, by_id


_resolve_set_problems = resolve_set_problems
_set_progress = set_progress


def _progress_bar(pct: float, width: int = 20) -> str:
    filled = int(round(width * pct / 100.0))
    _, color = level_label(pct)
    return f"[{color}]{'█' * filled}[/{color}][dim]{'░' * (width - filled)}[/dim]"


def _diff_markup(diff: str) -> str:
    name = DIFF_NAME.get(diff, diff)
    color = {"E": "green", "M": "yellow", "H": "red"}.get(diff, "white")
    return f"[{color}]{name}[/{color}]"


@app.command("sets")
@app.command("ss", hidden=True)
def list_sets(
    offline: bool = typer.Option(
        False, "--offline", help="Do not contact LeetCode; skip solved status"
    ),
):
    """List problem sets by skill/topic with your progress in each."""
    all_sets = get_all_sets()
    index = None if offline else _fetch_problem_index()

    table = Table(title="Problem Sets")
    table.add_column("Set", style="cyan", no_wrap=True)
    table.add_column("Description", overflow="ellipsis", max_width=44)
    table.add_column("Solved", justify="right", no_wrap=True, min_width=6)
    table.add_column("E/M/H", justify="center", style="dim", no_wrap=True, min_width=13)
    table.add_column("Progress", no_wrap=True, min_width=25)
    table.add_column("Level", no_wrap=True, min_width=9)

    for name, body in all_sets.items():
        resolved = _resolve_set_problems(body["problems"], index)
        prog = _set_progress(resolved)
        bd = prog["by_diff"]
        label, color = level_label(prog["pct"])
        emh = "/".join(f"{bd[d][0]}:{bd[d][1]}" for d in ("E", "M", "H"))
        display_name = f"{name} [dim](user)[/dim]" if body.get("user") else name
        table.add_row(
            display_name,
            body["description"],
            f"{prog['solved']}/{prog['total']}",
            emh,
            f"{_progress_bar(prog['pct'])} {prog['pct']:3.0f}%",
            f"[{color}]{label}[/{color}]" if index else "[dim]-[/dim]",
        )
    console.print(table)
    console.print(
        "[dim]Progress is weighted: Easy=1, Medium=2, Hard=3. "
        "E/M/H shows solved:total per difficulty. "
        "Use `lc set <name>` to see the problems.[/dim]"
    )


@app.command("set")
@app.command("s", hidden=True)
def show_set(
    name: str = typer.Argument(..., help="Set name (prefix is fine, e.g. 'dp')"),
    unsolved: bool = typer.Option(
        False, "-u", "--unsolved", help="Only show problems you have not solved"
    ),
    diff: str = typer.Option(
        None, "-d", "--diff", help="Filter by difficulty: easy, medium, hard"
    ),
    random_pick: bool = typer.Option(
        False, "-r", "--random", help="Open a random unsolved problem from the set"
    ),
    next_pick: bool = typer.Option(
        False, "-n", "--next", help="Open the first unsolved problem from the set"
    ),
    offline: bool = typer.Option(
        False, "--offline", help="Do not contact LeetCode; skip solved status"
    ),
):
    """Show the problems in a set, with solved status and progress."""
    all_sets = get_all_sets()
    found = find_set(name, all_sets)
    if not found:
        key = name.strip().lower()
        candidates = [k for k in all_sets if key in k]
        if candidates:
            console.print(
                f"[red]'{name}' is ambiguous.[/red] Did you mean: "
                + ", ".join(f"[cyan]{c}[/cyan]" for c in candidates)
            )
        else:
            console.print(f"[red]No set named '{name}'.[/red] Available sets:")
            console.print("  " + ", ".join(all_sets.keys()))
        return
    set_name, body = found

    index = None if offline else _fetch_problem_index()
    resolved = _resolve_set_problems(body["problems"], index)
    prog = _set_progress(resolved)

    rows = resolved
    if diff:
        d = diff.strip().lower()[:1].upper()
        rows = [p for p in rows if p["diff"] == d]
    if unsolved or random_pick or next_pick:
        rows = [p for p in rows if p["status"] != "ac"]

    if random_pick or next_pick:
        candidates = [p for p in rows if p["slug"] and not p["paid"]]
        if not candidates:
            console.print("[green]Nothing left to solve here. Nice![/green]")
            return
        if random_pick:
            import random as rand

            chosen = rand.choice(candidates)
        else:
            chosen = candidates[0]
        console.print(
            f"[dim]{set_name}: picked [cyan]{chosen['slug']}[/cyan] "
            f"({len(candidates)} unsolved remaining)[/dim]\n"
        )
        q = api.get_question_detail(chosen["slug"])
        if not q:
            console.print("[red]Problem not found.[/red]")
            return
        _display_question(q)
        return

    label, color = level_label(prog["pct"])
    bd = prog["by_diff"]
    header = f"{set_name} — {body['description']}"
    table = Table(title=header)
    table.add_column("Status", justify="center")
    table.add_column("ID", style="dim", justify="right")
    table.add_column("Title")
    table.add_column("Difficulty")
    table.add_column("Slug", style="magenta")

    for p in rows:
        if p["status"] == "ac":
            mark = "[green]✔[/green]"
        elif p["status"] == "notac":
            mark = "[red]✘[/red]"
        else:
            mark = ""
        title = p["title"]
        if p["paid"]:
            title += " [yellow]🔒[/yellow]"
        table.add_row(mark, p["id"], title, _diff_markup(p["diff"]), p["slug"] or "")
    console.print(table)

    if index:
        console.print(
            f"Solved [bold]{prog['solved']}/{prog['total']}[/bold]  "
            f"(E {bd['E'][0]}/{bd['E'][1]}, M {bd['M'][0]}/{bd['M'][1]}, "
            f"H {bd['H'][0]}/{bd['H'][1]})  "
            f"{_progress_bar(prog['pct'])} {prog['pct']:.0f}%  "
            f"[{color}]{label}[/{color}]"
        )
    else:
        console.print(f"[dim]{prog['total']} problems (offline, no status)[/dim]")
    if unsolved and not rows:
        if diff:
            console.print(f"[green]No unsolved {diff.lower()} problems left in this set.[/green]")
        else:
            console.print("[green]Everything in this set is solved.[/green]")


@app.command("list")
@app.command("l", hidden=True)
def list_problems(
    tag: str = typer.Option(None, "-t", "--tag", help="Filter by tag"),
    diff: str = typer.Option(None, "-d", "--diff",
                             help="Filter by difficulty"),
    search: str = typer.Option(None, "-s", "--search", help="Search query"),
    limit: int = typer.Option(50, "-m", "--limit", help="Max results"),
    high: str = typer.Option(
        None, "--high", help="Sort descending by: ac or freq"),
    low: str = typer.Option(
        None, "--low", help="Sort ascending by: ac or freq"),
):
    """List problems with optional filters."""
    filters = {}
    if tag:
        filters["tags"] = [tag]
    if diff:
        # 1: Easy, 2: Medium, 3: Hard
        d_map = {"easy": "EASY", "medium": "MEDIUM", "hard": "HARD"}
        filters["difficulty"] = d_map.get(diff.lower(), "EASY")
    if search:
        filters["searchKeywords"] = search

    if high:
        s = high.lower()
        if s in ["ac", "ac_rate", "acceptance"]:
            filters["orderBy"] = "AC_RATE"
            filters["sortOrder"] = "DESCENDING"
        elif s in ["freq", "frequency"]:
            filters["orderBy"] = "FREQUENCY"
            filters["sortOrder"] = "DESCENDING"
    elif low:
        s = low.lower()
        if s in ["ac", "ac_rate", "acceptance"]:
            filters["orderBy"] = "AC_RATE"
            filters["sortOrder"] = "ASCENDING"
        elif s in ["freq", "frequency"]:
            filters["orderBy"] = "FREQUENCY"
            filters["sortOrder"] = "ASCENDING"

    data = api.get_questions_list(limit=limit, filters=filters)
    questions = data.get("questions", [])

    table = Table(title="LeetCode Problems")
    table.add_column("Status", justify="center")
    table.add_column("ID", style="dim")
    table.add_column("Title")
    table.add_column("Difficulty")
    table.add_column("Acceptance")

    for q in questions:
        status_val = q.get("status")
        if status_val == "ac":
            status_mark = "[green]✔[/green]"
        elif status_val == "notac":
            status_mark = "[red]✘[/red]"
        else:
            status_mark = ""

        diff_color = (
            "green"
            if q["difficulty"] == "Easy"
            else "yellow"
            if q["difficulty"] == "Medium"
            else "red"
        )
        ac_rate = str(round(q.get("acRate", 0), 2)) + "%"
        table.add_row(
            status_mark,
            q["frontendQuestionId"],
            q["title"],
            f"[{diff_color}]{q['difficulty']}[/{diff_color}]",
            ac_rate,
        )
    console.print(table)


@app.command("random")
@app.command("r", hidden=True)
def random_problem(
    tag: str = typer.Option(None, "-t", "--tag", help="Filter by tag"),
    diff: str = typer.Option(None, "-d", "--diff",
                             help="Filter by difficulty"),
):
    """Pick a random problem, optionally filtered."""
    import random as rand

    filters = {}
    if tag:
        filters["tags"] = [tag]
    if diff:
        d_map = {"easy": "EASY", "medium": "MEDIUM", "hard": "HARD"}
        filters["difficulty"] = d_map.get(diff.lower(), "EASY")

    data = api.get_questions_list(limit=1, filters=filters, category_slug="algorithms")
    total = data.get("total", 0)
    category_slug = "algorithms"

    if total == 0:
        data = api.get_questions_list(limit=1, filters=filters)
        total = data.get("total", 0)
        category_slug = ""

    if total == 0:
        console.print("[red]No problems found matching criteria.[/red]")
        return

    # Try up to 10 times to find a Python problem
    for _ in range(10):
        skip = rand.randint(0, total - 1)
        data = api.get_questions_list(
            limit=1, skip=skip, filters=filters, category_slug=category_slug)
        q_list = data.get("questions", [])
        if not q_list:
            continue

        slug = q_list[0].get("titleSlug")
        detail = api.get_question_detail(slug)
        if detail:
            snippets = detail.get("codeSnippets", [])
            if any(s.get("langSlug") in ["python", "python3"] for s in snippets):
                _display_question(detail)
                return

    console.print(
        "[red]Could not find a Python problem after multiple attempts.[/red]")



def pager(content: str):
    """Print content directly to the terminal."""
    console.file.write(content)
    console.file.flush()


def _display_question(q: dict):
    """Render question details to terminal."""
    parts, images = core.problem_markdown_parts(q)

    output = io.StringIO()
    # Use a temporary console to render into our StringIO
    temp_console = Console(file=output, force_terminal=True, color_system="truecolor")

    temp_console.print(f"{core.problem_title_markup(q)}\n{core.problem_url(q)}\n")

    for i, part in enumerate(parts):
        if part.strip():
            temp_console.print(Markdown(part))
        if i < len(images):
            url = images[i]
            if url:
                try:
                    image_path, _ = download_image_to_tempfile(url)
                    if not image_path:
                        temp_console.print(f"[dim]Image: {url}[/dim]")
                        continue

                    try:
                        # Use wezterm imgcat for high-quality rendering if possible
                        res = get_image_rendering(image_path, prefer_text=False)
                        if res:
                            output.write(res)
                            if not res.endswith("\n"):
                                output.write("\n")
                        else:
                            temp_console.print(f"[dim]Image: {url}[/dim]")
                    except Exception:
                        temp_console.print(f"[dim]Image: {url}[/dim]")
                    finally:
                        if image_path and os.path.exists(image_path):
                            os.unlink(image_path)
                except Exception:
                    temp_console.print(f"[dim]Image: {url}[/dim]")

    pager(output.getvalue())


@app.command()
@app.command("p", hidden=True)
def pick(slug: str):
    """Show details of a specific problem."""
    slug = resolve_slug(slug)
    q = api.get_question_detail(slug)
    if not q:
        console.print("[red]Problem not found.[/red]")
        return

    _display_question(q)


@app.command("debug-image")
def debug_image(
    url: str = typer.Argument(
        "https://assets.leetcode.com/uploads/2025/11/17/tree2.png"
    ),
):
    """Verbose debugging for inline image rendering."""
    debug_render_image(url)


@app.command()
@app.command("e", hidden=True)
def edit(slug: str):
    """Generate boilerplate for a problem and open in editor."""
    slug = resolve_slug(slug)
    q = api.get_question_detail(slug)
    if not q:
        console.print("[red]Problem not found.[/red]")
        return

    if not core.python_snippet(q):
        console.print("[red]Python3 snippet not found for this problem.[/red]")
        return

    file_name = core.solution_path(q)
    overwrite = False
    if os.path.exists(file_name):
        overwrite = typer.confirm(
            f"File {file_name} already exists. Re-initialize and overwrite it?",
            default=False,
        )

    file_name, created = core.write_solution_file(q, overwrite=overwrite)
    if created:
        console.print(f"[green]Created {file_name}[/green] [dim](scratch file, kept until reboot)[/dim]")
    else:
        console.print(f"Opening existing {file_name}...")

    editor = os.environ.get("EDITOR", "vi")
    subprocess.call([editor, file_name])


@app.command(name="exec")
@app.command("x", hidden=True)
def exec_cmd(file_path: str):
    """Submit a python file to LeetCode."""
    core.run_submit(file_path, console)


@app.command()
@app.command("t", hidden=True)
def test(file_path: str):
    """Run tests for a python file on LeetCode."""
    core.run_test(file_path, console)


def _lists_path() -> Path:
    import lists

    return core.data_dir() / lists.LISTS


def _problem_info(problem: str | None) -> dict | None:
    """{"slug", "id", "title", "status"} for an ID/slug, or the most recently opened problem."""
    import json
    import sync

    if not problem:
        recent = sync.read_recent(core.data_dir() / sync.RECENT)
        if not recent:
            console.print("[red]No recently opened problem; name one (ID or slug).[/red]")
            return None
        problem = recent[0]["slug"]
    try:
        index = json.loads((core.data_dir() / "index.json").read_text())
    except Exception:
        index = []
    for q in index:
        if problem in (q.get("titleSlug"), str(q.get("frontendQuestionId"))):
            return {
                "slug": q["titleSlug"], "id": str(q.get("frontendQuestionId") or ""),
                "title": q.get("title") or "", "status": q.get("status"),
            }
    q = api.get_question_detail(resolve_slug(problem))
    if not q:
        console.print(f"[red]Problem not found: {problem}[/red]")
        return None
    return {
        "slug": q["titleSlug"], "id": q.get("questionFrontendId") or "",
        "title": q.get("title") or "", "status": q.get("status"),
    }


@app.command("save")
@app.command("sv", hidden=True)
def save_cmd(
    list_name: str = typer.Argument(..., metavar="LIST", help="List to save to (created if new)"),
    problem: str = typer.Argument(None, help="Problem ID or slug (default: the one you opened last)"),
    note: str = typer.Option(None, "-m", "--note", help="Note to keep with the problem"),
):
    """Save a problem to one of your lists, with a note."""
    import lists

    info = _problem_info(problem)
    if not info:
        raise typer.Exit(1)
    path = _lists_path()
    data = lists.read_lists(path)
    name = list_name.strip()
    existing = next((n for n in data if n.lower() == name.lower()), None)
    name = existing or name
    if note is None:  # keep the note it already has in this list
        old = data.get(name, {}).get(info["slug"], {})
        note = "" if old.get("removed") else old.get("note", "")
    added = lists.save(path, name, info["slug"], note, info["id"], info["title"])
    label = f"{info['id']}. {info['title']}" if info["id"] else info["slug"]
    verb = "Saved" if added else "Updated"
    console.print(f"[green]{verb}[/green] {label} in [bold]{name}[/bold]" + (f": {note}" if note else ""))


@app.command("unsave")
def unsave_cmd(
    list_name: str = typer.Argument(..., metavar="LIST", help="List name (unique prefix is fine)"),
    problem: str = typer.Argument(None, help="Problem ID or slug (default: the one you opened last)"),
):
    """Remove a problem from one of your lists."""
    import lists

    path = _lists_path()
    name = lists.find_list(lists.read_lists(path), list_name)
    if not name:
        console.print(f"[red]No list named '{list_name}'.[/red]")
        raise typer.Exit(1)
    info = _problem_info(problem)
    if not info:
        raise typer.Exit(1)
    if lists.remove(path, name, info["slug"]):
        console.print(f"Removed {info['slug']} from [bold]{name}[/bold].")
    else:
        console.print(f"[yellow]{info['slug']} is not in {name}.[/yellow]")


@app.command("lists")
@app.command("ls", hidden=True)
def lists_cmd(
    list_name: str = typer.Argument(None, metavar="[LIST]", help="Show one list's problems and notes"),
):
    """Show your lists, or the problems and notes in one list."""
    import json
    import lists

    data = lists.read_lists(_lists_path())
    live = lists.live_lists(data)
    try:
        index = {q["titleSlug"]: q for q in json.loads((core.data_dir() / "index.json").read_text())}
    except Exception:
        index = {}

    if not list_name:
        if not live:
            console.print("[dim]No lists yet. Save a problem with `lc save <list> \\[problem] -m <note>`.[/dim]")
            return
        table = Table(title="Your Lists")
        table.add_column("List", style="cyan", no_wrap=True)
        table.add_column("Problems", justify="right")
        table.add_column("Solved", justify="right")
        for name, items in live.items():
            solved = sum(1 for e in items if index.get(e["slug"], {}).get("status") == "ac")
            table.add_row(name, str(len(items)), str(solved) if index else "-")
        console.print(table)
        return

    name = lists.find_list(data, list_name)
    if not name:
        console.print(f"[red]No list named '{list_name}'.[/red] Lists: " + ", ".join(live))
        raise typer.Exit(1)
    table = Table(title=name)
    table.add_column("Status", justify="center")
    table.add_column("ID", style="dim", justify="right")
    table.add_column("Title")
    table.add_column("Difficulty")
    table.add_column("Note")
    for e in live[name]:
        q = index.get(e["slug"], {})
        status = q.get("status")
        mark = "[green]✔[/green]" if status == "ac" else "[red]✘[/red]" if status == "notac" else ""
        diff = (q.get("difficulty") or "?")[0]
        table.add_row(
            mark, str(q.get("frontendQuestionId") or e.get("id") or ""),
            q.get("title") or e.get("title") or e["slug"],
            _diff_markup(diff) if q else "", e.get("note", ""),
        )
    console.print(table)


@app.command("tui")
@app.command("ui", hidden=True)
def tui_cmd():
    """Open the TUI (main + editor panes; uses tmux when available)."""
    from tui import launch_tui

    launch_tui()


@app.command("sync-setup")
def sync_setup():
    """Configure syncing the recent list and lists through Google Drive (rclone)."""
    import sync

    sync.setup_drive(core.data_dir(), console)


@app.command("_pane", hidden=True)
def pane_cmd(
    role: str = typer.Argument(..., help="main"),
    session_dir: str = typer.Option(..., "--session-dir"),
    edit_pane: str = typer.Option(None, "--edit-pane"),
    window: str = typer.Option(None, "--window"),
):
    """Internal: run one pane of the tmux TUI layout."""
    from tui import run_pane

    run_pane(role, session_dir, edit_pane=edit_pane, window=window)


if __name__ == "__main__":
    app()
