"""Two-pane TUI for leetcode-cli.

Layout (tmux):

    +---------------------------+---------------------------+
    | main                      | edit                      |
    |  menu > list > problem    |  $EDITOR (nvim via RPC)   |
    |  (hjkl navigation)        |                           |
    |---------------------------|                           |
    |  test / submit / sync log |                           |
    +---------------------------+---------------------------+

``launch_tui()`` builds the layout with tmux and runs ``lc _pane main`` in the
left pane.  The main pane opens solution files in the editor pane (nvim
``--server`` RPC, or tmux ``send-keys`` for vi-likes).  Without tmux the main
app runs alone and ``e`` suspends it to run ``$EDITOR``.

Solution files are scratch files in ``core.work_dir()`` (under the system temp
directory, so they last until reboot).  The tool's own state (problem index,
recently opened problems) lives in ``core.data_dir()`` (``~/leetcode``); the
recent list is synced through Google Drive or git on startup/quit (see sync.py).
"""

from __future__ import annotations

import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from rich.theme import Theme as RichTheme
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import DataTable, Footer, Input, RichLog, Static

import api
import config
import core
import sync
from sets import DIFF_NAME, get_all_sets, level_label, resolve_set_problems, set_progress

DIFF_COLOR = {"E": "green", "M": "yellow", "H": "red"}
DEFAULT_THEME = "solarized-light"
CODE_THEMES = {"solarized-light": "solarized-light", "solarized-dark": "solarized-dark"}

# (kind, key, label, description)
MENU = [
    ("sets", "c", "curated", "Skill sets with your progress"),
    ("tags", "t", "topics", "Problems by tag"),
    ("search", "s", "search", "Search all problems"),
    ("recent", "r", "recent", "Recently opened problems"),
    ("passed", "p", "passed", "Accepted problems"),
    ("failed", "f", "failed", "Attempted but not accepted"),
]
PROBLEM_LISTS = {"set", "tag", "search", "recent", "passed", "failed"}


def tui_theme() -> str:
    return str(config.get_config("tui_theme", DEFAULT_THEME))


def code_theme() -> str:
    theme = tui_theme()
    return CODE_THEMES.get(theme, "default" if theme.endswith("light") else "monokai")


# ---------------------------------------------------------------------------
# Session / tmux plumbing
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """How the main pane finds the editor pane."""

    session_dir: Path | None = None  # temp dir holding the nvim socket
    edit_pane: str | None = None
    window: str | None = None

    @property
    def standalone(self) -> bool:
        return self.edit_pane is None

    @property
    def nvim_socket(self) -> Path | None:
        return self.session_dir / "nvim.sock" if self.session_dir else None


