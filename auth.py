import tempfile
import sqlite3
import shutil
import glob
import configparser
from pathlib import Path
from rich.console import Console
from config import update_config, get_config

console = Console()

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

def extract_cookies():
    """Extract LEETCODE_SESSION and csrftoken directly using native firefox parsing."""
    cookies_db = _find_firefox_cookies_db()
    if not cookies_db or not cookies_db.is_file():
        console.print("[bold yellow]Could not find a Firefox cookies.sqlite.[/bold yellow]")
        return False
        
    values = _read_leetcode_cookies(cookies_db)
    if "csrftoken" not in values or "LEETCODE_SESSION" not in values:
        console.print("[bold yellow]Could not find LeetCode cookies in Firefox.[/bold yellow]")
        return False

    update_config("LEETCODE_SESSION", values["LEETCODE_SESSION"])
    update_config("csrftoken", values["csrftoken"])
    
    # Store everything we got just in case we need cf_clearance
    update_config("ALL_COOKIES", values)
    
    console.print(f"[bold green]Successfully extracted LeetCode cookies natively from Firefox sqlite![/bold green]")
    return True

def get_auth_cookies() -> dict:
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
    csrf = get_config("csrftoken")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Referer": "https://leetcode.com/",
        "Content-Type": "application/json"
    }
    if csrf:
        headers["x-csrftoken"] = csrf
    return headers
