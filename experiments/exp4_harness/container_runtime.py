"""Run the exported, pinned OCI filesystem with Bubblewrap on hosts without Podman."""
import json
from pathlib import Path


def bubblewrap_command(image_root, mounts, environment, command, cwd="/"):
    image_root = Path(image_root)
    metadata = json.loads((image_root.parent / "image.json").read_text())
    env = dict(item.split("=", 1) for item in metadata["Config"].get("Env", []))
    env.update(environment)
    args = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session",
            "--uid", "0", "--gid", "0", "--clearenv",
            "--ro-bind", str(image_root), "/", "--proc", "/proc", "--dev", "/dev",
            "--tmpfs", "/run", "--tmpfs", "/sys", "--cap-drop", "ALL", "--chdir", cwd]
    for source, destination, mode in mounts:
        args += ["--ro-bind" if mode == "ro" else "--bind", str(Path(source).resolve()), destination]
    for key, value in env.items():
        args += ["--setenv", key, value]
    return [*args, "--", *command]
