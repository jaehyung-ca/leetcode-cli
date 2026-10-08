import base64
import json
import tempfile
import threading
import time
import webbrowser
import sqlite3
import shutil
import glob
import configparser
from pathlib import Path
from rich.console import Console
from config import update_config, get_config

console = Console()

LOGIN_URL = "https://leetcode.com/accounts/login/"
LOGIN_TIMEOUT = 300

# Where login prompts go; the TUI points this at its log pane.
notify = console.print

def _firefox_base_dirs():
    home = Path.home()
    return [
        home / ".mozilla" / "firefox",
        home / "Library" / "Application Support" / "Firefox",
    ]

def _find_firefox_cookies_db():
    candidates = []
    for base_dir in _firefox_base_dirs():
        ini_path = base_dir / "profiles.ini"
        if ini_path.exists():
            config = configparser.ConfigParser()
            config.read(ini_path)
            for section in config.sections():
                if section.startswith("Install") and config.get(section, "Default", fallback=""):
                    candidates.append(base_dir / config.get(section, "Default"))
            for section in config.sections():
                if config.get(section, "Default", fallback="0") == "1":
                    rel = config.get(section, "IsRelative", fallback="1") == "1"
                    path = config.get(section, "Path", fallback="")
                    if not path:
                        continue
                    candidates.append((base_dir / path) if rel else Path(path))
            for section in config.sections():
                path = config.get(section, "Path", fallback="")
                if path:
                    rel = config.get(section, "IsRelative", fallback="1") == "1"
                    candidates.append((base_dir / path) if rel else Path(path))

        for path in glob.glob(str(base_dir / "*.default-release*")):
            candidates.append(Path(path))
        for path in glob.glob(str(base_dir / "*.default*")):
            candidates.append(Path(path))

    seen = set()
    for profile_dir in candidates:
        if not profile_dir or profile_dir in seen:
            continue
        seen.add(profile_dir)
        cookies_db = profile_dir / "cookies.sqlite"
        if cookies_db.exists():
            return cookies_db

    matches = []
    for base_dir in _firefox_base_dirs():
        matches.extend(glob.glob(str(base_dir / "*" / "cookies.sqlite")))
    return Path(matches[0]) if matches else None


def _read_leetcode_cookies(cookies_db_path):
    if not cookies_db_path or not cookies_db_path.is_file():
        raise FileNotFoundError(f"cookies.sqlite not found: {cookies_db_path}")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_db = Path(tmpdir) / "cookies.sqlite"
        shutil.copy2(cookies_db_path, tmp_db)
        wal = cookies_db_path.with_name(cookies_db_path.name + "-wal")
        if wal.exists():
            shutil.copy2(wal, tmp_db.with_name(tmp_db.name + "-wal"))
        conn = sqlite3.connect(str(tmp_db))
        try:
            cursor = conn.execute(
                """
                SELECT name, value
                FROM moz_cookies
                WHERE name IN ('csrftoken', 'LEETCODE_SESSION', 'cf_clearance')
                  AND host LIKE '%leetcode.com'
                ORDER BY lastAccessed DESC
                """
            )
            values = {}
            for name, value in cursor.fetchall():
                if name not in values:
                    values[name] = value
            return values
        finally:
            conn.close()

def _read_leetcode_cookies_from_firefox():
    """Return LeetCode cookies from Firefox's cookies.sqlite, or {} if unavailable."""
    cookies_db = _find_firefox_cookies_db()
    if not cookies_db or not cookies_db.is_file():
        return {}
    return _read_leetcode_cookies(cookies_db)


def _read_leetcode_cookies_from_brave():
    """Return LeetCode cookies from Brave via browser_cookie3, or {} if unavailable.

    Brave is Chromium-based and stores cookie values encrypted with a key from
    the OS keyring, so we lean on browser_cookie3 to handle decryption for us.
    """
    try:
        import browser_cookie3
    except ImportError:
        return {}

    wanted = {"csrftoken", "LEETCODE_SESSION", "cf_clearance"}
    try:
        jar = browser_cookie3.brave(domain_name="leetcode.com")
    except Exception:
        return {}

    values = {}
    for cookie in jar:
        if cookie.name in wanted and cookie.name not in values:
            values[cookie.name] = cookie.value
    return values


def _read_browser_cookies():
    """Return (browser name, cookies) from the first browser logged in to LeetCode."""
    sources = [
        ("Brave", _read_leetcode_cookies_from_brave),
        ("Firefox", _read_leetcode_cookies_from_firefox),
    ]
    for name, reader in sources:
        try:
            values = reader()
        except Exception:
            continue
        if "csrftoken" in values and "LEETCODE_SESSION" in values:
            return name, values
    return None, {}


