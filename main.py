import io
import os
import time
import subprocess
import shutil
import hashlib
import shlex
import typer
from rich.console import Console
from rich.table import Table
from rich.markdown import Markdown
import click
from markdownify import markdownify as md
from pathlib import Path
from urllib.parse import urlparse
import json
from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("leetcode-cli")
except PackageNotFoundError:
    __version__ = "unknown"

from auth import extract_cookies
import api
import config

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


def build_wezterm_imgcat_command(image_path: str, width: int | None = None) -> str:
    """Build the command to render an image using wezterm imgcat, with optional tmux passthrough."""
    # Use wezterm imgcat with specified or auto width.
    w_arg = f"--width {width}" if width else "--width auto"
    command = f"wezterm imgcat {w_arg} {shlex.quote(image_path)}"
    if os.environ.get("TMUX"):
        # Wrap in tmux DCS passthrough
        command += " | sed 's|\\x1b|\\x1bPtmux;\\x1b\\x1b|g; s|$|\\x1b\\\\|'"
    return command


def render_image_with_wezterm(image_path: str, width: str | None = None) -> str | None:
    """Render an image inline when running inside WezTerm."""
    if not shutil.which("wezterm"):
        return None

    # Even if not in a wezterm session, we might be in tmux which is inside wezterm
    if not is_wezterm_session() and not os.environ.get("TMUX"):
        return None

    try:
        # width can be a number (cells) or a string like "100px"
        w_arg = f"--width {width}" if width else "--width auto"
        command = f"wezterm imgcat {w_arg} {shlex.quote(image_path)}"
        if os.environ.get("TMUX"):
            # Wrap in tmux DCS passthrough
            command += " | sed 's|\\x1b|\\x1bPtmux;\\x1b\\x1b|g; s|$|\\x1b\\\\|'"
        
        result = subprocess.run(
            command,
            shell=True,
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
    """Try to render an image using wezterm imgcat and estimate height for pager.
    
    Uses original width if it fits in the terminal, otherwise scales down.
    """
    if prefer_text:
        return None

    # Constants for estimation
    CELL_W_PX = 10  # Typical width of a character cell in pixels
    CELL_H_PX = 20  # Typical height of a character cell in pixels
    MAX_W_CELLS = max(20, console.width - 4)
    MAX_W_PX = MAX_W_CELLS * CELL_W_PX

    dims = get_image_dimensions(image_path)
    
    width_arg = str(MAX_W_CELLS)
    est_width_cells = MAX_W_CELLS
    img_aspect = 1.0

    if dims:
        img_w, img_h = dims
        img_aspect = img_h / img_w
        if img_w < MAX_W_PX:
            # Use original width in pixels to avoid upscaling
            width_arg = f"{img_w}px"
            est_width_cells = img_w / CELL_W_PX
        else:
            # Scale down to terminal width
            width_arg = str(MAX_W_CELLS)
            est_width_cells = MAX_W_CELLS
    
    res = render_image_with_wezterm(image_path, width=width_arg)
    if not res:
        return None

    # Estimate height in terminal lines to pad with newlines for the pager.
    # terminal_height = (rendered_width_cells * img_h / img_w) * (cell_width / cell_height)
    height = int(est_width_cells * img_aspect * (CELL_W_PX / CELL_H_PX))
    # Clamp height to something reasonable
    height = max(1, min(height, 100))
    
    # Pre-padding technique:
    # 1. Move to start of line (\r)
    # 2. Print 'height' newlines to reserve space in the pager/terminal.
    # 3. Move cursor back UP 'height' lines.
    # 4. Print the image (which will then fill that space and move cursor back down).
    padding = "\n" * height
    cursor_up = f"\x1b[{height}A"
    return "\r" + padding + cursor_up + res


def render_image(image_path: str) -> bool:
    """Try various methods to render an image in the terminal."""
    res = get_image_rendering(image_path)
    if res:
        console.file.write(res)
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

        command = build_wezterm_imgcat_command(image_path)
        console.print(f"wezterm_imgcat_command={command}")
        result = subprocess.run(
            command,
            shell=True,
            stdout=console.file,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
            timeout=10,
        )
        console.print(f"wezterm_imgcat_returncode={result.returncode}")
        console.print(
            f"wezterm_imgcat_stderr={
                result.stderr.decode(errors='replace').strip() or '<empty>'
            }"
        )
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


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    pass


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
    table.add_column("ID", style="dim")
    table.add_column("Title")
    table.add_column("Difficulty")
    table.add_column("Acceptance")

    for q in questions:
        diff_color = (
            "green"
            if q["difficulty"] == "Easy"
            else "yellow"
            if q["difficulty"] == "Medium"
            else "red"
        )
        ac_rate = str(round(q.get("acRate", 0), 2)) + "%"
        table.add_row(
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

    data = api.get_questions_list(limit=1, filters=filters)
    total = data.get("total", 0)

    if total == 0:
        console.print("[red]No problems found matching criteria.[/red]")
        return

    skip = rand.randint(0, total - 1)
    data = api.get_questions_list(limit=1, skip=skip, filters=filters)
    q = data.get("questions", [])
    if not q:
        console.print("[red]Failed to fetch random problem.[/red]")
        return

    slug = q[0].get("titleSlug")
    pick(slug)


def resolve_slug(slug_or_id: str) -> str:
    if not slug_or_id.isdigit():
        return slug_or_id

    data = api.get_questions_list(
        limit=50, filters={"searchKeywords": slug_or_id})
    questions = data.get("questions", [])
    for q in questions:
        if str(q.get("frontendQuestionId")) == slug_or_id:
            return q.get("titleSlug")

    return slug_or_id


def compare_answers(exp, act, status_msg: str | None = None) -> bool:
    """Compare expected and actual results logically, considering LeetCode's flexibility."""
    if status_msg == "Accepted":
        return True

    if exp == act:
        return True

    def parse_if_json(s):
        if not isinstance(s, str):
            return s
        s_clean = s.strip()
        if not s_clean:
            return s
        if (s_clean.startswith("[") and s_clean.endswith("]")) or (
            s_clean.startswith("{") and s_clean.endswith("}")
        ):
            try:
                return json.loads(s_clean)
            except:
                return s
        return s

    exp_obj = parse_if_json(exp)
    act_obj = parse_if_json(act)

    if exp_obj == act_obj:
        return True

    # Deep sort for list comparison where order doesn't matter (e.g. 3Sum)
    if isinstance(exp_obj, list) and isinstance(act_obj, list):

        def deep_sort(obj):
            if isinstance(obj, list):
                # Recursively sort items
                items = [deep_sort(x) for x in obj]
                try:
                    # Sort by string representation to handle mixed types or unhashable items
                    return sorted(items, key=lambda x: str(x))
                except:
                    return items
            return obj

        if deep_sort(exp_obj) == deep_sort(act_obj):
            return True

    # Fallback: compare strings without whitespace
    if isinstance(exp, str) and isinstance(act, str):
        if exp.replace(" ", "") == act.replace(" ", ""):
            return True

    return False


def get_target_file(arg: str) -> str:
    import re

    if os.path.exists(arg) and arg.endswith(".py"):
        return arg

    cache_dir = config.get_config("cache_dir", str(Path.home() / "leetcode"))
    if not os.path.exists(cache_dir):
        return arg

    for fname in os.listdir(cache_dir):
        if fname.endswith(".py"):
            match = re.match(r"(\d+)\.(.+)\.py", fname)
            if match:
                f_id = match.group(1)
                f_slug = match.group(2)
                if arg == f_id or arg == f_slug:
                    return os.path.join(cache_dir, fname)

    return arg


def pager(content: str):
    """A robust pager that supports images and standard navigation."""
    # If we might have images, 'less -r' is better than 'less -R'
    # as it passes through all control sequences including OSC for WezTerm.
    pager_env = os.environ.get("PAGER", "less")
    if "less" in pager_env and "-r" not in pager_env.lower():
        # Add -r if it's less and doesn't already have it
        # We use -r instead of -R to support high-res images (OSC sequences)
        os.environ["PAGER"] = f"{pager_env} -r"

    click.echo_via_pager(content)


@app.command()
@app.command("p", hidden=True)
def pick(slug: str):
    """Show details of a specific problem."""
    slug = resolve_slug(slug)
    q = api.get_question_detail(slug)
    if not q:
        console.print("[red]Problem not found.[/red]")
        return

    import re
    from bs4 import BeautifulSoup

    html = q.get("content", "")
    soup = BeautifulSoup(html, "html.parser")
    images = []

    for img in soup.find_all("img"):
        url = img.get("src")
        if url and url.startswith("/"):
            url = f"https://leetcode.com{url}"
        token = f"TOKENSPLITIMAGE{len(images)}TOKENSPLIT"
        images.append(url)
        img.replace_with(token)

    cleaned_md = md(str(soup))
    parts = re.split(r"TOKENSPLITIMAGE\d+TOKENSPLIT", cleaned_md)

    output = io.StringIO()
    # Use a temporary console to render into our StringIO
    temp_console = Console(
        file=output, force_terminal=True, color_system="truecolor")

    temp_console.print(
        f"[bold]{q['questionFrontendId']}. {q['title']}[/bold] (Difficulty: {
            q['difficulty']
        })\n"
        f"https://leetcode.com/problems/{slug}/\n"
    )

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
                        res = get_image_rendering(
                            image_path, prefer_text=False)
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

    code_snippets = q.get("codeSnippets", [])
    python_snippet = next(
        (c for c in code_snippets if c["langSlug"] == "python3"), None
    )

    if not python_snippet:
        console.print("[red]Python3 snippet not found for this problem.[/red]")
        return

    cache_dir = config.get_config("cache_dir", str(Path.home() / "leetcode"))
    os.makedirs(cache_dir, exist_ok=True)
    file_name = os.path.join(cache_dir, f"{q['questionFrontendId']}.{slug}.py")

    should_write = True
    if os.path.exists(file_name):
        should_write = typer.confirm(
            f"File {file_name} already exists. Re-initialize and overwrite it?",
            default=False,
        )

    if should_write:
        header = f'"""{q["questionFrontendId"]}. {q["title"]} (Difficulty: {
            q["difficulty"]
        })\n'
        header += f"https://leetcode.com/problems/{slug}/\n"

        testcases = q.get("exampleTestcases", "")
        header += f"\n[TESTCASES]\n{testcases}\n"
        header += '"""\n'
        header += "from typing import List\n\n\n"

        with open(file_name, "w") as f:
            f.write(header + python_snippet["code"] + "\n")
        console.print(f"[green]Created {file_name}![/green]")
    else:
        console.print(f"Opening existing {file_name}...")

    editor = os.environ.get("EDITOR", "vi")
    subprocess.call([editor, file_name])


