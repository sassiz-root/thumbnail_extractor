
# 🖼️ Thumbcache Viewer & Recoverer
#    Programmed by: Sassiz-Root
> Recover lost photos from Windows 10/11 thumbnail caches with AI-powered upscaling.

[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows%20%7C%20macOS-lightgrey)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![OpenCV](https://img.shields.io/badge/opencv-optional-orange)]()

When photos are lost from disk and traditional recovery fails, Windows still
keeps **thumbnail caches** of every image ever shown. This tool extracts those
thumbnails and — using AI super-resolution (Real-ESRGAN / waifu2x) or classic
image processing — restores them as close to the originals as physically possible.

---

## ✨ Features

- 📂 **Reads all thumbcache formats** — Vista through Windows 11
  (`thumbcache_*.db`, `thumbcache_exif.db`)
- 🎨 **Gallery UI** — Tkinter, no browser, no extra frameworks
- 🚀 **Three-engine pipeline** (auto fallback):
  1. **Real-ESRGAN** — best quality (needs Vulkan-capable GPU)
  2. **waifu2x-ncnn-vulkan** — excellent and works on older GPUs
  3. **Classical** (OpenCV) — always available, no GPU required
- ⚙️ **Live controls** — adjust denoise, sharpen, CLAHE, saturation, quality
- 🖼️ **Raw + Enhanced export** — save both for comparison
- 📊 **Real-time log & progress bar**
- 🛑 **Cancellable** mid-batch
- 📦 **Flat output** — all recovered images in one folder

---

## 🔧 Installation

### 1. Python dependencies

```bash
pip install dissect.thumbcache Pillow numpy opencv-python --break-system-packages
