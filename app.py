import sys, os
import tkinter as tk
import threading
from tkinter import ttk, filedialog, messagebox, simpledialog
from PIL import Image, ImageTk, ImageDraw
from collections import Counter

from constants import (
    APP_TITLE, DEFAULT_TRIGGERS_FILE, DEFAULT_TRANSLATIONS_FILE, DEFAULT_GROUPS_FILE,
    CAPTION_JOINER, IMAGE_EXTS, SETTINGS_FILE
)
from io_store import (
    normalize_trigger,
    load_triggers, save_triggers,
    load_translations, save_translations, upsert_translation,
    load_groups, save_groups,
    parse_caption_tokens,
    load_settings, save_settings
)
from ui_widgets import ScrollableFrame

class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1100x700")

        # ===== UI STATE =====
        self.current_group = tk.StringVar(value="All")
        self.group_title = tk.StringVar(value="Group: All")
        self.filter_var = tk.StringVar(value="")
        self.auto_save_var = tk.BooleanVar(value=False)

        # ===== DATA STATE =====
        self.triggers_path = os.path.abspath(DEFAULT_TRIGGERS_FILE)
        self.translations_path = os.path.abspath(DEFAULT_TRANSLATIONS_FILE)
        self.groups_path = os.path.abspath(DEFAULT_GROUPS_FILE)

        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(__file__)

        self.settings_path = os.path.join(base, "settings.json")
        self.settings = load_settings(self.settings_path)

        self.group_order = self.settings.get("group_order", [])
        if not isinstance(self.group_order, list):
            self.group_order = []

        if not isinstance(self.group_order, list):
            self.group_order = []

        self.triggers = load_triggers(self.triggers_path)
        self.translations = load_translations(self.translations_path) or {}
        self.groups = load_groups(self.groups_path)

        self.var_map = {}
        self.selected_set = set()
        self.loaded_caption_tokens = []
        self.unknown_caption_tokens = []

        self._temp_caption_group_name = None
        self._temp_caption_triggers = []
        self._temp_caption_groups_path = os.path.join(os.path.dirname(self.settings_path), "_temp_caption_groups.txt")

        # ===== runtime state =====
        self.current_image_path = None
        self.current_pil_image = None
        self.original_pil_image = None
        self.current_tk_image = None

        self._resize_after_id = None
        self._load_job_id = 0
        self._loading_label_id = None

        self.folder_images = []
        self.folder_index = -1

        self._suppress_tree_select = False
        self.deleted_triggers = set()

        # ===== folder/list UI state =====
        self.current_folder = None
        self.image_tree = None
        self._imglist_check_icon = None
        self._imglist_iid_to_path = {}
        self._imglist_path_to_iid = {}

        from theme_manager import ThemeManager

        self.theme_manager = ThemeManager(self, assets_dir=os.path.abspath("assets"))

        saved_theme = self.settings.get("theme", "Light")
        self.theme_var = tk.StringVar(value=saved_theme if saved_theme else "Light")

        # ===== UI BUILD =====
        self._resize_job_id = 0
        print("INIT resize_job_id =", self._resize_job_id)
        self._build_ui()
        self.image_tree.tag_configure("hascap", foreground="#BB9F00")

        self.theme_manager.apply(self.theme_var.get())
        self._apply_combobox_theme(self.theme_var.get())

        self._render_trigger_list()
        self.after(0, self._open_last_folder_on_start)

        self.theme_var.trace_add("write", lambda *_: self._save_settings())
        self._used_triggers_windows = []
        self._themed_dialogs = []

    def _open_last_folder_on_start(self):
        folder = (self.settings or {}).get("last_folder")
        if not folder:
            return
        if not os.path.isdir(folder):
            return

        self.current_folder = folder

        self._set_status(f"Opening last folder: {folder}")
        try:
            self.open_folder_btn.configure(state="disabled")
        except Exception:
            pass

        t = threading.Thread(target=self._scan_folder_worker, args=(folder,), daemon=True)
        t.start()

    def _save_settings(self):
        self.settings["theme"] = self.theme_var.get()
        self.settings["group_order"] = self.group_order

        if getattr(self, "current_folder", None) and os.path.isdir(self.current_folder):
            self.settings["last_folder"] = self.current_folder
        else:
            if "last_folder" in self.settings:
                self.settings.pop("last_folder", None)

        save_settings(self.settings_path, self.settings)

    def _trigger_to_group(self, t: str) -> str | None:
        for gname, items in self.groups.items():
            if t in items:
                return gname
        return None

    def _ordered_selected_triggers_for_caption(self) -> list[str]:
        trig_to_group = {}
        for gname, arr in (self.groups or {}).items():
            for tr in arr:
                trig_to_group[tr] = gname

        order = self.group_order or []
        pr = {g: i for i, g in enumerate(order)}
        unknown_pr = 10_000

        #selected = [t for t in self.triggers if t in self.selected_set]
        selected = [t for t in self._get_all_triggers_for_ui() if t in self.selected_set]

        selected.sort(key=lambda t: (
            pr.get(trig_to_group.get(t, ""), unknown_pr),
            (trig_to_group.get(t, "") or "").lower(),
            t.lower()
        ))
        return selected

    def _group_cycle_list(self) -> list[str]:
        vals = self._group_values()
        return vals if vals else ["All"]

    def next_group(self):
        vals = self._group_cycle_list()
        cur = self.current_group.get() or "All"
        if cur not in vals:
            cur = "All"
        i = vals.index(cur)
        self.current_group.set(vals[(i + 1) % len(vals)])
        self._render_trigger_list()

    def prev_group(self):
        vals = self._group_cycle_list()
        cur = self.current_group.get() or "All"
        if cur not in vals:
            cur = "All"
        i = vals.index(cur)
        self.current_group.set(vals[(i - 1) % len(vals)])
        self._render_trigger_list()

    def _clear_selections_for_next_image(self):
        self.selected_set.clear()
        self.loaded_caption_tokens = []
        self.unknown_caption_tokens = []
        for var in self.var_map.values():
            var.set(False)

    def _hotkeys_allowed(self) -> bool:
        w = self.focus_get()
        if w is None:
            return True
        cls = w.winfo_class()
        return cls not in {"Entry", "TEntry", "Text", "TCombobox", "Combobox", "Spinbox", "TSpinbox"}

    def _clear_temp_caption_group(self):
        if not self._temp_caption_group_name:
            return

        g = self._temp_caption_group_name
        self.groups.pop(g, None)

        if isinstance(self.group_order, list):
            self.group_order = [x for x in self.group_order if x != g]

        self._temp_caption_group_name = None
        self._temp_caption_triggers = []

        try:
            with open(self._temp_caption_groups_path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass

    def _get_all_triggers_for_ui(self) -> list[str]:
        base = list(self.triggers)

        for t in getattr(self, "_temp_caption_triggers", []):
            if t not in base:
                base.append(t)

        return base

    def _apply_temp_caption_group_for_image(self, image_path: str):
        self._clear_temp_caption_group()

        cap_path = os.path.splitext(image_path)[0] + ".caption"
        if not os.path.exists(cap_path):
            return

        try:
            with open(cap_path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return

        tokens = parse_caption_tokens(text)
        trigger_set = set(self.triggers)

        unknown = []
        for t in tokens:
            if t in trigger_set:
                continue
            if hasattr(self, "deleted_triggers") and t in self.deleted_triggers:
                continue
            if t not in unknown:
                unknown.append(t)

        if not unknown:
            return

        base = os.path.basename(image_path)
        group_name = f"{base}.caption"

        self.groups[group_name] = list(unknown)
        self._temp_caption_group_name = group_name
        self._temp_caption_triggers = list(unknown)

        try:
            with open(self._temp_caption_groups_path, "w", encoding="utf-8") as f:
                f.write(f"[{group_name}]\n")
                f.write(", ".join(unknown) + "\n")
        except Exception:
            pass

    def manage_trigger(self):
        if not self.triggers:
            messagebox.showinfo("Info", "No triggers to manage.")
            return

        win = tk.Toplevel(self)
        win.title("Manage Trigger")
        win.geometry("520x240")
        win.transient(self)
        win.grab_set()

        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Trigger:").grid(row=0, column=0, sticky="w")
        trig_var = tk.StringVar(value=self.triggers[0])
        trig_cb = ttk.Combobox(frm, textvariable=trig_var, state="readonly", values=self.triggers, style="ICap.TCombobox")
        trig_cb.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(frm, text="Translation:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        tr_var = tk.StringVar(value=self.translations.get(trig_var.get(), ""))
        tr_entry = ttk.Entry(frm, textvariable=tr_var)
        tr_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(10, 0))

        ttk.Label(frm, text="Move to group:").grid(row=2, column=0, sticky="w", pady=(10, 0))

        grp_var = tk.StringVar(value="")
        grp_cb = ttk.Combobox(frm, textvariable=grp_var, state="normal", values=self._group_values()[1:], style="ICap.TCombobox")
        grp_cb.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(10, 0))
        ttk.Label(frm, text="(empty = don't change group)").grid(row=3, column=1, sticky="w", padx=(8, 0))

        frm.columnconfigure(1, weight=1)

        def refresh_fields(*_):
            t = trig_var.get()
            tr_var.set(self.translations.get(t, ""))

        trig_cb.bind("<<ComboboxSelected>>", refresh_fields)

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))

        ttk.Button(btns, text="Apply", command=lambda: self._apply_trigger_changes(win, trig_var.get(), tr_var.get(), grp_var.get())).pack(side="left")
        ttk.Button(btns, text="Delete", command=lambda: self._delete_trigger(win, trig_var.get())).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="left", padx=(8, 0))

    def _on_image_area_resize(self, _event=None):
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except Exception:
                pass
            self._resize_after_id = None

        if self.original_pil_image is None:
            return

        self._resize_after_id = self.after(120, self._resize_preview_async)

    def _resize_preview_worker(self, job_id: int, cw: int, ch: int):
        try:
            img = self.original_pil_image
            w, h = img.size
            if w <= 0 or h <= 0:
                return

            scale = min(cw / w, ch / h)
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))

            preview = img.resize(new_size, Image.Resampling.BILINEAR)

            self.after(0, lambda: self._apply_resized_preview(job_id, preview))
        except Exception:
            return


    def _apply_resized_preview(self, job_id: int, preview: Image.Image):
        if job_id != self._resize_job_id:
            return
        if self.original_pil_image is None:
            return

        self.current_tk_image = ImageTk.PhotoImage(preview)

        if self._canvas_text_id is not None:
            self.image_canvas.delete(self._canvas_text_id)
            self._canvas_text_id = None

        cw = self.image_canvas.winfo_width()
        ch = self.image_canvas.winfo_height()

        if self._canvas_img_id is None:
            self._canvas_img_id = self.image_canvas.create_image(
                cw // 2, ch // 2, image=self.current_tk_image, anchor="center"
            )
        else:
            self.image_canvas.itemconfigure(self._canvas_img_id, image=self.current_tk_image)
            self.image_canvas.coords(self._canvas_img_id, cw // 2, ch // 2)

    def _resize_preview_async(self):
        self._resize_after_id = None
        if self.original_pil_image is None:
            return

        self._resize_job_id += 1
        job_id = self._resize_job_id

        cw = max(self.image_canvas.winfo_width(), 1)
        ch = max(self.image_canvas.winfo_height(), 1)

        t = threading.Thread(
            target=self._resize_preview_worker,
            args=(job_id, cw, ch),
            daemon=True
        )
        t.start()

    def _apply_trigger_changes(self, win, trigger: str, new_translation: str, move_to_group: str):
        trigger = normalize_trigger(trigger)
        new_translation = (new_translation or "").strip()
        move_to_group = (move_to_group or "").strip()

        if new_translation:
            self.translations[trigger] = new_translation
        else:
            self.translations.pop(trigger, None)
        save_translations(self.translations_path, self.translations)

        if move_to_group:
            for g in list(self.groups.keys()):
                if trigger in self.groups[g]:
                    self.groups[g] = [t for t in self.groups[g] if t != trigger]

            if move_to_group not in self.groups:
                self.groups[move_to_group] = []
            if trigger not in self.groups[move_to_group]:
                self.groups[move_to_group].append(trigger)

            save_groups(self.groups_path, self.groups)
            self.group_combo["values"] = self._group_values()

        self._render_trigger_list()
        self._set_status(f"Updated: {trigger}")
        win.destroy()

    def _delete_trigger(self, win, trigger: str):
        trigger = normalize_trigger(trigger)
        if not trigger:
            return

        if not messagebox.askyesno("Confirm delete", f"Delete trigger '{trigger}'?\n\nThis will remove it from:\n- triggers.txt\n- translations.txt\n- all groups\n- current selections"):
            return

        self.triggers = [t for t in self.triggers if t != trigger]
        save_triggers(self.triggers_path, self.triggers)

        if trigger in self.translations:
            self.translations.pop(trigger, None)
            save_translations(self.translations_path, self.translations)

        changed = False
        for g in list(self.groups.keys()):
            if trigger in self.groups[g]:
                self.groups[g] = [t for t in self.groups[g] if t != trigger]
                changed = True
        if changed:
            save_groups(self.groups_path, self.groups)
            self.group_combo["values"] = self._group_values()

        if hasattr(self, "selected_set"):
            self.selected_set.discard(trigger)

        self._render_trigger_list()
        self._set_status(f"Deleted: {trigger}")
        win.destroy()
        self.deleted_triggers.add(trigger)
        self.unknown_caption_tokens = [t for t in self.unknown_caption_tokens if t != trigger]
        self.loaded_caption_tokens = [t for t in self.loaded_caption_tokens if t != trigger]

    def _group_values(self):
        names = sorted(self.groups.keys(), key=lambda s: s.lower())
        return ["All"] + names
    
    def _scan_folder_worker(self, folder: str):
        items = []
        err = None
        try:
            for entry in os.scandir(folder):
                if not entry.is_file():
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if ext in IMAGE_EXTS:
                    img_path = entry.path
                    cap_path = os.path.splitext(img_path)[0] + ".caption"
                    has_cap = os.path.exists(cap_path)
                    items.append((img_path, has_cap))

            items.sort(key=lambda x: os.path.basename(x[0]).lower())
        except Exception as e:
            err = str(e)

        count = len(items)
        self.after(0, lambda: self._set_status(f"Scan done: {count} images. Building list..."))
        self.after(0, lambda: self._on_folder_scanned(items, err))
    
    def _on_folder_scanned(self, items, err: str | None):
        try:
            self.open_folder_btn.configure(state="normal")
        except Exception:
            pass

        if err:
            self.folder_images = []
            self.folder_index = -1
            self._set_status(f"Folder scan failed: {err}")
            return

        self.folder_images = [p for (p, _has) in items]
        self.folder_index = 0 if self.folder_images else -1

        self._populate_image_tree_batched(items)

        if not self.folder_images:
            self._set_status("No images found in folder")
            return

        self.after(200, lambda: self._open_first_image_after_folder())

    def _open_first_image_after_folder(self):
        if not self.folder_images:
            return
        self._clear_selections_for_next_image()
        self.load_image(self.folder_images[0])
        self._set_status(f"Loaded {len(self.folder_images)} images")

    def _show_loading_text(self, text="Loading..."):
        if not hasattr(self, "image_canvas"):
            return
        if self._canvas_img_id is not None:
            self.image_canvas.delete(self._canvas_img_id)
            self._canvas_img_id = None
        if self._canvas_text_id is not None:
            self.image_canvas.delete(self._canvas_text_id)
        self._canvas_text_id = self.image_canvas.create_text(10, 10, anchor="nw", text=text)

    def _populate_image_tree_batched(self, items, batch=10):
        if not self.image_tree:
            return

        self.image_tree.delete(*self.image_tree.get_children())
        self._imglist_iid_to_path = {}
        self._imglist_path_to_iid = {}

        self._imglist_pending_items = list(items)
        self._imglist_batch_size = int(batch)

        self._set_status(f"Building list... 0 / {len(self._imglist_pending_items)}")

        self._populate_image_tree_batched_step()

    def _populate_image_tree_batched_step(self):
        if not self.image_tree:
            return

        pending = getattr(self, "_imglist_pending_items", None)
        if not pending:
            self._imglist_pending_items = []
            if self.current_image_path and self.current_image_path in self._imglist_path_to_iid:
                iid = self._imglist_path_to_iid[self.current_image_path]
                self._suppress_tree_select = True
                try:
                    self.image_tree.selection_set(iid)
                    self.image_tree.see(iid)
                finally:
                    self.after(0, lambda: setattr(self, "_suppress_tree_select", False))
            return

        total = len(pending)
        batch = getattr(self, "_imglist_batch_size", 200)

        n = min(batch, total)
        chunk = pending[:n]
        del pending[:n]

        for (p, has_cap) in chunk:
            base = os.path.basename(p)
            mark = "✓" if has_cap else ""
            iid = self.image_tree.insert("", "end", text=base, values=(mark,))
            if has_cap:
                self.image_tree.item(iid, tags=("hascap",))
            self._imglist_iid_to_path[iid] = p
            self._imglist_path_to_iid[p] = iid

        done = len(self._imglist_path_to_iid)
        self._set_status(f"Building list... {done} / {done + len(pending)}")

        self.after(1, self._populate_image_tree_batched_step)

    def open_used_triggers(self):
        if not getattr(self, "current_folder", None):
            messagebox.showinfo("Used Triggers", "Open a folder first.")
            return

        win = tk.Toplevel(self)
        win.title("Used Triggers")
        win.transient(self)
        win.geometry("720x640")

        top = ttk.Frame(win, padding=10)
        top.pack(fill="x")

        info_var = tk.StringVar(value=f"Scanning: {self.current_folder}")
        ttk.Label(top, textvariable=info_var).pack(side="left", anchor="w")

        btns = ttk.Frame(top)
        btns.pack(side="right")

        body = ttk.Frame(win, padding=(10, 0, 10, 10))
        body.pack(fill="both", expand=True)

        txt = tk.Text(body, wrap="none")
        txt.pack(side="left", fill="both", expand=True)

        ysb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        ysb.pack(side="right", fill="y")
        txt.configure(yscrollcommand=ysb.set)

        xsb = ttk.Scrollbar(win, orient="horizontal", command=txt.xview)
        xsb.pack(side="bottom", fill="x")
        txt.configure(xscrollcommand=xsb.set)

        win._used_triggers_text = txt
        win._used_triggers_info = info_var

        self._used_triggers_windows.append(win)
        win.bind("<Destroy>", lambda _e: self._used_triggers_windows.remove(win) if win in self._used_triggers_windows else None)

        self._apply_used_triggers_theme(win)

        def _save_as():
            content = txt.get("1.0", "end-1c")
            if not content.strip():
                messagebox.showinfo("Save", "Nothing to save yet.")
                return
            path = filedialog.asksaveasfilename(
                title="Save used triggers as...",
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
            )
            if not path:
                return
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

        ttk.Button(btns, text="Save as...", command=_save_as).pack(side="left")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="left", padx=(8, 0))

        t = threading.Thread(
            target=self._used_triggers_worker,
            args=(self.current_folder, win),
            daemon=True
        )
        t.start()

    def _apply_used_triggers_theme(self, win: tk.Toplevel):
        if not win or not win.winfo_exists():
            return
        txt = getattr(win, "_used_triggers_text", None)
        if not txt:
            return

        name = self.theme_var.get() if hasattr(self, "theme_var") else "Light"

        if name == "Dark":
            bg = "#1e1e1e"
            fg = "#e6e6e6"
            sel_bg = "#3a3a3a"
            sel_fg = "#ffffff"
            ins = "#e6e6e6"
        else:
            bg = "#ffffff"
            fg = "#111111"
            sel_bg = "#d9d9d9"
            sel_fg = "#111111"
            ins = "#111111"

        txt.configure(
            bg=bg,
            fg=fg,
            insertbackground=ins,
            selectbackground=sel_bg,
            selectforeground=sel_fg,
            highlightthickness=0,
            relief="flat",
        )

    def _apply_combobox_theme(self, theme_name: str):
    
        if theme_name == "Dark":
            cb_bg = "#202020"
            cb_fg = "#e6e6e6"
            sel_bg = "#3a3a3a"
            sel_fg = "#ffffff"
        else:
            cb_bg = "#ffffff"
            cb_fg = "#111111"
            sel_bg = "#d9d9d9"
            sel_fg = "#111111"

        style = ttk.Style(self)

        style.configure(
            "ICap.TCombobox",
            fieldbackground=cb_bg,
            background=cb_bg,
            foreground=cb_fg,
            borderwidth=1
        )

        style.map(
            "ICap.TCombobox",
            fieldbackground=[("readonly", cb_bg), ("!readonly", cb_bg)],
            foreground=[("readonly", cb_fg), ("!readonly", cb_fg)],
        )

        self.option_add("*TCombobox*Listbox.background", cb_bg)
        self.option_add("*TCombobox*Listbox.foreground", cb_fg)
        self.option_add("*TCombobox*Listbox.selectBackground", sel_bg)
        self.option_add("*TCombobox*Listbox.selectForeground", sel_fg)


    def _apply_listbox_theme(self, lb: tk.Listbox, theme_name: str):

        if theme_name == "Dark":
            bg = "#202020"
            fg = "#e6e6e6"
            sel_bg = "#3a3a3a"
            sel_fg = "#ffffff"
        else:
            bg = "#ffffff"
            fg = "#111111"
            sel_bg = "#d9d9d9"
            sel_fg = "#111111"

        lb.configure(
            bg=bg,
            fg=fg,
            selectbackground=sel_bg,
            selectforeground=sel_fg,
            highlightthickness=0,
            relief="flat",
            activestyle="none"
        )

    def _used_triggers_worker(self, folder: str, win: tk.Toplevel):
        counter = Counter()
        total_files = 0
        try:
            for root, _, files in os.walk(folder):
                for f in files:
                    if f.lower().endswith(".caption"):
                        total_files += 1
                        p = os.path.join(root, f)
                        try:
                            with open(p, "r", encoding="utf-8") as fh:
                                text = fh.read()
                        except Exception:
                            continue

                        tokens = parse_caption_tokens(text)
                        counter.update(tokens)
        except Exception as e:
            msg = f"Scan failed:\n{e}"
            self.after(0, messagebox.showerror, "Used Triggers", msg)
            return

        result_text = self._format_used_triggers(counter)

        def _apply():
            if not win.winfo_exists():
                return
            win._used_triggers_info.set(f"Scanned: {total_files} caption files. Unique triggers: {len(counter)}")
            txt: tk.Text = win._used_triggers_text
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", result_text)
            txt.configure(state="disabled")

        self.after(0, _apply)

    def _format_used_triggers(self, counter: Counter) -> str:
        trig_to_group = {}

        for gname, lst in (self.groups or {}).items():
            for t in lst:
                trig_to_group[t] = gname

        grouped = {}
        for trig, cnt in counter.items():
            g = trig_to_group.get(trig) or "Без группы"
            grouped.setdefault(g, []).append((trig, cnt))

        order = []
        if "Без группы" in grouped:
            order.append("Без группы")

        for g in (self.group_order or []):
            if g in grouped and g not in order:
                order.append(g)

        for g in sorted(grouped.keys(), key=lambda s: s.lower()):
            if g not in order:
                order.append(g)

        lines = []
        for g in order:
            lines.append(f"[{g}]")
            def _sort_key(item):
                trig, _cnt = item
                trn = (self.translations or {}).get(trig, "").strip()

                if not trn:
                    return (0, trig.lower())
                else:
                    return (1, trn.lower())

            items = sorted(grouped[g], key=_sort_key)
            for trig, _cnt in items:
                trn = (self.translations or {}).get(trig, "").strip()
                if trn:
                    lines.append(f"{trn} - {trig}")
                else:
                    lines.append(f"{trig}")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(side="top", fill="x")

        # ===== Row 1: folder + image nav + save =====
        row1 = ttk.Frame(top)
        row1.pack(side="top", fill="x")

        self.open_folder_btn = ttk.Button(row1, text="Open Folder", command=self.open_folder)
        self.open_folder_btn.pack(side="left")

        ttk.Button(row1, text="Prev", command=self.prev_image).pack(side="left", padx=(8, 0))
        ttk.Button(row1, text="Next", command=self.next_image).pack(side="left", padx=(8, 0))

        ttk.Checkbutton(
            row1,
            text="Auto-save on Next/Prev",
            variable=self.auto_save_var
        ).pack(side="left", padx=(12, 0))

        ttk.Separator(row1, orient="vertical").pack(side="left", fill="y", padx=12)

        ttk.Button(row1, text="Save .caption", command=self.save_caption).pack(side="left")


        # ===== Row 2: dataset tools (groups/triggers scan/clean) =====
        row2 = ttk.Frame(top)
        row2.pack(side="top", fill="x", pady=(6, 0))

        ttk.Button(row2, text="Used Triggers", command=self.open_used_triggers).pack(side="left")
        ttk.Button(row2, text="Edit Groups", command=self.open_groups_editor).pack(side="left", padx=(8, 0))
        ttk.Button(row2, text="Order Groups", command=self.open_group_order_dialog).pack(side="left", padx=(8, 0))

        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=12)

        ttk.Button(row2, text="Clean", command=self.clean).pack(side="left")


        # ===== Row 3: trigger sources + theme =====
        row3 = ttk.Frame(top)
        row3.pack(side="top", fill="x", pady=(6, 0))

        ttk.Button(row3, text="Reload Triggers", command=self.reload_triggers).pack(side="left")
        ttk.Button(row3, text="Add Trigger", command=self.add_trigger).pack(side="left", padx=(8, 0))
        ttk.Button(row3, text="Manage Trigger", command=self.manage_trigger).pack(side="left", padx=(8, 0))

        ttk.Separator(row3, orient="vertical").pack(side="left", fill="y", padx=12)

        ttk.Button(row3, text="Set triggers.txt", command=self.set_triggers_file).pack(side="left")
        ttk.Button(row3, text="Set translations.txt", command=self.set_translations_file).pack(side="left", padx=(8, 0))

        ttk.Separator(row3, orient="vertical").pack(side="left", fill="y", padx=12)

        ttk.Label(row3, text="Theme:").pack(side="left", padx=(12, 0))

        theme_cb = ttk.Combobox(
            row3,
            textvariable=self.theme_var,
            state="readonly",
            values=self.theme_manager.available(),
            width=10,
            style="ICap.TCombobox"
        )
        theme_cb.pack(side="left", padx=(6, 0))

        def _on_theme_change(_e=None):
            self.theme_manager.apply(self.theme_var.get())
            self.settings["theme"] = self.theme_var.get()
            save_settings(self.settings_path, self.settings)

        theme_cb.bind("<<ComboboxSelected>>", _on_theme_change)

        self.status = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status, padding=(10, 0)).pack(side="top", fill="x")

        main = ttk.Frame(self, padding=10)
        main.pack(side="top", fill="both", expand=True)

        # ===== image list (LEFT COLUMN) =====
        img_list_panel = ttk.Frame(main, width=260)
        img_list_panel.pack(side="left", fill="y")
        img_list_panel.pack_propagate(False)

        ttk.Label(img_list_panel, text="Images").pack(side="top", anchor="w")

        tree_wrap = ttk.Frame(img_list_panel)
        tree_wrap.pack(side="top", fill="both", expand=True, pady=(6, 0))

        self.image_tree = ttk.Treeview(
            tree_wrap,
            columns=("cap",),
            show="tree headings",
            selectmode="browse",
            height=20
        )
        self.image_tree.heading("#0", text="File")
        self.image_tree.heading("cap", text="")
        self.image_tree.column("#0", width=200, stretch=True)
        self.image_tree.column("cap", width=30, stretch=False, anchor="center")

        tree_scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.image_tree.yview)
        self.image_tree.configure(yscrollcommand=tree_scroll.set)

        self.image_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        self.image_tree.bind("<<TreeviewSelect>>", self.on_image_select)

        # left: image preview
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)

        self.image_canvas = tk.Canvas(left, highlightthickness=0)
        self.image_canvas.pack(side="top", fill="both", expand=True)
        self.image_canvas.bind("<Configure>", self._on_image_area_resize)

        self._canvas_img_id = None
        self._canvas_text_id = self.image_canvas.create_text(
            10, 10, anchor="nw", text="No image loaded"
        )

        self.image_info = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.image_info).pack(side="top", fill="x", pady=(8, 0))

        self.caption_info = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.caption_info).pack(side="top", fill="x", pady=(4, 0))

        # right: trigger list
        right = ttk.Frame(main, width=420)
        right.pack(side="right", fill="both")
        right.pack_propagate(False)

        ttk.Label(right, text="Triggers").pack(side="top", anchor="w")

        hdr = ttk.Frame(right)
        hdr.pack(side="top", fill="x", pady=(6, 0))

        ttk.Label(hdr, textvariable=self.group_title).pack(side="left", anchor="w")

        ttk.Button(hdr, text="Prev Group", command=self.prev_group).pack(side="right")
        ttk.Button(hdr, text="Next Group", command=self.next_group).pack(side="right", padx=(0, 6))

        self.scroll = ScrollableFrame(right)
        self.scroll.pack(side="top", fill="both", expand=True, pady=(6, 0))

        group_bar = ttk.Frame(right)
        group_bar.pack(side="top", fill="x", pady=(8, 0))
        ttk.Label(group_bar, text="Group:").pack(side="left")
        self.group_combo = ttk.Combobox(
            group_bar,
            textvariable=self.current_group,
            state="readonly",
            values=self._group_values(),
            style="ICap.TCombobox"
        )
        self.group_combo.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.group_combo.bind("<<ComboboxSelected>>", lambda e: self._render_trigger_list())

        filter_bar = ttk.Frame(right)
        filter_bar.pack(side="top", fill="x", pady=(8, 0))
        ttk.Label(filter_bar, text="Filter:").pack(side="left")
        self.filter_entry = ttk.Entry(filter_bar, textvariable=self.filter_var)
        self.filter_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.filter_entry.bind("<KeyRelease>", lambda e: self._render_trigger_list())

    def open_groups_editor(self):
        win = tk.Toplevel(self)
        win.title("Edit groups")
        win.transient(self)
        win.grab_set()
        win.geometry("520x520")

        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Groups:").pack(anchor="w")

        lb = tk.Listbox(frm, height=18, activestyle="none")
        self._apply_listbox_theme(lb, self.theme_var.get())
        self._themed_dialogs.append(win)
        win.bind("<Destroy>", lambda _e: self._themed_dialogs.remove(win) if win in self._themed_dialogs else None)
        lb.pack(fill="both", expand=True, pady=(8, 8))

        def _refresh():
            lb.delete(0, "end")
            names = sorted((self.groups or {}).keys(), key=lambda s: s.lower())
            for g in names:
                lb.insert("end", g)

        _refresh()

        btns = ttk.Frame(frm)
        btns.pack(fill="x")

        def _get_selected_group():
            sel = lb.curselection()
            if not sel:
                return None
            return lb.get(sel[0])

        def _rename():
            g = _get_selected_group()
            if not g:
                return
            new = simpledialog.askstring("Rename group", f"New name for '{g}':", parent=win)
            if not new:
                return
            new = new.strip()
            if not new:
                return
            self._rename_group(g, new)
            _refresh()

        def _delete_keep():
            g = _get_selected_group()
            if not g:
                return
            self._delete_group_keep_triggers(g)
            _refresh()

        def _delete_all():
            g = _get_selected_group()
            if not g:
                return
            self._delete_group_and_triggers(g)
            _refresh()
        def _add():
            new = simpledialog.askstring("Add group", "Group name:", parent=win)
            if not new:
                return
            new = new.strip()
            if not new:
                return
            self._add_group(new)
            _refresh()

        ttk.Button(btns, text="Add group...", command=_add).pack(side="left")
        ttk.Button(btns, text="Rename...", command=_rename).pack(side="left")
        ttk.Button(btns, text="Delete group (keep triggers)", command=_delete_keep).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Delete group + delete triggers", command=_delete_all).pack(side="left", padx=(8, 0))

        bottom = ttk.Frame(frm)
        bottom.pack(fill="x", pady=(12, 0))
        ttk.Button(bottom, text="Close", command=win.destroy).pack(side="right")

    def _add_group(self, name: str):
        name = (name or "").strip()
        if not name:
            return

        if not hasattr(self, "groups") or self.groups is None:
            self.groups = {}

        if name in self.groups:
            messagebox.showerror("Add group", f"Group '{name}' already exists.")
            return

        self.groups[name] = []

        if isinstance(getattr(self, "group_order", None), list):
            if name not in self.group_order:
                self.group_order.append(name)
        else:
            self.group_order = [name]

        save_groups(self.groups_path, self.groups)
        self._save_settings()

        # UI
        self.group_combo["values"] = self._group_values()
        self._render_trigger_list()
        self._set_status(f"Added group: {name}")

    def _rename_group(self, old: str, new: str):
        old = (old or "").strip()
        new = (new or "").strip()
        if not old or not new:
            return
        if old not in self.groups:
            return
        if new in self.groups and new != old:
            messagebox.showerror("Rename group", f"Group '{new}' already exists.")
            return

        if old == new:
            return

        self.groups[new] = list(self.groups.get(old, []))
        self.groups.pop(old, None)

        if isinstance(self.group_order, list):
            self.group_order = [new if g == old else g for g in self.group_order]

        self.group_combo["values"] = self._group_values()

        if self.current_group.get() == old:
            self.current_group.set(new)

        save_groups(self.groups_path, self.groups)
        self._save_settings()
        self._render_trigger_list()
        self._set_status(f"Renamed group: {old} → {new}")

    def _delete_group_keep_triggers(self, gname: str):
        gname = (gname or "").strip()
        if not gname or gname not in self.groups:
            return

        if not messagebox.askyesno(
            "Confirm delete group",
            f"Delete group '{gname}'?\n\nTriggers will be kept (only ungrouped)."
        ):
            return

        self.groups.pop(gname, None)

        if isinstance(self.group_order, list):
            self.group_order = [g for g in self.group_order if g != gname]

        if self.current_group.get() == gname:
            self.current_group.set("All")

        save_groups(self.groups_path, self.groups)
        self._save_settings()

        self.group_combo["values"] = self._group_values()
        self._render_trigger_list()
        self._set_status(f"Deleted group: {gname} (kept triggers)")

    def _delete_group_and_triggers(self, gname: str):
        gname = (gname or "").strip()
        if not gname or gname not in self.groups:
            return

        trig_list = list(self.groups.get(gname, []))
        if not trig_list:
            self._delete_group_keep_triggers(gname)
            return

        if not messagebox.askyesno(
            "Confirm delete group + triggers",
            f"Delete group '{gname}' AND delete its {len(trig_list)} triggers everywhere?\n\n"
            f"This will remove them from:\n- triggers.txt\n- translations.txt\n- all groups\n- current selections"
        ):
            return

        self.deleted_triggers.update(trig_list)

        trig_set = set(trig_list)
        self.unknown_caption_tokens = [t for t in self.unknown_caption_tokens if t not in trig_set]
        self.loaded_caption_tokens = [t for t in self.loaded_caption_tokens if t not in trig_set]
        self.triggers = [t for t in self.triggers if t not in trig_set]
        save_triggers(self.triggers_path, self.triggers)

        changed_trans = False
        for t in trig_list:
            if t in self.translations:
                self.translations.pop(t, None)
                changed_trans = True
        if changed_trans:
            save_translations(self.translations_path, self.translations)

        for g in list(self.groups.keys()):
            self.groups[g] = [t for t in self.groups[g] if t not in trig_set]
        self.groups.pop(gname, None)

        save_groups(self.groups_path, self.groups)

        if hasattr(self, "selected_set"):
            for t in trig_list:
                self.selected_set.discard(t)

        if isinstance(self.group_order, list):
            self.group_order = [g for g in self.group_order if g != gname]
        if self.current_group.get() == gname:
            self.current_group.set("All")

        self._save_settings()

        self.group_combo["values"] = self._group_values()
        self._render_trigger_list()
        self._set_status(f"Deleted group: {gname} + deleted {len(trig_list)} triggers")


    def open_group_order_dialog(self):
        all_groups = sorted([g for g in (self.groups or {}).keys() if g and g != "All"], key=str.lower)

        current = [g for g in (self.group_order or []) if g in all_groups]
        for g in all_groups:
            if g not in current:
                current.append(g)

        win = tk.Toplevel(self)
        win.title("Group order for caption")
        win.transient(self)
        win.grab_set()
        win.geometry("420x420")

        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Order groups (top → first in caption):").pack(anchor="w")

        lb = tk.Listbox(frm, height=16, activestyle="none")
        self._apply_listbox_theme(lb, self.theme_var.get())
        self._themed_dialogs.append(win)
        win.bind("<Destroy>", lambda _e: self._themed_dialogs.remove(win) if win in self._themed_dialogs else None)
        lb.pack(fill="both", expand=True, pady=(8, 8))

        for g in current:
            lb.insert("end", g)

        btns = ttk.Frame(frm)
        btns.pack(fill="x")

        def _selected_index():
            sel = lb.curselection()
            return sel[0] if sel else None

        def _move(delta: int):
            i = _selected_index()
            if i is None:
                return
            j = i + delta
            if j < 0 or j >= lb.size():
                return
            txt = lb.get(i)
            lb.delete(i)
            lb.insert(j, txt)
            lb.selection_clear(0, "end")
            lb.selection_set(j)
            lb.activate(j)

        def _add_missing():
            present = {lb.get(i) for i in range(lb.size())}
            for g in all_groups:
                if g not in present:
                    lb.insert("end", g)

        ttk.Button(btns, text="Up", command=lambda: _move(-1)).pack(side="left")
        ttk.Button(btns, text="Down", command=lambda: _move(+1)).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Add missing", command=_add_missing).pack(side="left", padx=(12, 0))

        bottom = ttk.Frame(frm)
        bottom.pack(fill="x", pady=(12, 0))

        def _save():
            new_order = [lb.get(i) for i in range(lb.size())]
            new_order = [g for g in new_order if g in all_groups]
            self.group_order = new_order
            self._save_settings()
            win.destroy()

        ttk.Button(bottom, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(bottom, text="Save", command=_save).pack(side="right", padx=(0, 8))

    def on_theme_applied(self, name: str):
        if name == "Dark":
            bg = "#1e1e1e"
            fg = "#e6e6e6" 
            panel_bg = "#202020"
        else:
            bg = "#f3f3f3"
            fg = "#111111"
            panel_bg = "#ffffff"

        self.configure(bg=bg)

        if hasattr(self, "image_canvas"):
            self.image_canvas.configure(bg=panel_bg, highlightthickness=0)

        if hasattr(self, "scroll") and hasattr(self.scroll, "canvas"):
            self.scroll.canvas.configure(bg=panel_bg, highlightthickness=0)

        if hasattr(self, "right_panel"):
            try:
                self.right_panel.configure(style="TFrame")
            except Exception:
                pass

        # ===== Image list (Treeview) theme =====
        try:
            style = ttk.Style(self)

            if name == "Dark":
                tv_bg = "#202020"
                tv_fg = "#e6e6e6"
                tv_sel_bg = "#3a3a3a"
                tv_sel_fg = "#ffffff"
                heading_bg = "#202020"
                heading_fg = "#e6e6e6"
            else:
                # Light and Image
                tv_bg = "#ffffff"
                tv_fg = "#111111"
                tv_sel_bg = "#d9d9d9"
                tv_sel_fg = "#111111"
                heading_bg = "#ffffff"
                heading_fg = "#111111"

            style.configure(
                "ICap.Treeview",
                background=tv_bg,
                foreground=tv_fg,
                fieldbackground=tv_bg,
                borderwidth=0,
                relief="flat",
            )
            style.map(
                "ICap.Treeview",
                background=[("selected", tv_sel_bg)],
                foreground=[("selected", tv_sel_fg)],
            )

            style.configure(
                "ICap.Treeview.Heading",
                background=heading_bg,
                foreground=heading_fg,
                relief="flat",
            )

            if hasattr(self, "image_tree") and self.image_tree:
                self.image_tree.configure(style="ICap.Treeview")
                try:
                    self.image_tree.tag_configure("hascap", foreground="#BB9F00")
                except Exception:
                    pass

        # ===== Buttons hover/pressed colors =====
        #try:
            style = ttk.Style(self)

           
            style.map("TButton", background=[], foreground=[])

            if name == "Dark":
                btn_bg = "#2a2a2a"
                btn_fg = "#e6e6e6"
                btn_active_bg = "#3a3a3a"
                btn_pressed_bg = "#444444"
                btn_active_fg = "#ffffff"
            else:
                btn_bg = "#f3f3f3"
                btn_fg = "#111111"
                btn_active_bg = "#e6e6e6"
                btn_pressed_bg = "#d9d9d9"
                btn_active_fg = "#111111"

           
            style.configure("TButton", background=btn_bg, foreground=btn_fg)

            
            style.map(
                "TButton",
                background=[
                    ("pressed", btn_pressed_bg),
                    ("active", btn_active_bg),
                ],
                foreground=[
                    ("active", btn_active_fg),
                ],
            )

        except Exception:
            pass

        except Exception:
            pass

        # ===== Used Triggers windows (tk.Text) theme =====
        for w in list(getattr(self, "_used_triggers_windows", [])):
            try:
                self._apply_used_triggers_theme(w)
            except Exception:
                pass

        # ===== Combobox + dropdown theme =====
        try:
            self._apply_combobox_theme(name)
        except Exception:
            pass

        # ===== Re-theme open dialogs (Edit Groups / Order Groups / Manage Trigger) =====
        for w in list(getattr(self, "_themed_dialogs", [])):
            try:
                if w and w.winfo_exists():
                    for child in w.winfo_children():
                        for sub in child.winfo_children():
                            if isinstance(sub, tk.Listbox):
                                self._apply_listbox_theme(sub, name)
            except Exception:
                pass

        self.configure(bg=bg)

        self.update_idletasks()

        def hk_prev(_e=None):
            if self._hotkeys_allowed():
                self.prev_image()

        def hk_next(_e=None):
            if self._hotkeys_allowed():
                self.next_image()

        def hk_save(_e=None):
            if self._hotkeys_allowed():
                self.save_caption()

        def hk_clean(_e=None):
            if self._hotkeys_allowed():
                self.clean()


        self.bind_all("<Left>", hk_prev)
        self.bind_all("<Right>", hk_next)


        self.bind_all("a", hk_prev)
        self.bind_all("d", hk_next)
        self.bind_all("s", hk_save)
        self.bind_all("c", hk_clean)


    def _set_status(self, msg: str):
        self.status.set(msg)

    def set_triggers_file(self):
        path = filedialog.askopenfilename(
            title="Select triggers.txt",
            filetypes=[("Text", "*.txt"), ("All", "*.*")]
        )
        if not path:
            return
        self.triggers_path = path
        self.reload_triggers()

    def set_translations_file(self):
        path = filedialog.askopenfilename(
            title="Select translations.txt",
            filetypes=[("Text", "*.txt"), ("All", "*.*")]
        )
        if not path:
            return
        self.translations_path = path
        self.translations = load_translations(self.translations_path) or {}
        self._render_trigger_list()
        self._set_status(f"Translations loaded: {os.path.basename(self.translations_path)}")

    def reload_triggers(self):
        self.triggers = load_triggers(self.triggers_path)
        self.selected_set = {t for t in self.selected_set if t in set(self.triggers)}
        self.translations = load_translations(self.translations_path) or {}
        self.groups = load_groups(self.groups_path)
        self.group_combo["values"] = self._group_values()
        if self.current_group.get() not in self.group_combo["values"]:
            self.current_group.set("All")
        self._render_trigger_list()
        if self.loaded_caption_tokens:
            self._apply_caption_to_checkboxes()
        self._set_status(f"Triggers loaded: {len(self.triggers)} items")

    def open_folder(self):
        folder = filedialog.askdirectory(title="Select folder with images")
        if not folder:
            return

        self.current_folder = folder
        self._save_settings()

        self._set_status("Scanning folder...")
        try:
            self.open_folder_btn.configure(state="disabled")
        except Exception:
            pass

      
        t = threading.Thread(target=self._scan_folder_worker, args=(folder,), daemon=True)
        t.start()

    def _load_folder_images(self, folder: str):
        return
        # build list of images in folder
        files = []
        try:
            for name in os.listdir(folder):
                ext = os.path.splitext(name)[1].lower()
                if ext in IMAGE_EXTS:
                    files.append(os.path.join(folder, name))
        except Exception as e:
            self.folder_images = []
            self.folder_index = -1
            self._set_status(f"Folder scan failed: {e}")
            return

        files.sort(key=lambda p: os.path.basename(p).lower())
        self.folder_images = files
        self.folder_index = 0 if files else -1

        # fill left list UI
        self._populate_image_tree()

    def _populate_image_tree(self):
        return
        if not self.image_tree:
            return

        self.image_tree.delete(*self.image_tree.get_children())
        self._imglist_iid_to_path = {}
        self._imglist_path_to_iid = {}

        for p in self.folder_images:
            base = os.path.basename(p)
            has_cap = self._caption_exists(p)

            # show icon if caption exists; otherwise no icon
            iid = self.image_tree.insert(
                "",
                "end",
                text=base,
                values=("",),
                image=(self._imglist_check_icon if has_cap else "")
            )
            self._imglist_iid_to_path[iid] = p
            self._imglist_path_to_iid[p] = iid

        # auto-select current image if any
        if self.current_image_path and self.current_image_path in self._imglist_path_to_iid:
            iid = self._imglist_path_to_iid[self.current_image_path]
            self.image_tree.selection_set(iid)
            self.image_tree.see(iid)

    def on_image_select(self, _event=None):
        if getattr(self, "_suppress_tree_select", False):
            return
        if not self.image_tree:
            return
        sel = self.image_tree.selection()
        if not sel:
            return
        iid = sel[0]
        path = self._imglist_iid_to_path.get(iid)
        if not path:
            return

        self._maybe_autosave_before_nav()

        try:
            self.folder_index = self.folder_images.index(path)
        except ValueError:
            self.folder_index = -1

        self._clear_selections_for_next_image()
        if path == self.current_image_path:
            return

        self.load_image(path)

    def _caption_exists(self, image_path: str) -> bool:
        base_noext = os.path.splitext(image_path)[0]
        cap_path = base_noext + ".caption"
        return os.path.exists(cap_path)

    def _refresh_image_tree_marker_for_path(self, image_path: str):
        if not self.image_tree or not image_path:
            return
        iid = self._imglist_path_to_iid.get(image_path)
        if not iid:
            return
        has_cap = self._caption_exists(image_path)
        self.image_tree.item(iid, values=("✓" if has_cap else "",))
        self.image_tree.item(iid, tags=("hascap",) if has_cap else ())

    def load_image(self, path: str):
        print("ASYNC load_image:", path)
        self._load_job_id += 1
        job_id = self._load_job_id

        self.current_image_path = path
        self._show_loading_text("Loading image...")

        if self.image_tree and self._imglist_path_to_iid:
            iid = self._imglist_path_to_iid.get(path)
            if iid:
                self.image_tree.see(iid)

        #self._load_existing_caption_for_image()
        #self._apply_caption_to_checkboxes()
        self._load_existing_caption_for_image()

        self._apply_temp_caption_group_for_image(path)
        self.group_combo["values"] = self._group_values()

        self._render_trigger_list()

        t = threading.Thread(target=self._load_image_worker, args=(job_id, path), daemon=True)
        t.start()

    def _load_image_worker(self, job_id: int, path: str):
        try:
            img = Image.open(path)
            img.load()

            w, h = img.size

            area = [900, 600]
            ev = threading.Event()

            def _capture():
                aw = max(self.image_canvas.winfo_width(), 1)
                ah = max(self.image_canvas.winfo_height(), 1)
                area[0], area[1] = aw, ah
                ev.set()

            self.after(0, _capture)
            ev.wait(timeout=1.0)

            area_w, area_h = area
            scale = min(area_w / w, area_h / h)
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))

            preview = img.resize(new_size, Image.Resampling.BILINEAR)

            self.after(0, lambda: self._on_image_loaded(job_id, path, img, preview))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error", f"Failed to load image:\n{e}"))

    def _on_image_loaded(self, job_id: int, path: str, full_img: Image.Image, preview: Image.Image):
        if job_id != self._load_job_id:
            return
        if path != self.current_image_path:
            return

        self.original_pil_image = full_img
        self._on_image_area_resize()
        self.current_tk_image = ImageTk.PhotoImage(preview)

        if self._canvas_text_id is not None:
            self.image_canvas.delete(self._canvas_text_id)
            self._canvas_text_id = None

        cw = self.image_canvas.winfo_width()
        ch = self.image_canvas.winfo_height()

        if self._canvas_img_id is None:
            self._canvas_img_id = self.image_canvas.create_image(
                cw // 2, ch // 2, image=self.current_tk_image, anchor="center"
            )
        else:
            self.image_canvas.itemconfigure(self._canvas_img_id, image=self.current_tk_image)
            self.image_canvas.coords(self._canvas_img_id, cw // 2, ch // 2)

        w, h = full_img.size
        self.image_info.set(f"{os.path.basename(path)}  |  {w}x{h}")
        self._set_status("Image loaded")

    def _build_folder_index(self, image_path: str):
        def norm(p: str) -> str:
            return os.path.normcase(os.path.normpath(os.path.abspath(p)))

        folder = os.path.dirname(image_path)
        files = []
        try:
            for name in os.listdir(folder):
                ext = os.path.splitext(name)[1].lower()
                if ext in IMAGE_EXTS:
                    full = os.path.join(folder, name)
                    files.append(full)
        except Exception:
            self.folder_images = []
            self.folder_index = -1
            self._set_status("Folder scan failed")
            return

        files.sort(key=lambda p: os.path.basename(p).lower())
        self.folder_images = files

        target = norm(image_path)
        norm_map = [norm(p) for p in self.folder_images]

        if target in norm_map:
            self.folder_index = norm_map.index(target)
        else:
            base = os.path.basename(image_path).lower()
            try:
                self.folder_index = [os.path.basename(p).lower() for p in self.folder_images].index(base)
            except ValueError:
                self.folder_index = -1

        self._set_status(f"Folder images: {len(self.folder_images)}, index: {self.folder_index}")


    def _maybe_autosave_before_nav(self):
        if not self.auto_save_var.get():
            return
        if not self.current_image_path:
            return
        selected = self._ordered_selected_triggers_for_caption()
        final_tokens = selected[:]
        #for u in self.unknown_caption_tokens:
        #    if u in self.deleted_triggers:
        #        continue
        #    if u not in final_tokens:
        #        final_tokens.append(u)

        caption_text = CAPTION_JOINER.join(final_tokens)
        caption_path = self._caption_path_for_current_image()
        if not caption_path:
            return
        try:
            with open(caption_path, "w", encoding="utf-8") as f:
                f.write(caption_text)
            self._refresh_image_tree_marker_for_path(self.current_image_path)
            self.loaded_caption_tokens = parse_caption_tokens(caption_text)
            trigger_set = set(self.triggers)
            self.unknown_caption_tokens = [t for t in self.loaded_caption_tokens if t not in trigger_set]
            if self.deleted_triggers:
                self.unknown_caption_tokens = [t for t in self.unknown_caption_tokens if t not in self.deleted_triggers]
            if self.unknown_caption_tokens:
                self.caption_info.set(
                    f"caption: autosaved ({len(self.loaded_caption_tokens)}), unknown: {len(self.unknown_caption_tokens)}"
                )
            else:
                self.caption_info.set(f"caption: autosaved ({len(self.loaded_caption_tokens)})")
        except Exception:
            pass

    def next_image(self):

        if not self.current_image_path:
            self._set_status("Next: no image loaded")
            return

        if not self.folder_images or self.folder_index == -1:
            self._build_folder_index(self.current_image_path)

        if not self.folder_images or self.folder_index == -1:
            self._set_status("Next: cannot build folder index")
            return

        self._maybe_autosave_before_nav()

        if self.folder_index < len(self.folder_images) - 1:
            self.folder_index += 1
            self._clear_selections_for_next_image()
            self.load_image(self.folder_images[self.folder_index])
        else:
            self._set_status("Next: end of folder")


    def prev_image(self):
        if not self.current_image_path:
            self._set_status("Prev: no image loaded")
            return

        if not self.folder_images or self.folder_index == -1:
            self._build_folder_index(self.current_image_path)

        if not self.folder_images or self.folder_index == -1:
            self._set_status("Prev: cannot build folder index")
            return

        self._maybe_autosave_before_nav()

        if self.folder_index > 0:
            self.folder_index -= 1
            self._clear_selections_for_next_image()
            self.load_image(self.folder_images[self.folder_index])
        else:
            self._set_status("Prev: start of folder")



    def _caption_path_for_current_image(self) -> str | None:
        if not self.current_image_path:
            return None
        img_dir = os.path.dirname(self.current_image_path)
        base = os.path.splitext(os.path.basename(self.current_image_path))[0]
        return os.path.join(img_dir, base + ".caption")

    def _load_existing_caption_for_image(self):
        """Читает существующий .caption (если есть) и раскладывает на known/unknown."""
        self.loaded_caption_tokens = []
        self.unknown_caption_tokens = []
        self.caption_info.set("")

        cap_path = self._caption_path_for_current_image()
        if not cap_path:
            return

        if not os.path.exists(cap_path):
            self.caption_info.set("caption: (none)")
            return

        try:
            with open(cap_path, "r", encoding="utf-8") as f:
                text = f.read()
            tokens = parse_caption_tokens(text)
            self.loaded_caption_tokens = tokens

            trigger_set = set(self.triggers)
            #known = [t for t in tokens if t in trigger_set]
            #self.selected_set = set(known)

            #trigger_set = set(self.triggers)
            #self.unknown_caption_tokens = [t for t in tokens if t not in trigger_set]
            trigger_set = set(self.triggers)

            known = [t for t in tokens if t in trigger_set]
            unknown = [t for t in tokens if t not in trigger_set]

            if self.deleted_triggers:
                known = [t for t in known if t not in self.deleted_triggers]
                unknown = [t for t in unknown if t not in self.deleted_triggers]

            self.selected_set = set(known + unknown)

            #self.unknown_caption_tokens = []
            if unknown:
                self.caption_info.set(f"caption: loaded ({len(tokens)}), unknown: {len(unknown)}")
            else:
                self.caption_info.set(f"caption: loaded ({len(tokens)})")

            if self.unknown_caption_tokens:
                self.caption_info.set(f"caption: loaded ({len(tokens)}), unknown: {len(self.unknown_caption_tokens)}")
            else:
                self.caption_info.set(f"caption: loaded ({len(tokens)})")
        except Exception as e:
            self.caption_info.set("caption: error")
            messagebox.showwarning("Warning", f"Failed to read existing .caption:\n{e}")

    def _clear_trigger_widgets(self):
        for child in self.scroll.inner.winfo_children():
            child.destroy()
        self.var_map.clear()

    def _render_trigger_list(self):
        grp = self.current_group.get() or "All"
        self.group_title.set(f"Group: {grp}")

        self._clear_trigger_widgets()

        #triggers = list(self.triggers)
        triggers = list(self._get_all_triggers_for_ui())


        if grp != "All":
            allowed = set(self.groups.get(grp, []))
            triggers = [t for t in triggers if t in allowed]

        flt = self.filter_var.get().strip().lower()
        if flt:
            triggers = [
                t for t in triggers
                if flt in t.lower() or flt in self._display_text_for_trigger(t).lower()
            ]

        triggers.sort(key=lambda t: self._display_text_for_trigger(t).lower())

        if grp != "All":
            allowed = set(self.groups.get(grp, []))
            triggers = [t for t in triggers if t in allowed]

        flt = self.filter_var.get().strip().lower()
        if flt:
            triggers = [
                t for t in triggers
                if flt in t.lower() or flt in self._display_text_for_trigger(t).lower()
            ]

        triggers.sort(key=lambda t: self._display_text_for_trigger(t).lower())

        theme = self.theme_var.get()
        if theme == "Dark":
            row_bg = "#202020"
            row_fg = "#e6e6e6"
            select_bg = "#b02020"
        else:
            row_bg = "#ffffff"
            row_fg = "#111111"
            select_bg = "#0a7a2a"

        for t in triggers:
            var = tk.BooleanVar(value=(t in self.selected_set))
            self.var_map[t] = var

            def _on_toggle(*_args, _t=t, _v=var):
                if _v.get():
                    self.selected_set.add(_t)
                else:
                    self.selected_set.discard(_t)

            var.trace_add("write", _on_toggle)

            label_text = self._display_text_for_trigger(t)

            cb = tk.Checkbutton(
                self.scroll.inner,
                text=label_text,
                variable=var,
                onvalue=True,
                offvalue=False,
                indicatoron=0,
                bg=row_bg,
                fg=row_fg,
                activebackground=row_bg,
                activeforeground=row_fg,
                selectcolor=select_bg,
                anchor="w",
                padx=8,
                pady=4,
                bd=0,
                highlightthickness=0
            )
            cb.pack(fill="x", padx=2, pady=1)

        all_total = len(self._get_all_triggers_for_ui())

        ttk.Label(
            self.scroll.inner,
            #text=f"Shown: {len(triggers)} / Total: {len(self.triggers)}",
            text=f"Shown: {len(triggers)} / Total: {all_total}",
            padding=(0, 8)
        ).pack(side="top", anchor="w")

        if self.loaded_caption_tokens:
            self._apply_caption_to_checkboxes()

    def _current_check_style(self) -> str:
        t = self.theme_var.get()
        if t == "Dark":
            return "ICap.Dark.TCheckbutton"
        else:
            return "ICap.Light.TCheckbutton"

    def _apply_caption_to_checkboxes(self):
        for t, var in self.var_map.items():
            var.set(t in self.selected_set)

    def selected_triggers(self) -> list[str]:
        s = self.selected_set
        return [t for t in self.triggers if t in s]

    def save_caption(self, silent: bool = False):
        if not self.current_image_path:
            messagebox.showwarning("No image", "Load an image first.")
            return

        selected = self._ordered_selected_triggers_for_caption()

        final_tokens = selected[:]
        #for u in self.unknown_caption_tokens:
        #    if u in self.deleted_triggers:
        #        continue
        #    if u not in final_tokens:
        #        final_tokens.append(u)

        if not final_tokens:
            if not messagebox.askyesno("Empty caption", "No triggers selected. Save empty .caption anyway?"):
                return

        caption_text = CAPTION_JOINER.join(final_tokens)

        caption_path = self._caption_path_for_current_image()
        if not caption_path:
            messagebox.showerror("Error", "Internal error: caption path not resolved.")
            return

        try:
            with open(caption_path, "w", encoding="utf-8") as f:
                f.write(caption_text)

            self.loaded_caption_tokens = parse_caption_tokens(caption_text)
            trigger_set = set(self.triggers)
            self.unknown_caption_tokens = [t for t in self.loaded_caption_tokens if t not in trigger_set]
            if self.deleted_triggers:
                self.unknown_caption_tokens = [t for t in self.unknown_caption_tokens if t not in self.deleted_triggers]
            if self.unknown_caption_tokens:
                self.caption_info.set(
                    f"caption: saved ({len(self.loaded_caption_tokens)}), unknown: {len(self.unknown_caption_tokens)}"
                )
            else:
                self.caption_info.set(f"caption: saved ({len(self.loaded_caption_tokens)})")

            self._set_status(f"Saved: {os.path.basename(caption_path)}")
            self._refresh_image_tree_marker_for_path(self.current_image_path)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save caption:\n{e}")

    def clean(self):
        self.current_image_path = None
        self.current_pil_image = None
        self.current_tk_image = None

        self.loaded_caption_tokens = []
        self.unknown_caption_tokens = []

        self.image_canvas.delete("all")
        self._canvas_img_id = None
        self._canvas_text_id = self.image_canvas.create_text(10, 10, anchor="nw", text="No image loaded")
        self.image_info.set("")
        self.caption_info.set("")

        for var in self.var_map.values():
            var.set(False)

        self._set_status("Cleaned")

    def add_trigger(self):
        new_t = simpledialog.askstring("Add Trigger", "Enter new trigger word:", parent=self)
        if not new_t:
            return
        new_t = normalize_trigger(new_t)
        if not new_t:
            return

        if new_t in self.triggers:
            messagebox.showinfo("Info", "Trigger already exists.", parent=self)
        else:
            self.triggers.append(new_t)
            save_triggers(self.triggers_path, self.triggers)

        tr = simpledialog.askstring(
            "Translation (optional)",
            f"Translation for '{new_t}' (optional):",
            parent=self
        )
        if tr is not None:
            tr = tr.strip()
            if tr:
                upsert_translation(self.translations_path, new_t, tr)
                self.translations = load_translations(self.translations_path)

        group_list = ", ".join(sorted(self.groups.keys(), key=lambda s: s.lower()))
        g = simpledialog.askstring(
            "Group (optional)",
            "Enter group name to add this trigger.\n"
            "Leave empty to skip.\n\n"
            f"Existing groups:\n{group_list}",
            parent=self
        )
        if g is not None:
            g = g.strip()
            if g:
                if g not in self.groups:
                    self.groups[g] = []
                if new_t not in self.groups[g]:
                    self.groups[g].append(new_t)
                save_groups(self.groups_path, self.groups)
                self.group_combo["values"] = self._group_values()

        self.selected_set.add(new_t)
        self._render_trigger_list()

    def _display_text_for_trigger(self, t: str) -> str:
        tr = (self.translations or {}).get(t, "")
        tr = tr.strip() if isinstance(tr, str) else ""
        if tr:
            return f"{tr} ({t})"
        return t