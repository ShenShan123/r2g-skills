"""Safety checks for adopting real, already running Linux capture pipes."""
from contextlib import contextmanager
import errno
import importlib.util
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scale_gf180_signoff.py"
SPEC = importlib.util.spec_from_file_location("scale_gf180_signoff_tested", SCRIPT)
scale = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scale)
LINUX = sys.platform == "linux" and Path("/proc/self/stat").exists()


def await_condition(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.01)
    raise AssertionError("Timed out waiting for the local test subprocess")


@contextmanager
def stopped_capture_parent(tmp_path, rc=0, report=None):
    """The Python parent owns subprocess.run(capture_output=True), just as legacy does."""
    marker = tmp_path / "child.pid"
    gate = tmp_path / "child.go"
    child_code = '''
import os, pathlib, sys, time
marker, gate, report, rc = sys.argv[1:]
pathlib.Path(marker).write_text(str(os.getpid()))
deadline = time.monotonic() + 8
while not pathlib.Path(gate).exists():
    if time.monotonic() > deadline:
        raise SystemExit(99)
    time.sleep(.01)
def emit(fd, data):
    while data:
        data = data[os.write(fd, data):]
emit(1, b"STDOUT:" + b"o" * 196608 + b":OUT-END\\n")
emit(2, b"STDERR:" + b"e" * 131072 + b":ERR-END\\n")
if report:
    pathlib.Path(report).write_text("<report-database><items><item/><item/></items></report-database>")
os._exit(int(rc))
'''
    parent_code = '''
import subprocess, sys
subprocess.run([sys.executable, "-I", "-c"] + sys.argv[1:], capture_output=True)
'''
    parent = subprocess.Popen(
        [sys.executable, "-I", "-c", parent_code, child_code,
         str(marker), str(gate), str(report or ""), str(rc)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        await_condition(lambda: marker.is_file() and marker.read_text().strip())
        child_pid = int(marker.read_text())
        assert scale.proc(child_pid)["ppid"] == parent.pid
        os.kill(parent.pid, signal.SIGSTOP)
        await_condition(lambda: scale.proc(parent.pid)["state"] in ("T", "t"))
        identity = scale.proc(child_pid)
        yield parent, identity, gate
    finally:
        # Resume first so the real parent can reap its completed child. The
        # process-group fallback also handles failed assertions/blocked writes.
        if parent.poll() is None:
            os.kill(parent.pid, signal.SIGCONT)
            try:
                parent.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                os.killpg(parent.pid, signal.SIGKILL)
                parent.wait(timeout=2)


@pytest.mark.skipif(not LINUX, reason="Requires Linux /proc and POSIX capture pipes")
@pytest.mark.parametrize("rc", [0, 7, 124])
def test_real_capture_pipes_drain_while_parent_stopped_and_preserve_wait_status(tmp_path, rc):
    expected_out = b"STDOUT:" + b"o" * 196608 + b":OUT-END\n"
    expected_err = b"STDERR:" + b"e" * 131072 + b":ERR-END\n"
    with stopped_capture_parent(tmp_path, rc=rc) as (parent, child, gate):
        readers = scale.pipe_readers(parent.pid, child["pid"])
        data = [bytearray(), bytearray()]
        eof = [False, False]
        try:
            assert len(readers) == 2
            gate.touch()
            deadline = time.monotonic() + 5
            while not all(eof):
                assert time.monotonic() < deadline, "Taking over capture pipes stalled"
                for index, fd in enumerate(readers):
                    if eof[index]:
                        continue
                    try:
                        block = os.read(fd, 65536)
                    except BlockingIOError:
                        continue
                    if block:
                        data[index].extend(block)
                    else:
                        eof[index] = True
                time.sleep(.005)
            exited = await_condition(lambda: (
                p if (p := scale.proc(child["pid"]))["state"] == "Z" else None))
            assert scale.proc(parent.pid)["state"] in ("T", "t")
            assert exited["start"] == child["start"]
            assert os.waitstatus_to_exitcode(exited["wait_status"]) == rc
            assert bytes(data[0]) == expected_out
            assert bytes(data[1]) == expected_err
        finally:
            for fd in readers:
                os.close(fd)


@pytest.mark.skipif(not LINUX, reason="Requires Linux /proc and POSIX capture pipes")
@pytest.mark.parametrize("rc,status,violations", [(0, "violations", 2), (7, "drc_failed", None),
                                                (124, "timeout", None)])
def test_adopt_real_capture_child_records_only_completed_successful_report(tmp_path, rc, status, violations):
    control = tmp_path / "control"
    control.mkdir()
    runs = tmp_path / "runs"
    original = runs / "C00001"
    staged = tmp_path / "projects/C00001/backend/RUN_1/results"
    for folder in (original, staged):
        folder.mkdir(parents=True)
        (folder / "6_final.gds").write_bytes(b"frozen original GDS")
    report = staged.parents[2] / "drc/6_drc.lyrdb"
    report.parent.mkdir()
    recorded = []
    driver = SimpleNamespace(record=lambda path, result: recorded.append((path, result)))
    args = SimpleNamespace(control=control, runs=runs)
    with stopped_capture_parent(tmp_path, rc=rc, report=report) as (parent, child, gate):
        readers = scale.pipe_readers(parent.pid, child["pid"])
        row = {**child, "design": "C00001", "project": str(staged.parents[2]),
               "gds_sha256": scale.sha(original / "6_final.gds")}
        returned = queue.Queue()

        def invoke():
            try:
                returned.put((True, scale.adopt(row, readers, args, driver)))
            except BaseException as exc:
                returned.put((False, exc))

        worker = threading.Thread(target=invoke, daemon=True)
        worker.start()
        gate.touch()
        okay, value = returned.get(timeout=6)
        if not okay:
            raise value
        worker.join(timeout=.5)
        assert not worker.is_alive()
        assert value["podman_exit_code"] == rc
        assert value["drc_status"] == status
        assert value["violations"] == violations
        assert value["adopted_legacy_check"] is True
        assert recorded == [(original, value)]
        assert scale.proc(parent.pid)["state"] in ("T", "t")
        assert (original / "6_drc.lyrdb").read_bytes() == report.read_bytes()
        log = (control / "adopted_logs/C00001.log").read_bytes()
        assert log.count(b"o") == 196608
        assert log.count(b"e") == 131072
        assert b":OUT-END\n" in log and b":ERR-END\n" in log
        assert json.loads((control / "adopted_results/C00001.json").read_text()) == value


def test_report_items_counts_violation_items_not_geometry_values(tmp_path):
    report = tmp_path / "drc.lyrdb"
    report.write_text("<report-database><items><item><values><value>a</value>"
                      "<value>b</value></values></item><item/></items></report-database>")
    assert scale.report_items(report) == 2


@pytest.mark.parametrize("xml", ["", "<report-database><items><item/>",
                                  "<report-database><items></report-database>",
                                  "<report-database></report-database>truncated"])
def test_truncated_or_invalid_xml_never_yields_clean_verdict(tmp_path, xml):
    report = tmp_path / "drc.lyrdb"
    report.write_text(xml)
    with pytest.raises(ET.ParseError):
        scale.report_items(report)


def podman_command(projroot, design="C00001"):
    project = projroot / design
    return ["podman", "run", "--rm", "-v", f"{projroot.parent}/gf180_deck_signoff:/deck:ro,Z",
            "-v", f"{project}/backend/RUN_1/results:/src:ro,Z",
            "-v", f"{project}/drc:/out:Z", "r2g-orfs:26Q3", "bash", "-lc",
            "cd /deck && timeout --kill-after=60 21600 klayout -b -r main.drc "
            "-rd input=/src/6_final.gds -rd topcell=real_top -rd report=/out/6_drc.lyrdb "
            "-rd metal_top=9K -rd mim_option=B -rd metal_level=5LM -rd feol=true "
            "-rd beol=true -rd conn_drc=true -rd run_mode=flat -rd offgrid=false -rd thr=4"]


def test_classify_accepts_only_own_direct_project_layout(tmp_path):
    root = tmp_path / "rerun_gf180_drcproj"
    command = podman_command(root)
    assert scale.classify_podman(command, root) == {
        "design": "C00001", "project": str(root / "C00001"), "top": "real_top"}


@pytest.mark.parametrize("case", ["executable", "no_rm", "other_campaign", "nested_project",
                                  "writable_input", "duplicate_input", "other_output",
                                  "wrong_timeout", "wrong_mode", "wrong_threads", "no_top",
                                  "wrong_image", "writable_deck", "no_deck", "extra_shell"])
def test_classify_rejects_cross_campaign_or_changed_checker_contract(tmp_path, case):
    root = tmp_path / "rerun_gf180_drcproj"
    command = podman_command(root)
    if case == "executable":
        command[0] = "python3"
    elif case == "no_rm":
        command.remove("--rm")
    elif case == "other_campaign":
        command = podman_command(tmp_path / "other_campaign")
    elif case == "nested_project":
        command = podman_command(root / "nested")
    elif case == "writable_input":
        command[6] = command[6].replace(":/src:ro,Z", ":/src:Z")
    elif case == "duplicate_input":
        command[3:3] = ["-v", command[6]]
    elif case == "other_output":
        command[8] = str(root / "C00002/drc") + ":/out:Z"
    elif case == "wrong_timeout":
        command[-1] = command[-1].replace("21600", "43200")
    elif case == "wrong_mode":
        command[-1] = command[-1].replace("run_mode=flat", "run_mode=deep")
    elif case == "wrong_threads":
        command[-1] = command[-1].replace("thr=4", "thr=40")
    elif case == "no_top":
        command[-1] = command[-1].replace("-rd topcell=real_top", "")
    elif case == "wrong_image":
        command[9] = "unrelated-checker:latest"
    elif case == "writable_deck":
        command[4] = command[4].replace(":/deck:ro,Z", ":/deck:Z")
    elif case == "no_deck":
        del command[3:5]
    elif case == "extra_shell":
        command[-1] += " && echo a-different-operation"
    with pytest.raises(RuntimeError):
        scale.classify_podman(command, root)


def test_classify_rejects_symlink_escape(tmp_path):
    root = tmp_path / "rerun_gf180_drcproj"
    root.mkdir()
    outside = tmp_path / "other_campaign/C00001"
    (outside / "backend/RUN_1/results").mkdir(parents=True)
    (root / "C00001").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError):
        scale.classify_podman(podman_command(root), root)


@pytest.mark.parametrize("text,expected", [
    ("Final result: Circuits match uniquely.\n", "clean"),
    ("Final result: Netlists match uniquely.\n", "clean"),
    ("Subcircuit logic: Circuits match uniquely.\n"
     "Final result: Top level cell failed pin matching.\n", "pin_mismatch"),
    ("Final result: Circuits match uniquely.\n"
     "Final result: Top level cell failed pin matching.\n", "pin_mismatch"),
    ("Final result: Circuits match uniquely.\n"
     "Final result: Netlists do not match.\n", "mismatch"),
    ("Final result: Circuits match uniquely.\n"
     "Final result: **MISMATCH**\n", "mismatch"),
    ("Final result: Circuits match uniquely.\n"
     "Final result: Circuits match with warnings.\n", "clean_with_warnings"),
    ("Circuits match uniquely.\n", ""),
    ("submodule says: Final result: Circuits match uniquely.\n", ""),
    ("Final result: Circuits match uniquely.\n"
     "Final result: Interrupted before top-level decision.\n", ""),
    ("Final result: Circuits match uniquely.\nFinal result:\n", ""),
])
def test_lvs_uses_last_final_top_level_result_only(text, expected):
    assert scale.final_lvs(text) == expected


@pytest.mark.parametrize("xml", [
    "<error/>",
    "<error>Checker failed before creating a report.</error>",
    "<report-database/>",
    "<report-database><cells/><categories/></report-database>",
])
def test_non_report_or_missing_item_container_is_not_clean(tmp_path, xml):
    report = tmp_path / "6_drc.lyrdb"
    report.write_text(xml)
    with pytest.raises(ValueError):
        scale.report_items(report)


def test_complete_empty_item_container_can_be_clean(tmp_path):
    report = tmp_path / "6_drc.lyrdb"
    report.write_text("<report-database><cells/><categories/><items/></report-database>")
    assert scale.report_items(report) == 0


@pytest.mark.skipif(not LINUX, reason="Requires Linux /proc and POSIX signals")
def test_pinned_signal_enosys_fallback_stops_resumes_and_terminates_own_child(monkeypatch):
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    attempts = []

    def unsupported_pidfd(pid):
        attempts.append(pid)
        raise OSError(errno.ENOSYS, "Test kernel has no pidfd support")

    try:
        identity = scale.proc(child.pid)
        monkeypatch.setattr(scale.os, "pidfd_open", unsupported_pidfd, raising=False)
        scale.pinned_signal(identity, signal.SIGSTOP)
        await_condition(lambda: scale.proc(child.pid)["state"] in ("T", "t"))
        scale.pinned_signal(identity, signal.SIGCONT)
        await_condition(lambda: scale.proc(child.pid)["state"] not in ("T", "t"))
        scale.pinned_signal(identity, signal.SIGTERM)
        assert child.wait(timeout=2) == -signal.SIGTERM
        assert attempts == [child.pid] * 3
    finally:
        if child.poll() is None:
            os.kill(child.pid, signal.SIGCONT)
            child.kill()
            child.wait(timeout=2)


@pytest.mark.skipif(not LINUX, reason="Requires Linux /proc and POSIX signals")
@pytest.mark.parametrize("changed", ["start", "ppid"])
def test_pinned_signal_enosys_does_not_signal_changed_identity(monkeypatch, changed):
    identity = scale.proc(os.getpid())
    supplied = dict(identity)
    supplied[changed] = str(int(identity[changed]) + 1) if changed == "start" else identity[changed] + 1

    def unsupported_pidfd(_):
        raise OSError(errno.ENOSYS, "Test kernel has no pidfd support")

    def forbidden_kill(*_):
        pytest.fail("Changed process identity must fail before issuing a signal")

    monkeypatch.setattr(scale.os, "pidfd_open", unsupported_pidfd, raising=False)
    monkeypatch.setattr(scale.os, "kill", forbidden_kill)
    with pytest.raises(RuntimeError, match="identity changed"):
        scale.pinned_signal(supplied, signal.SIGTERM)