@app.command(name="exec")
@app.command("x", hidden=True)
def exec_cmd(file_path: str):
    """Submit a python file to LeetCode."""
    import re

    file_path = get_target_file(file_path)
    if not os.path.exists(file_path):
        console.print(
            f"[red]Could not find a valid matching file for '{
                file_path
            }' in cache directory.[/red]"
        )
        return

    # Extract slug from filename assuming 1.two-sum.py
    base = os.path.basename(file_path)
    match = re.match(r"\d+\.(.+)\.py", base)
    if not match:
        console.print(
            "[red]Filename must be in format ID.slug.py (e.g. 1.two-sum.py)[/red]"
        )
        return
    slug = match.group(1)

    q = api.get_question_detail(slug)
    if not q:
        console.print(
            "[red]Could not match local file to a LeetCode problem.[/red]")
        return

    question_id = q["questionId"]

    with open(file_path, "r") as f:
        code = f.read()

    console.print("[cyan]Submitting...[/cyan]")
    try:
        sub_resp = api.submit_code(slug, question_id, "python3", code)
        sub_id = sub_resp.get("submission_id")
        if not sub_id:
            console.print(f"[red]Submission failed: {sub_resp}[/red]")
            return

        console.print(f"Submission ID: {sub_id}. Polling for result...")

        while True:
            time.sleep(2)
            check = api.check_submission(sub_id)
            state = check.get("state")
            if state == "PENDING" or state == "STARTED":
                console.print(".", end="", style="dim")
                continue

            console.print(f"\n[bold]Result: {check.get('status_msg')}[/bold]")
            if check.get("status_msg") == "Accepted":
                rt_perc = check.get("runtime_percentile")
                mem_perc = check.get("memory_percentile")
                rt_str = check.get("status_runtime", "N/A")
                mem_str = check.get("status_memory", "N/A")

                if rt_perc is not None:
                    try:
                        rt_str += f" (Beats {float(rt_perc):.2f}%)"
                    except ValueError:
                        pass
                if mem_perc is not None:
                    try:
                        mem_str += f" (Beats {float(mem_perc):.2f}%)"
                    except ValueError:
                        pass

                console.print(f"Runtime: {rt_str} | Memory: {mem_str}")
            else:
                if "compile_error" in check:
                    console.print(f"[red]{check.get('compile_error')}[/red]")
                if "runtime_error" in check:
                    console.print(f"[red]{check.get('runtime_error')}[/red]")
                if "last_testcase" in check and check.get("last_testcase"):
                    inputs = check.get("last_testcase").replace("\n", ", ")
                    console.print(f"Input:    {inputs}")
                if "expected_output" in check:
                    console.print(f"Expected: {check.get('expected_output')}")
                    console.print(f"Output:   {check.get('code_output')}")
                if "std_output" in check and check.get("std_output"):
                    console.print("Stdout:")
                    for line in (
                        check.get("std_output")
                        .replace("\r", "")
                        .strip("\n")
                        .split("\n")
                    ):
                        console.print(f"  {line}")
            break

    except Exception as e:
        console.print(f"[red]Error submitting: {e}[/red]")


