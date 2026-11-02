#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Develped by: Sassiz-Root
thumbnail_extractor.py — View & recover Windows thumbcache images
Engine chain: realesrgan-ncnn-vulkan → waifu2x-ncnn-vulkan → classical pipeline
"""

import os, sys, io, shutil, subprocess, tempfile, threading, queue, traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

try:
    from dissect.thumbcache import ThumbcacheFile
    from PIL import Image, ImageTk, ImageEnhance, ImageFilter
except ImportError as e:
    print(f"ERROR: {e}\nRun: pip install dissect.thumbcache pillow")
    sys.exit(1)

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


# ============================================================
# ENGINE DETECTION
# ============================================================
REALESRGAN = shutil.which('realesrgan-ncnn-vulkan')
WAIFU2X = shutil.which('waifu2x-ncnn-vulkan')

ENGINE_STATE = {
    'realesrgan': 'untested',   # 'untested' | 'ok' | 'broken'
    'waifu2x':    'untested',
}


def _find_realesrgan_models():
    for p in [os.path.expanduser('~/realesrgan/models'),
              '/usr/share/realesrgan/models',
              '/opt/realesrgan/models']:
        if os.path.exists(os.path.join(p, 'realesrgan-x4plus.param')):
            return p
    return None


ESRGAN_MODEL_PATH = _find_realesrgan_models()


# ============================================================
# VALIDATION
# ============================================================
def _image_mean(img):
    if HAS_NUMPY:
        return float(np.asarray(img).mean())
    w, h = img.size
    total = 0; n = 0
    step = max(1, min(w, h) // 20)
    for x in range(0, w, step):
        for y in range(0, h, step):
            r, g, b = img.getpixel((x, y))[:3]
            total += r + g + b; n += 3
    return total / max(n, 1)


def _validate_ai_output(orig_bytes, ai_bytes, min_mean=10):
    """Reject black/invalid AI output. Compares to input to allow dark originals."""
    try:
        orig = Image.open(io.BytesIO(orig_bytes)).convert('RGB')
        out = Image.open(io.BytesIO(ai_bytes)).convert('RGB')
        om = _image_mean(orig)
        am = _image_mean(out)
        if am < min_mean and om > 25:
            return False  # input bright, output black → broken
        return True
    except Exception:
        return False


# ============================================================
# AI ENGINES
# ============================================================
def enhance_realesrgan(data, scale=2, quality=95, timeout=300):
    img = Image.open(io.BytesIO(data)).convert('RGB')
    orig_size = img.size
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, 'in.png')
        outp = os.path.join(tmp, 'out.png')
        img.save(inp, 'PNG')
        cmd = [REALESRGAN, '-i', inp, '-o', outp, '-s', str(scale),
               '-n', 'realesrgan-x4plus', '-f', 'png']
        if ESRGAN_MODEL_PATH:
            cmd += ['-m', ESRGAN_MODEL_PATH]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0 or not os.path.exists(outp):
            raise RuntimeError(f"exit={res.returncode}: {res.stderr[:200]}")
        if os.path.getsize(outp) < 1000:
            raise RuntimeError(f"output too small: {os.path.getsize(outp)}B")
        enh = Image.open(outp).convert('RGB')
        buf = io.BytesIO()
        enh.save(buf, 'JPEG', quality=quality, optimize=True, progressive=True)
        result = buf.getvalue()
        if not _validate_ai_output(data, result):
            raise RuntimeError("black/invalid output")
        return result, orig_size, enh.size, ['Real-ESRGAN']


def enhance_waifu2x(data, scale=2, quality=95, timeout=300):
    img = Image.open(io.BytesIO(data)).convert('RGB')
    orig_size = img.size
    models = []
    rg = _find_realesrgan_models()
    if rg:
        models.append(('realesrgan-x4plus', rg))
    models.extend([
        ('models-upconv_7_photo', None),
        ('models-cunet', None),
    ])
    last_err = None
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, 'in.png')
        img.save(inp, 'PNG')
        for model_name, model_dir in models:
            outp = os.path.join(tmp, f'out_{model_name.replace("/", "_")}.png')
            cmd = [WAIFU2X, '-i', inp, '-o', outp, '-s', str(scale),
                   '-n', model_name, '-f', 'png']
            if model_dir:
                cmd += ['-m', model_dir]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                if res.returncode != 0 or not os.path.exists(outp):
                    last_err = f"{model_name}: exit={res.returncode} {res.stderr[:120]}"
                    continue
                if os.path.getsize(outp) < 1000:
                    last_err = f"{model_name}: output too small"
                    continue
                enh = Image.open(outp).convert('RGB')
                buf = io.BytesIO()
                enh.save(buf, 'JPEG', quality=quality, optimize=True, progressive=True)
                result = buf.getvalue()
                if not _validate_ai_output(data, result):
                    last_err = f"{model_name}: black output"
                    continue
                return result, orig_size, enh.size, [f'waifu2x/{model_name}']
            except subprocess.TimeoutExpired:
                last_err = f"{model_name}: timeout"
                continue
            except Exception as e:
                last_err = f"{model_name}: {e}"
                continue
    raise RuntimeError(f"all models failed ({last_err})")


# ============================================================
# CLASSICAL PIPELINE
# ============================================================
def _denoise(img, strength):
    if strength == 0 or not (HAS_CV2 and HAS_NUMPY):
        return img
    arr = np.asarray(img)
    if strength == 1:
        arr = cv2.bilateralFilter(arr, d=5, sigmaColor=15, sigmaSpace=15)
    else:
        arr = cv2.bilateralFilter(arr, d=7, sigmaColor=25, sigmaSpace=20)
    return Image.fromarray(arr)


def _ibp_upscale(img, scale, iterations, cancel_flag=None):
    def chk():
        if cancel_flag and cancel_flag.is_set():
            raise InterruptedError("Cancelled")
    if HAS_CV2 and HAS_NUMPY:
        try:
            lr = np.asarray(img).astype(np.float32) / 255.0
            lr_h, lr_w = lr.shape[:2]
            hr_h, hr_w = int(lr_h * scale), int(lr_w * scale)
            hr = cv2.resize(lr, (hr_w, hr_h), interpolation=cv2.INTER_LANCZOS4)
            for _ in range(iterations):
                chk()
                sim = cv2.resize(hr, (lr_w, lr_h), interpolation=cv2.INTER_AREA)
                err = lr - sim
                err_hr = cv2.resize(err, (hr_w, hr_h), interpolation=cv2.INTER_CUBIC)
                hr = np.clip(hr + 0.5 * err_hr, 0, 1)
            return Image.fromarray((hr * 255).astype(np.uint8))
        except InterruptedError:
            raise
        except Exception:
            pass
    w, h = img.size
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def _sharpen(img, amount):
    if amount <= 0:
        return img
    if HAS_CV2 and HAS_NUMPY:
        arr = np.asarray(img).astype(np.float32)
        for radius, base_amt in [(1.0, 0.6), (2.5, 0.4), (5.0, 0.2)]:
            ksize = max(3, (int(radius * 6) | 1))
            blur = cv2.GaussianBlur(arr, (ksize, ksize), radius)
            arr = arr + (base_amt * amount) * (arr - blur)
        arr = np.clip(arr, 0, 255).astype(np.uint8)
        return Image.fromarray(arr)
    for r, p, t in [(1.0, int(130 * amount), 2),
                    (2.5, int(85 * amount), 3),
                    (5.0, int(55 * amount), 5)]:
        img = img.filter(ImageFilter.UnsharpMask(radius=r, percent=p, threshold=t))
    return img


def _clahe(img, clip):
    if clip <= 0 or not (HAS_CV2 and HAS_NUMPY):
        return img
    arr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2LAB)
    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
    arr[:, :, 0] = clahe.apply(arr[:, :, 0])
    arr = cv2.cvtColor(arr, cv2.COLOR_LAB2RGB)
    return Image.fromarray(arr)


def _detail_enhance(img, sigma_s):
    if sigma_s <= 0 or not (HAS_CV2 and HAS_NUMPY):
        return img
    arr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    arr = cv2.detailEnhance(arr, sigma_s=sigma_s, sigma_r=0.15)
    return Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))


def enhance_classical(data, params, progress_cb=None, cancel_flag=None):
    def log(m):
        if progress_cb:
            progress_cb(m)
    def chk():
        if cancel_flag and cancel_flag.is_set():
            raise InterruptedError("Cancelled")

    img = Image.open(io.BytesIO(data))
    orig_size = img.size
    if img.mode != 'RGB':
        img = img.convert('RGB')
    methods = []
    chk()

    if params['denoise'] > 0:
        log("      denoise")
        img = _denoise(img, params['denoise'])
        methods.append('bilateral')

    chk()
    w, h = img.size
    short = min(w, h)
    if short < params['target_short'] and short > 0:
        scale = min(params['target_short'] / short, params['max_scale'])
        log(f"      upscale {scale:.2f}x (IBP {params['ibp_iters']} iter)")
        img = _ibp_upscale(img, scale, params['ibp_iters'], cancel_flag)
        methods.append(f'IBP-{scale:.2f}x')

    chk()
    if params['sharpen'] > 0:
        log(f"      sharpen {params['sharpen']:.2f}")
        img = _sharpen(img, params['sharpen'])
        methods.append(f'USM({params["sharpen"]:.2f})')

    chk()
    if params['clahe'] > 0:
        log(f"      CLAHE {params['clahe']:.1f}")
        img = _clahe(img, params['clahe'])
        methods.append(f'CLAHE({params["clahe"]:.1f})')

    chk()
    if params['detail'] > 0:
        log(f"      detailEnhance {params['detail']:.0f}")
        img = _detail_enhance(img, params['detail'])
        methods.append(f'detail({params["detail"]:.0f})')

    chk()
    img = ImageEnhance.Color(img).enhance(params['color'])
    img = ImageEnhance.Contrast(img).enhance(params['contrast'])
    methods.append(f'color×{params["color"]:.2f},contrast×{params["contrast"]:.2f}')

    out = io.BytesIO()
    img.save(out, 'JPEG', quality=params['quality'], optimize=True, progressive=True)
    return out.getvalue(), orig_size, img.size, methods


# ============================================================
# AUTO (fallback chain)
# ============================================================
def enhance_auto(data, params, progress_cb=None, cancel_flag=None, use_ai=True):
    """
    Returns: (bytes, orig_size, new_size, methods, engine_name)
    Engine priority: realesrgan → waifu2x → classical
    """
    def log(m):
        if progress_cb:
            progress_cb(m)

    if use_ai:
        # 1) Real-ESRGAN
        if REALESRGAN and ENGINE_STATE['realesrgan'] != 'broken':
            log("      trying Real-ESRGAN...")
            try:
                r = enhance_realesrgan(data, scale=2, quality=params['quality'])
                ENGINE_STATE['realesrgan'] = 'ok'
                log("      ✓ Real-ESRGAN")
                return r[0], r[1], r[2], r[3], 'Real-ESRGAN'
            except Exception as e:
                ENGINE_STATE['realesrgan'] = 'broken'
                log(f"      ✗ Real-ESRGAN: {e}")
                log("        (disabled for rest of session)")

        # 2) waifu2x
        if WAIFU2X and ENGINE_STATE['waifu2x'] != 'broken':
            log("      trying waifu2x...")
            try:
                r = enhance_waifu2x(data, scale=2, quality=params['quality'])
                ENGINE_STATE['waifu2x'] = 'ok'
                log(f"      ✓ {r[3][0]}")
                return r[0], r[1], r[2], r[3], r[3][0]
            except Exception as e:
                ENGINE_STATE['waifu2x'] = 'broken'
                log(f"      ✗ waifu2x: {e}")
                log("        (disabled for rest of session)")

    # 3) Classical
    log("      using classical pipeline")
    r = enhance_classical(data, params, progress_cb, cancel_flag)
    return r[0], r[1], r[2], r[3], 'Classical'


# ============================================================
# GUI
# ============================================================
class ThumbcacheViewer:
    DEFAULTS = {
        'denoise': 1, 'target_short': 1920, 'max_scale': 3.0,
        'ibp_iters': 4, 'sharpen': 0.35, 'clahe': 1.5,
        'detail': 10, 'color': 1.02, 'contrast': 1.01, 'quality': 93,
    }

    def __init__(self, root):
        self.root = root
        self.root.title("Thumbcache Viewer & Recoverer")
        self.root.geometry("1280x900")
        self.entries = []
        self.thumbnails = []
        self.current_index = 0
        self.worker_thread = None
        self.cancel_flag = threading.Event()
        self.result_queue = queue.Queue()
        self.params = dict(self.DEFAULTS)
        self.use_ai = tk.BooleanVar(value=True)
        self._build_ui()
        self._poll_queue()
        self._log_capabilities()

    def _log_capabilities(self):
        self.log("=== Capabilities ===", 'info')
        if REALESRGAN:
            mpath = f" (models: {ESRGAN_MODEL_PATH})" if ESRGAN_MODEL_PATH else " (bundled models)"
            self.log(f"  Real-ESRGAN: {REALESRGAN}{mpath}", 'ok')
        else:
            self.log("  Real-ESRGAN: not installed", 'warn')
        if WAIFU2X:
            self.log(f"  waifu2x:    {WAIFU2X}", 'ok')
        else:
            self.log("  waifu2x:    not installed", 'warn')
        self.log(f"  numpy:      {'YES' if HAS_NUMPY else 'NO'}", 'ok' if HAS_NUMPY else 'err')
        self.log(f"  opencv:     {'YES' if HAS_CV2 else 'NO (install opencv-python for faster classical)'}",
                 'ok' if HAS_CV2 else 'warn')
        self.log("  Chain: Real-ESRGAN → waifu2x → Classical", 'info')
        self._update_engine_label()
        self.log("")

    def _update_engine_label(self):
        parts = []
        if REALESRGAN:
            st = ENGINE_STATE['realesrgan']
            parts.append(f"Real-ESRGAN:{'✓' if st == 'ok' else '?' if st == 'untested' else '✗'}")
        if WAIFU2X:
            st = ENGINE_STATE['waifu2x']
            parts.append(f"waifu2x:{'✓' if st == 'ok' else '?' if st == 'untested' else '✗'}")
        if not REALESRGAN and not WAIFU2X:
            parts.append("Classical only")
        self.engine_label.config(text="  |  ".join(parts))

    def _build_ui(self):
        # Toolbar
        toolbar = ttk.Frame(self.root, padding=5)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(toolbar, text="Open thumbcache", command=self.open_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Extract Selected", command=self.extract_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Extract All", command=self.extract_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Reset Controls", command=self.reset_params).pack(side=tk.LEFT, padx=2)
        self.cancel_btn = ttk.Button(toolbar, text="Cancel",
                                     command=self.cancel_extraction, state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT, padx=2)
        self.engine_label = ttk.Label(toolbar, text="", foreground="#0066cc",
                                       font=('TkDefaultFont', 9, 'bold'))
        self.engine_label.pack(side=tk.RIGHT, padx=10)

        # Main paned
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Gallery
        gf = ttk.LabelFrame(paned, text="Image Gallery", padding=5)
        paned.add(gf, weight=2)
        self.canvas = tk.Canvas(gf, bg="white")
        sb = ttk.Scrollbar(gf, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        self.gallery_inner = ttk.Frame(self.canvas)
        self.gallery_win = self.canvas.create_window((0, 0), window=self.gallery_inner, anchor="nw")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.gallery_inner.bind("<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",
            lambda e: self.canvas.itemconfig(self.gallery_win, width=e.width))

        # Preview
        pf = ttk.LabelFrame(paned, text="Preview", padding=5)
        paned.add(pf, weight=3)
        self.preview_label = ttk.Label(pf, text="No image selected", anchor="center")
        self.preview_label.pack(fill=tk.BOTH, expand=True)

        # Controls
        cf = ttk.LabelFrame(paned, text="Classical Controls (used only if AI fails)", padding=8)
        paned.add(cf, weight=1)
        self._build_controls(cf)

        # Bottom: progress + log
        bottom = ttk.Frame(self.root, padding=5)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        prog = ttk.Frame(bottom)
        prog.pack(fill=tk.X, pady=(0, 5))
        self.count_label = ttk.Label(prog, text="Images: 0")
        self.count_label.pack(side=tk.LEFT, padx=(0, 10))
        self.progress = ttk.Progressbar(prog, mode='determinate')
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self.progress_label = ttk.Label(prog, text="Idle")
        self.progress_label.pack(side=tk.RIGHT)

        lf = ttk.LabelFrame(bottom, text="Log", padding=3)
        lf.pack(fill=tk.BOTH, expand=False)
        self.log_text = tk.Text(lf, height=11, wrap=tk.WORD, state=tk.DISABLED, font=('Courier', 9))
        lsb = ttk.Scrollbar(lf, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        for tag, color in [('info', 'black'), ('ok', 'green'), ('warn', '#cc6600'),
                           ('err', 'red'), ('step', '#0055aa')]:
            self.log_text.tag_configure(tag, foreground=color)

    def _build_controls(self, parent):
        ttk.Checkbutton(parent, text="Use AI engines (slower, better quality)",
                        variable=self.use_ai).grid(row=0, column=0, columnspan=3,
                                                    sticky='w', pady=(0, 8))
        row = [1]
        def add_slider(label, key, from_, to, res, is_int=False):
            ttk.Label(parent, text=label).grid(row=row[0], column=0, sticky='w', pady=(4, 0))
            var = tk.DoubleVar(value=self.params[key])
            scale = ttk.Scale(parent, from_=from_, to=to, variable=var,
                              orient=tk.HORIZONTAL, length=160)
            scale.grid(row=row[0], column=1, sticky='ew', padx=4, pady=(4, 0))
            val_lbl = ttk.Label(parent, text=f"{self.params[key]}")
            val_lbl.grid(row=row[0], column=2, sticky='w', pady=(4, 0))
            def on_change(v, k=key, is_i=is_int, lbl=val_lbl):
                val = float(v)
                if is_i:
                    val = int(round(val))
                self.params[k] = val
                lbl.config(text=f"{val:.2f}" if isinstance(val, float) and not is_i else f"{int(val)}")
            scale.configure(command=on_change)
            row[0] += 1

        add_slider("Denoise (0-2)", 'denoise', 0, 2, 1, True)
        add_slider("Target short side", 'target_short', 1024, 3072, 64, True)
        add_slider("Max upscale", 'max_scale', 1.0, 4.0, 0.1)
        add_slider("IBP iterations", 'ibp_iters', 1, 10, 1, True)
        add_slider("Sharpen", 'sharpen', 0.0, 1.5, 0.05)
        add_slider("CLAHE", 'clahe', 0.0, 5.0, 0.1)
        add_slider("Detail enhance", 'detail', 0, 40, 1, True)
        add_slider("Saturation", 'color', 0.5, 1.5, 0.01)
        add_slider("Contrast", 'contrast', 0.5, 1.5, 0.01)
        add_slider("JPEG quality", 'quality', 70, 100, 1, True)
        parent.columnconfigure(1, weight=1)

    def log(self, msg, tag='info'):
        ts = datetime.now().strftime('%H:%M:%S')
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f'[{ts}] {msg}\n', tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def reset_params(self):
        self.params = dict(self.DEFAULTS)
        self.log("Controls reset to defaults", 'info')

    # ---------- File I/O ----------
    def open_file(self):
        path = filedialog.askopenfilename(title="Select thumbcache file",
            filetypes=[("Thumbcache DB", "thumbcache_*.db"), ("All files", "*.*")])
        if not path:
            return
        self._clear_gallery()
        self.entries = []; self.thumbnails = []; self.current_index = 0
        self.log(f"Opening: {path}")
        try:
            with open(path, "rb") as f:
                cf = ThumbcacheFile(f)
                for entry in cf.entries():
                    try:
                        data = entry.data
                    except Exception:
                        continue
                    if not data or len(data) < 100:
                        continue
                    ext = self._guess_ext(data)
                    if ext is None:
                        continue
                    name = getattr(entry, "identifier", None) or f"e{len(self.entries)}"
                    self.entries.append((name, data, ext))
        except Exception as e:
            self.log(f"Failed: {e}", 'err')
            messagebox.showerror("Error", str(e))
            return
        self.log(f"Loaded {len(self.entries)} image(s)", 'ok')
        self.count_label.config(text=f"Images: {len(self.entries)}")
        self._populate_gallery()

    def _populate_gallery(self):
        if not self.entries:
            self.preview_label.config(text="No images found.")
            return
        for idx, (_, data, _ext) in enumerate(self.entries):
            try:
                img = Image.open(io.BytesIO(data))
                img.thumbnail((150, 150))
                photo = ImageTk.PhotoImage(img)
                self.thumbnails.append(photo)
                btn = ttk.Button(self.gallery_inner, image=photo, width=160,
                                 command=lambda i=idx: self._show_preview(i))
                btn.image = photo
                btn.grid(row=idx // 3, column=idx % 3, padx=5, pady=5)
            except Exception:
                continue
        self.gallery_inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _show_preview(self, idx):
        if idx >= len(self.entries):
            return
        self.current_index = idx
        _, data, _ext = self.entries[idx]
        try:
            img = Image.open(io.BytesIO(data))
            w = self.preview_label.winfo_width() or 400
            h = self.preview_label.winfo_height() or 400
            img.thumbnail((max(w - 20, 100), max(h - 20, 100)))
            photo = ImageTk.PhotoImage(img)
            self.preview_label.config(image=photo, text="")
            self.preview_label.image = photo
        except Exception as e:
            self.preview_label.config(image="", text=f"Error: {e}")

    # ---------- Extraction ----------
    def _ask_mode(self, count):
        dlg = tk.Toplevel(self.root)
        dlg.title("Extraction Mode")
        dlg.transient(self.root); dlg.grab_set(); dlg.resizable(False, False)
        result = {'mode': None}
        msg = f"Extracting {count} image(s).\n\nChoose:\n"
        msg += "  • Raw — original bytes\n"
        msg += "  • Enhanced — engine chain\n"
        msg += "  • Both — save both\n\n"
        if self.use_ai.get():
            ai = []
            if REALESRGAN and ENGINE_STATE['realesrgan'] != 'broken':
                ai.append("Real-ESRGAN")
            if WAIFU2X and ENGINE_STATE['waifu2x'] != 'broken':
                ai.append("waifu2x")
            msg += ("AI chain: " + " → ".join(ai) + " → Classical") if ai else "AI disabled: Classical only"
        else:
            msg += "AI disabled: Classical pipeline only"
        ttk.Label(dlg, padding=15, justify=tk.LEFT, text=msg).pack()
        bf = ttk.Frame(dlg, padding=10); bf.pack()
        def pick(m):
            result['mode'] = m; dlg.destroy()
        ttk.Button(bf, text="Raw", command=lambda: pick('raw')).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text="Enhanced", command=lambda: pick('enhanced')).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text="Both", command=lambda: pick('both')).pack(side=tk.LEFT, padx=4)
        ttk.Button(bf, text="Cancel", command=lambda: pick(None)).pack(side=tk.LEFT, padx=4)
        dlg.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")
        dlg.wait_window()
        return result['mode']

    def extract_selected(self):
        if not self.entries:
            messagebox.showinfo("Notice", "Open a file first.")
            return
        mode = self._ask_mode(1)
        if mode is None:
            return
        out_dir = filedialog.askdirectory(title="Destination folder")
        if not out_dir:
            return
        self._start_extraction([self.current_index], out_dir, mode)

    def extract_all(self):
        if not self.entries:
            messagebox.showinfo("Notice", "Open a file first.")
            return
        mode = self._ask_mode(len(self.entries))
        if mode is None:
            return
        out_dir = filedialog.askdirectory(title="Destination folder for all")
        if not out_dir:
            return
        self._start_extraction(list(range(len(self.entries))), out_dir, mode)

    def cancel_extraction(self):
        self.cancel_flag.set()
        self.log("Cancellation requested...", 'warn')

    def _start_extraction(self, indices, out_dir, mode):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("Busy", "Extraction already running.")
            return
        self.cancel_flag.clear()
        self.progress['value'] = 0
        self.progress['maximum'] = len(indices)
        self.progress_label.config(text=f"0/{len(indices)}")
        self.cancel_btn.config(state=tk.NORMAL)
        params_snapshot = dict(self.params)
        use_ai = self.use_ai.get()
        self.worker_thread = threading.Thread(
            target=self._worker,
            args=(indices, out_dir, mode, params_snapshot, use_ai),
            daemon=True)
        self.worker_thread.start()

    def _worker(self, indices, out_dir, mode, params, use_ai):
        try:
            total = len(indices)
            ok = fail = 0
            engine_used = {}
            self.result_queue.put(('log', f"Start: {total} image(s), mode={mode}, ai={use_ai}", 'info'))

            for i, idx in enumerate(indices, 1):
                if self.cancel_flag.is_set():
                    self.result_queue.put(('log', f"Cancelled at {i-1}/{total}", 'warn'))
                    break
                if idx >= len(self.entries):
                    continue
                name, data, ext = self.entries[idx]
                base = f"thumb_{idx:04d}"
                parts = []
                t0 = datetime.now()

                # RAW
                if mode in ('raw', 'both'):
                    try:
                        with open(os.path.join(out_dir, f"{base}_raw.{ext}"), 'wb') as f:
                            f.write(data)
                        parts.append(f"raw:{ext.upper()}({len(data)//1024}KB)")
                        ok += 1
                    except Exception as e:
                        parts.append(f"raw FAILED:{e}")
                        fail += 1

                # ENHANCED
                if mode in ('enhanced', 'both'):
                    self.result_queue.put(('log', f"[{i}/{total}] {base} enhanced:", 'step'))
                    try:
                        enh, orig, newsz, methods, engine = enhance_auto(
                            data, params,
                            progress_cb=lambda m: self.result_queue.put(('log', m, 'step')),
                            cancel_flag=self.cancel_flag,
                            use_ai=use_ai)
                        out_path = os.path.join(out_dir, f"{base}_enhanced.jpg")
                        with open(out_path, 'wb') as f:
                            f.write(enh)
                        parts.append(
                            f"enh:{orig[0]}x{orig[1]}→{newsz[0]}x{newsz[1]} "
                            f"[{engine}]")
                        engine_used[engine] = engine_used.get(engine, 0) + 1
                        ok += 1
                    except InterruptedError:
                        self.result_queue.put(('log', "Cancelled", 'warn'))
                        break
                    except Exception as ee:
                        parts.append(f"enh FAILED:{ee}")
                        self.result_queue.put(('log', traceback.format_exc(), 'err'))
                        fail += 1

                elapsed = (datetime.now() - t0).total_seconds()
                tag = 'ok' if fail == 0 else 'warn'
                self.result_queue.put(('log',
                    f"[{i}/{total}] {base}: " + " | ".join(parts) + f"  ({elapsed:.1f}s)", tag))
                self.result_queue.put(('progress', i, total))

            # Summary
            if engine_used:
                summary = "  ".join(f"{k}:{v}" for k, v in engine_used.items())
                self.result_queue.put(('log', f"Engines used: {summary}", 'info'))
            self.result_queue.put(('log',
                f"Done. OK={ok} FAIL={fail} | {out_dir}",
                'ok' if fail == 0 else 'warn'))
        except Exception:
            self.result_queue.put(('log',
                f"WORKER CRASH:\n{traceback.format_exc()}", 'err'))
        finally:
            self.result_queue.put(('done',))

    def _poll_queue(self):
        try:
            while True:
                msg = self.result_queue.get_nowait()
                if msg[0] == 'log':
                    self.log(msg[1], msg[2])
                    if 'Real-ESRGAN' in msg[1] or 'waifu2x' in msg[1]:
                        self._update_engine_label()
                elif msg[0] == 'progress':
                    _, cur, tot = msg
                    self.progress['value'] = cur
                    self.progress['maximum'] = tot
                    self.progress_label.config(text=f"{cur}/{tot}")
                elif msg[0] == 'done':
                    self.progress_label.config(text="Done")
                    self.cancel_btn.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    @staticmethod
    def _guess_ext(data):
        if data[:3] == b'\xff\xd8\xff':                   return 'jpg'
        if data[:8] == b'\x89PNG\r\n\x1a\n':              return 'png'
        if data[:2] == b'BM':                             return 'bmp'
        if data[:4] == b'GIF8':                           return 'gif'
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP': return 'webp'
        return None

    def _clear_gallery(self):
        for c in self.gallery_inner.winfo_children():
            c.destroy()
        self.canvas.configure(scrollregion=(0, 0, 0, 0))


def main():
    root = tk.Tk()
    ThumbcacheViewer(root)
    root.mainloop()


if __name__ == '__main__':
    main()
