# themes/light.py
import tkinter as tk
from tkinter import ttk

def apply_light(root: tk.Tk, style: ttk.Style):

    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(".", background="#f3f3f3", foreground="#111111")
    style.configure("TFrame", background="#f3f3f3")
    style.configure("TLabel", background="#f3f3f3", foreground="#111111")
    style.configure("TButton", padding=6)
    style.configure("TCombobox", padding=4)
    # базовая тема ttk
    style.theme_use("clam")

    # базовые цвета
    bg = "#f3f3f3"
    fg = "#111111"
    field_bg = "#ffffff"
    border = "#c8c8c8"

    root.configure(bg=bg)

    style.configure(".", background=bg, foreground=fg)
    style.configure("TFrame", background=bg)
    style.configure("TLabel", background=bg, foreground=fg)

    style.configure("TButton", padding=(10, 6))
    style.map("TButton",
              foreground=[("disabled", "#888888")])

    style.configure("TEntry", fieldbackground=field_bg, bordercolor=border)
    style.configure("TCombobox", fieldbackground=field_bg)

    style.configure("TSeparator", background=border)

    # ===== Custom Checkbutton (GREEN filled, no tick) via images =====
    if not hasattr(root, "_theme_imgrefs"):
        root._theme_imgrefs = {}

    def _mk_box(name: str, size: int, bgc: str, fillc: str | None, borderc: str):
        img = tk.PhotoImage(master=root, width=size, height=size)
        # background
        img.put(bgc, to=(0, 0, size, size))
        # border 1px
        img.put(borderc, to=(0, 0, size, 1))
        img.put(borderc, to=(0, size-1, size, size))
        img.put(borderc, to=(0, 0, 1, size))
        img.put(borderc, to=(size-1, 0, size, size))
        # fill
        if fillc:
            img.put(fillc, to=(1, 1, size-1, size-1))
        root._theme_imgrefs[name] = img
        return img

    size = 16
    unchecked = _mk_box("cb_light_unchecked", size, bg, None, "#2b2b2b")
    checked = _mk_box("cb_light_checked", size, bg, "#1f7a1f", "#2b2b2b")  # тёмно-зелёный

    # indicator element (image-based)
    #style.element_create(
    #    "Green.Checkbutton.indicator",
    #    "image",
    #    unchecked,
    #    ("selected", checked),
    #    sticky="w"
    #)

    #style.configure("Green.TCheckbutton", background=bg, foreground=fg)

    # ===== Custom Checkbutton style: green filled, no tick (safe layout clone) =====
    if not hasattr(root, "_theme_imgrefs"):
        root._theme_imgrefs = {}

    def _mk_box(name: str, size: int, bgc: str, fillc: str | None, borderc: str):
        img = tk.PhotoImage(master=root, width=size, height=size)
        img.put(bgc, to=(0, 0, size, size))
        img.put(borderc, to=(0, 0, size, 1))
        img.put(borderc, to=(0, size - 1, size, size))
        img.put(borderc, to=(0, 0, 1, size))
        img.put(borderc, to=(size - 1, 0, size, size))
        if fillc:
            img.put(fillc, to=(1, 1, size - 1, size - 1))
        root._theme_imgrefs[name] = img
        return img

    def _replace_indicator(lspec, new_elem: str):
        # lspec: list of (elementName, dict)
        out = []
        for elem_name, opts in lspec:
            if elem_name == "Checkbutton.indicator":
                elem_name = new_elem
            new_opts = dict(opts) if isinstance(opts, dict) else opts
            if isinstance(new_opts, dict) and "children" in new_opts and isinstance(new_opts["children"], list):
                new_opts["children"] = _replace_indicator(new_opts["children"], new_elem)
            out.append((elem_name, new_opts))
        return out

    size = 16
    unchecked = _mk_box("icap_cb_light_unchecked", size, bg, None, "#2b2b2b")
    checked   = _mk_box("icap_cb_light_checked",   size, bg, "#1f7a1f", "#2b2b2b")  # тёмно-зелёный

    indicator_elem = "ICap.Light.indicator"
    if indicator_elem not in style.element_names():
        style.element_create(indicator_elem, "image", unchecked, ("selected", checked), sticky="w")

    base_layout = style.layout("TCheckbutton")  # берем валидный layout темы
    new_layout = _replace_indicator(base_layout, indicator_elem)

    style.layout("ICap.Light.TCheckbutton", new_layout)
    style.configure("ICap.Light.TCheckbutton", background=bg, foreground=fg)

