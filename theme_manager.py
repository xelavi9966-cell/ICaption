# theme_manager.py
import os
import tkinter as tk
from tkinter import ttk

from themes.light import apply_light
from themes.dark import apply_dark
from themes.image_theme import apply_image_theme

THEMES = ("Light", "Dark", "Image")


class ThemeManager:
    def __init__(self, root: tk.Tk, assets_dir: str):
        self.root = root
        self.style = ttk.Style(root)
        self.assets_dir = assets_dir
        self._image_refs = {}  # чтобы PhotoImage не удалялись GC

    def available(self):
        return THEMES

    def apply(self, name: str):
        name = name.strip()
        # сброс ссылок на изображения при смене темы
        self._image_refs = {}

        if name == "Light":
            apply_light(self.root, self.style)
        elif name == "Dark":
            apply_dark(self.root, self.style)
        elif name == "Image":
            apply_image_theme(self.root, self.style, self.assets_dir, self._image_refs)
        else:
            apply_light(self.root, self.style)

        if hasattr(self.root, "_render_trigger_list"):
            try:
                self.root._render_trigger_list()
            except Exception:
                pass

        # обновление
        self.root.update_idletasks()

        if hasattr(self.root, "on_theme_applied"):
            self.root.on_theme_applied(name)