@app.command()
@app.command("t", hidden=True)
def test(file_path: str):
    """Run tests for a python file on LeetCode."""
    import re

    file_path = get_target_file(file_path)
    if not os.path.exists(file_path):
        console.print(
            f"[red]Could not find a valid matching file for '{
                file_path
            }' in cache directory.[/red]"
        )
        return

    base = os.path.basename(file_path)
    match = re.match(r"\d+\.(.+)\.py", base)
    if not match:
        console.print(
            "[red]Filename must be in format ID.slug.py (e.g. 1.two-sum.py)[/red]"
        )
        return
    slug = match.group(1)

    q = api.get_question_detail(slug)
    if not q:
        console.print(
            "[red]Could not match local file to a LeetCode problem.[/red]")
        return

    question_id = q["questionId"]

    with open(file_path, "r") as f:
        code = f.read()

    match_tc = re.search(
        r"\[TESTCASES\]\n(.*?)\n(?:\"\"\"|''')", code, re.DOTALL)
    if match_tc:
        test_cases = match_tc.group(1).strip()
    else:
        test_cases = q.get("exampleTestcases", "")

    if not test_cases:
        console.print(
            "[yellow]No example testcases found, testing with empty input.[/yellow]"
        )

    console.print("[cyan]Running tests...[/cyan]")
    try:
        try:
            sub_resp = api.test_code(
                slug, question_id, "python3", code, test_cases)
        except Exception as e:
            console.print(f"[red]Error starting test: {e}[/red]")
            return

        run_id = sub_resp.get("interpret_id")
        if not run_id:
            console.print(
                f"[red]Test failed (Often due to missing Cookies/Cloudflare if non-JSON, or rate limit): {
                    sub_resp
                }[/red]"
            )
            return

        console.print(f"Test Run ID: {run_id}. Polling for result...")

        while True:
            time.sleep(2)
            try:
                check = api.check_test_run(run_id)
            except Exception as e:
                console.print(f"\n[red]Error polling result: {e}[/red]")
                break

            state = check.get("state")
            if state == "PENDING" or state == "STARTED":
                console.print(".", end="", style="dim", flush=True)
                continue

            console.print(f"\n[bold]Test Result: {
                          check.get('status_msg')}[/bold]")

            runtime = check.get("status_runtime")
            if runtime:
                console.print(f"Runtime: {runtime}")

            if "compile_error" in check and check.get("compile_error"):
                console.print(f"[red]{check.get('compile_error')}[/red]")
            elif "runtime_error" in check and check.get("runtime_error"):
                console.print(f"[red]{check.get('runtime_error')}[/red]")
            else:
                expected = check.get("expected_code_answer", [])
                actual = check.get("code_answer", [])
                stdout = check.get("std_output_list",
                                   check.get("code_output", []))

                # LeetCode's backend API returns a trailing empty string due to newline splitting artifacts
                if isinstance(expected, list) and expected and expected[-1] == "":
                    expected.pop()
                if isinstance(actual, list) and actual and actual[-1] == "":
                    actual.pop()
                if isinstance(stdout, list) and stdout and stdout[-1] == "":
                    stdout.pop()

                raw_tc_lines = [
                    line for line in test_cases.strip("\n").split("\n") if line.strip()
                ]
                num_cases = max(
                    len(expected) if isinstance(expected, list) else 0,
                    len(actual) if isinstance(actual, list) else 0,
                )

                if num_cases > 0:
                    args_per_case = len(raw_tc_lines) // num_cases
                else:
                    args_per_case = 1

                for i in range(num_cases):
                    console.print(f"\n[bold]Test Case {i + 1}:[/bold]")

                    if args_per_case > 0 and i * args_per_case < len(raw_tc_lines):
                        inputs = raw_tc_lines[
                            i * args_per_case: (i + 1) * args_per_case
                        ]
                        console.print(
                            f"  Input:    [magenta]{
                                ', '.join(inputs)}[/magenta]"
                        )

                    exp = (
                        expected[i]
                        if isinstance(expected, list) and i < len(expected)
                        else "N/A"
                    )
                    act = (
                        actual[i]
                        if isinstance(actual, list) and i < len(actual)
                        else "N/A"
                    )

                    match = compare_answers(exp, act, check.get("status_msg"))
                    match_col = "green" if match else "red"

                    console.print(f"  Expected: {exp}")
                    console.print("  Output:   ", end="")
                    console.print(act, style=match_col)

                    if isinstance(stdout, list) and i < len(stdout) and stdout[i]:
                        console.print("  Stdout:")
                        for line in stdout[i].replace("\r", "").strip("\n").split("\n"):
                            console.print(f"    {line}")
            break

    except Exception as e:
        console.print(f"[red]Error starting test: {e}[/red]")


if __name__ == "__main__":
    app()
