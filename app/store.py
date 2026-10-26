"""Per-user dataset persistence (parsed Excel data saved as JSON under instance/datasets)."""
import json
import os

from flask import current_app


def _path(user_id: int) -> str:
    folder = os.path.join(current_app.instance_path, "datasets")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"user_{int(user_id)}.json")


def load_dataset(user_id: int) -> dict:
    p = _path(user_id)
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def save_dataset(user_id: int, dataset: dict) -> None:
    p = _path(user_id)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(dataset, fh, ensure_ascii=False)
    os.replace(tmp, p)


def clear_dataset(user_id: int) -> None:
    p = _path(user_id)
    if os.path.exists(p):
        os.remove(p)


def upload_folder(user_id: int) -> str:
    folder = os.path.join(current_app.instance_path, "uploads", f"user_{int(user_id)}")
    os.makedirs(folder, exist_ok=True)
    return folder
