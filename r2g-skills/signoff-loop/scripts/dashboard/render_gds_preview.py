#!/usr/bin/env python3
"""
Render a GDS file to a PNG preview image using KLayout batch mode.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap


def _klayout_cmd():
    """KLAYOUT_CMD, else the skill's pinned env.local.sh, else PATH.

    A bare 'klayout' only worked where a system KLayout happened to be on PATH;
    a no-root install gets KLayout from the toolchain bundle, pinned in
    env.local.sh, so the preview failed there (2026-10-23).
    """
    if os.environ.get("KLAYOUT_CMD"):
        return os.environ["KLAYOUT_CMD"]
    pin = Path(__file__).resolve().parents[2] / "references" / "env.local.sh"
    try:
        m = re.search(r'^export KLAYOUT_CMD="?([^"\n]+)"?$', pin.read_text(), re.M)
        if m and os.access(m.group(1), os.X_OK):
            return m.group(1)
    except OSError:
        pass
    return "klayout"


def main():
    if len(sys.argv) < 3:
        print("usage: render_gds_preview.py <input.gds> <output.png> [size]", file=sys.stderr)
        sys.exit(1)

    gds = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 1600

    if not gds.exists():
        print(f"input GDS not found: {gds}", file=sys.stderr)
        sys.exit(1)

    out.parent.mkdir(parents=True, exist_ok=True)

    script = textwrap.dedent(f'''
        import pya
        view = pya.LayoutView()
        view.load_layout(r"{str(gds)}", 0)
        view.max_hier()
        view.zoom_fit()
        view.save_image(r"{str(out)}", {size}, {size})
    ''')

    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as f:
        f.write(script)
        script_path = f.name

    try:
        subprocess.run([
            _klayout_cmd(), '-b', '-nc', '-r', script_path
        ], check=True)
    finally:
        Path(script_path).unlink(missing_ok=True)

    print(out)


if __name__ == '__main__':
    main()
