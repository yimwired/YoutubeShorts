import json
import os

_FILE = "topic_history.json"
_MAX  = 200


def load_history() -> list[str]:
    if not os.path.exists(_FILE):
        return []
    with open(_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_topic(title_en: str) -> None:
    history = load_history()
    if title_en and title_en not in history:
        history.append(title_en)
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(history[-_MAX:], f, ensure_ascii=False, indent=2)
