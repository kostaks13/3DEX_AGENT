"""
3DEXPERIENCE Parametre İsim Kontrol Aracı
Offline LLM ile CATIA parametre isimlerini analiz eder.
"""

import json
import os
import threading
import re
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext
from pathlib import Path

from llama_cpp import Llama

MODEL_DIR = Path(__file__).parent / "models"
DEFAULT_MODEL = "qwen2.5-1.5b-instruct-q4_k_m.gguf"

# ── Catppuccin Mocha ──
CRUST = "#11111b"
MANTLE = "#181825"
BASE = "#1e1e2e"
SURFACE0 = "#313244"
SURFACE1 = "#45475a"
OVERLAY0 = "#6c7086"
SUBTEXT0 = "#a6adc8"
TEXT = "#cdd6f4"
LAVENDER = "#b4befe"
BLUE = "#89b4fa"
GREEN = "#a6e3a1"
PEACH = "#fab387"
RED = "#f38ba8"
YELLOW = "#f9e2af"

FONT_FAMILY = "Consolas"
FONT_SM = (FONT_FAMILY, 10)
FONT_MD = (FONT_FAMILY, 11)
FONT_LG = (FONT_FAMILY, 13, "bold")



class HoverButton(tk.Canvas):
    def __init__(self, parent, text="", command=None,
                 bg_color=BLUE, hover_color=LAVENDER, fg_color=CRUST,
                 width=100, height=34, radius=8, font=FONT_MD, **kw):
        super().__init__(parent, width=width, height=height,
                         highlightthickness=0, bd=0,
                         bg=parent.cget("bg") if hasattr(parent, "cget") else BASE, **kw)
        self._text = text
        self._command = command
        self._bg = bg_color
        self._hover = hover_color
        self._fg = fg_color
        self._radius = radius
        self._font = font
        self._current_bg = bg_color

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_click)
        self.bind("<Configure>", self._draw)

    def _draw(self, _e=None):
        self.delete("all")
        w, h, r = self.winfo_width(), self.winfo_height(), self._radius
        pts = [
            r, 0, r, 0, w - r, 0, w - r, 0,
            w, 0, w, r, w, r, w, h - r,
            w, h - r, w, h, w - r, h, w - r, h,
            r, h, r, h, 0, h, 0, h - r,
            0, h - r, 0, r, 0, r, 0, 0,
        ]
        self.create_polygon(pts, smooth=True, fill=self._current_bg, outline="")
        self.create_text(w // 2, h // 2, text=self._text, fill=self._fg, font=self._font)

    def _on_enter(self, _e):
        self._current_bg = self._hover
        self._draw()

    def _on_leave(self, _e):
        self._current_bg = self._bg
        self._draw()

    def _on_click(self, _e):
        if self._command:
            self._command()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("3DEXPERIENCE Parametre Kontrol")
        self.geometry("960x640")
        self.configure(bg=CRUST)
        self.minsize(760, 480)

        self._llm = None
        self._loaded_params = None
        self._checking = False

        self._build_styles()
        self._build_ui()

    def _build_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame", background=BASE)
        s.configure("TLabel", background=BASE, foreground=TEXT, font=FONT_MD)
        s.configure("Treeview",
                     background=SURFACE0, foreground=TEXT, fieldbackground=SURFACE0,
                     font=FONT_SM, rowheight=24)
        s.configure("Treeview.Heading",
                     background=SURFACE1, foreground=LAVENDER,
                     font=(FONT_FAMILY, 10, "bold"))
        s.map("Treeview", background=[("selected", BLUE)], foreground=[("selected", CRUST)])

    # ── Ana düzen ──
    def _build_ui(self):
        # Üst başlık
        top = tk.Frame(self, bg=MANTLE, height=52)
        top.pack(fill=tk.X)
        top.pack_propagate(False)

        tk.Label(top, text="◆  3DEXPERIENCE Parametre Kontrol",
                 bg=MANTLE, fg=BLUE, font=(FONT_FAMILY, 15, "bold")).pack(side=tk.LEFT, padx=16)

        self._status_var = tk.StringVar(value="Model: hazır değil")
        self._status_label = tk.Label(top, textvariable=self._status_var,
                                       bg=MANTLE, fg=OVERLAY0, font=FONT_SM)
        self._status_label.pack(side=tk.RIGHT, padx=16)

        # Gövde: sol (parametre listesi) + sağ (sonuçlar)
        body = tk.Frame(self, bg=CRUST)
        body.pack(fill=tk.BOTH, expand=True)

        self._build_left_panel(body)
        tk.Frame(body, bg=SURFACE0, width=1).pack(side=tk.LEFT, fill=tk.Y)
        self._build_right_panel(body)

    # ── Sol panel: JSON yükle + parametre tablosu ──
    def _build_left_panel(self, parent):
        left = tk.Frame(parent, bg=BASE, width=380)
        left.pack(side=tk.LEFT, fill=tk.BOTH)
        left.pack_propagate(False)

        # Butonlar
        btn_bar = tk.Frame(left, bg=BASE)
        btn_bar.pack(fill=tk.X, padx=12, pady=(12, 0))

        HoverButton(
            btn_bar, text="📂  JSON Yükle", command=self._load_json,
            bg_color=SURFACE0, hover_color=SURFACE1, fg_color=PEACH,
            width=170, height=36, font=FONT_MD,
        ).pack(side=tk.LEFT)

        HoverButton(
            btn_bar, text="🔍  Kontrol Et", command=self._check_params,
            bg_color=BLUE, hover_color=LAVENDER, fg_color=CRUST,
            width=170, height=36, font=FONT_MD,
        ).pack(side=tk.RIGHT)

        # Dosya bilgisi
        self._file_var = tk.StringVar(value="Henüz dosya yüklenmedi")
        tk.Label(left, textvariable=self._file_var, bg=BASE, fg=OVERLAY0,
                 font=FONT_SM).pack(anchor="w", padx=14, pady=(8, 4))

        # Parametre tablosu
        tk.Label(left, text="Parametreler", bg=BASE, fg=LAVENDER,
                 font=FONT_LG).pack(anchor="w", padx=14, pady=(4, 4))

        tree_frame = tk.Frame(left, bg=BASE)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self._tree = ttk.Treeview(tree_frame, columns=("num", "name"),
                                   show="headings", selectmode="browse")
        self._tree.heading("num", text="#")
        self._tree.heading("name", text="Parametre İsmi")
        self._tree.column("num", width=40, anchor="center")
        self._tree.column("name", width=280)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree.tag_configure("bad", foreground=RED)
        self._tree.tag_configure("ok", foreground=GREEN)

    # ── Sağ panel: analiz sonuçları ──
    def _build_right_panel(self, parent):
        right = tk.Frame(parent, bg=BASE)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        header = tk.Frame(right, bg=BASE)
        header.pack(fill=tk.X, padx=14, pady=(12, 4))

        tk.Label(header, text="Analiz Sonuçları", bg=BASE, fg=LAVENDER,
                 font=FONT_LG).pack(side=tk.LEFT)

        self._result_count_var = tk.StringVar(value="")
        tk.Label(header, textvariable=self._result_count_var, bg=BASE, fg=OVERLAY0,
                 font=FONT_SM).pack(side=tk.RIGHT)

        self._result_text = scrolledtext.ScrolledText(
            right, bg=SURFACE0, fg=TEXT, insertbackground=TEXT,
            font=FONT_MD, wrap=tk.WORD, relief=tk.FLAT, bd=8,
            state=tk.DISABLED,
        )
        self._result_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 12))

        self._result_text.tag_configure("header", foreground=LAVENDER, font=FONT_LG)
        self._result_text.tag_configure("good", foreground=GREEN)
        self._result_text.tag_configure("bad", foreground=RED)
        self._result_text.tag_configure("warn", foreground=YELLOW)
        self._result_text.tag_configure("dim", foreground=OVERLAY0)
        self._result_text.tag_configure("info", foreground=PEACH)

        self._write_result("JSON dosyası yükleyin ve 'Kontrol Et' butonuna tıklayın.\n", "dim")

    # ── Sonuç alanına yazma ──
    def _write_result(self, text, tag=""):
        self._result_text.configure(state=tk.NORMAL)
        self._result_text.insert(tk.END, text, tag)
        self._result_text.see(tk.END)
        self._result_text.configure(state=tk.DISABLED)

    def _clear_results(self):
        self._result_text.configure(state=tk.NORMAL)
        self._result_text.delete("1.0", tk.END)
        self._result_text.configure(state=tk.DISABLED)

    # ── JSON yükleme ──
    def _load_json(self):
        path = filedialog.askopenfilename(
            title="3DEXPERIENCE Parametre JSON Seç",
            filetypes=[("JSON", "*.json"), ("Tümü", "*.*")],
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self._file_var.set(f"Hata: {e}")
            return

        raw_params = data.get("Parameters", [])
        if not raw_params:
            self._file_var.set("Hata: 'Parameters' anahtarı bulunamadı")
            return

        # Hem string listesi hem dict listesi destekle
        if isinstance(raw_params[0], str):
            params = raw_params
        else:
            params = [p["name"] for p in raw_params]

        data["_names"] = params
        self._loaded_params = data
        fname = Path(path).name
        part = data.get("PartNumber", "?")
        product = data.get("Product", "?")

        self._file_var.set(f"✓  {fname}  •  {part} / {product}  •  {len(params)} parametre")

        for row in self._tree.get_children():
            self._tree.delete(row)

        for i, name in enumerate(params):
            self._tree.insert("", tk.END, values=(i + 1, name))

        self._clear_results()
        self._write_result(f"Yüklendi: {fname}\n", "info")
        self._write_result(f"Parça: {part} / {product}\n", "dim")
        self._write_result(f"{len(params)} parametre bulundu.\n\n", "dim")
        self._write_result("'Kontrol Et' ile analizi başlatın.\n", "dim")

    # ── Parametre kontrol ──
    def _check_params(self):
        if not self._loaded_params:
            self._clear_results()
            self._write_result("Önce bir JSON dosyası yükleyin.\n", "warn")
            return

        if self._checking:
            return

        self._checking = True
        params = self._loaded_params["_names"]

        self._clear_results()
        self._write_result("Analiz Sonuçları\n\n", "header")
        self._write_result(f"{len(params)} parametre analiz ediliyor...\n\n", "dim")
        self._status_var.set("● LLM analiz ediyor...")
        self._status_label.config(fg=PEACH)

        threading.Thread(
            target=self._llm_classify_thread, args=(params,), daemon=True
        ).start()

    def _llm_classify_thread(self, params):
        try:
            llm = self._ensure_llm()

            lines = []
            for i, name in enumerate(params):
                expanded = name.replace("_", " ")
                if expanded != name:
                    lines.append(f"{i+1}. {name} (words: {expanded})")
                else:
                    lines.append(f"{i+1}. {name}")
            names_block = "\n".join(lines)

            prompt = (
                "<|im_start|>system\n"
                "You check CATIA parameter names. For each name:\n"
                "- Treat underscores as spaces and check each word individually.\n"
                "- If ANY word is not a real English word → BAD non-English.\n"
                "- If the name is random characters → BAD meaningless.\n"
                "- If the name is a lazy/temp name (tmp, a1, foo) → BAD unprofessional.\n"
                "- If the name contains backslash \\ → OK (CATIA reference).\n"
                "- If all words are real English engineering terms → OK.\n"
                "- If unsure whether a word is English → BAD.\n\n"
                "One line per name: N. Name → OK or BAD reason\n\n"
                "Example:\n"
                "1. Length → OK\n"
                "2. kalinlik_deger (words: kalinlik deger) → BAD non-English\n"
                "3. HoleDiameter → OK\n"
                "4. asdf1234 → BAD meaningless\n"
                "5. WallThickness → OK\n"
                "6. Copy of Pad.1\\Length → OK\n"
                "7. xxx → BAD meaningless\n"
                "8. vida_cap (words: vida cap) → BAD non-English\n"
                "9. BoltPitch → OK\n"
                "10. tmp → BAD unprofessional\n"
                "11. Pad.2\\FirstLimit\\Length → OK\n"
                "12. a1 → BAD unprofessional\n"
                "<|im_end|>\n"
                f"<|im_start|>user\n{names_block}<|im_end|>\n"
                "<|im_start|>assistant\n"
            )

            output = llm(
                prompt,
                max_tokens=1024,
                temperature=0.1,
                stop=["<|im_end|>", "<|im_start|>"],
            )
            reply = output["choices"][0]["text"].strip()

            bad_list = {}
            for line in reply.split("\n"):
                line = line.strip()
                if not line:
                    continue
                m = re.match(r"(\d+)\.\s*(.+?)\s*→\s*BAD\s*(.*)", line, re.IGNORECASE)
                if m:
                    idx = int(m.group(1)) - 1
                    reason = m.group(3).strip() or "sorunlu"
                    if 0 <= idx < len(params):
                        bad_list[params[idx]] = (idx + 1, reason)

            self.after(0, self._on_classify_done, params, bad_list)
        except Exception as e:
            self.after(0, self._on_check_error, str(e))

    def _on_classify_done(self, params, bad_list):
        self._checking = False
        self._status_var.set("● Kontrol tamamlandı")
        self._status_label.config(fg=GREEN)

        self._clear_results()
        self._write_result("Analiz Sonuçları\n\n", "header")

        if not bad_list:
            self._write_result("  ✓  Tüm parametre isimleri uygun!\n", "good")
            self._result_count_var.set(f"0 sorunlu / {len(params)} toplam")
        else:
            self._write_result(f"Sorunlu parametreler ({len(bad_list)}):\n\n", "warn")
            for name, (num, reason) in bad_list.items():
                self._write_result(f"  ✗  [{num}] {name}  →  {reason}\n", "bad")

            good_count = len(params) - len(bad_list)
            self._write_result(f"\n{'─' * 40}\n", "dim")
            self._write_result(f"Toplam: {len(bad_list)} sorunlu / {len(params)} parametre\n", "warn")
            self._write_result(f"{good_count} parametre uygun.\n", "good")
            self._result_count_var.set(f"{len(bad_list)} sorunlu / {len(params)} toplam")

        bad_names = set(bad_list.keys())
        for item in self._tree.get_children():
            tree_name = self._tree.item(item, "values")[1]
            self._tree.item(item, tags=("bad" if tree_name in bad_names else "ok",))

    def _on_check_error(self, error_msg):
        self._checking = False
        self._status_var.set("● Hata")
        self._status_label.config(fg=RED)
        self._write_result(f"\nHata: {error_msg}\n", "bad")

    # ── LLM ──
    def _ensure_llm(self):
        if self._llm is not None:
            return self._llm

        self.after(0, lambda: self._status_var.set("● Model yükleniyor..."))
        self.after(0, lambda: self._status_label.config(fg=PEACH))

        model_path = None
        default = MODEL_DIR / DEFAULT_MODEL
        if default.exists():
            model_path = default
        else:
            gguf_files = sorted(MODEL_DIR.glob("*.gguf"))
            if gguf_files:
                model_path = gguf_files[0]

        if not model_path:
            raise FileNotFoundError("models/ klasöründe .gguf dosyası bulunamadı.")

        self._llm = Llama(
            model_path=str(model_path),
            n_ctx=4096,
            n_batch=512,
            n_threads=os.cpu_count() or 4,
            verbose=False,
        )
        return self._llm


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
