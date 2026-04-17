import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "leetcode-cli"
CONFIG_FILE = CONFIG_DIR / "config.json"

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=4)

def update_config(key: str, value: any):
    config = load_config()
    config[key] = value
    save_config(config)

def get_config(key: str, default=None):
    config = load_config()
    return config.get(key, default)
