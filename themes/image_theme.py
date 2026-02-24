# themes/image_theme.py
import os
import tkinter as tk
from tkinter import ttk

def _load_png(root: tk.Tk, path: str, image_refs: dict, key: str):
    img = tk.PhotoImage(master=root, file=path)
    image_refs[key] = img
    return img

def apply_image_theme(root: tk.Tk, style: ttk.Style, assets_dir: str, image_refs: dict):
    """
    PNG theme scaffold:
    - Custom.TButton uses images for normal/active/pressed/disabled
    - Custom.TFrame uses a background image (optional)
    You can expand to entries/combobox/scrollbar later.
    """
    style.theme_use("clam")

    theme_dir = os.path.join(assets_dir, "theme_image")

    # REQUIRED placeholders
    btn_normal = _load_png(root, os.path.join(theme_dir, "button_normal.png"), image_refs, "btn_normal")
    btn_active = _load_png(root, os.path.join(theme_dir, "button_active.png"), image_refs, "btn_active")
    btn_pressed = _load_png(root, os.path.join(theme_dir, "button_pressed.png"), image_refs, "btn_pressed")
    btn_disabled = _load_png(root, os.path.join(theme_dir, "button_disabled.png"), image_refs, "btn_disabled")

    # OPTIONAL
    frame_bg_path = os.path.join(theme_dir, "frame_bg.png")
    frame_bg = None
    if os.path.exists(frame_bg_path):
        frame_bg = _load_png(root, frame_bg_path, image_refs, "frame_bg")

    # 9-slice-like borders: left, top, right, bottom
    # Подгони эти значения под свои PNG (рамка/тени)
    border = (8, 8, 8, 8)

    # --- Custom Button element ---
    # создаём элемент, который умеет разные изображения по состояниям
    style.element_create(
        "Custom.Button.border",
        "image",
        btn_normal,
        ("active", btn_active),
        ("pressed", btn_pressed),
        ("disabled", btn_disabled),
        border=border,
        sticky="nsew"
    )
    style.layout(
        "Custom.TButton",
        [
            ("Custom.Button.border", {"sticky": "nsew", "children": [
                ("Button.padding", {"sticky": "nsew", "children": [
                    ("Button.label", {"sticky": "nsew"})
                ]})
            ]})
        ]
    )
    style.configure("Custom.TButton", padding=(12, 8))

    # --- Custom Frame element (optional) ---
    if frame_bg is not None:
        style.element_create(
            "Custom.Frame.bg",
            "image",
            frame_bg,
            border=border,
            sticky="nsew"
        )
        style.layout("Custom.TFrame", [("Custom.Frame.bg", {"sticky": "nsew"})])
    else:
        # если нет картинки фона — просто базовый тёмный/нейтральный фон
        style.configure("Custom.TFrame", background="#202020")

    # базовые цвета текста для PNG темы (можешь менять)
    style.configure("TLabel", foreground="#f0f0f0", background="#202020")
    root.configure(bg="#202020")
