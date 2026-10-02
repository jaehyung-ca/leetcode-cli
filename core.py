"""Shared logic used by both the CLI commands and the TUI.

Everything here is UI-agnostic: functions that need to report progress take a
rich ``Console`` (``out``) so the caller decides where the text goes.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify as md
from rich.console import Console

import api
import config

IMAGE_TOKEN = "TOKENSPLITIMAGE{}TOKENSPLIT"
IMAGE_TOKEN_RE = re.compile(r"TOKENSPLITIMAGE\d+TOKENSPLIT")

DIFF_COLOR = {"Easy": "green", "Medium": "yellow", "Hard": "red"}


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def data_dir() -> Path:
    """Where the tool keeps its state (recent list, problem index).

    Defaults to ``~/.config/leetcode-cli``; override with ``data_dir`` in the
    config or the ``LC_DATA_DIR`` environment variable.  Solution files never
    go here.
    """
    d = Path(os.environ.get("LC_DATA_DIR") or config.get_config("data_dir", str(config.CONFIG_DIR)))
    d.mkdir(parents=True, exist_ok=True)
    if (d / ".git").exists():  # git-sync mode: never commit the bulky index
        ignore = d / ".gitignore"
        lines = ignore.read_text().splitlines() if ignore.exists() else []
        if "index.json" not in lines:
            with open(ignore, "a") as f:
                f.write(("" if not lines or lines[-1] == "" else "\n") + "index.json\n")
    return d


def work_dir() -> str:
    """Temporary directory for solution files used by the CLI commands.

    Solutions are scratch files: they are recreated from the LeetCode snippet
    on demand and are never recorded anywhere.
    """
    d = os.environ.get("LC_WORK_DIR") or os.path.join(tempfile.gettempdir(), "lc-work")
    os.makedirs(d, exist_ok=True)
    return d


def solution_filename(q: dict) -> str:
    return f"{q['questionFrontendId']}.{q['titleSlug']}.py"


def solution_path(q: dict, directory: str | None = None) -> str:
    return os.path.join(directory or work_dir(), solution_filename(q))


def solution_template(q: dict, snippet_code: str) -> str:
    slug = q["titleSlug"]
    header = f'"""{q["questionFrontendId"]}. {q["title"]} (Difficulty: {q["difficulty"]})\n'
    header += f"https://leetcode.com/problems/{slug}/\n"
    header += f"\n[TESTCASES]\n{q.get('exampleTestcases', '')}\n"
    header += '"""\n'
    header += "from typing import List\n\n\n"
    return header + snippet_code + "\n"


def python_snippet(q: dict) -> dict | None:
    return next(
        (c for c in q.get("codeSnippets", []) if c.get("langSlug") == "python3"),
        None,
    )


def write_solution_file(
    q: dict, overwrite: bool = False, directory: str | None = None
) -> tuple[str, bool]:
    """Create the starter file for ``q`` in ``directory`` unless it exists.

    Returns ``(path, created)``. Raises ``ValueError`` when the problem has no
    Python3 snippet.
    """
    snippet = python_snippet(q)
    if not snippet:
        raise ValueError("Python3 snippet not found for this problem.")
    path = solution_path(q, directory)
    if os.path.exists(path) and not overwrite:
        return path, False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(solution_template(q, snippet["code"]))
    return path, True


def get_target_file(arg: str) -> str:
    """Resolve an ID, slug or path to a solution file in the work directory."""
    if os.path.exists(arg) and arg.endswith(".py"):
        return arg

    cdir = work_dir()
    for fname in os.listdir(cdir):
        if fname.endswith(".py"):
            match = re.match(r"(\d+)\.(.+)\.py", fname)
            if match:
                f_id = match.group(1)
                f_slug = match.group(2)
                if arg == f_id or arg == f_slug:
                    return os.path.join(cdir, fname)

    return arg


def slug_from_filename(file_path: str) -> str | None:
    match = re.match(r"\d+\.(.+)\.py", os.path.basename(file_path))
    return match.group(1) if match else None


def resolve_slug(slug_or_id: str) -> str:
    if not slug_or_id.isdigit():
        return slug_or_id

    data = api.get_questions_list(limit=50, filters={"searchKeywords": slug_or_id})
    for q in data.get("questions", []):
        if str(q.get("frontendQuestionId")) == slug_or_id:
            return q.get("titleSlug")

    return slug_or_id


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def problem_markdown_parts(q: dict) -> tuple[list[str], list[str]]:
    """Convert the problem's HTML content to markdown.

    Returns ``(parts, images)``: the markdown is split where images occurred,
    so ``images[i]`` belongs between ``parts[i]`` and ``parts[i + 1]``.
    """
    soup = BeautifulSoup(q.get("content", "") or "", "html.parser")
    images: list[str] = []

    for img in soup.find_all("img"):
        url = img.get("src")
        if url and url.startswith("/"):
            url = f"https://leetcode.com{url}"
        img.replace_with(IMAGE_TOKEN.format(len(images)))
        images.append(url)

    for tag in soup.find_all("sup"):
        text = tag.get_text()
        tag.replace_with(f"^({text})" if len(text) > 1 else f"^{text}")
    for tag in soup.find_all("sub"):
        text = tag.get_text()
        tag.replace_with(f"_({text})" if len(text) > 1 else f"_{text}")

    cleaned = md(str(soup))
    return IMAGE_TOKEN_RE.split(cleaned), images


