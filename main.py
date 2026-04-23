import os
import time
import subprocess
import shutil
import hashlib
import typer
from rich.console import Console
from rich.table import Table
from rich.markdown import Markdown
from markdownify import markdownify as md
from pathlib import Path
from urllib.parse import urlparse

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


def build_wezterm_imgcat_command(image_path: str) -> list[str]:
    command = ["wezterm", "imgcat", "--width", "auto"]
    if os.environ.get("TMUX"):
        command.extend(["--tmux-passthru", "detect"])
    command.append(image_path)
    return command


def render_image_with_wezterm(image_path: str) -> bool:
    """Render an image inline when running inside WezTerm."""
    if not shutil.which("wezterm"):
        return False

    if not is_wezterm_session():
        return False

    try:
        result = subprocess.run(
            build_wezterm_imgcat_command(image_path),
            stdout=console.file,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
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

        command = build_wezterm_imgcat_command(image_path)
        console.print(f"wezterm_imgcat_command={' '.join(command)}")
        result = subprocess.run(
            command,
            stdout=console.file,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=10,
        )
        console.print(f"wezterm_imgcat_returncode={result.returncode}")
        console.print(
            f"wezterm_imgcat_stderr={result.stderr.strip() or '<empty>'}"
        )
    except subprocess.TimeoutExpired as exc:
        console.print(
            f"[red]wezterm_imgcat_timeout={exc.timeout}s after command start[/red]"
        )
    except Exception as exc:
        console.print(f"[red]debug_exception={type(exc).__name__}: {exc}[/red]")
    finally:
        if image_path and os.path.exists(image_path):
            os.unlink(image_path)


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


@app.command()
@app.command("l", hidden=True)
def list(
    tag: str = typer.Option(None, "-t", "--tag", help="Filter by tag"),
    diff: str = typer.Option(None, "-d", "--diff", help="Filter by difficulty"),
    search: str = typer.Option(None, "-s", "--search", help="Search query"),
    limit: int = typer.Option(50, "-m", "--limit", help="Max results"),
    high: str = typer.Option(None, "--high", help="Sort descending by: ac or freq"),
    low: str = typer.Option(None, "--low", help="Sort ascending by: ac or freq"),
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


@app.command()
@app.command("r", hidden=True)
def random(
    tag: str = typer.Option(None, "-t", "--tag", help="Filter by tag"),
    diff: str = typer.Option(None, "-d", "--diff", help="Filter by difficulty"),
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

    data = api.get_questions_list(limit=50, filters={"searchKeywords": slug_or_id})
    questions = data.get("questions", [])
    for q in questions:
        if str(q.get("frontendQuestionId")) == slug_or_id:
            return q.get("titleSlug")

    return slug_or_id


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


@app.command()
@app.command("p", hidden=True)
def pick(slug: str):
    """Show details of a specific problem."""
    slug = resolve_slug(slug)
    q = api.get_question_detail(slug)
    if not q:
        console.print("[red]Problem not found.[/red]")
        return

    console.print(
        f"[bold]{q['questionFrontendId']}. {q['title']}[/bold] (Difficulty: {q['difficulty']})\n"
        f"https://leetcode.com/problems/{slug}/\n"
    )
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

    for i, part in enumerate(parts):
        if part.strip():
            console.print(Markdown(part))
        if i < len(images):
            url = images[i]
            if url:
                try:
                    image_path, _ = download_image_to_tempfile(url)
                    if not image_path:
                        console.print(f"[dim]Image: {url}[/dim]")
                        continue

                    try:
                        if not render_image_with_wezterm(image_path):
                            console.print(f"[dim]Image: {url}[/dim]")
                    except Exception:
                        console.print(f"[dim]Image: {url}[/dim]")
                    finally:
                        os.unlink(image_path)
                except Exception:
                    console.print(f"[dim]Image: {url}[/dim]")


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
        import click

        console.print(
            f"File {file_name} already exists. Re-initialize and overwrite it? [y/N] ",
            end="",
        )
        char = click.getchar()
        console.print(char)
        should_write = char.lower() == "y"

    if should_write:
        header = f'"""{q["questionFrontendId"]}. {q["title"]} (Difficulty: {q["difficulty"]})\n'
        header += f'https://leetcode.com/problems/{slug}/\n'
        
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
            f"[red]Could not find a valid matching file for '{file_path}' in cache directory.[/red]"
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
        console.print("[red]Could not match local file to a LeetCode problem.[/red]")
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
                    for line in check.get("std_output").replace('\r', '').strip('\n').split('\n'):
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
            f"[red]Could not find a valid matching file for '{file_path}' in cache directory.[/red]"
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
        console.print("[red]Could not match local file to a LeetCode problem.[/red]")
        return

    question_id = q["questionId"]

    with open(file_path, "r") as f:
        code = f.read()

    match_tc = re.search(r"\[TESTCASES\]\n(.*?)\n(?:\"\"\"|''')", code, re.DOTALL)
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
        sub_resp = api.test_code(slug, question_id, "python3", code, test_cases)
        run_id = sub_resp.get("interpret_id")
        if not run_id:
            console.print(
                f"[red]Test failed (Often due to missing Cookies/Cloudflare if non-JSON, or rate limit): {sub_resp}[/red]"
            )
            return

        console.print(f"Test Run ID: {run_id}. Polling for result...")

        while True:
            time.sleep(2)
            check = api.check_submission(run_id)
            state = check.get("state")
            if state == "PENDING" or state == "STARTED":
                console.print(".", end="", style="dim", flush=True)
                continue

            console.print(f"\n[bold]Test Result: {check.get('status_msg')}[/bold]")
            if "compile_error" in check:
                console.print(f"[red]{check.get('compile_error')}[/red]")
            elif "runtime_error" in check:
                console.print(f"[red]{check.get('runtime_error')}[/red]")
            else:
                expected = check.get("expected_code_answer", [])
                actual = check.get("code_answer", [])
                stdout = check.get("std_output_list", check.get("code_output", []))

                # LeetCode's backend API returns a trailing empty string due to newline splitting artifacts
                if expected and expected[-1] == "":
                    expected.pop()
                if actual and actual[-1] == "":
                    actual.pop()
                if stdout and stdout[-1] == "":
                    stdout.pop()

                raw_tc_lines = test_cases.strip("\n").split("\n")
                num_cases = max(len(expected), len(actual))
                args_per_case = len(raw_tc_lines) // max(1, num_cases)

                for i in range(num_cases):
                    console.print(f"Test Case {i + 1}:")

                    if i * args_per_case < len(raw_tc_lines):
                        inputs = raw_tc_lines[
                            i * args_per_case : (i + 1) * args_per_case
                        ]
                        console.print(f"  Input:    {', '.join(inputs)}")

                    exp = expected[i] if i < len(expected) else "N/A"
                    act = actual[i] if i < len(actual) else "N/A"
                    out = stdout[i] if i < len(stdout) else ""
                    match_col = "green" if str(exp) == str(act) else "red"
                    console.print(f"  Expected: {exp}")
                    console.print(f"  Output:   [{match_col}]{act}[/{match_col}]")
                    if out:
                        console.print("  Stdout:")
                        for line in out.replace('\r', '').strip('\n').split('\n'):
                            console.print(f"    {line}")
            break

    except Exception as e:
        console.print(f"[red]Error starting test: {e}[/red]")


if __name__ == "__main__":
    app()
