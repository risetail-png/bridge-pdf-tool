#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bridge_pdf_link_app.py
======================
橋梁定期点検PDF ナビゲーションボタン追加ツール（GUIアプリ版）

起動方法:
    python bridge_pdf_link_app.py

必要ライブラリ:
    pip install pikepdf pypdf pdf2image pillow pytesseract tkinterdnd2
    ※ tkinterdnd2 はドラッグ＆ドロップ用（任意。なくても動作します）
"""

import io
import os
import queue
import re
import sys
import threading
import tkinter as tk
from collections import defaultdict
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

# ── オプション依存 ────────────────────────────────────────────────────────────
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# ── 必須ライブラリチェック ────────────────────────────────────────────────────
MISSING = []
try:
    import pikepdf
    from pikepdf import Array, Dictionary, Name, Stream
except ImportError:
    MISSING.append("pikepdf")

try:
    import pypdf
except ImportError:
    MISSING.append("pypdf")

try:
    from pdf2image import convert_from_path
except ImportError:
    MISSING.append("pdf2image")

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    MISSING.append("pillow")

try:
    import pytesseract
except ImportError:
    MISSING.append("pytesseract")


# ═══════════════════════════════════════════════════════════════════════════════
#  PDF処理ロジック（bridge_pdf_link.py と同等）
# ═══════════════════════════════════════════════════════════════════════════════

BTN_Y1, BTN_Y2 = 8.0, 46.0
BTN_H  = BTN_Y2 - BTN_Y1
BTN_GAP = 5.0
IMG_SCALE = 3

COLOR_FORWARD         = (46,  97, 184)
COLOR_OUTLINE_FORWARD = (20,  55, 130)
COLOR_BACK            = (34, 139,  69)
COLOR_OUTLINE_BACK    = (20,  90,  45)

KEYWORD_DIAGRAM = "データ記録様式(その９)"
KEYWORD_PHOTO   = "データ記録様式(その１０)"
RE_PHOTO_NUM    = re.compile(r'写真番号\s*(\d+)')


def find_japanese_font():
    candidates = [
        r"C:\Windows\Fonts\msgothic.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\yugothm.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Osaka.ttf",
        "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def classify_pages(pdf_path):
    reader = pypdf.PdfReader(pdf_path)
    diag, photo = [], []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if KEYWORD_DIAGRAM in text:
            diag.append(i)
        elif KEYWORD_PHOTO in text:
            photo.append(i)
    return diag, photo


def get_photo_nums_on_page(pdf_path, page_idx, dpi, use_ocr=False):
    reader = pypdf.PdfReader(pdf_path)
    text = reader.pages[page_idx].extract_text() or ""
    nums = [int(m.group(1)) for m in RE_PHOTO_NUM.finditer(text)]
    if not nums and use_ocr:
        imgs = convert_from_path(pdf_path, dpi=dpi,
                                 first_page=page_idx + 1, last_page=page_idx + 1)
        if imgs:
            t = pytesseract.image_to_string(imgs[0], lang='jpn', config='--psm 11')
            nums = [int(m.group(1)) for m in RE_PHOTO_NUM.finditer(t)]
    return sorted(set(nums))


def ocr_diagram_page(pdf_path, page_idx, dpi):
    imgs = convert_from_path(pdf_path, dpi=dpi,
                             first_page=page_idx + 1, last_page=page_idx + 1)
    if not imgs:
        return []
    t = pytesseract.image_to_string(imgs[0], lang='jpn', config='--psm 11')
    return sorted(set(int(m.group(1)) for m in RE_PHOTO_NUM.finditer(t)))


def get_page_size(pdf, page_idx):
    mb = pdf.pages[page_idx]['/MediaBox']
    return float(mb[2]), float(mb[3])


def render_button_jpeg(btn_list, total_w_pt, btn_h_pt,
                       fill_color, outline_color, font_path):
    img_w = int(total_w_pt * IMG_SCALE)
    img_h = int(btn_h_pt  * IMG_SCALE)
    img   = Image.new('RGB', (img_w, img_h), (255, 255, 255))
    draw  = ImageDraw.Draw(img)
    n        = len(btn_list)
    gap_px   = int(BTN_GAP * IMG_SCALE)
    btn_w_px = (img_w - gap_px * (n + 1)) // n
    fsize    = int(9 * IMG_SCALE)
    try:
        fnt = ImageFont.truetype(font_path, fsize)
    except Exception:
        fnt = ImageFont.load_default()
    for i, (label, _) in enumerate(btn_list):
        bx = gap_px + i * (btn_w_px + gap_px)
        by, bh = int(2 * IMG_SCALE), img_h - int(4 * IMG_SCALE)
        draw.rounded_rectangle([bx, by, bx + btn_w_px, by + bh],
                               radius=int(4 * IMG_SCALE),
                               fill=fill_color, outline=outline_color, width=2)
        bb = draw.textbbox((0, 0), label, font=fnt)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        draw.text((bx + (btn_w_px - tw) // 2, by + (bh - th) // 2),
                  label, fill=(255, 255, 255), font=fnt)
    buf = io.BytesIO()
    img.save(buf, format='JPEG', quality=92)
    return buf.getvalue(), img_w, img_h


def add_buttons_to_page(pdf, page_idx, btn_list, page_w, page_h,
                        fill_color, outline_color, font_path, xobj_prefix):
    page      = pdf.pages[page_idx]
    margin_l  = 64.0
    margin_r  = page_w - 48.0
    btn_total = margin_r - margin_l

    jpeg_bytes, img_w, img_h = render_button_jpeg(
        btn_list, btn_total, BTN_H, fill_color, outline_color, font_path)

    xobj = Stream(pdf, jpeg_bytes)
    xobj['/Type']             = Name('/XObject')
    xobj['/Subtype']          = Name('/Image')
    xobj['/Width']            = img_w
    xobj['/Height']           = img_h
    xobj['/ColorSpace']       = Name('/DeviceRGB')
    xobj['/BitsPerComponent'] = 8
    xobj['/Filter']           = Name('/DCTDecode')
    xobj_ref = pdf.make_indirect(xobj)

    if '/XObject' not in page['/Resources']:
        page['/Resources']['/XObject'] = pikepdf.Dictionary()
    xname = f'/{xobj_prefix}{page_idx}'
    page['/Resources']['/XObject'][xname] = xobj_ref

    content = (f"q\n{btn_total:.4f} 0 0 {BTN_H:.4f} "
               f"{margin_l:.4f} {BTN_Y1:.4f} cm\n{xname} Do\nQ\n").encode('latin-1')
    cstream = Stream(pdf, content)

    existing = page['/Contents']
    page['/Contents'] = pikepdf.Array(
        (list(existing) if isinstance(existing, pikepdf.Array) else [existing])
        + [pdf.make_indirect(cstream)]
    )

    n        = len(btn_list)
    btn_w_pt = (btn_total - BTN_GAP * (n + 1)) / n
    annots   = list(page.get('/Annots', pikepdf.Array()))
    for i, (_, target_idx) in enumerate(btn_list):
        bx1 = margin_l + BTN_GAP + i * (btn_w_pt + BTN_GAP)
        bx2 = bx1 + btn_w_pt
        dest = pikepdf.Array([pdf.pages[target_idx].obj, Name('/XYZ'),
                              pikepdf.Real(0), pikepdf.Real(page_h), pikepdf.Real(0)])
        annots.append(pdf.make_indirect(Dictionary(
            Type=Name('/Annot'), Subtype=Name('/Link'),
            Rect=Array([pikepdf.Real(bx1), pikepdf.Real(BTN_Y1),
                        pikepdf.Real(bx2), pikepdf.Real(BTN_Y2)]),
            Border=Array([pikepdf.Real(0)] * 3),
            Dest=dest, H=Name('/I'),
        )))
    page['/Annots'] = pikepdf.Array(annots)


def run_process(input_path, output_path, dpi, log_cb, done_cb):
    """バックグラウンドスレッドで実行されるメイン処理"""
    try:
        font_path = find_japanese_font()
        if not font_path:
            raise RuntimeError(
                "日本語フォントが見つかりません。\n"
                "MS ゴシック / ヒラギノ / IPAフォント等をインストールしてください。")

        log_cb(f"フォント: {Path(font_path).name}")
        log_cb("ページ分類中...")
        diag_pages, photo_pages = classify_pages(input_path)

        if not diag_pages:
            raise RuntimeError(f"損傷図ページ（{KEYWORD_DIAGRAM}）が見つかりません。")
        if not photo_pages:
            raise RuntimeError(f"損傷写真ページ（{KEYWORD_PHOTO}）が見つかりません。")

        log_cb(f"損傷図ページ    : {[p+1 for p in diag_pages]}")
        log_cb(f"損傷写真ページ  : {[p+1 for p in photo_pages]}")

        log_cb("損傷写真ページの写真番号を取得中...")
        photo_num_to_page = {}
        for pidx in photo_pages:
            nums = get_photo_nums_on_page(input_path, pidx, dpi, use_ocr=True)
            for n in nums:
                photo_num_to_page[n] = pidx
            log_cb(f"  p.{pidx+1}: 写真番号 {nums}")

        log_cb("損傷図ページをOCR中（日本語）...")
        diagram_to_photos = {}
        for didx in diag_pages:
            nums = ocr_diagram_page(input_path, didx, dpi)
            diagram_to_photos[didx] = nums
            log_cb(f"  p.{didx+1}: 写真番号 {nums}")

        # 対応関係を構築
        diag_to_photo_pages = defaultdict(list)
        for didx, nums in diagram_to_photos.items():
            seen = set()
            for num in sorted(nums):
                pp = photo_num_to_page.get(num)
                if pp is not None and pp not in seen:
                    diag_to_photo_pages[didx].append(pp)
                    seen.add(pp)

        photo_to_diag_pages = defaultdict(list)
        for didx, plist in diag_to_photo_pages.items():
            for pp in plist:
                if didx not in photo_to_diag_pages[pp]:
                    photo_to_diag_pages[pp].append(didx)

        log_cb("ボタンを追加中...")
        reader = pypdf.PdfReader(input_path)
        pdf    = pikepdf.open(input_path, allow_overwriting_input=True)

        # 損傷図ページに青ボタン
        for didx, plist in diag_to_photo_pages.items():
            if not plist:
                continue
            pw, ph = get_page_size(pdf, didx)
            btn_list = []
            for pp in plist:
                nums_here = sorted(
                    n for n, pg in photo_num_to_page.items()
                    if pg == pp and n in diagram_to_photos.get(didx, []))
                label = (f"写真番号 {min(nums_here)}〜{max(nums_here)} を見る"
                         if nums_here else f"損傷写真 p.{pp+1} を見る")
                btn_list.append((label, pp))
            log_cb(f"  損傷図 p.{didx+1} → {[b[0] for b in btn_list]}")
            add_buttons_to_page(pdf, didx, btn_list, pw, ph,
                                COLOR_FORWARD, COLOR_OUTLINE_FORWARD,
                                font_path, 'FwdBtn')

        # 損傷写真ページに緑ボタン
        for pp, dlist in photo_to_diag_pages.items():
            if not dlist:
                continue
            pw, ph = get_page_size(pdf, pp)
            btn_list = []
            for didx in dlist:
                text = reader.pages[didx].extract_text() or ""
                m    = re.search(
                    r'(桁下面|橋面|A\d+橋台|橋台|橋脚|床版|主桁|支承|伸縮|高欄|防護柵)',
                    text)
                title = m.group(1) if m else f"p.{didx+1}"
                btn_list.append((f"← 損傷図（{title}）を見る", didx))
            log_cb(f"  損傷写真 p.{pp+1} → {[b[0] for b in btn_list]}")
            add_buttons_to_page(pdf, pp, btn_list, pw, ph,
                                COLOR_BACK, COLOR_OUTLINE_BACK,
                                font_path, 'BackBtn')

        pdf.save(output_path)
        in_mb  = os.path.getsize(input_path)  / 1024 / 1024
        out_mb = os.path.getsize(output_path) / 1024 / 1024
        log_cb(f"保存完了: {output_path}")
        log_cb(f"ファイルサイズ: {in_mb:.1f} MB → {out_mb:.1f} MB")
        done_cb(True, output_path)

    except Exception as e:
        log_cb(f"エラー: {e}")
        done_cb(False, str(e))


# ═══════════════════════════════════════════════════════════════════════════════
#  GUI
# ═══════════════════════════════════════════════════════════════════════════════

class App(tk.Tk if not HAS_DND else TkinterDnD.Tk):

    # ── パレット ──────────────────────────────────────────────────────────────
    BG       = "#1a1f2e"
    PANEL    = "#242938"
    BORDER   = "#2e3548"
    ACCENT   = "#4a7fe8"
    ACCENT2  = "#22a06b"
    TEXT     = "#e8ecf4"
    SUBTEXT  = "#8892aa"
    SUCCESS  = "#22a06b"
    ERROR    = "#e8516a"
    WARNING  = "#f0a040"
    BTN_HOV  = "#5a8ff8"

    def __init__(self):
        super().__init__()
        self.title("橋梁点検PDF リンク追加ツール")
        self.geometry("780x640")
        self.minsize(680, 540)
        self.configure(bg=self.BG)
        self.resizable(True, True)

        self._input_path  = tk.StringVar()
        self._output_path = tk.StringVar()
        self._dpi         = tk.IntVar(value=150)
        self._status      = tk.StringVar(value="PDFファイルを選択してください")
        self._log_queue   = queue.Queue()
        self._processing  = False

        self._build_ui()
        self._poll_log()

        if MISSING:
            self._show_missing()

    # ── UI構築 ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # ヘッダー
        hdr = tk.Frame(self, bg=self.BG)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 0))
        tk.Label(hdr, text="橋梁点検PDF", font=("Yu Gothic UI", 10),
                 fg=self.SUBTEXT, bg=self.BG).pack(anchor="w")
        tk.Label(hdr, text="リンク追加ツール",
                 font=("Yu Gothic UI Bold", 20, "bold"),
                 fg=self.TEXT, bg=self.BG).pack(anchor="w")
        tk.Label(hdr,
                 text="損傷図（その９）と損傷写真（その１０）の間にナビゲーションボタンを自動追加します",
                 font=("Yu Gothic UI", 9), fg=self.SUBTEXT, bg=self.BG).pack(anchor="w", pady=(2, 0))

        sep = tk.Frame(self, bg=self.BORDER, height=1)
        sep.grid(row=0, column=0, sticky="ew", padx=24, pady=(60, 0))

        # メインパネル
        main = tk.Frame(self, bg=self.BG)
        main.grid(row=1, column=0, sticky="nsew", padx=24, pady=16)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        # ── ファイル選択エリア ──
        file_frame = tk.Frame(main, bg=self.PANEL,
                              highlightbackground=self.BORDER,
                              highlightthickness=1)
        file_frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        file_frame.columnconfigure(1, weight=1)

        # ドロップゾーン
        self._drop_zone = tk.Label(
            file_frame,
            text="📂  ここにPDFをドラッグ＆ドロップ\nまたはクリックして選択",
            font=("Yu Gothic UI", 10), fg=self.SUBTEXT, bg=self.PANEL,
            cursor="hand2", pady=20
        )
        self._drop_zone.grid(row=0, column=0, columnspan=3,
                             sticky="ew", padx=16, pady=12)
        self._drop_zone.bind("<Button-1>", lambda e: self._browse_input())
        self._drop_zone.bind("<Enter>",
            lambda e: self._drop_zone.configure(fg=self.ACCENT))
        self._drop_zone.bind("<Leave>",
            lambda e: self._drop_zone.configure(fg=self.SUBTEXT))

        if HAS_DND:
            self._drop_zone.drop_target_register(DND_FILES)
            self._drop_zone.dnd_bind('<<Drop>>', self._on_drop)

        # 入力パス
        self._mk_row(file_frame, "入力PDF", self._input_path,
                     lambda: self._browse_input(), row=1)
        # 出力パス
        self._mk_row(file_frame, "出力PDF", self._output_path,
                     lambda: self._browse_output(), row=2)

        # ── 設定エリア ──
        cfg_frame = tk.Frame(main, bg=self.PANEL,
                             highlightbackground=self.BORDER,
                             highlightthickness=1)
        cfg_frame.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        cfg_frame.grid_forget()  # 初期は非表示

        # ── 設定折りたたみ ──
        cfg_outer = tk.Frame(main, bg=self.BG)
        cfg_outer.grid(row=0, column=0, sticky="ew", pady=(140, 0))
        self._cfg_visible = False
        self._cfg_toggle = tk.Label(
            cfg_outer, text="▶ 詳細設定",
            font=("Yu Gothic UI", 9), fg=self.SUBTEXT, bg=self.BG,
            cursor="hand2"
        )
        self._cfg_toggle.pack(anchor="w", pady=(0, 4))
        self._cfg_toggle.bind("<Button-1>", self._toggle_cfg)

        self._cfg_panel = tk.Frame(cfg_outer, bg=self.PANEL,
                                   highlightbackground=self.BORDER,
                                   highlightthickness=1)

        tk.Label(self._cfg_panel, text="OCR解像度 (DPI)",
                 font=("Yu Gothic UI", 9), fg=self.SUBTEXT, bg=self.PANEL
                 ).grid(row=0, column=0, sticky="w", padx=16, pady=10)
        dpi_frame = tk.Frame(self._cfg_panel, bg=self.PANEL)
        dpi_frame.grid(row=0, column=1, sticky="w", padx=(0, 16), pady=10)
        for val, label in [(100, "低速・粗"), (150, "標準"), (200, "高精度"), (250, "最高精度（遅）")]:
            rb = tk.Radiobutton(
                dpi_frame, text=f"{val}  {label}",
                variable=self._dpi, value=val,
                font=("Yu Gothic UI", 9), fg=self.TEXT, bg=self.PANEL,
                selectcolor=self.PANEL, activebackground=self.PANEL,
                activeforeground=self.ACCENT
            )
            rb.pack(side="left", padx=(0, 12))

        tk.Label(self._cfg_panel,
                 text="※ 解像度が高いほど写真番号の認識精度が上がりますが処理時間が増加します",
                 font=("Yu Gothic UI", 8), fg=self.SUBTEXT, bg=self.PANEL
                 ).grid(row=1, column=0, columnspan=2, sticky="w", padx=16, pady=(0, 10))

        # ── ログエリア ──
        log_frame = tk.Frame(main, bg=self.PANEL,
                             highlightbackground=self.BORDER,
                             highlightthickness=1)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)

        tk.Label(log_frame, text="処理ログ",
                 font=("Yu Gothic UI", 9), fg=self.SUBTEXT, bg=self.PANEL
                 ).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 2))

        self._log = tk.Text(
            log_frame, bg="#131720", fg=self.SUBTEXT,
            font=("Consolas", 9), relief="flat", bd=0,
            state="disabled", wrap="word",
            insertbackground=self.TEXT,
            selectbackground=self.ACCENT,
        )
        self._log.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        scrollbar = ttk.Scrollbar(log_frame, command=self._log.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=(0, 8), padx=(0, 4))
        self._log['yscrollcommand'] = scrollbar.set

        # タグ設定
        self._log.tag_configure("info",    foreground=self.SUBTEXT)
        self._log.tag_configure("success", foreground=self.SUCCESS)
        self._log.tag_configure("error",   foreground=self.ERROR)
        self._log.tag_configure("warn",    foreground=self.WARNING)
        self._log.tag_configure("accent",  foreground=self.ACCENT)

        # ── フッター（実行ボタン・ステータス） ──
        footer = tk.Frame(self, bg=self.BG)
        footer.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 16))
        footer.columnconfigure(0, weight=1)

        tk.Label(footer, textvariable=self._status,
                 font=("Yu Gothic UI", 9), fg=self.SUBTEXT, bg=self.BG,
                 anchor="w").grid(row=0, column=0, sticky="w")

        self._progress = ttk.Progressbar(footer, mode='indeterminate', length=200)
        self._progress.grid(row=0, column=1, padx=(12, 12))

        self._run_btn = tk.Button(
            footer, text="▶  処理開始",
            font=("Yu Gothic UI Bold", 10, "bold"),
            fg="white", bg=self.ACCENT,
            activeforeground="white", activebackground=self.BTN_HOV,
            relief="flat", bd=0, padx=20, pady=8,
            cursor="hand2",
            command=self._start
        )
        self._run_btn.grid(row=0, column=2)
        self._run_btn.bind("<Enter>",
            lambda e: self._run_btn.configure(bg=self.BTN_HOV))
        self._run_btn.bind("<Leave>",
            lambda e: self._run_btn.configure(bg=self.ACCENT))

    def _mk_row(self, parent, label, var, browse_cmd, row):
        tk.Label(parent, text=label, font=("Yu Gothic UI", 9),
                 fg=self.SUBTEXT, bg=self.PANEL, width=8, anchor="e"
                 ).grid(row=row, column=0, sticky="e", padx=(16, 6), pady=4)

        entry = tk.Entry(parent, textvariable=var,
                         font=("Yu Gothic UI", 9),
                         bg="#131720", fg=self.TEXT,
                         insertbackground=self.TEXT,
                         relief="flat", bd=4,
                         disabledbackground="#131720")
        entry.grid(row=row, column=1, sticky="ew", padx=(0, 6), pady=4)
        parent.columnconfigure(1, weight=1)

        btn = tk.Button(parent, text="参照…",
                        font=("Yu Gothic UI", 9),
                        fg=self.TEXT, bg=self.BORDER,
                        activeforeground=self.TEXT, activebackground=self.ACCENT,
                        relief="flat", bd=0, padx=10, pady=3,
                        cursor="hand2", command=browse_cmd)
        btn.grid(row=row, column=2, padx=(0, 16), pady=4)

    # ── イベント ──────────────────────────────────────────────────────────────
    def _toggle_cfg(self, _=None):
        self._cfg_visible = not self._cfg_visible
        if self._cfg_visible:
            self._cfg_panel.pack(fill="x")
            self._cfg_toggle.configure(text="▼ 詳細設定", fg=self.ACCENT)
        else:
            self._cfg_panel.pack_forget()
            self._cfg_toggle.configure(text="▶ 詳細設定", fg=self.SUBTEXT)

    def _on_drop(self, event):
        raw = event.data
        path = raw.strip().strip('{}').strip('"')
        if path.lower().endswith('.pdf'):
            self._set_input(path)
        else:
            self._log_msg("PDFファイルをドロップしてください", "warn")

    def _browse_input(self):
        p = filedialog.askopenfilename(
            title="入力PDFを選択",
            filetypes=[("PDFファイル", "*.pdf"), ("すべてのファイル", "*.*")]
        )
        if p:
            self._set_input(p)

    def _set_input(self, path):
        self._input_path.set(path)
        stem = Path(path).stem
        out  = str(Path(path).parent / f"{stem}_linked.pdf")
        self._output_path.set(out)
        self._drop_zone.configure(
            text=f"📄  {Path(path).name}",
            fg=self.ACCENT
        )
        self._status.set(f"ファイル選択済: {Path(path).name}")
        self._log_msg(f"ファイル選択: {path}", "accent")

    def _browse_output(self):
        p = filedialog.asksaveasfilename(
            title="出力ファイル名を指定",
            defaultextension=".pdf",
            filetypes=[("PDFファイル", "*.pdf")]
        )
        if p:
            self._output_path.set(p)

    # ── 処理実行 ──────────────────────────────────────────────────────────────
    def _start(self):
        if MISSING:
            self._show_missing()
            return
        if self._processing:
            return

        inp = self._input_path.get().strip()
        out = self._output_path.get().strip()

        if not inp:
            messagebox.showwarning("ファイル未選択", "入力PDFを選択してください。")
            return
        if not os.path.exists(inp):
            messagebox.showerror("エラー", f"ファイルが見つかりません:\n{inp}")
            return
        if not out:
            messagebox.showwarning("出力先未設定", "出力ファイルのパスを入力してください。")
            return

        self._processing = True
        self._run_btn.configure(state="disabled", text="処理中…", bg="#333d55")
        self._progress.start(12)
        self._status.set("処理中…　しばらくお待ちください")
        self._clear_log()
        self._log_msg("=" * 48, "info")
        self._log_msg("処理開始", "accent")
        self._log_msg(f"入力: {inp}", "info")
        self._log_msg(f"出力: {out}", "info")
        self._log_msg(f"OCR DPI: {self._dpi.get()}", "info")
        self._log_msg("=" * 48, "info")

        thread = threading.Thread(
            target=run_process,
            args=(inp, out, self._dpi.get(),
                  lambda msg: self._log_queue.put(("info", msg)),
                  self._on_done),
            daemon=True
        )
        thread.start()

    def _on_done(self, success, detail):
        self._log_queue.put(("done", (success, detail)))

    # ── ログポーリング ────────────────────────────────────────────────────────
    def _poll_log(self):
        while not self._log_queue.empty():
            kind, msg = self._log_queue.get_nowait()
            if kind == "info":
                tag = ("success" if "完了" in msg or "保存" in msg
                       else "error" if "エラー" in msg
                       else "warn"  if "警告" in msg
                       else "info")
                self._log_msg(msg, tag)
            elif kind == "done":
                success, detail = msg
                self._processing = False
                self._progress.stop()
                if success:
                    self._run_btn.configure(state="normal",
                                            text="▶  処理開始", bg=self.ACCENT)
                    self._status.set("✓  処理完了！")
                    self._log_msg("=" * 48, "success")
                    self._log_msg("✓  正常に完了しました", "success")
                    self._log_msg("=" * 48, "success")
                    messagebox.showinfo(
                        "完了",
                        f"処理が完了しました。\n\n出力ファイル:\n{detail}"
                    )
                else:
                    self._run_btn.configure(state="normal",
                                            text="▶  処理開始", bg=self.ACCENT)
                    self._status.set("✗  エラーが発生しました")
                    self._log_msg("=" * 48, "error")
                    self._log_msg(f"✗  エラー: {detail}", "error")
                    self._log_msg("=" * 48, "error")
                    messagebox.showerror("エラー", f"処理中にエラーが発生しました:\n\n{detail}")
        self.after(100, self._poll_log)

    # ── ログ操作 ──────────────────────────────────────────────────────────────
    def _log_msg(self, msg, tag="info"):
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n", tag)
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    # ── ライブラリ不足の警告 ──────────────────────────────────────────────────
    def _show_missing(self):
        libs = "\n".join(f"  pip install {m}" for m in MISSING)
        messagebox.showerror(
            "ライブラリ不足",
            f"以下のライブラリをインストールしてください:\n\n{libs}\n\n"
            "インストール後、アプリを再起動してください。"
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  エントリポイント
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = App()
    app.mainloop()