def problem_markdown(q: dict) -> str:
    """Single markdown document with image URLs inlined as links."""
    parts, images = problem_markdown_parts(q)
    out = []
    for i, part in enumerate(parts):
        out.append(part)
        if i < len(images) and images[i]:
            out.append(f"\n[image]({images[i]})\n")
    return "".join(out)


def problem_title_markup(q: dict) -> str:
    """Rich-markup header line: ``1. Two Sum ✔ (Difficulty: Easy | Tags: ...)``."""
    status = q.get("status")
    mark = " [green]✔[/green]" if status == "ac" else " [red]✘[/red]" if status == "notac" else ""
    diff = q.get("difficulty", "Unknown")
    color = DIFF_COLOR.get(diff, "white")
    tags = [t["name"] for t in (q.get("topicTags") or []) if t.get("name")]
    tags_display = (
        " | Tags: " + ", ".join(f"[cyan]{t}[/cyan]" for t in tags) if tags else ""
    )
    return (
        f"[bold]{q['questionFrontendId']}. {q['title']}{mark}[/bold] "
        f"(Difficulty: [{color}]{diff}[/{color}]{tags_display})"
    )


def problem_url(q: dict) -> str:
    return f"https://leetcode.com/problems/{q['titleSlug']}/"


# ---------------------------------------------------------------------------
# Test / submit
# ---------------------------------------------------------------------------


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
            except Exception:
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
                items = [deep_sort(x) for x in obj]
                try:
                    return sorted(items, key=lambda x: str(x))
                except Exception:
                    return items
            return obj

        if deep_sort(exp_obj) == deep_sort(act_obj):
            return True

    # Fallback: compare strings without whitespace
    if isinstance(exp, str) and isinstance(act, str):
        if exp.replace(" ", "") == act.replace(" ", ""):
            return True

    return False


def _load_solution(file_path: str, out: Console, q: dict | None):
    """Common prologue for test/submit: returns (q, code) or None."""
    file_path = get_target_file(file_path)
    if not os.path.exists(file_path):
        out.print(
            f"[red]Could not find a valid matching file for '{file_path}' "
            "in cache directory.[/red]"
        )
        return None

    slug = slug_from_filename(file_path)
    if not slug:
        out.print("[red]Filename must be in format ID.slug.py (e.g. 1.two-sum.py)[/red]")
        return None

    if q is None or q.get("titleSlug") != slug:
        q = api.get_question_detail(slug)
    if not q:
        out.print("[red]Could not match local file to a LeetCode problem.[/red]")
        return None

    with open(file_path, "r") as f:
        code = f.read()
    return q, code


def run_submit(file_path: str, out: Console, q: dict | None = None) -> dict | None:
    """Submit a solution file; prints progress to ``out``.

    Returns the final check payload (``status_msg`` etc.) or None on failure.
    """
    loaded = _load_solution(file_path, out, q)
    if not loaded:
        return None
    q, code = loaded
    slug = q["titleSlug"]

    out.print("[cyan]Submitting...[/cyan]")
    try:
        sub_resp = api.submit_code(slug, q["questionId"], "python3", code)
        sub_id = sub_resp.get("submission_id")
        if not sub_id:
            out.print(f"[red]Submission failed: {sub_resp}[/red]")
            return None

        out.print(f"Submission ID: {sub_id}. Polling for result...")

        while True:
            time.sleep(2)
            check = api.check_submission(sub_id)
            state = check.get("state")
            if state in ("PENDING", "STARTED"):
                out.print(".", end="", style="dim")
                out.file.flush()
                continue

            status = check.get("status_msg")
            color = "green" if status == "Accepted" else "red"
            out.print(f"\n[bold {color}]Result: {status}[/bold {color}]")
            if status == "Accepted":
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

                out.print(f"Runtime: {rt_str} | Memory: {mem_str}")
            else:
                if check.get("compile_error"):
                    out.print(f"[red]{check.get('compile_error')}[/red]")
                if check.get("runtime_error"):
                    out.print(f"[red]{check.get('runtime_error')}[/red]")
                if check.get("last_testcase"):
                    inputs = check.get("last_testcase").replace("\n", ", ")
                    out.print(f"Input:    {inputs}")
                if "expected_output" in check:
                    out.print(f"Expected: {check.get('expected_output')}")
                    out.print(f"Output:   {check.get('code_output')}")
                if check.get("std_output"):
                    out.print("Stdout:")
                    for line in check["std_output"].replace("\r", "").strip("\n").split("\n"):
                        out.print(f"  {line}")
            return check

    except Exception as e:
        out.print(f"[red]Error submitting: {e}[/red]")
        return None


