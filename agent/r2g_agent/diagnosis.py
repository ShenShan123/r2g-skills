"""Run diagnosis with the same discovered tools as the physical repair loop."""
from pathlib import Path

from .paths import signoff_scripts


def command(project: Path | str, check: str, mode: str = "--list",
            output: Path | None = None) -> list[str]:
    if check not in ("drc", "lvs", "route", "timing") or mode not in ("--list", "--next"):
        raise ValueError("unsupported diagnosis request")
    scripts = signoff_scripts()
    # Pass paths as positional arguments, never interpolate them into shell code.
    shell = 'source "$1" >&2 || exit $?; shift; '
    args = [str(scripts / "flow/_env.sh")]
    if output is not None:
        shell += 'out="$1"; shift; exec python3 "$@" > "$out"'
        args.append(str(output))
    else:
        shell += 'exec python3 "$@"'
    return ["bash", "-c", shell, "r2g-diagnose", *args,
            str(scripts / "reports/diagnose_signoff_fix.py"), str(project), "--check", check, mode]
