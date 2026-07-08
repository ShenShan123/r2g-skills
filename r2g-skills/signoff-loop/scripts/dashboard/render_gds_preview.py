#!/usr/bin/env python3
"""
Render a GDS file to a PNG preview image using KLayout batch mode.
"""
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap


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
            'klayout', '-b', '-nc', '-r', script_path
        ], check=True)
    finally:
        Path(script_path).unlink(missing_ok=True)

    print(out)


if __name__ == '__main__':
    main()
