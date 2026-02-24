import os, re, json
from typing import Dict, List

def normalize_trigger(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s

def load_triggers(triggers_path: str) -> list[str]:
    if not os.path.exists(triggers_path):
        with open(triggers_path, "w", encoding="utf-8") as f:
            f.write("")
        return []

    with open(triggers_path, "r", encoding="utf-8") as f:
        data = f.read()

    raw = [x for x in re.split(r",|\n", data) if x.strip()]
    triggers = [normalize_trigger(x) for x in raw]

    seen = set()
    out = []
    for t in triggers:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out

def save_triggers(triggers_path: str, triggers: list[str]) -> None:
    content = ", ".join(triggers)
    with open(triggers_path, "w", encoding="utf-8") as f:
        f.write(content)

def load_translations(translations_path: str) -> dict[str, str]:
    if not os.path.exists(translations_path):
        with open(translations_path, "w", encoding="utf-8") as f:
            f.write("")
        return {}

    translations: dict[str, str] = {}
    with open(translations_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = normalize_trigger(k)
            v = v.strip()
            if k:
                translations[k] = v
    return translations
    
def save_translations(translations_path: str, translations: dict[str, str]) -> None:
    lines = [f"{k}={translations[k]}" for k in sorted(translations.keys())]
    with open(translations_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))

def upsert_translation(translations_path: str, key: str, value: str) -> None:
    key = normalize_trigger(key)
    value = value.strip()
    translations = load_translations(translations_path)
    translations[key] = value
    lines = [f"{k}={translations[k]}" for k in sorted(translations.keys())]
    with open(translations_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))

def load_groups(groups_path: str) -> dict[str, list[str]]:
    if not os.path.exists(groups_path):
        with open(groups_path, "w", encoding="utf-8") as f:
            f.write("[Default]\n")
        return {"Default": []}

    groups: dict[str, list[str]] = {}
    current = None

    with open(groups_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^\[(.+?)\]$", line)
            if m:
                current = m.group(1).strip()
                groups.setdefault(current, [])
                continue
            if current is None:
                continue
            parts = [p.strip() for p in re.split(r",", line) if p.strip()]
            for p in parts:
                t = normalize_trigger(p)
                if t and t not in groups[current]:
                    groups[current].append(t)

    if not groups:
        groups = {"Default": []}
    return groups

def save_groups(groups_path: str, groups: dict[str, list[str]]) -> None:
    lines = []
    for gname in sorted(groups.keys(), key=lambda s: s.lower()):
        lines.append(f"[{gname}]")
        for t in groups[gname]:
            lines.append(t)
        lines.append("")  # пустая строка между группами
    with open(groups_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n")

def parse_caption_tokens(text: str) -> list[str]:
    """
    Парсит caption в список токенов.
    Поддержка:
      - разделители: запятая, перенос строки
      - лишние пробелы
    """
    text = text.strip()
    if not text:
        return []
    parts = re.split(r",|\n", text)
    tokens = [normalize_trigger(p) for p in parts if p.strip()]
    # уникализируем, сохраняя порядок
    seen = set()
    out = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out

def load_settings(path: str) -> dict:
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_settings(path: str, data: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass