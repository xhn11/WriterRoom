"""
WriterRoom – hlavní okno aplikace.
Virtuální místnost pro spisovatele: kostra scény → kapitola → audioknihá (XTTS-v2).

Spuštění:
    python main.py
"""
from __future__ import annotations

import sys, os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path
from typing import Optional

# ── Sdílené moduly (import+ ekvivalent) ──────────────────────────────────────
_loader = Path(__file__).parents[1] / "modules_loader.py"
if _loader.exists():
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location("modules_loader", _loader)
    _m = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_m)  # type: ignore

from core.models import Chapter, Project
from core.generator import generate_chapter, summarize_chapter, split_raw_text
from core.tts_engine import get_engine, BUILTIN_SPEAKERS

# ── Konfigurace / autosave ────────────────────────────────────────────────────
_WRITERROOM_DIR = Path.home() / ".writerroom"
_CONFIG_FILE    = _WRITERROOM_DIR / "config.json"
_AUTOSAVE_FILE  = _WRITERROOM_DIR / "autosave.wrp"
_AUTOSAVE_INTERVAL_MS = 60_000  # ms

# ── Barvy (tmavé téma) ────────────────────────────────────────────────────────
BG       = "#1e1e2e"
PANEL    = "#2a2a3e"
ENTRY    = "#313244"
ACCENT   = "#7c3aed"
FG       = "#cdd6f4"
FG_DIM   = "#6c7086"
BTN      = "#45475a"
GREEN    = "#a6e3a1"


# ── Pomocná třída: modální dialog pro nastavení projektu ──────────────────────

class ProjectDialog(tk.Toplevel):
    """Modální dialog pro vytvoření / editaci nastavení projektu."""

    def __init__(self, parent: tk.Tk, dialog_title: str = "Projekt",
                 initial: Optional[dict] = None) -> None:
        super().__init__(parent)
        self.title(dialog_title)
        self.resizable(False, False)
        self.configure(bg=BG)
        self.result: Optional[dict] = None
        initial = initial or {}

        pad = {"padx": 12, "pady": 5}

        def lbl(row: int, text: str) -> None:
            tk.Label(self, text=text, bg=BG, fg=FG,
                     font=("Segoe UI", 10), anchor=tk.W).grid(
                row=row, column=0, sticky=tk.W, **pad)

        lbl(0, "Název díla:")
        self._title_entry = tk.Entry(
            self, bg=ENTRY, fg=FG, insertbackground=FG,
            relief=tk.FLAT, font=("Segoe UI", 10), width=44)
        self._title_entry.insert(0, initial.get("title", "Nový projekt"))
        self._title_entry.grid(row=0, column=1, **pad)

        lbl(1, "Žánr:")
        self._genre_entry = tk.Entry(
            self, bg=ENTRY, fg=FG, insertbackground=FG,
            relief=tk.FLAT, font=("Segoe UI", 10), width=44)
        self._genre_entry.insert(0, initial.get("genre", "Fantasy"))
        self._genre_entry.grid(row=1, column=1, **pad)

        lbl(2, "Styl / poznámky:")
        self._style_text = tk.Text(
            self, bg=ENTRY, fg=FG, insertbackground=FG,
            relief=tk.FLAT, font=("Segoe UI", 10), width=44, height=4)
        self._style_text.insert("1.0", initial.get("style_notes", ""))
        self._style_text.grid(row=2, column=1, **pad)

        btn_row = tk.Frame(self, bg=BG)
        btn_row.grid(row=3, column=0, columnspan=2, pady=12)
        tk.Button(btn_row, text="OK", command=self._ok,
                  bg=ACCENT, fg="white", relief=tk.FLAT,
                  padx=24, pady=6, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_row, text="Zrušit", command=self.destroy,
                  bg=BTN, fg=FG, relief=tk.FLAT,
                  padx=24, pady=6, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=4)

        self._title_entry.focus_set()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")
        parent.wait_window(self)

    def _ok(self) -> None:
        proj_title = self._title_entry.get().strip()
        if not proj_title:
            messagebox.showwarning("Upozornění", "Název díla nesmí být prázdný.", parent=self)
            return
        self.result = {
            "title": proj_title,
            "genre": self._genre_entry.get().strip(),
            "style_notes": self._style_text.get("1.0", tk.END).strip(),
        }
        self.destroy()


# ── Hlavní aplikace ───────────────────────────────────────────────────────────