def run_test(file_path: str, out: Console, q: dict | None = None) -> dict | None:
    """Run the example test cases for a solution file; prints to ``out``."""
    loaded = _load_solution(file_path, out, q)
    if not loaded:
        return None
    q, code = loaded
    slug = q["titleSlug"]

    match_tc = re.search(r"\[TESTCASES\]\n(.*?)\n(?:\"\"\"|''')", code, re.DOTALL)
    if match_tc:
        test_cases = match_tc.group(1).strip()
    else:
        test_cases = q.get("exampleTestcases", "")

    if not test_cases:
        out.print("[yellow]No example testcases found, testing with empty input.[/yellow]")

    out.print("[cyan]Running tests...[/cyan]")
    try:
        try:
            sub_resp = api.test_code(slug, q["questionId"], "python3", code, test_cases)
        except Exception as e:
            out.print(f"[red]Error starting test: {e}[/red]")
            return None

        run_id = sub_resp.get("interpret_id")
        if not run_id:
            out.print(
                "[red]Test failed (Often due to missing Cookies/Cloudflare if non-JSON, "
                f"or rate limit): {sub_resp}[/red]"
            )
            return None

        out.print(f"Test Run ID: {run_id}. Polling for result...")

        while True:
            time.sleep(2)
            try:
                check = api.check_test_run(run_id)
            except Exception as e:
                out.print(f"\n[red]Error polling result: {e}[/red]")
                return None

            state = check.get("state")
            if state in ("PENDING", "STARTED"):
                out.print(".", end="", style="dim")
                out.file.flush()
                continue

            status = check.get("status_msg")
            color = "green" if status == "Accepted" else "red"
            out.print(f"\n[bold {color}]Test Result: {status}[/bold {color}]")

            runtime = check.get("status_runtime")
            if runtime:
                out.print(f"Runtime: {runtime}")

            runtime_error = check.get("runtime_error")
            if check.get("compile_error"):
                out.print(f"[red]{check.get('compile_error')}[/red]")
            else:
                # A runtime error still reports the stdout of every case that ran
                # (including the crashing one), so fall through to the cases.
                if runtime_error:
                    out.print(check.get("full_runtime_error") or runtime_error, style="red", markup=False)
                expected = check.get("expected_code_answer", [])
                actual = check.get("code_answer", [])
                stdout = check.get("std_output_list", check.get("code_output", []))

                # LeetCode's backend returns a trailing empty string (newline split artifact)
                for lst in (expected, actual, stdout):
                    if isinstance(lst, list) and lst and lst[-1] == "":
                        lst.pop()

                raw_tc_lines = [
                    line for line in test_cases.strip("\n").split("\n") if line.strip()
                ]
                num_cases = max(
                    len(expected) if isinstance(expected, list) else 0,
                    len(actual) if isinstance(actual, list) else 0,
                )
                args_per_case = len(raw_tc_lines) // num_cases if num_cases > 0 else 1
                if runtime_error:
                    # Only the cases that ran: those with an answer, plus the one that crashed.
                    ran = max(
                        len(actual) if isinstance(actual, list) else 0,
                        len(stdout) if isinstance(stdout, list) else 0,
                    )
                    num_cases = min(num_cases, ran) if num_cases else ran

                for i in range(num_cases):
                    out.print(f"\n[bold]Test Case {i + 1}:[/bold]")

                    if args_per_case > 0 and i * args_per_case < len(raw_tc_lines):
                        inputs = raw_tc_lines[i * args_per_case : (i + 1) * args_per_case]
                        out.print(f"  Input:    [magenta]{', '.join(inputs)}[/magenta]")

                    exp = expected[i] if isinstance(expected, list) and i < len(expected) else "N/A"
                    has_act = isinstance(actual, list) and i < len(actual)
                    act = actual[i] if has_act else "N/A"

                    out.print(f"  Expected: {exp}")
                    out.print("  Output:   ", end="")
                    if not has_act and runtime_error:
                        out.print("Runtime Error", style="bold red")
                    else:
                        ok = compare_answers(exp, act, status)
                        out.print(act, style="green" if ok else "red")

                    if isinstance(stdout, list) and i < len(stdout) and stdout[i]:
                        out.print("  Stdout:")
                        for line in stdout[i].replace("\r", "").strip("\n").split("\n"):
                            out.print(f"    {line}")
            return check

    except Exception as e:
        out.print(f"[red]Error starting test: {e}[/red]")
        return None
