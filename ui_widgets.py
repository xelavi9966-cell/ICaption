import tkinter as tk
from tkinter import ttk

class ScrollableFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        root = self.winfo_toplevel()
        bg = "#f3f3f3"
        try:
            bg = root.cget("bg")
        except Exception:
            pass

        self.canvas = tk.Canvas(self, highlightthickness=0, bg=bg)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas_window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self._bind_mousewheel(self.canvas)

    def _on_inner_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._update_scrollbar_visibility()

    def _on_canvas_configure(self, event):
        self.canvas.itemconfig(self.canvas_window, width=event.width)
        self._update_scrollbar_visibility()

    def _content_is_scrollable(self) -> bool:
        bbox = self.canvas.bbox("all")
        if not bbox:
            return False
        content_h = bbox[3] - bbox[1]
        view_h = self.canvas.winfo_height()
        return content_h > view_h + 2

    def _update_scrollbar_visibility(self):
        if self._content_is_scrollable():
            if not self.scrollbar.winfo_ismapped():
                self.scrollbar.pack(side="right", fill="y")
        else:
            if self.scrollbar.winfo_ismapped():
                self.scrollbar.pack_forget()
            # сброс позиции, чтобы не было “пролистывания в пустоту”
            self.canvas.yview_moveto(0)

    def _bind_mousewheel(self, widget):
        def on_mousewheel(event):
            # прокручиваем только если есть что прокручивать
            bbox = self.canvas.bbox("all")
            if not bbox:
                return
            content_h = bbox[3] - bbox[1]
            view_h = self.canvas.winfo_height()
            if content_h <= view_h + 2:
                return

            if event.delta:
                self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return "break"

        def _bind(_e=None):
            widget.bind("<MouseWheel>", on_mousewheel)

        def _unbind(_e=None):
            widget.unbind("<MouseWheel>")

        widget.bind("<Enter>", _bind)
        widget.bind("<Leave>", _unbind)