class WriterRoomApp(tk.Tk):

    def __init__(self) -> None:
        super().__init__()
        self.title("WriterRoom")
        self.geometry("1320x820")
        self.minsize(960, 600)
        self.configure(bg=BG)

        self.project: Optional[Project] = None
        self.project_path: Optional[Path] = None
        self.current_idx: Optional[int] = None

        # TTS state
        self.voice_wav: Optional[Path] = None
        self.output_dir: Path = Path("audio")

        _WRITERROOM_DIR.mkdir(parents=True, exist_ok=True)

        self._build_menu()
        self._build_ui()
        self._restore_last_project()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_autosave()

    # ── Menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self) -> None:
        bar = tk.Menu(self, bg=PANEL, fg=FG, activebackground=ACCENT)
        file_m = tk.Menu(bar, tearoff=0, bg=PANEL, fg=FG, activebackground=ACCENT)
        file_m.add_command(label="Nový projekt…",   command=self._new_project,  accelerator="Ctrl+N")
        file_m.add_command(label="Otevřít…",        command=self._open_project, accelerator="Ctrl+O")
        file_m.add_command(label="Uložit",          command=self._save_project, accelerator="Ctrl+S")
        file_m.add_command(label="Uložit jako…",    command=self._save_as)
        file_m.add_separator()
        file_m.add_command(label="Konec",           command=self.quit)
        bar.add_cascade(label="Soubor", menu=file_m)
        self.config(menu=bar)
        self.bind("<Control-n>", lambda _e: self._new_project())
        self.bind("<Control-o>", lambda _e: self._open_project())
        self.bind("<Control-s>", lambda _e: self._save_project())

    # ── Sestavení UI ──────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root_frame = tk.Frame(self, bg=BG)
        root_frame.pack(fill=tk.BOTH, expand=True)

        # Info lišta
        info = tk.Frame(root_frame, bg=PANEL, pady=6, padx=12)
        info.pack(fill=tk.X)
        tk.Label(info, text="Projekt:", bg=PANEL, fg=FG, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self._lbl_title = tk.Label(info, text="—", bg=PANEL, fg=ACCENT,
                                    font=("Segoe UI", 10, "bold"))
        self._lbl_title.pack(side=tk.LEFT, padx=4)
        tk.Label(info, text="Žánr:", bg=PANEL, fg=FG,
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(20, 4))
        self._lbl_genre = tk.Label(info, text="—", bg=PANEL, fg=FG, font=("Segoe UI", 10))
        self._lbl_genre.pack(side=tk.LEFT)
        self._mkbtn(info, "⚙ Nastavení projektu",
                    self._edit_project).pack(side=tk.RIGHT)

        # Hlavní oblast
        area = tk.Frame(root_frame, bg=BG)
        area.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Levý panel: seznam kapitol
        left = tk.Frame(area, bg=PANEL, width=230)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        left.pack_propagate(False)

        tk.Label(left, text="KAPITOLY", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 9, "bold"), pady=8).pack(fill=tk.X)

        btn_row = tk.Frame(left, bg=PANEL)
        btn_row.pack(fill=tk.X, padx=4, pady=(0, 4))
        self._mkbtn(btn_row, "+ Přidat",      self._add_chapter).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._mkbtn(btn_row, "✕", self._delete_chapter, width=3).pack(side=tk.LEFT, padx=(4, 0))

        self._ch_list = tk.Listbox(
            left, bg=ENTRY, fg=FG, selectbackground=ACCENT,
            selectforeground="white", relief=tk.FLAT, borderwidth=0,
            font=("Segoe UI", 10), activestyle="none")
        self._ch_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._ch_list.bind("<<ListboxSelect>>", self._on_ch_select)
        self._ch_list.bind("<Double-Button-1>", self._rename_chapter)

        # Pravý panel: záložky
        right = tk.Frame(area, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_notebook(right)

        # Status bar
        self._status_var = tk.StringVar(value="Připraven")
        tk.Label(root_frame, textvariable=self._status_var, bg=PANEL, fg=FG,
                 font=("Segoe UI", 9), anchor=tk.W, padx=10,
                 pady=4).pack(fill=tk.X, side=tk.BOTTOM)

    def _build_notebook(self, parent: tk.Frame) -> None:
        style = ttk.Style()
        style.theme_use("default")
        style.configure("W.TNotebook",     background=BG,    borderwidth=0)
        style.configure("W.TNotebook.Tab", background=PANEL, foreground=FG,
                         padding=[14, 6], font=("Segoe UI", 10))
        style.map("W.TNotebook.Tab",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "white")])

        nb = ttk.Notebook(parent, style="W.TNotebook")
        nb.pack(fill=tk.BOTH, expand=True)
        self._nb = nb

        self._build_tab_skeleton(nb)
        self._build_tab_chapter(nb)
        self._build_tab_tts(nb)
        self._build_tab_import(nb)

    # ── Záložka: Kostra ───────────────────────────────────────────────────────

    def _build_tab_skeleton(self, nb: ttk.Notebook) -> None:
        f = tk.Frame(nb, bg=BG)
        nb.add(f, text="  Kostra  ")

        toolbar = tk.Frame(f, bg=BG, pady=6)
        toolbar.pack(fill=tk.X, padx=8)
        tk.Label(toolbar, text="Osnova / kostra kapitoly (scény, dialogy, poznámky):",
                 bg=BG, fg=FG, font=("Segoe UI", 10)).pack(side=tk.LEFT)

        temp_frm = tk.Frame(toolbar, bg=BG)
        temp_frm.pack(side=tk.RIGHT)
        tk.Label(temp_frm, text="Kreativita:", bg=BG, fg=FG,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._temp_var = tk.DoubleVar(value=0.85)
        tk.Scale(temp_frm, variable=self._temp_var, from_=0.1, to=1.0,
                 resolution=0.05, orient=tk.HORIZONTAL, length=110,
                 bg=BG, fg=FG, highlightthickness=0,
                 troughcolor=ENTRY, activebackground=ACCENT,
                 showvalue=True).pack(side=tk.LEFT, padx=4)

        sk_frm = tk.Frame(f, bg=BG)
        sk_frm.pack(fill=tk.BOTH, expand=True, padx=8)
        self._sk_text = tk.Text(sk_frm, bg=ENTRY, fg=FG, insertbackground=FG,
                                 font=("Segoe UI", 11), relief=tk.FLAT,
                                 wrap=tk.WORD, padx=12, pady=10)
        sk_scroll = tk.Scrollbar(sk_frm, command=self._sk_text.yview, bg=PANEL)
        self._sk_text.configure(yscrollcommand=sk_scroll.set)
        sk_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._sk_text.pack(fill=tk.BOTH, expand=True)

        bot = tk.Frame(f, bg=BG, pady=8)
        bot.pack(fill=tk.X, padx=8)
        self._btn_gen = self._mkbtn(
            bot, "✦  Generovat kapitolu z kostry",
            self._generate_chapter, bg=ACCENT, fg="white")
        self._btn_gen.pack(side=tk.LEFT)
        self._mkbtn(bot, "💾 Uložit", self._save_current).pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(f, text="Tip: Popis scén, postav, děje, nálady – AI vše rozepíše do plné kapitoly.",
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).pack(anchor=tk.W, padx=8, pady=(0, 4))

    # ── Záložka: Kapitola ─────────────────────────────────────────────────────

    def _build_tab_chapter(self, nb: ttk.Notebook) -> None:
        f = tk.Frame(nb, bg=BG)
        nb.add(f, text="  Kapitola  ")

        ch_frm = tk.Frame(f, bg=BG)
        ch_frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._ch_text = tk.Text(ch_frm, bg=ENTRY, fg=FG, insertbackground=FG,
                                 font=("Georgia", 12), relief=tk.FLAT,
                                 wrap=tk.WORD, padx=16, pady=12,
                                 spacing1=3, spacing3=3)
        ch_scroll = tk.Scrollbar(ch_frm, command=self._ch_text.yview, bg=PANEL)
        self._ch_text.configure(yscrollcommand=ch_scroll.set)
        ch_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._ch_text.pack(fill=tk.BOTH, expand=True)

        bot = tk.Frame(f, bg=BG, pady=6)
        bot.pack(fill=tk.X, padx=8)
        self._mkbtn(bot, "💾 Uložit", self._save_current).pack(side=tk.LEFT)
        self._mkbtn(bot, "↩ Zpět na kostru",
                    lambda: self._nb.select(0)).pack(side=tk.LEFT, padx=(8, 0))
        self._lbl_words = tk.Label(bot, text="0 slov", bg=BG, fg=FG_DIM,
                                    font=("Segoe UI", 9))
        self._lbl_words.pack(side=tk.RIGHT)
        self._ch_text.bind("<KeyRelease>", self._update_words)

    # ── Záložka: Audiokniha ───────────────────────────────────────────────────

    def _build_tab_tts(self, nb: ttk.Notebook) -> None:
        f = tk.Frame(nb, bg=BG)
        nb.add(f, text="  Audiokniha  ")

        # Nastavení hlasu
        voice_frm = tk.LabelFrame(f, text="Nastavení hlasu (XTTS-v2)",
                                   bg=BG, fg=FG, font=("Segoe UI", 10),
                                   padx=10, pady=8)
        voice_frm.pack(fill=tk.X, padx=12, pady=12)

        # Jazyk
        row0 = tk.Frame(voice_frm, bg=BG)
        row0.pack(fill=tk.X, pady=2)
        tk.Label(row0, text="Jazyk:", width=14, anchor=tk.W,
                 bg=BG, fg=FG, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self._lang_var = tk.StringVar(value="cs")
        lang_cb = ttk.Combobox(row0, textvariable=self._lang_var, width=8, state="readonly")
        lang_cb["values"] = ["cs", "en", "de", "fr", "es", "pl", "sk", "it", "pt", "ru"]
        lang_cb.pack(side=tk.LEFT)

        # Vestavěný mluvčí
        row1 = tk.Frame(voice_frm, bg=BG)
        row1.pack(fill=tk.X, pady=2)
        tk.Label(row1, text="Vestavěný mluvčí:", width=14, anchor=tk.W,
                 bg=BG, fg=FG, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self._speaker_var = tk.StringVar(value="Ana Florence")
        spk_cb = ttk.Combobox(row1, textvariable=self._speaker_var,
                               values=BUILTIN_SPEAKERS, width=22, state="readonly")
        spk_cb.pack(side=tk.LEFT)
        tk.Label(row1, text="(použije se, pokud nevyberete vlastní WAV)",
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=8)

        # Vlastní hlas
        row2 = tk.Frame(voice_frm, bg=BG)
        row2.pack(fill=tk.X, pady=2)
        tk.Label(row2, text="Vlastní hlas (WAV):", width=14, anchor=tk.W,
                 bg=BG, fg=FG, font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self._voice_lbl_var = tk.StringVar(value="— žádný —")
        tk.Label(row2, textvariable=self._voice_lbl_var, bg=BG, fg=ACCENT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=4)
        self._mkbtn(row2, "📂 Vybrat WAV…", self._pick_voice).pack(side=tk.LEFT, padx=4)
        self._mkbtn(row2, "✕ Smazat", self._clear_voice).pack(side=tk.LEFT)
        tk.Label(voice_frm, text="Vzorový hlas: WAV soubor ~6 s (klonování hlasu).",
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).pack(anchor=tk.W)

        # Cíl a výstup
        mid = tk.Frame(f, bg=BG)
        mid.pack(fill=tk.X, padx=12, pady=4)

        tk.Label(mid, text="Generovat:", bg=BG, fg=FG,
                 font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self._tts_target = tk.StringVar(value="aktuální kapitolu")
        ttk.Combobox(mid, textvariable=self._tts_target, state="readonly", width=22,
                     values=["aktuální kapitolu", "celou knihu"]).pack(side=tk.LEFT, padx=8)

        tk.Label(mid, text="Výstupní složka:", bg=BG, fg=FG,
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(20, 4))
        self._outdir_var = tk.StringVar(value="audio")
        tk.Label(mid, textvariable=self._outdir_var, bg=BG, fg=ACCENT,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._mkbtn(mid, "📂", self._pick_outdir).pack(side=tk.LEFT, padx=4)

        # Tlačítko generovat
        gen_row = tk.Frame(f, bg=BG)
        gen_row.pack(fill=tk.X, padx=12, pady=(8, 4))
        self._btn_tts = self._mkbtn(
            gen_row, "▶  Generovat audio (XTTS-v2)",
            self._generate_audio, bg=ACCENT, fg="white")
        self._btn_tts.pack(side=tk.LEFT)

        # Progress
        self._tts_msg = tk.StringVar(value="")
        tk.Label(f, textvariable=self._tts_msg, bg=BG, fg=FG,
                 font=("Segoe UI", 9), wraplength=700,
                 justify=tk.LEFT).pack(anchor=tk.W, padx=12)
        self._tts_pb = ttk.Progressbar(f, mode="indeterminate")
        self._tts_pb.pack(fill=tk.X, padx=12, pady=4)

        # Seznam vygenerovaných souborů
        tk.Label(f, text="Vygenerované soubory:", bg=BG, fg=FG,
                 font=("Segoe UI", 10)).pack(anchor=tk.W, padx=12, pady=(6, 2))
        audio_frm = tk.Frame(f, bg=BG)
        audio_frm.pack(fill=tk.BOTH, expand=True, padx=12)
        self._audio_list = tk.Listbox(audio_frm, bg=ENTRY, fg=FG,
                                       selectbackground=ACCENT, relief=tk.FLAT,
                                       font=("Segoe UI", 10))
        asc = tk.Scrollbar(audio_frm, command=self._audio_list.yview, bg=PANEL)
        self._audio_list.configure(yscrollcommand=asc.set)
        asc.pack(side=tk.RIGHT, fill=tk.Y)
        self._audio_list.pack(fill=tk.BOTH, expand=True)

        self._mkbtn(f, "▶ Přehrát vybraný soubor",
                    self._play_audio).pack(anchor=tk.W, padx=12, pady=6)

    # ── Záložka: Import textu ─────────────────────────────────────────────────

    def _build_tab_import(self, nb: ttk.Notebook) -> None:
        f = tk.Frame(nb, bg=BG)
        nb.add(f, text="  Import textu  ")

        tk.Label(
            f,
            text="Vlož surový text (scénář, poznámky, celá kniha…) – AI ho automaticky rozdělí na kapitoly:",
            bg=BG, fg=FG, font=("Segoe UI", 10),
        ).pack(anchor=tk.W, padx=12, pady=(12, 4))

        inp_frm = tk.Frame(f, bg=BG)
        inp_frm.pack(fill=tk.BOTH, expand=True, padx=12)
        self._import_text = tk.Text(
            inp_frm, bg=ENTRY, fg=FG, insertbackground=FG,
            font=("Segoe UI", 11), relief=tk.FLAT,
            wrap=tk.WORD, padx=12, pady=10,
        )
        sc = tk.Scrollbar(inp_frm, command=self._import_text.yview, bg=PANEL)
        self._import_text.configure(yscrollcommand=sc.set)
        sc.pack(side=tk.RIGHT, fill=tk.Y)
        self._import_text.pack(fill=tk.BOTH, expand=True)

        # Volby
        opt_row = tk.Frame(f, bg=BG)
        opt_row.pack(fill=tk.X, padx=12, pady=(6, 0))

        tk.Label(opt_row, text="Přidat kapitoly:", bg=BG, fg=FG,
                 font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self._import_mode = tk.StringVar(value="na konec projektu")
        ttk.Combobox(
            opt_row, textvariable=self._import_mode, state="readonly", width=22,
            values=["na konec projektu", "nahradit celý projekt"],
        ).pack(side=tk.LEFT, padx=8)

        self._import_char_lbl = tk.Label(opt_row, text="0 znaků", bg=BG, fg=FG_DIM,
                                          font=("Segoe UI", 9))
        self._import_char_lbl.pack(side=tk.RIGHT)
        self._import_text.bind("<KeyRelease>", self._update_import_chars)

        # Tlačítko
        bot = tk.Frame(f, bg=BG, pady=8)
        bot.pack(fill=tk.X, padx=12)
        self._btn_import = self._mkbtn(
            bot, "✦  Rozdělit na kapitoly (AI)",
            self._run_import, bg=ACCENT, fg="white")
        self._btn_import.pack(side=tk.LEFT)
        self._mkbtn(bot, "Vymazat", self._clear_import).pack(side=tk.LEFT, padx=(8, 0))

        # Náhled výsledku
        tk.Label(f, text="Náhled rozpoznaných kapitol:", bg=BG, fg=FG,
                 font=("Segoe UI", 10)).pack(anchor=tk.W, padx=12, pady=(4, 2))
        prev_frm = tk.Frame(f, bg=BG)
        prev_frm.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        self._import_preview = tk.Listbox(
            prev_frm, bg=ENTRY, fg=FG, selectbackground=ACCENT,
            relief=tk.FLAT, font=("Segoe UI", 10), height=6,
        )
        psc = tk.Scrollbar(prev_frm, command=self._import_preview.yview, bg=PANEL)
        self._import_preview.configure(yscrollcommand=psc.set)
        psc.pack(side=tk.RIGHT, fill=tk.Y)
        self._import_preview.pack(fill=tk.BOTH, expand=True)

        self._import_result: list[dict] = []
        self._btn_import_confirm = self._mkbtn(
            f, "✔  Přidat rozpoznané kapitoly do projektu",
            self._confirm_import)
        self._btn_import_confirm.pack(anchor=tk.W, padx=12, pady=(0, 12))
        self._btn_import_confirm.config(state=tk.DISABLED)

    def _update_import_chars(self, _event=None) -> None:
        n = len(self._import_text.get("1.0", tk.END).strip())
        self._import_char_lbl.config(text=f"{n:,} znaků".replace(",", "\u00a0"))

    def _clear_import(self) -> None:
        self._import_text.delete("1.0", tk.END)
        self._import_preview.delete(0, tk.END)
        self._import_result = []
        self._btn_import_confirm.config(state=tk.DISABLED)
        self._update_import_chars()

    def _run_import(self) -> None:
        raw = self._import_text.get("1.0", tk.END).strip()
        if not raw:
            messagebox.showinfo("Upozornění", "Nejprve vlož text.", parent=self)
            return

        self._btn_import.config(state=tk.DISABLED, text="AI rozděluje…")
        self._import_preview.delete(0, tk.END)
        self._import_result = []
        self._btn_import_confirm.config(state=tk.DISABLED)
        self._set_status("AI rozděluje text na kapitoly…")

        def run() -> None:
            try:
                from copilot_api import is_available
                if not is_available():
                    self.after(0, lambda: messagebox.showerror(
                        "AI nedostupná", "Spusť: gh auth login"))
                    self.after(0, lambda: self._btn_import.config(
                        state=tk.NORMAL, text="✦  Rozdělit na kapitoly (AI)"))
                    return
                chapters = split_raw_text(raw)

                def update() -> None:
                    self._import_result = chapters
                    self._import_preview.delete(0, tk.END)
                    for i, ch in enumerate(chapters, 1):
                        preview = ch["text"][:60].replace("\n", " ")
                        self._import_preview.insert(
                            tk.END, f"{i:2}. {ch['title']}  —  {preview}…")
                    self._btn_import_confirm.config(state=tk.NORMAL)
                    self._btn_import.config(
                        state=tk.NORMAL, text="✦  Rozdělit na kapitoly (AI)")
                    self._set_status(
                        f"Rozpoznáno {len(chapters)} kapitol – potvrď přidání do projektu")

                self.after(0, update)
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda: messagebox.showerror("Chyba AI", err))
                self.after(0, lambda: self._btn_import.config(
                    state=tk.NORMAL, text="✦  Rozdělit na kapitoly (AI)"))
                self.after(0, lambda: self._set_status("Chyba importu"))

        threading.Thread(target=run, daemon=True).start()

    def _confirm_import(self) -> None:
        if not self._import_result or not self.project:
            return
        mode = self._import_mode.get()
        if mode == "nahradit celý projekt":
            if not messagebox.askyesno(
                "Potvrdit", "Smazat všechny stávající kapitoly a nahradit importovanými?",
                parent=self,
            ):
                return
            self.project.chapters.clear()
            self.current_idx = None
            self._clear_editors()

        for ch_data in self._import_result:
            self.project.chapters.append(
                Chapter(title=ch_data["title"], text=ch_data["text"])
            )

        self._refresh_ch_list()
        n = len(self._import_result)
        self._set_status(f"Přidáno {n} kapitol z importu")
        self._import_result = []
        self._import_preview.delete(0, tk.END)
        self._btn_import_confirm.config(state=tk.DISABLED)
        # Přepni na seznam kapitol
        self._nb.select(0)

    # ── Projekt management ────────────────────────────────────────────────────

    def _new_project(self) -> None:
        dlg = ProjectDialog(self, "Nový projekt")
        if not dlg.result:
            if self.project is None:
                # Vytvoříme prázdný projekt aby app nebyla nefunkční
                self.project = Project(title="Nový projekt")
                self._refresh_ch_list()
                self._update_info()
            return
        self.project = Project(
            title=dlg.result["title"],
            genre=dlg.result["genre"],
            style_notes=dlg.result["style_notes"],
        )
        self.project_path = None
        self.current_idx = None
        self._refresh_ch_list()
        self._update_info()
        self.title(f"WriterRoom – {self.project.title}")
        self._clear_editors()
        self._add_chapter(default_name="Kapitola 1")

    def _open_project(self) -> None:
        path = filedialog.askopenfilename(
            title="Otevřít projekt",
            filetypes=[("WriterRoom projekt", "*.wrp"),
                       ("JSON", "*.json"),
                       ("Všechny soubory", "*.*")])
        if not path:
            return
        try:
            self.project = Project.load(Path(path))
            self.project_path = Path(path)
            self.current_idx = None
            self._refresh_ch_list()
            self._update_info()
            self.title(f"WriterRoom – {self.project.title}")
            self._clear_editors()
            if self.project.chapters:
                self._ch_list.selection_set(0)
                self._on_ch_select(None)
        except Exception as exc:
            messagebox.showerror("Chyba", f"Nelze otevřít projekt:\n{exc}")

    def _save_project(self) -> None:
        if not self.project:
            return
        self._sync_to_model()
        if self.project_path:
            self.project.save(self.project_path)
            self._save_config({"last_project": str(self.project_path)})
            self._set_status(f"Uloženo: {self.project_path.name}")
        else:
            self._save_as()

    def _save_as(self) -> None:
        if not self.project:
            return
        self._sync_to_model()
        path = filedialog.asksaveasfilename(
            title="Uložit projekt jako",
            defaultextension=".wrp",
            filetypes=[("WriterRoom projekt", "*.wrp"), ("JSON", "*.json")],
            initialfile=self.project.title)
        if not path:
            return
        self.project_path = Path(path)
        self.project.save(self.project_path)
        self._save_config({"last_project": str(self.project_path)})
        self.title(f"WriterRoom \u2013 {self.project.title}")
        self._set_status(f"Uloženo: {self.project_path.name}")

    def _save_current(self) -> None:
        self._sync_to_model()
        self._save_project()

    def _edit_project(self) -> None:
        if not self.project:
            return
        dlg = ProjectDialog(self, "Nastavení projektu", initial={
            "title": self.project.title,
            "genre": self.project.genre,
            "style_notes": self.project.style_notes,
        })
        if dlg.result:
            self.project.title = dlg.result["title"]
            self.project.genre = dlg.result["genre"]
            self.project.style_notes = dlg.result["style_notes"]
            self._update_info()
            self.title(f"WriterRoom – {self.project.title}")

    # ── Kapitoly ──────────────────────────────────────────────────────────────

    def _add_chapter(self, default_name: Optional[str] = None) -> None:
        if not self.project:
            return
        if default_name:
            name = default_name
        else:
            n = len(self.project.chapters) + 1
            name = simpledialog.askstring(
                "Nová kapitola", "Název kapitoly:",
                initialvalue=f"Kapitola {n}", parent=self)
            if not name:
                return
        self._sync_to_model()
        ch = Chapter(title=name)
        self.project.chapters.append(ch)
        self._refresh_ch_list()
        idx = len(self.project.chapters) - 1
        self._ch_list.selection_clear(0, tk.END)
        self._ch_list.selection_set(idx)
        self._on_ch_select(None)

    def _delete_chapter(self) -> None:
        if not self.project or not self.project.chapters:
            return
        sel = self._ch_list.curselection()
        if not sel:
            messagebox.showinfo("Upozornění", "Nejprve vyber kapitolu.", parent=self)
            return
        idx = sel[0]
        ch = self.project.chapters[idx]
        if not messagebox.askyesno("Smazat",
                                    f'Smazat kapitolu \u201e{ch.title}\u201c?', parent=self):
            return
        self.project.chapters.pop(idx)
        self.current_idx = None
        self._refresh_ch_list()
        self._clear_editors()
        if self.project.chapters:
            new_idx = min(idx, len(self.project.chapters) - 1)
            self._ch_list.selection_set(new_idx)
            self._on_ch_select(None)

    def _rename_chapter(self, _event=None) -> None:
        sel = self._ch_list.curselection()
        if not sel or not self.project:
            return
        idx = sel[0]
        ch = self.project.chapters[idx]
        new_name = simpledialog.askstring(
            "Přejmenovat", "Nový název:",
            initialvalue=ch.title, parent=self)
        if new_name:
            ch.title = new_name
            self._refresh_ch_list()
            self._ch_list.selection_set(idx)

    def _on_ch_select(self, _event) -> None:
        sel = self._ch_list.curselection()
        if not sel:
            return
        if self.current_idx is not None:
            self._sync_to_model()
        idx = sel[0]
        self.current_idx = idx
        ch = self.project.chapters[idx]
        self._sk_text.delete("1.0", tk.END)
        self._sk_text.insert("1.0", ch.skeleton)
        self._ch_text.delete("1.0", tk.END)
        self._ch_text.insert("1.0", ch.text)
        self._update_words()
        self._set_status(f"Kapitola: {ch.title}")

    def _refresh_ch_list(self) -> None:
        self._ch_list.delete(0, tk.END)
        if not self.project:
            return
        for ch in self.project.chapters:
            marker = "●" if ch.text else "○"
            self._ch_list.insert(tk.END, f"  {marker}  {ch.title}")

    def _sync_to_model(self) -> None:
        if self.current_idx is None or not self.project:
            return
        if self.current_idx >= len(self.project.chapters):
            return
        ch = self.project.chapters[self.current_idx]
        ch.skeleton = self._sk_text.get("1.0", tk.END).rstrip("\n")
        ch.text     = self._ch_text.get("1.0", tk.END).rstrip("\n")

    def _clear_editors(self) -> None:
        self._sk_text.delete("1.0", tk.END)
        self._ch_text.delete("1.0", tk.END)

    def _update_words(self, _event=None) -> None:
        words = len(self._ch_text.get("1.0", tk.END).split())
        self._lbl_words.config(text=f"{words} slov")

    # ── AI generování kapitoly ────────────────────────────────────────────────

    def _generate_chapter(self) -> None:
        if self.current_idx is None or not self.project:
            messagebox.showinfo("Upozornění", "Nejprve vyber kapitolu.", parent=self)
            return
        skeleton = self._sk_text.get("1.0", tk.END).strip()
        if not skeleton:
            messagebox.showinfo("Upozornění",
                                "Nejprve napiš kostru kapitoly na záložce Kostra.", parent=self)
            return

        self._btn_gen.config(state=tk.DISABLED, text="Generuji…")
        self._set_status("AI generuje kapitolu…")

        temp        = self._temp_var.get()
        project     = self.project
        idx         = self.current_idx

        # Shrnutí předchozí kapitoly pro kontext
        prev_summary = ""
        if idx > 0 and project.chapters[idx - 1].text:
            prev_summary = project.chapters[idx - 1].title

        def run() -> None:
            try:
                from copilot_api import is_available
                if not is_available():
                    self.after(0, lambda: messagebox.showerror(
                        "AI nedostupná", "Copilot AI nedostupný.\nSpusť: gh auth login"))
                    self.after(0, lambda: self._btn_gen.config(
                        state=tk.NORMAL, text="✦  Generovat kapitolu z kostry"))
                    return
                text = generate_chapter(
                    skeleton=skeleton,
                    project_title=project.title,
                    genre=project.genre,
                    style_notes=project.style_notes,
                    previous_summary=prev_summary,
                    temperature=temp,
                    max_tokens=3000,
                )
                def update() -> None:
                    self._ch_text.delete("1.0", tk.END)
                    self._ch_text.insert("1.0", text)
                    self._update_words()
                    self._sync_to_model()
                    self._refresh_ch_list()
                    self._nb.select(1)
                    w = len(text.split())
                    self._set_status(f"Kapitola vygenerována ({w} slov)")
                    self._btn_gen.config(state=tk.NORMAL,
                                         text="✦  Generovat kapitolu z kostry")
                self.after(0, update)
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda: messagebox.showerror("Chyba AI", err))
                self.after(0, lambda: self._btn_gen.config(
                    state=tk.NORMAL, text="✦  Generovat kapitolu z kostry"))
                self.after(0, lambda: self._set_status("Chyba AI generování"))

        threading.Thread(target=run, daemon=True).start()

    # ── TTS / Audiokniha ──────────────────────────────────────────────────────

    def _pick_voice(self) -> None:
        path = filedialog.askopenfilename(
            title="Vyber vzorový hlas (WAV ~6 s)",
            filetypes=[("WAV soubory", "*.wav"), ("Všechny soubory", "*.*")])
        if path:
            self.voice_wav = Path(path)
            self._voice_lbl_var.set(Path(path).name)

    def _clear_voice(self) -> None:
        self.voice_wav = None
        self._voice_lbl_var.set("— žádný —")

    def _pick_outdir(self) -> None:
        d = filedialog.askdirectory(title="Výstupní složka pro audio")
        if d:
            self.output_dir = Path(d)
            self._outdir_var.set(str(self.output_dir))

    def _generate_audio(self) -> None:
        if not self.project:
            return
        self._sync_to_model()

        target = self._tts_target.get()
        if target == "aktuální kapitolu":
            if self.current_idx is None:
                messagebox.showinfo("Upozornění", "Nejprve vyber kapitolu.", parent=self)
                return
            ch_obj = self.project.chapters[self.current_idx]
            if not ch_obj.text.strip():
                messagebox.showinfo("Upozornění",
                                    "Vybraná kapitola nemá žádný text.\n"
                                    "Nejprve ji vygeneruj nebo napiš.", parent=self)
                return
            chapters_todo = [ch_obj]
        else:
            chapters_todo = [ch for ch in self.project.chapters if ch.text.strip()]
            if not chapters_todo:
                messagebox.showinfo("Upozornění",
                                    "Žádná kapitola neobsahuje text.", parent=self)
                return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._btn_tts.config(state=tk.DISABLED, text="Generuji audio…")
        self._tts_pb.start()

        voice_wav   = self.voice_wav
        speaker     = self._speaker_var.get()
        language    = self._lang_var.get()
        output_dir  = self.output_dir

        def progress(msg: str) -> None:
            self.after(0, lambda m=msg: self._tts_msg.set(m))
            self.after(0, lambda m=msg: self._set_status(m))

        def run() -> None:
            try:
                engine = get_engine()
                out_files: list[Path] = []
                for ch in chapters_todo:
                    safe = "".join(c if c.isalnum() or c in " -_" else "_"
                                   for c in ch.title).strip()
                    out_path = output_dir / f"{safe}.wav"
                    engine.synthesize(
                        text=ch.text,
                        output_path=out_path,
                        speaker_wav=voice_wav,
                        speaker=speaker,
                        language=language,
                        progress_callback=progress,
                    )
                    ch.audio_path = str(out_path)
                    out_files.append(out_path)

                def done() -> None:
                    self._btn_tts.config(state=tk.NORMAL,
                                          text="▶  Generovat audio (XTTS-v2)")
                    self._tts_pb.stop()
                    self._refresh_audio_list()
                    self._set_status(
                        f"Audio hotovo: {len(out_files)} soubor(ů) → {output_dir}")

                self.after(0, done)
            except Exception as exc:
                err = str(exc)
                self.after(0, lambda: messagebox.showerror("Chyba TTS", err))
                self.after(0, lambda: self._btn_tts.config(
                    state=tk.NORMAL, text="▶  Generovat audio (XTTS-v2)"))
                self.after(0, self._tts_pb.stop)
                self.after(0, lambda: self._set_status("Chyba TTS generování"))

        threading.Thread(target=run, daemon=True).start()

    def _refresh_audio_list(self) -> None:
        self._audio_list.delete(0, tk.END)
        if not self.output_dir.exists():
            return
        for f in sorted(self.output_dir.glob("*.wav")):
            self._audio_list.insert(tk.END, f.name)

    def _play_audio(self) -> None:
        sel = self._audio_list.curselection()
        if not sel:
            return
        name = self._audio_list.get(sel[0])
        path = self.output_dir / name
        if not path.exists():
            messagebox.showerror("Chyba", f"Soubor nenalezen:\n{path}")
            return
        try:
            os.startfile(str(path))
            self._set_status(f"Přehrávám: {name}")
        except Exception as exc:
            messagebox.showerror("Chyba přehrávání", str(exc))

    # ── Pomocné metody ────────────────────────────────────────────────────────

    def _mkbtn(self, parent: tk.Widget, text: str, command,
               bg: str = BTN, fg: str = FG,
               width: Optional[int] = None) -> tk.Button:
        kw: dict = dict(text=text, command=command, bg=bg, fg=fg,
                        relief=tk.FLAT, padx=10, pady=5,
                        font=("Segoe UI", 10), cursor="hand2",
                        activebackground=ACCENT, activeforeground="white")
        if width is not None:
            kw["width"] = width
        return tk.Button(parent, **kw)

    def _set_status(self, msg: str) -> None:
        self._status_var.set(msg)
        self.update_idletasks()

    def _update_info(self) -> None:
        if self.project:
            self._lbl_title.config(text=self.project.title)
            self._lbl_genre.config(text=self.project.genre or "—")

    # ── Autosave / konfigurace ─────────────────────────────────────────────────

    def _load_config(self) -> dict:
        try:
            import json as _json
            return _json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_config(self, data: dict) -> None:
        try:
            import json as _json
            _CONFIG_FILE.write_text(
                _json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _restore_last_project(self) -> None:
        cfg = self._load_config()
        last = cfg.get("last_project", "")
        if last and Path(last).exists():
            try:
                self.project = Project.load(Path(last))
                self.project_path = Path(last)
                self.current_idx = None
                self._refresh_ch_list()
                self._update_info()
                self.title(f"WriterRoom \u2013 {self.project.title}")
                if self.project.chapters:
                    self._ch_list.selection_set(0)
                    self._on_ch_select(None)
                self._set_status(f"Obnoven projekt: {Path(last).name}")
                return
            except Exception:
                pass
        # Žádný uložený projekt – spusť nový
        self._new_project()

    def _autosave(self) -> None:
        """Uloží projekt bez dotazu. Pokud nemá cestu, uloží do autosave souboru."""
        if not self.project:
            return
        self._sync_to_model()
        if self.project_path:
            target = self.project_path
        else:
            target = _AUTOSAVE_FILE
            self.project_path = target
        try:
            self.project.save(target)
            self._save_config({"last_project": str(target)})
        except Exception:
            pass

    def _schedule_autosave(self) -> None:
        self._autosave()
        self.after(_AUTOSAVE_INTERVAL_MS, self._schedule_autosave)

    def _on_close(self) -> None:
        self._autosave()
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = WriterRoomApp()
    app.mainloop()