def _store_cookies(values):
    update_config("LEETCODE_SESSION", values["LEETCODE_SESSION"])
    update_config("csrftoken", values["csrftoken"])
    # Store everything we got just in case we need cf_clearance
    update_config("ALL_COOKIES", values)


def _session_expired(values):
    """True if the LEETCODE_SESSION token says it has expired.

    The token is a JWT whose payload carries refreshed_at and _session_expiry
    (a lifetime in seconds); anything unreadable counts as not expired.
    """
    try:
        payload = values["LEETCODE_SESSION"].split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return data["refreshed_at"] + data["_session_expiry"] < time.time()
    except Exception:
        return False


def _signed_in(values):
    """Ask LeetCode whether these cookies are a signed-in session."""
    from curl_cffi import requests

    try:
        response = requests.post(
            "https://leetcode.com/graphql",
            json={"query": "{ userStatus { isSignedIn } }"},
            headers={"x-csrftoken": values["csrftoken"], "Referer": "https://leetcode.com/"},
            cookies=values,
            impersonate="chrome",
            timeout=15,
        )
        return bool(response.json()["data"]["userStatus"]["isSignedIn"])
    except Exception:
        # Network trouble is not a reason to send the user to the login page.
        return True


_refresh_lock = threading.Lock()
_refreshed = False
_login_given_up = False


def _login_via_browser(stale_session):
    """Open the LeetCode login page and wait for a new session in the browser.

    Returns the new cookies, or {} if no browser could be opened or the user
    did not log in within LOGIN_TIMEOUT.  Gives up for the rest of the process
    after one failed attempt so a session without a browser does not keep waiting.
    """
    global _login_given_up
    if _login_given_up:
        return {}
    _login_given_up = True
    try:
        opened = webbrowser.open(LOGIN_URL)
    except Exception:
        opened = False
    if not opened:
        notify(f"[yellow]LeetCode login expired. Log in at {LOGIN_URL}, then run `lc auth`.[/yellow]")
        return {}

    notify("[yellow]LeetCode login expired. Log in in the browser window that just opened; waiting...[/yellow]")
    deadline = time.time() + LOGIN_TIMEOUT
    while time.time() < deadline:
        time.sleep(3)
        name, values = _read_browser_cookies()
        if (
            values
            and values["LEETCODE_SESSION"] != stale_session
            and not _session_expired(values)
            and _signed_in(values)
        ):
            notify(f"[green]Logged in to LeetCode (cookies from {name}).[/green]")
            _login_given_up = False
            return values
    notify("[red]Timed out waiting for the LeetCode login. Run `lc auth` after logging in.[/red]")
    return {}


def refresh_cookies(rejected=None):
    """Pull fresh cookies from the browser into the config.

    Without arguments this runs once per process.  Pass the LEETCODE_SESSION
    LeetCode just rejected to look again; if the browser has nothing newer and
    LeetCode confirms the session is signed out, the user is sent to log in.
    Returns True if new usable cookies were stored.
    """
    global _refreshed
    with _refresh_lock:
        if _refreshed and rejected is None:
            return False
        _refreshed = True
        _, values = _read_browser_cookies()
        stale = values.get("LEETCODE_SESSION")
        if (
            not values
            or _session_expired(values)
            or (stale == rejected and not _signed_in(values))
        ):
            values = _login_via_browser(stale or rejected)
        if not values or values["LEETCODE_SESSION"] == rejected:
            return False
        if values != get_config("ALL_COOKIES"):
            _store_cookies(values)
        return True


def extract_cookies():
    """Extract LEETCODE_SESSION and csrftoken from Firefox or Brave."""
    name, values = _read_browser_cookies()
    if values:
        _store_cookies(values)
        console.print(f"[bold green]Successfully extracted LeetCode cookies from {name}![/bold green]")
        return True

    console.print("[bold yellow]Could not find LeetCode cookies in Firefox or Brave.[/bold yellow]")
    return False

def get_auth_cookies() -> dict:
    refresh_cookies()
    all_cookies = get_config("ALL_COOKIES", {})
    if all_cookies:
        return all_cookies
    
    session = get_config("LEETCODE_SESSION")
    csrf = get_config("csrftoken")
    if not session or not csrf:
        return {}
    return {
        "LEETCODE_SESSION": session,
        "csrftoken": csrf
    }

def get_auth_headers() -> dict:
    refresh_cookies()
    csrf = get_config("csrftoken")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Referer": "https://leetcode.com/",
        "Content-Type": "application/json"
    }
    if csrf:
        headers["x-csrftoken"] = csrf
    return headers
