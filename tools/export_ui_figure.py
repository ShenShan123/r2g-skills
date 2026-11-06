#!/usr/bin/env python3
"""Capture a web page (e.g. the R2G interface) as a single-page PDF figure for the paper.

Renders URL in headless Chromium at WIDTHxHEIGHT CSS pixels and SCALE device-pixel ratio, then wraps the
screenshot in a one-page PDF whose page size equals the image (no margins, no header/footer), so LaTeX can
\\includegraphics it at any width. Raster, but at SCALE=2+ it stays sharp at column width.

  $PY export_ui_figure.py URL OUT.pdf   (needs pillow; on 208: /home/yangao/r2g_toolchain/pyenvs/r2g-cnn/bin/python)
  export_ui_figure.py URL OUT.pdf [--width 1600] [--height 1000] [--scale 2] [--wait-ms 4000] [--png OUT.png]

Chromium: $CHROME, else Playwright's cached build under ~/.cache/ms-playwright, else chromium/google-chrome on PATH.
"""
import argparse, glob, os, shutil, subprocess, sys, tempfile
from pathlib import Path


def find_chrome():
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    cands = sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")), reverse=True)
    cands += sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-*/headless_shell")), reverse=True)
    for c in cands + [shutil.which(n) for n in ("chromium", "chromium-browser", "google-chrome")]:
        if c and os.access(c, os.X_OK):
            return c
    sys.exit("no Chromium found: set $CHROME")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url"); ap.add_argument("out", type=Path)
    ap.add_argument("--width", type=int, default=1600); ap.add_argument("--height", type=int, default=1000)
    ap.add_argument("--scale", type=float, default=2.0); ap.add_argument("--wait-ms", type=int, default=4000)
    ap.add_argument("--png", type=Path, help="also keep the screenshot")
    a = ap.parse_args()
    from PIL import Image  # pillow
    with tempfile.TemporaryDirectory() as td:
        shot = Path(td) / "shot.png"
        cmd = [find_chrome(), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
               "--window-size=%d,%d" % (a.width, a.height), "--force-device-scale-factor=%g" % a.scale,
               "--virtual-time-budget=%d" % a.wait_ms, "--screenshot=%s" % shot, a.url]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if not shot.exists():
            sys.exit("screenshot failed (rc %d): %s" % (r.returncode, (r.stderr or "")[-400:]))
        img = Image.open(shot).convert("RGB")
        if a.png:
            img.save(a.png)
        # page size = image size at 72*scale dpi -> the PDF page is width x height CSS pixels in points
        img.save(a.out, "PDF", resolution=72.0 * a.scale)
    print("%s: 1 page, %dx%d px (%dx%d CSS px at %gx)" % (a.out, img.width, img.height, a.width, a.height, a.scale))


if __name__ == "__main__":
    main()