def _tmux(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["tmux", *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"tmux {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def _tmux_ok(*args: str) -> bool:
    return (
        subprocess.run(
            ["tmux", *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def _pane_alive(pane_id: str | None) -> bool:
    if not pane_id:
        return False
    try:
        return pane_id in _tmux("list-panes", "-a", "-F", "#{pane_id}").split()
    except RuntimeError:
        return False


def _lc_command() -> list[str]:
    """How to re-invoke this program from another pane."""
    argv0 = os.path.abspath(sys.argv[0])
    if argv0.endswith(".py"):
        return [sys.executable, argv0]
    return [argv0]


def _editor() -> tuple[list[str], str]:
    """Return (editor argv, kind) where kind is 'nvim', 'vi' or 'other'."""
    editor = shlex.split(os.environ.get("EDITOR", "vi")) or ["vi"]
    name = os.path.basename(editor[0])
    if name == "nvim":
        return editor, "nvim"
    if name in ("vi", "vim", "gvim", "vimx"):
        return editor, "vi"
    return editor, "other"


def _editor_pane_command(session: Session, file_path: str | None = None) -> str:
    editor, kind = _editor()
    if kind == "nvim":
        cmd = [*editor, "--listen", str(session.nvim_socket)]
        if file_path:
            cmd.append(file_path)
        return shlex.join(cmd)
    if file_path:
        return shlex.join([*editor, file_path])
    if kind == "vi":
        return shlex.join(editor)
    # Unknown editor: idle in a shell until a problem is opened.
    return os.environ.get("SHELL", "sh")


def launch_tui() -> None:
    """Entry point for ``lc`` / ``lc tui``."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("lc tui needs an interactive terminal.")
    if sync.needs_setup(core.data_dir()):
        try:
            sync.setup_drive(core.data_dir(), Console(), auto=True)
        except (KeyboardInterrupt, EOFError):
            print()
    if not shutil.which("tmux") or config.get_config("tui_tmux", True) is False:
        MainApp(Session()).run()
        return

    session = Session(session_dir=Path(tempfile.mkdtemp(prefix="lc-tui-")))
    lc = _lc_command()
    cwd = core.work_dir()
    main_width = str(config.get_config("tui_main_width", "50%"))

    # Panes inherit the tmux server's environment, not ours: forward what matters.
    env_args: list[str] = []
    for var in ("EDITOR", "LC_DATA_DIR", "LC_WORK_DIR"):
        if os.environ.get(var):
            env_args += ["-e", f"{var}={os.environ[var]}"]

    editor_cmd = _editor_pane_command(session)
    in_tmux = bool(os.environ.get("TMUX"))
    if in_tmux:
        window = _tmux(
            "new-window", "-P", "-F", "#{window_id}", "-n", "leetcode", "-c", cwd,
            *env_args, editor_cmd,
        )
    else:
        size = shutil.get_terminal_size()
        tmux_session = f"leetcode-{os.getpid()}"
        _tmux(
            "new-session", "-d", "-s", tmux_session, "-n", "leetcode", "-c", cwd,
            "-x", str(size.columns), "-y", str(size.lines), *env_args, editor_cmd,
        )
        window = _tmux("display-message", "-p", "-t", tmux_session, "#{window_id}")

    session.window = window
    session.edit_pane = _tmux("list-panes", "-t", window, "-F", "#{pane_id}").split()[0]
    main_cmd = shlex.join(
        [
            *lc, "_pane", "main",
            "--session-dir", str(session.session_dir),
            "--edit-pane", session.edit_pane,
            "--window", window,
        ]
    )
    main_pane = _tmux(
        "split-window", "-h", "-b", "-t", session.edit_pane, "-P", "-F", "#{pane_id}",
        "-l", main_width, "-c", cwd, *env_args, main_cmd,
    )
    _tmux("select-pane", "-t", main_pane)

    if not in_tmux:
        os.execvp("tmux", ["tmux", "attach-session", "-t", tmux_session])


def run_pane(role: str, session_dir: str, edit_pane=None, window=None):
    if role != "main":
        raise SystemExit(f"unknown pane role: {role}")
    MainApp(Session(session_dir=Path(session_dir), edit_pane=edit_pane, window=window)).run()


class EditorBridge:
    """Open files in / save the editor pane."""

    def __init__(self, session: Session):
        self.session = session
        self.argv, self.kind = _editor()

    def _nvim_send(self, keys: str) -> bool:
        # stdin must not be our tty: the nvim client sets O_NONBLOCK on it while
        # it runs, and Textual's input thread then dies with BlockingIOError.
        return (
            subprocess.run(
                ["nvim", "--server", str(self.session.nvim_socket), "--remote-send", keys],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )

    def _send_keys(self, *keys: str) -> bool:
        if not _pane_alive(self.session.edit_pane):
            return False
        return _tmux_ok("send-keys", "-t", self.session.edit_pane, *keys)

    def _respawn(self, command: str) -> None:
        """Run ``command`` in the edit pane, recreating the pane if needed."""
        s = self.session
        if _pane_alive(s.edit_pane):
            _tmux("respawn-pane", "-k", "-t", s.edit_pane, command)
            return
        s.edit_pane = _tmux(
            "split-window", "-h", "-d", "-P", "-F", "#{pane_id}", "-c", core.work_dir(), command
        )

    def open(self, file_path: str) -> None:
        if self.session.standalone:
            return
        if self.kind == "nvim":
            if self._nvim_send(f"<C-\\><C-N>:silent! wall | e {file_path}<CR>"):
                return
        elif self.kind == "vi":
            if self._send_keys("Escape", f":silent! wall | e {file_path}", "Enter"):
                return
        self._respawn(_editor_pane_command(self.session, file_path))

    def save_all(self) -> None:
        if self.session.standalone:
            return
        if self.kind == "nvim":
            self._nvim_send("<C-\\><C-N>:silent! wall<CR>")
        elif self.kind == "vi":
            self._send_keys("Escape", ":silent! wall", "Enter")

    def focus(self) -> None:
        if not self.session.standalone and _pane_alive(self.session.edit_pane):
            _tmux_ok("select-pane", "-t", self.session.edit_pane)

    def edit_here(self, file_path: str) -> None:
        """Standalone mode: block on the editor in this terminal."""
        subprocess.call([*self.argv, file_path])


# ---------------------------------------------------------------------------
# State: problem index + recent list (in data_dir)
# ---------------------------------------------------------------------------


def load_index_cache() -> list[dict] | None:
    try:
        return json.loads((core.data_dir() / "index.json").read_text())
    except Exception:
        return None


def save_index_cache(questions: list[dict]) -> None:
    try:
        (core.data_dir() / "index.json").write_text(json.dumps(questions))
    except Exception:
        pass


def load_recent() -> list[str]:
    """Most-recently-opened slugs first; seeded from scratch files when empty."""
    entries = sync.read_recent(core.data_dir() / sync.RECENT)
    if entries:
        return [e["slug"] for e in entries]
    files = []
    try:
        for name in os.listdir(core.work_dir()):
            slug = core.slug_from_filename(name)
            if slug:
                files.append((os.path.getmtime(os.path.join(core.work_dir(), name)), slug))
    except OSError:
        pass
    return [slug for _, slug in sorted(files, reverse=True)]


def question_row(q: dict) -> dict:
    return {
        "id": str(q.get("frontendQuestionId") or "?"),
        "slug": q.get("titleSlug"),
        "title": q.get("title") or "",
        "diff": (q.get("difficulty") or "?")[0],
        "status": q.get("status"),
        "paid": bool(q.get("paidOnly")),
        "ac_rate": q.get("acRate"),
        "freq": q.get("frequency"),
        "tags": [t.get("slug") for t in (q.get("topicTags") or [])],
    }


def _id_key(row: dict):
    try:
        return int(row["id"])
    except (TypeError, ValueError):
        return 10**9


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class ProblemView(Vertical):
    """Title line + scrollable markdown body."""

    DEFAULT_CSS = """
    ProblemView { height: 1fr; }
    ProblemView #ptitle { height: auto; padding: 0 1; background: $panel; }
    ProblemView #purl { height: 1; padding: 0 1; color: $text-muted; }
    ProblemView VerticalScroll { height: 1fr; }
    ProblemView #pbody { padding: 1 1; }
    """

    def compose(self) -> ComposeResult:
        yield Static(Text("No problem selected."), id="ptitle")
        yield Static("", id="purl")
        with VerticalScroll(id="pscroll"):
            yield Static("", id="pbody")

    def on_mount(self) -> None:
        # rich's markdown defaults (e.g. inline code = cyan on black) assume a
        # dark terminal; derive them from the active Textual theme instead.
        theme = self.app.current_theme
        fg = theme.primary or "cyan"
        bg = theme.panel or theme.surface or ("black" if theme.dark else "white")
        self.app.console.push_theme(
            RichTheme(
                {
                    "markdown.code": f"bold {fg} on {bg}",
                    "markdown.h1": f"bold {theme.accent or fg}",
                    "markdown.h2": f"bold {theme.accent or fg}",
                    "markdown.link_url": f"underline {fg}",
                    "markdown.item.bullet": f"bold {fg}",
                }
            )
        )

    def show(self, q: dict) -> None:
        self.query_one("#ptitle", Static).update(Text.from_markup(core.problem_title_markup(q)))
        self.query_one("#purl", Static).update(core.problem_url(q))
        self.query_one("#pbody", Static).update(
            RichMarkdown(core.problem_markdown(q), code_theme=code_theme())
        )
        self.query_one("#pscroll", VerticalScroll).scroll_home(animate=False)

    def loading(self, label: str) -> None:
        self.query_one("#ptitle", Static).update(Text(label, style="bold"))
        self.query_one("#purl", Static).update("")
        self.query_one("#pbody", Static).update(Text("Fetching…", style="dim"))

    def scroll(self, how: str) -> None:
        sc = self.query_one("#pscroll", VerticalScroll)
        {
            "down": sc.scroll_down,
            "up": sc.scroll_up,
            "page_down": sc.scroll_page_down,
            "page_up": sc.scroll_page_up,
            "home": sc.scroll_home,
            "end": sc.scroll_end,
        }[how](animate=False)


class LogWriter:
    """File-like object that forwards complete lines to the app's log."""

    def __init__(self, app: "MainApp"):
        self.app = app
        self.buf = ""

    def write(self, s: str) -> int:
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.app.call_from_thread(self.app.log_ansi, line)
        return len(s)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        if self.buf:
            self.app.call_from_thread(self.app.log_ansi, self.buf)
            self.buf = ""


@dataclass
class View:
    """One level of the navigation stack."""

    kind: str  # menu | sets | set | tags | tag | search | recent | passed | failed | problem
    arg: str | None = None  # set name, tag slug or problem slug
    label: str = ""
    cursor: int = 0
    filter: str = ""
    rows: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main pane
# ---------------------------------------------------------------------------


class MainApp(App):
    """menu > list > problem, navigated with hjkl; test/submit from anywhere."""

    TITLE = "leetcode"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    #crumb { height: 1; padding: 0 1; background: $primary; color: $text; }
    #filter { height: 1; border: none; padding: 0 1; background: $surface; }
    #table { height: 1fr; }
    #status { height: 1; padding: 0 1; background: $panel; }
    #log { height: 30%; min-height: 6; border-top: solid $panel; padding: 0 1; }
    .hidden { display: none; }
    """

    BINDINGS = [
        Binding("j,down", "down", "Down", show=False),
        Binding("k,up", "up", "Up", show=False),
        Binding("h,escape,backspace", "back", "Back"),
        Binding("l,enter", "forward", "Open"),
        Binding("l", "focus_editor", "Editor", show=False),
        # Menu sections (first letter).
        *[Binding(key, f"section('{kind}')", label) for kind, key, label, _ in MENU],
        Binding("slash", "filter", "Filter"),
        Binding("u", "toggle_unsolved", "Unsolved"),
        Binding("d", "cycle_diff", "Diff"),
        Binding("f", "toggle_freq", "Freq"),
        Binding("r", "random_pick", "Random"),
        Binding("n", "next_pick", "Next", show=False),
        Binding("t", "run_test", "Test"),
        Binding("x", "submit", "Submit"),
        Binding("e", "open_editor", "Edit"),
        Binding("o", "open_browser", "Browser", show=False),
        Binding("ctrl+d,pagedown", "scroll('page_down')", "Page down", show=False),
        Binding("ctrl+u,pageup", "scroll('page_up')", "Page up", show=False),
        Binding("g,home", "scroll('home')", "Top", show=False),
        Binding("G,end", "scroll('end')", "Bottom", show=False),
        Binding("ctrl+r", "refresh", "Refresh", show=False),
        Binding("ctrl+l", "clear_log", "Clear log", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, session: Session):
        super().__init__()
        self.theme = tui_theme()
        self.session = session
        self.editor = EditorBridge(session)
        self.questions: list[dict] = []
        self.index: tuple[dict, dict] | None = None
        self.index_state = "loading"  # loading | online | offline
        self.details: dict[str, dict] = {}
        self.all_sets = get_all_sets()
        self.stack: list[View] = [View("menu")]
        self.unsolved_only = False
        self.diff_filter: str | None = None
        self.sort_freq = False  # sort problem lists by interview frequency
        self.current: dict | None = None  # {"q": detail, "path": file}
        self.busy: str | None = None
        self.recent = load_recent()
        self._filter_timer = None
        self._quit_forced = False

    # -- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="left"):
            yield Static("", id="crumb")
            yield Input(placeholder="filter  (/ to focus, Esc to leave)", id="filter")
            yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
            yield ProblemView(id="problem", classes="hidden")
            yield Static("", id="status")
            yield RichLog(id="log", wrap=True, markup=True, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        cached = load_index_cache()
        if cached:
            self._set_index(cached, state="loading")
        self.render_view()
        self.call_after_refresh(self.render_view)  # real widths once laid out
        self.update_status()
        self.query_one("#table", DataTable).focus()
        self.log_line("[dim]Pick a section by its first letter · hjkl to navigate · ? help[/dim]")
        self.load_index()
        if sync.sync_mode(core.data_dir()) == "drive":
            self.pull_recent(self._log_width())

    @work(thread=True, exclusive=True, group="pull")
    def pull_recent(self, width: int) -> None:
        out, writer = self._out(width)
        try:
            sync.startup_sync(core.data_dir(), out)
        except Exception as e:
            out.print(f"[red]Drive sync failed: {e}[/red]")
        finally:
            writer.close()
        self.call_from_thread(self._recent_pulled)

    def _recent_pulled(self) -> None:
        self.recent = load_recent()
        if self.view.kind == "recent":
            self.render_view()

    def on_resize(self) -> None:
        self.render_view()

    # -- logging --------------------------------------------------------------

    def log_line(self, markup: str) -> None:
        self.query_one("#log", RichLog).write(Text.from_markup(markup))

    def log_ansi(self, line: str) -> None:
        self.query_one("#log", RichLog).write(Text.from_ansi(line))

    # -- index ----------------------------------------------------------------

    @work(thread=True, exclusive=True, group="index")
    def load_index(self) -> None:
        try:
            questions = api.get_all_questions()
        except Exception as e:
            self.call_from_thread(self._index_failed, str(e))
            return
        save_index_cache(questions)
        self.call_from_thread(self._set_index, questions, "online")
        self.call_from_thread(self.log_line, f"[dim]Loaded {len(questions)} problems.[/dim]")

    def _index_failed(self, err: str) -> None:
        self.index_state = "offline"
        self.log_line(f"[yellow]Could not fetch problems from LeetCode: {err}[/yellow]")
        self.log_line("[yellow]Showing cached/offline data. Run `lc auth` if cookies expired.[/yellow]")
        self.update_crumb()

    def _set_index(self, questions: list[dict], state: str) -> None:
        self.questions = questions
        by_slug = {q["titleSlug"]: q for q in questions if q.get("titleSlug")}
        by_id = {str(q["frontendQuestionId"]): q for q in questions if q.get("frontendQuestionId")}
        self.index = (by_slug, by_id)
        self.index_state = state
        self.render_view()

    # -- navigation stack -------------------------------------------------------

    @property
    def view(self) -> View:
        return self.stack[-1]

    def push(self, view: View) -> None:
        self.view.cursor = self.query_one("#table", DataTable).cursor_row or 0
        self.stack.append(view)
        self.render_view(keep_cursor=False)

    def pop(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()
            self.render_view(restore_cursor=True)

    # -- rows for each view kind ---------------------------------------------------

    def _problem_rows(self, view: View) -> list[dict]:
        by_slug = self.index[0] if self.index else {}
        if view.kind == "set":
            return resolve_set_problems(self.all_sets[view.arg]["problems"], self.index)
        if view.kind == "recent":
            rows = []
            for slug in self.recent:
                q = by_slug.get(slug)
                rows.append(
                    question_row(q) if q else {
                        "id": "?", "slug": slug, "title": slug.replace("-", " ").title(),
                        "diff": "?", "status": None, "paid": False, "ac_rate": None,
                        "freq": None, "tags": [],
                    }
                )
            return rows
        rows = [question_row(q) for q in self.questions]
        if view.kind == "tag":
            rows = [r for r in rows if view.arg in r["tags"]]
        elif view.kind == "passed":
            rows = [r for r in rows if r["status"] == "ac"]
        elif view.kind == "failed":
            rows = [r for r in rows if r["status"] == "notac"]
        return sorted(rows, key=_id_key)

    def _tag_rows(self) -> list[dict]:
        tags: dict[str, dict] = {}
        for q in self.questions:
            for t in q.get("topicTags") or []:
                slug = t.get("slug")
                if not slug:
                    continue
                entry = tags.setdefault(
                    slug, {"tag": slug, "name": t.get("name") or slug, "count": 0, "solved": 0}
                )
                entry["count"] += 1
                if q.get("status") == "ac":
                    entry["solved"] += 1
        if not tags:
            return [{"tag": t["slug"], "name": t["name"], "count": 0, "solved": 0} for t in api.get_tags()]
        return sorted(tags.values(), key=lambda e: (-e["count"], e["name"]))

    def _matches(self, row: dict, text: str) -> bool:
        if self.unsolved_only and row["status"] == "ac":
            return False
        if self.diff_filter and row["diff"] != self.diff_filter:
            return False
        if text:
            hay = f"{row['id']} {row['title']} {row['slug'] or ''}".lower()
            return text in hay
        return True

    # -- rendering ---------------------------------------------------------------------

    def render_view(self, keep_cursor: bool = True, restore_cursor: bool = False) -> None:
        view = self.view
        table = self.query_one("#table", DataTable)
        problem = self.query_one("#problem", ProblemView)
        filt = self.query_one("#filter", Input)

        if view.kind == "problem":
            table.add_class("hidden")
            filt.add_class("hidden")
            problem.remove_class("hidden")
            self.set_focus(None)  # app-level hjkl bindings scroll the problem
            self.update_crumb()
            self.refresh_bindings()
            return

        problem.add_class("hidden")
        table.remove_class("hidden")
        filt.set_class(view.kind == "menu", "hidden")
        if not filt.has_focus:
            table.focus()
        if filt.value != view.filter:
            filt.value = view.filter
        text = view.filter.strip().lower()

        if restore_cursor:
            old = view.cursor
        elif keep_cursor:
            old = table.cursor_row or 0
        else:
            old = 0
        table.clear(columns=True)
        width = max(40, table.size.width - 2)  # minus scrollbar

        if view.kind == "menu":
            table.add_column(" ", width=1)
            table.add_column("Section", width=10)
            table.add_column("What", width=max(12, width - 11 - 8))
            view.rows = []
            for kind, key, label, desc in MENU:
                table.add_row(Text(key, style="bold"), label, Text(desc, style="dim"), key=kind)
                view.rows.append({"kind": kind, "label": label})
        elif view.kind == "sets":
            table.add_column("Set", width=max(12, width - (8 + 16 + 10) - 8))
            table.add_column("Solved", width=8)
            table.add_column("Progress", width=16)
            table.add_column("Level", width=10)
            view.rows = []
            for name, body in self.all_sets.items():
                if text and text not in f"{name} {body['description']}".lower():
                    continue
                prog = set_progress(resolve_set_problems(body["problems"], self.index))
                label, color = level_label(prog["pct"])
                bar = int(round(10 * prog["pct"] / 100))
                progress = Text.assemble(
                    ("█" * bar, color), ("░" * (10 - bar), "dim"), f" {prog['pct']:3.0f}%"
                )
                lvl = Text(label if self.index else "-", style=color if self.index else "dim")
                table.add_row(name, f"{prog['solved']}/{prog['total']}", progress, lvl, key=name)
                view.rows.append({"set": name, "label": name})
        elif view.kind == "tags":
            table.add_column("Tag", width=max(12, width - 18 - 6))
            table.add_column("Solved", width=8)
            table.add_column("Total", width=8)
            view.rows = [
                r for r in self._tag_rows() if not text or text in f"{r['tag']} {r['name']}".lower()
            ]
            for r in view.rows:
                table.add_row(r["name"], str(r["solved"]), str(r["count"]), key=r["tag"])
                r["label"] = r["name"]
        else:
            table.add_column(" ", width=1)
            table.add_column("ID", width=5)
            table.add_column("Title", width=max(12, width - (1 + 5 + 6 + 4) - 10))
            table.add_column("Diff", width=6)
            table.add_column("Freq", width=4)
            view.rows = [r for r in self._problem_rows(view) if self._matches(r, text)]
            if self.sort_freq:
                view.rows.sort(key=lambda r: -(r.get("freq") or 0.0))
            for i, r in enumerate(view.rows):
                if r["status"] == "ac":
                    mark = Text("✔", style="green")
                elif r["status"] == "notac":
                    mark = Text("✘", style="red")
                else:
                    mark = Text("")
                title = Text(r["title"])
                if r["paid"]:
                    title.append(" 🔒", style="yellow")
                diff = Text(DIFF_NAME.get(r["diff"], r["diff"]), style=DIFF_COLOR.get(r["diff"], ""))
                freq = r.get("freq")
                freq_cell = Text(f"{freq:3.0f}" if freq else "", style="dim", justify="right")
                table.add_row(mark, r["id"], title, diff, freq_cell, key=f"{i}:{r['slug'] or r['id']}")

        if view.rows:
            table.move_cursor(row=max(0, min(old, len(view.rows) - 1)))
        self.update_crumb()
        self.refresh_bindings()

    def update_crumb(self) -> None:
        crumbs = ["[bold]leetcode[/bold]"] + [v.label or v.kind for v in self.stack[1:]]
        where = " ▸ ".join(crumbs)
        flags = []
        if self.unsolved_only:
            flags.append("unsolved")
        if self.diff_filter:
            flags.append(DIFF_NAME[self.diff_filter].lower())
        if self.sort_freq:
            flags.append("by frequency")
        show_flags = flags and self.view.kind in PROBLEM_LISTS
        flag_str = f"  [dim]({' · '.join(flags)})[/dim]" if show_flags else ""
        if self.index_state == "loading":
            right = "[dim]loading…[/dim]"
        elif self.index_state == "offline":
            right = "[yellow]offline[/yellow]"
        else:
            solved = sum(1 for q in self.questions if q.get("status") == "ac")
            right = f"[dim]{solved} solved / {len(self.questions)}[/dim]"
        self.query_one("#crumb", Static).update(Text.from_markup(f"{where}{flag_str}   {right}"))

    def update_status(self) -> None:
        state = f"[yellow]⏳ {self.busy}[/yellow]  " if self.busy else ""
        if self.current:
            q = self.current["q"]
            path = self.current["path"] or "[red]no python3 snippet[/red]"
            path = path.replace(str(Path.home()), "~")
            line = f"{state}[bold]{q['questionFrontendId']}. {q['title']}[/bold]  [dim]{path}[/dim]"
        else:
            line = f"{state}[dim]No problem open.[/dim]"
        self.query_one("#status", Static).update(Text.from_markup(line))

    def selected_row(self) -> dict | None:
        table = self.query_one("#table", DataTable)
        rows = self.view.rows
        if rows and table.cursor_row is not None and 0 <= table.cursor_row < len(rows):
            return rows[table.cursor_row]
        return None

    # -- which bindings apply where --------------------------------------------------------

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        kind = self.view.kind
        in_menu = kind == "menu"
        in_problem = kind == "problem"
        if action == "section":
            return in_menu
        if action == "forward":
            return not in_problem
        if action == "focus_editor":
            return in_problem and not self.session.standalone
        if action == "scroll":
            return in_problem
        if action in ("run_test", "submit", "open_editor"):
            return not in_menu
        if action in ("random_pick", "next_pick"):
            return kind in PROBLEM_LISTS or kind == "sets"
        if action in ("toggle_unsolved", "cycle_diff", "toggle_freq"):
            return kind in PROBLEM_LISTS
        if action == "filter":
            return not in_menu and not in_problem
        return True

    # -- filter input --------------------------------------------------------------------------

    @on(Input.Changed, "#filter")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        if self.view.filter == event.value:
            return
        self.view.filter = event.value
        if self._filter_timer:
            self._filter_timer.stop()
        self._filter_timer = self.set_timer(0.15, lambda: self.render_view(keep_cursor=False))

    @on(Input.Submitted, "#filter")
    def _on_filter_submitted(self) -> None:
        self.query_one("#table", DataTable).focus()

    # -- navigation actions ----------------------------------------------------------------------

    def action_down(self) -> None:
        if self.view.kind == "problem":
            self.query_one("#problem", ProblemView).scroll("down")
        else:
            self.query_one("#table", DataTable).action_cursor_down()

    def action_up(self) -> None:
        if self.view.kind == "problem":
            self.query_one("#problem", ProblemView).scroll("up")
        else:
            self.query_one("#table", DataTable).action_cursor_up()

    def action_scroll(self, how: str) -> None:
        self.query_one("#problem", ProblemView).scroll(how)

    def action_back(self) -> None:
        filt = self.query_one("#filter", Input)
        if filt.has_focus:
            self.query_one("#table", DataTable).focus()
            return
        self.pop()

    @on(DataTable.RowSelected)
    def _on_row_selected(self) -> None:
        self.action_forward()

    def action_forward(self) -> None:
        view = self.view
        if view.kind == "problem":
            return
        row = self.selected_row()
        if not row:
            return
        if view.kind == "menu":
            self.action_section(row["kind"])
        elif view.kind == "sets":
            self.push(View("set", row["set"], label=row["set"]))
        elif view.kind == "tags":
            self.push(View("tag", row["tag"], label=row["name"]))
        else:
            self.open_row(row)

    def action_section(self, kind: str) -> None:
        label = next(label for k, _, label, _ in MENU if k == kind)
        del self.stack[1:]  # sections hang directly off the menu
        self.push(View(kind, label=label))
        if kind == "search":
            self.query_one("#filter", Input).focus()

    def action_focus_editor(self) -> None:
        self.editor.focus()

    def action_filter(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_toggle_unsolved(self) -> None:
        self.unsolved_only = not self.unsolved_only
        self.render_view()

    def action_cycle_diff(self) -> None:
        order = [None, "E", "M", "H"]
        self.diff_filter = order[(order.index(self.diff_filter) + 1) % len(order)]
        self.render_view()

    def action_toggle_freq(self) -> None:
        self.sort_freq = not self.sort_freq
        self.render_view(keep_cursor=False)

    # -- opening problems ------------------------------------------------------------------------

    def open_row(self, row: dict) -> None:
        if not row.get("slug"):
            self.log_line("[yellow]No slug for this problem (offline); cannot open.[/yellow]")
            return
        if row.get("paid"):
            self.log_line(f"[yellow]{row['title']} is premium-only; opening anyway.[/yellow]")
        label = f"{row['id']}. {row['title']}" if row.get("id") != "?" else row["slug"]
        self.push(View("problem", row["slug"], label=label))
        cached = self.details.get(row["slug"])
        problem = self.query_one("#problem", ProblemView)
        if cached:
            problem.show(cached)
        else:
            problem.loading(label)
        self.open_problem(row["slug"])

    @work(thread=True, exclusive=True, group="open")
    def open_problem(self, slug: str) -> None:
        q = self.details.get(slug)
        if q is None:
            try:
                q = api.get_question_detail(slug)
            except Exception as e:
                self.call_from_thread(self.log_line, f"[red]Failed to fetch {slug}: {e}[/red]")
                return
            if not q:
                self.call_from_thread(self.log_line, f"[red]Problem not found: {slug}[/red]")
                return
            self.details[slug] = q

        # Keep solved status in sync with the index (detail's status may lag).
        if self.index and slug in self.index[0] and self.index[0][slug].get("status") == "ac":
            q["status"] = "ac"

        path, created = None, False
        try:
            path, created = core.write_solution_file(q)
        except ValueError as e:
            self.call_from_thread(self.log_line, f"[red]{e}[/red]")

        self.call_from_thread(self._set_current, q, path, created)
        if path:
            self.editor.open(path)

    def _set_current(self, q: dict, path: str | None, created: bool) -> None:
        self.current = {"q": q, "path": path}
        slug = q["titleSlug"]
        try:
            self.recent = sync.touch_recent(core.data_dir() / sync.RECENT, slug)
        except Exception:
            self.recent = [slug] + [r for r in self.recent if r != slug]
        self.update_status()
        if path:
            short = path.replace(str(Path.home()), "~")
            self.log_line(f"[green]{'Created' if created else 'Opened'}[/green] {short}")
        if self.view.kind == "problem" and self.view.arg == slug:
            self.query_one("#problem", ProblemView).show(q)

    # -- picks ------------------------------------------------------------------------------------

    def _unsolved_candidates(self) -> list[dict]:
        if self.view.kind == "sets":
            row = self.selected_row()
            if not row:
                return []
            pool = [
                r for r in resolve_set_problems(self.all_sets[row["set"]]["problems"], self.index)
                if self._matches(r, "")
            ]
        else:
            pool = self.view.rows
        return [r for r in pool if r["status"] != "ac" and r["slug"] and not r["paid"]]

    def _pick(self, chosen: dict | None, how: str) -> None:
        if not chosen:
            self.log_line("[green]Nothing unsolved here. Nice![/green]")
            return
        if self.view.kind == "sets":
            row = self.selected_row()
            self.push(View("set", row["set"], label=row["set"]))
        for i, r in enumerate(self.view.rows):
            if r["slug"] == chosen["slug"]:
                self.query_one("#table", DataTable).move_cursor(row=i)
                break
        self.log_line(f"[dim]{how}: {chosen['slug']}[/dim]")
        self.open_row(chosen)

    def action_random_pick(self) -> None:
        cands = self._unsolved_candidates()
        self._pick(random.choice(cands) if cands else None, f"Random of {len(cands)} unsolved")

    def action_next_pick(self) -> None:
        cands = self._unsolved_candidates()
        self._pick(cands[0] if cands else None, "Next unsolved")

    # -- editor / browser -----------------------------------------------------------------------

    def action_open_editor(self) -> None:
        if not self.current or not self.current["path"]:
            self.log_line("[yellow]Open a problem first.[/yellow]")
            return
        path = self.current["path"]
        if self.session.standalone:
            with self.suspend():
                self.editor.edit_here(path)
        else:
            self.editor.open(path)
            self.editor.focus()

    def action_open_browser(self) -> None:
        slug = (self.current or {}).get("q", {}).get("titleSlug")
        row = self.selected_row() if self.view.kind != "problem" else None
        if row and row.get("slug"):
            slug = row["slug"]
        if not slug:
            return
        url = f"https://leetcode.com/problems/{slug}/"
        try:
            webbrowser.open(url)
            self.log_line(f"[dim]Opened {url}[/dim]")
        except Exception as e:
            self.log_line(f"[red]Could not open browser: {e}[/red]")

    def action_refresh(self) -> None:
        self.index_state = "loading"
        self.update_crumb()
        self.log_line("[dim]Refreshing problem list…[/dim]")
        self.load_index()

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    def action_help(self) -> None:
        self.log_line(
            "[bold]Keys[/bold]  j/k move (or scroll the problem) · l/Enter open · h back\n"
            "      menu: c curated · t topics · s search · r recent · p passed · f failed\n"
            "      lists: / filter · u unsolved only · d cycle difficulty · f sort by frequency · r random · n next\n"
            "      problem: l jump to editor · ^d/^u page · g/G top/bottom\n"
            "      t test · x submit · e editor · o browser · ^r refresh · ^l clear log\n"
            "      q save, sync the recent list (Drive or git), quit"
        )

    # -- test / submit ----------------------------------------------------------------------------

    def action_run_test(self) -> None:
        self._start_run("test")

    def action_submit(self) -> None:
        self._start_run("submit")

    def _start_run(self, kind: str) -> None:
        if self.busy:
            self.log_line(f"[yellow]Already running: {self.busy}[/yellow]")
            return
        if not self.current or not self.current["path"]:
            self.log_line("[yellow]Open a problem first.[/yellow]")
            return
        self.busy = "testing" if kind == "test" else "submitting"
        self.update_status()
        self.log_line("")
        self.run_solution(kind, self.current["q"], self.current["path"], self._log_width())

    def _log_width(self) -> int:
        return max(40, self.query_one("#log", RichLog).size.width - 4)

    def _out(self, width: int) -> tuple[Console, LogWriter]:
        writer = LogWriter(self)
        console = Console(file=writer, force_terminal=True, color_system="truecolor", width=width)
        return console, writer

    @work(thread=True, exclusive=True, group="run")
    def run_solution(self, kind: str, q: dict, path: str, width: int) -> None:
        self.editor.save_all()
        out, writer = self._out(width)
        try:
            if kind == "test":
                result = core.run_test(path, out, q=q)
            else:
                result = core.run_submit(path, out, q=q)
        except Exception as e:
            out.print(f"[red]{kind} failed: {e}[/red]")
            result = None
        finally:
            writer.close()
        self.call_from_thread(self._run_finished, kind, q, result)

    def _run_finished(self, kind: str, q: dict, result: dict | None) -> None:
        self.busy = None
        if kind == "submit" and result:
            slug = q["titleSlug"]
            accepted = result.get("status_msg") == "Accepted"
            if accepted or q.get("status") != "ac":
                status = "ac" if accepted else "notac"
                q["status"] = status
                if self.index and slug in self.index[0]:
                    self.index[0][slug]["status"] = status
            if self.view.kind == "problem":
                self.query_one("#problem", ProblemView).show(q)
            else:
                self.render_view()
        self.update_status()

    # -- quit: save, commit + push, close panes -------------------------------------------------------

    def action_quit(self) -> None:
        if self.busy and not self._quit_forced:
            self.log_line(f"[yellow]{self.busy}… press q again to quit anyway.[/yellow]")
            self._quit_forced = True
            return
        self.editor.save_all()
        if self._quit_forced or sync.sync_mode(core.data_dir()) == "none":
            self._finish_quit()
            return
        self.busy = "syncing"
        self.update_status()
        self.sync_and_quit(self._log_width())

    @work(thread=True, exclusive=True, group="run")
    def sync_and_quit(self, width: int) -> None:
        out, writer = self._out(width)
        try:
            ok = sync.quit_sync(core.data_dir(), out)
        except Exception as e:
            out.print(f"[red]sync failed: {e}[/red]")
            ok = False
        finally:
            writer.close()
        self.call_from_thread(self._sync_finished, ok)

    def _sync_finished(self, ok: bool) -> None:
        self.busy = None
        self.update_status()
        if ok:
            self._finish_quit()
        else:
            self._quit_forced = True
            self.log_line("[yellow]Sync failed. Fix it in the editor pane, or press q again to quit anyway.[/yellow]")

    def _finish_quit(self) -> None:
        s = self.session
        if not s.standalone:
            if _pane_alive(s.edit_pane):
                _tmux_ok("kill-pane", "-t", s.edit_pane)
            if s.session_dir:
                shutil.rmtree(s.session_dir, ignore_errors=True)
        self.exit()
