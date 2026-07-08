"""Loads the competitor watchlist and settings from config/watchlist.yaml."""
from pathlib import Path
import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "watchlist.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_competitors(path: Path = CONFIG_PATH) -> list[dict]:
    """Return the list of competitor pages to monitor."""
    return load_config(path).get("competitors", [])


def get_settings(path: Path = CONFIG_PATH) -> dict:
    return load_config(path).get("settings", {})