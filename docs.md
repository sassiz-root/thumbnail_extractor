# 📖 Thumbcache Viewer & Recoverer — Technical Documentation

Complete reference for installation, architecture, usage, and internals.

---

## Table of Contents

1. [Motivation](#1-motivation)
2. [How Windows Thumbcaches Work](#2-how-windows-thumbcaches-work)
3. [Architecture](#3-architecture)
4. [Installation](#4-installation)
5. [Usage Guide](#5-usage-guide)
6. [Engine Selection Logic](#6-engine-selection-logic)
7. [Enhancement Pipelines](#7-enhancement-pipelines)
8. [Configuration Reference](#8-configuration-reference)
9. [Troubleshooting](#9-troubleshooting)
10. [Performance](#10-performance)
11. [FAQ](#11-faq)

---

## 1. Motivation

When photos are deleted or a disk is corrupted, traditional file recovery tools
(`photorec`, `R-Studio`, `EaseUS`) often fail because:

- The filesystem metadata is gone
- Data blocks were overwritten by new files
- SSD TRIM permanently zeroed the blocks

**But Windows keeps thumbnails.** Every time File Explorer shows a folder in
thumbnail view, Windows generates a resized copy and caches it — sometimes
preserving 1280px or larger — inside `thumbcache_*.db` files. These thumbnails
survive even when original files are gone, because they live in a **different
location** (`AppData\Local\Microsoft\Windows\Explorer`).

This tool brings those thumbnails back as best-quality JPEGs.

---

## 2. How Windows Thumbcaches Work

Windows 10/11 stores thumbnails in these files under
`%LOCALAPPDATA%\Microsoft\Windows\Explorer\`:

| File | Resolution |
|------|-----------|
| `thumbcache_16.db` | 16×16 icons |
| `thumbcache_32.db` | 32×32 icons |
| `thumbcache_48.db` | 48×48 icons |
| `thumbcache_96.db` | 96×96 previews |
| `thumbcache_256.db` | 256×256 medium tiles |
| `thumbcache_768.db` | 768×768 large tiles |
| **`thumbcache_1280.db`** | **1280×1280 — best for recovery** |
| `thumbcache_1920.db` | 1920×1920 — when present |
| `thumbcache_exif.db` | EXIF metadata (no images) |
| `thumbcache_sr.db` | Super-resolution (rare) |
| `thumbcache_wide.db` | Wide thumbnails |

**Structure of each `.db`:** it's a CMMF (Compound Memory-Mapped File) —
essentially a small database of entries. Each entry has:

- A hash (identifies the original file path)
- A **format** (JPEG, PNG, BMP, WEBP, or EXIF)
- **The raw image bytes**

`dissect.thumbcache` parses all of this transparently.

**Important limitation:** thumbnails are *downsamples*, never originals. If the
original was 4000×3000 and the cache only has 1280×1280, the final image is at
most 1280×1280 real pixels. AI upscaling can enlarge this but cannot recreate
information that was never captured.

---

## 3. Architecture

```
thumbcache_viewer.py
├── Engine Detection (REALESRGAN, WAIFU2X via shutil.which)
├── Validation (_validate_ai_output — black-output detector)
├── AI Engines
│   ├── enhance_realesrgan()  ← Vulkan GPU
│   └── enhance_waifu2x()     ← Vulkan GPU (older support)
├── Classical Pipeline
│   ├── _denoise()        ← bilateral filter
│   ├── _ibp_upscale()    ← Iterative Back-Projection
│   ├── _sharpen()        ← 3-scale unsharp mask
│   ├── _clahe()          ← local contrast
│   ├── _detail_enhance() ← edge-aware enhancement
│   └── enhance_classical() — orchestrator
├── Auto (enhance_auto) — engine chain with fallback
└── GUI (ThumbcacheViewer)
    ├── Gallery (Tk Canvas + buttons)
    ├── Preview
    ├── Controls (sliders)
    └── Log + Progress
```

**Concurrency:** extraction runs in a daemon thread; UI updates flow through a
`queue.Queue` polled every 100 ms. Cancel is honored between stages (subprocess
AI calls cannot be interrupted mid-run, but the next stage won't start).

---

## 4. Installation

### 4.1 Python 3.9+

```bash
python3 --version  # ≥3.9 required
```

### 4.2 Dependencies

```bash
pip install -r requirements.txt --break-system-packages
```

If you're in a virtualenv, drop `--break-system-packages`.

### 4.3 AI Engines (optional)

**Real-ESRGAN** (best quality, requires Vulkan):

```bash
wget https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip
unzip *.zip -d ~/realesrgan
chmod +x ~/realesrgan/realesrgan-ncnn-vulkan
sudo ln -s ~/realesrgan/realesrgan-ncnn-vulkan /usr/local/bin/
```

Verify:
```bash
which realesrgan-ncnn-vulkan  # → /usr/local/bin/realesrgan-ncnn-vulkan
```

**waifu2x** (older GPU support):

```bash
wget https://github.com/nihui/waifu2x-ncnn-vulkan/releases/download/20220728/waifu2x-ncnn-vulkan-20220728-ubuntu.zip
unzip *.zip -d ~/waifu2x
chmod +x ~/waifu2x/waifu2x-ncnn-vulkan
sudo ln -s ~/waifu2x/waifu2x-ncnn-vulkan /usr/local/bin/
```

Verify with a quick test:
```bash
realesrgan-ncnn-vulkan -h
waifu2x-ncnn-vulkan -h
```

### 4.4 (Windows) GPU drivers

- NVIDIA: `nvidia-driver-535` or newer
- Intel: `mesa-vulkan-drivers` (Linux) or latest Intel graphics driver (Windows)
- AMD: `mesa-vulkan-drivers` (Linux)

Test Vulkan:
```bash
sudo apt install vulkan-tools
vulkaninfo | grep "device name"
```

---

## 5. Usage Guide

### 5.1 Getting the thumbcache files

**Option A — Windows (live)**
1. In File Explorer, enable *"Hidden items"* (View tab).
2. Navigate to:
   ```
   C:\Users\<you>\AppData\Local\Microsoft\Windows\Explorer
   ```
3. Copy the **entire** `thumbcache_*.db` files (especially `thumbcache_1280.db`).

**Option B — From a disk image**
Mount the image and copy the same path from `Users/<user>/AppData/...`.

**Option C — Live Linux mount**
```bash
# mount the Windows partition read-only
sudo mount -o ro /dev/sda2 /mnt/win
ls /mnt/win/Users/*/AppData/Local/Microsoft/Windows/Explorer/
```

### 5.2 Running the app

```bash
python3 thumbcache_viewer.py
```

### 5.3 Workflow

1. **Open thumbcache** → pick `thumbcache_1280.db`.
2. Watch the log: `Loaded 541 image(s)`.
3. Click a gallery thumbnail to preview on the right.
4. Choose **Extract All** (or single).
5. In the dialog, pick **Raw / Enhanced / Both**.
6. Choose destination folder → extraction starts.
7. Monitor progress bar + log. Use **Cancel** if needed.

### 5.4 Recommended settings

**For initial batch (slow but accurate):**
- ✅ Use AI engines
- Denoise: 1
- Sharpen: 0.35
- CLAHE: 1.5
- Detail: 10
- Quality: 93

**For fast full-batch extraction:**
- ⬜ Uncheck "Use AI engines"
- Target short: 1920, Max upscale: 2.0
- IBP iterations: 3
- Quality: 90

**If AI failed and you're stuck with classical:**
- Reduce Detail to 5
- Reduce Sharpen to 0.15
- CLAHE to 1.0
- This prevents the "painting" effect

---

## 6. Engine Selection Logic

At startup, `shutil.which()` detects installed binaries:

```python
REALESRGAN = shutil.which('realesrgan-ncnn-vulkan')
WAIFU2X    = shutil.which('waifu2x-ncnn-vulkan')
```

The engine state (`ENGINE_STATE`) is lazy — no startup test. First extraction
triggers evaluation:

```
For each image:
    If use_ai:
        If realesrgan available and not broken:
            try realesrgan → validate output → if OK, use it, RETURN
            if invalid → mark 'broken' (won't retry this session)
        If waifu2x available and not broken:
            try waifu2x → validate output → if OK, use it, RETURN
            if invalid → mark 'broken'
    Fall through to classical → RETURN
```

**Validation** (`_validate_ai_output`):
- Loads both input and output as PIL RGB
- Computes mean pixel value of each
- If output mean < 10 AND input mean > 25 → rejects (broken/black output)
- Otherwise accepts

This allows legitimately dark photos to pass while catching the "GPU produced
all-black" failure mode.

**Why cache broken state?** On a broken GPU, every call would waste 5–20 s
before falling back. Caching means: broken once = broken for the session.

**To reset:** restart the app.

---

## 7. Enhancement Pipelines

### 7.1 Real-ESRGAN

```
Input JPEG bytes
  → PNG encode
  → realesrgan-ncnn-vulkan -s 2 -n realesrgan-x4plus -m <models>
  → PNG decode
  → JPEG encode (quality=93)
  → validate
```

- **Model:** `realesrgan-x4plus` (photo-optimized)
- **Scale:** 2× (adjustable to 3 or 4 in code)
- **Best for:** NVIDIA/AMD GPUs with good Vulkan support

### 7.2 waifu2x

Tries three models in order until one succeeds:
1. `realesrgan-x4plus` (if Real-ESRGAN models directory exists)
2. `models-upconv_7_photo` (waifu2x bundled, photo-oriented)
3. `models-cunet` (waifu2x bundled, general)

- **Best for:** Intel Iris / older GPUs where Real-ESRGAN fails
- **NCNN runtime is more forgiving** of incomplete Vulkan implementations

### 7.3 Classical (fallback)

Six-stage pipeline (controlled by sliders):

1. **Bilateral filter** (denoise)
   - Strength 1: `d=5, sigmaColor=15`
   - Strength 2: `d=7, sigmaColor=25`
   - Preserves edges while removing JPEG artifacts

2. **Iterative Back-Projection** (upscale)
   - 5 iterations by default
   - Downsamples HR, compares to LR, back-propagates error
   - Better than pure bicubic — recovers some lost high-frequency detail

3. **3-scale Unsharp Mask** (sharpen)
   - Radii 1.0, 2.5, 5.0 px
   - Amounts scale by `sharpen` slider (0–1.5)

4. **CLAHE** (local contrast)
   - Applied to L channel in LAB space
   - `clipLimit = 1.5` default (gentle; higher causes halo/painting)

5. **cv2.detailEnhance** (structure)
   - `sigma_s=10, sigma_r=0.15` (fixed sigma_r preserves color edges)
   - Aggressive values cause waxy skin / cartoon effect

6. **Color & Contrast** final adjustment
   - Saturation ×1.02 (just under "noticeable")
   - Contrast ×1.01

**Why "painting" happens:** High `detailEnhance` sigma_s, high CLAHE, and
aggressive sharpen all destroy fine texture. The defaults are deliberately
conservative. If you still see painting, reduce all three.

---

## 8. Configuration Reference

### Classical pipeline sliders

| Control | Range | Default | Effect |
|---------|-------|---------|--------|
| Denoise | 0–2 | 1 | 0=off, 2=aggressive (waxy) |
| Target short side | 1024–3072 | 1920 | Upscale goal (px) |
| Max upscale | 1.0–4.0 | 3.0 | Scale cap |
| IBP iterations | 1–10 | 4 | Super-resolution quality |
| Sharpen | 0.0–1.5 | 0.35 | Sharpening strength |
| CLAHE | 0.0–5.0 | 1.5 | Local contrast |
| Detail enhance | 0–40 | 10 | Edge enhancement |
| Saturation | 0.5–1.5 | 1.02 | Color saturation |
| Contrast | 0.5–1.5 | 1.01 | Overall contrast |
| JPEG quality | 70–100 | 93 | Output quality |

### AI parameters (edit in code)

In `enhance_realesrgan` / `enhance_waifu2x`:

```python
scale = 2          # try 3 or 4 (slower, more memory)
quality = 95       # JPEG output quality
timeout = 300      # seconds
```

In `enhance_realesrgan`, add `-t 100` if GPU runs out of memory:
```python
cmd += ['-t', '100']
```

---

## 9. Troubleshooting

### "AI output is black"

**Cause:** GPU/driver incompatibility (esp. older Intel Haswell, some AMD).

**Fix:** App auto-detects and falls back. If both engines fail, classical
runs. Check the log to confirm which engine was used.

**To confirm the issue manually:**
```bash
realesrgan-ncnn-vulkan -i test.jpg -o out.png -s 2 -n realesrgan-x4plus -v
# If output is black and stderr shows "vkWaitForFences failed -4" → broken GPU
```

### "waifu2x not detected"

```bash
which waifu2x-ncnn-vulkan
# If empty, symlink it:
sudo ln -s ~/waifu2x/waifu2x-ncnn-vulkan /usr/local/bin/
```

### "No images extracted"

- Check the source file size (should be > 10 MB for `thumbcache_1280.db`)
- `thumbcache_exif.db` contains **metadata only**, not images — skip it
- Check the log for "Loaded 0 image(s)"

### "Extraction is very slow"

- Uncheck **Use AI engines** in controls → 5–10× faster (lower quality)
- Or reduce `scale` from 2 to 1 in code (or set `Max upscale=1.0`)

### "Memory error during AI"

Add tiling:
```python
# in enhance_realesrgan()
cmd += ['-t', '100']
```

### App freezes at startup

- If both AI binaries are missing, startup is instant
- If binaries exist, they are NOT tested at startup (lazy evaluation)
- If freezing: check console output for Python errors

---

## 10. Performance

### Per-image timing (720×1280 source)

| Engine | Time | GPU | Notes |
|--------|------|-----|-------|
| Real-ESRGAN | 10–20 s | RTX 3060 | Best quality |
| Real-ESRGAN | 30–60 s | Intel Iris | If it works at all |
| waifu2x | 5–15 s | Intel Iris | Excellent fallback |
| Classical (cv2) | 2–5 s | CPU | No GPU needed |
| Classical (PIL) | 10–20 s | CPU | If opencv missing |

### Batch estimates

| Count | AI (waifu2x) | Classical |
|-------|:---:|:---:|
| 100 | 15 min | 5 min |
| 500 | 1.5 h | 25 min |
| 1000 | 3 h | 50 min |

Use **Extract Selected** on a few images first to benchmark, then run batch
overnight or while AFK.

---

## 11. FAQ

**Q: Can AI bring back the original high-resolution photo?**
No. It can enlarge a thumbnail (e.g., 1280 → 2560) and sharpen it convincingly,
but it cannot invent details that were never in the thumbnail. The result is
"looks like a real photo at this resolution" — not "the original 4000×3000 image."

**Q: Why are some thumbnails tiny (e.g., 96×96)?**
Those come from `thumbcache_96.db` (Explorer small-view icons). Always prefer
`thumbcache_1280.db` — it's the highest-resolution cache Windows keeps.

**Q: My GPU produces black output — is my setup broken?**
Not your fault. Vulkan implementations vary widely; older Intel/AMD GPUs often
fail. That's exactly why the fallback chain exists. waifu2x uses a more
forgiving ncnn runtime and works where Real-ESRGAN doesn't.

**Q: Can I edit `thumbcache` files directly?**
Not safely — they're CMMF databases with checksums. Use the exported JPEGs.

**Q: Does the app modify my `.db` files?**
No. Read-only access. Your originals are safe.

**Q: Which thumbcache file should I open first?**
`thumbcache_1280.db`. Then `thumbcache_1920.db` (if present). Then `768.db`.
Avoid `96.db` (tiny previews) unless you're desperate.

**Q: Where are the outputs saved?**
Flat folder — one JPEG per original thumbnail. Raw exports keep original names
(`thumb_0001_raw.jpg`), enhanced exports get `_enhanced.jpg` suffix.

**Q: Can I batch-extract from multiple `.db` files at once?**
Open each `.db` separately. Each extraction is scoped to the currently-open file.

**Q: How do I completely reset engine state (e.g., to retry Real-ESRGAN)?**
Restart the application. `ENGINE_STATE` is in-memory only.

---

## License

MIT — see `LICENSE`.

## Support

- Open an issue on GitHub for bugs
- Include: OS, GPU, output of `which realesrgan-ncnn-vulkan waifu2x-ncnn-vulkan`,
  and the extraction log
