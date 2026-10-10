"""Whole-tree RAM/CPU enforcement and cancellation; no user shell commands."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4
from .capabilities import linux_cgroup_limits
from .contracts import Limits, WorkerDiagnostic

def folder_size(folder: Path) -> int:
    total = 0
    for path in folder.rglob('*'):
        if path.is_symlink():
            raise WorkerDiagnostic('CFD_WORKSPACE_LINK_PROHIBITED', 'Use regular files inside the isolated worker workspace.')
        if path.is_file():
            total += path.stat().st_size
    return total

def wait_for_cgroup_empty(group: Path, timeout_seconds: float=5) -> None:
    """Wait for every descendant, using the kernel's hierarchical live-task flag."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            events = dict((line.split() for line in (group / 'cgroup.events').read_text().splitlines()))
            if events.get('populated') == '0':
                return
            if events.get('populated') != '1':
                raise ValueError('Missing native populated flag')
        except (OSError, ValueError):
            raise WorkerDiagnostic('CFD_RESOURCE_CAP_UNAVAILABLE', "Inspect the delegated cgroup's native populated flag; cleanup cannot be claimed without it.") from None
        if time.monotonic() >= deadline:
            raise WorkerDiagnostic('CFD_PROCESS_CLEANUP_INCOMPLETE', 'The isolated cgroup still contains live descendants after bounded cancellation; stop the dedicated scope before another solver stage.')
        time.sleep(0.01)

class ProcessRunner:
    """One sequential process tree. Disk watchdog may overshoot between polls."""

    def __init__(self, workspace: Path, limits: Limits, cancel_file: Path):
        self.workspace, self.limits, self.cancel_file = (workspace.resolve(), limits, cancel_file)
        self.deadline = time.monotonic() + limits.timeout_seconds
        self.win_job = None
        self.cgroup = None
        self.process = None
        caps = linux_cgroup_limits()
        if not caps['supported'] or caps['memoryBytes'] > limits.memory_bytes or caps['cpuCores'] > 1:
            raise WorkerDiagnostic('CFD_RESOURCE_CAP_REQUIRED', 'Run in a dedicated cgroup v2 scope with MemoryMax <= input limit, CPUQuota <=100% and TasksMax <=32.')
        try:
            self.cgroup = Path(caps['cgroup']) / ('solver-tree-' + uuid4().hex)
            self.cgroup.mkdir()
            if not os.access(self.cgroup / 'cgroup.procs', os.W_OK) or not (self.cgroup / 'cgroup.kill').is_file():
                raise OSError('The scope is not delegated.')
        except OSError:
            if self.cgroup and self.cgroup.exists():
                self.cgroup.rmdir()
            self.cgroup = None
            raise WorkerDiagnostic('CFD_RESOURCE_CAP_REQUIRED', 'The bounded cgroup must permit an isolated child group for cancellation of all descendants.') from None

    def stop(self) -> None:
        if self.cgroup:
            (self.cgroup / 'cgroup.kill').write_text('1')
            wait_for_cgroup_empty(self.cgroup)
        elif self.process:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.process:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    def close(self) -> None:
        self.stop()
        if self.cgroup:
            for _ in range(100):
                try:
                    self.cgroup.rmdir()
                    self.cgroup = None
                    break
                except OSError:
                    time.sleep(0.01)

    def run(self, argv: list[str], cwd: Path, log: Path) -> dict:
        """Internal adapter calls only; argv is never copied from user input."""
        if not cwd.resolve().is_relative_to(self.workspace):
            raise WorkerDiagnostic('CFD_WORKSPACE_ESCAPE', 'Solver stages must execute inside this run workspace.')
        if self.cancel_file.exists():
            raise WorkerDiagnostic('CFD_CANCELLED', 'The study was cancelled; input/reference remain intact.')
        if time.monotonic() >= self.deadline:
            raise WorkerDiagnostic('CFD_TIMEOUT', 'The whole-job time budget was reached; reduce only explicitly justified study scope.')
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith(('NPSH_', 'POSTGRES_')) and (not any((secret in key.upper() for secret in ('TOKEN', 'SECRET', 'PASSWORD', 'SUPABASE', 'DATABASE', 'CLOUDFLARE', 'GITHUB', 'API_KEY', 'SERVICE_ROLE', 'PRIVATE_KEY', 'ACCESS_KEY'))))}
        env.update({'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'PYTHONDONTWRITEBYTECODE': '1'})
        log.parent.mkdir(parents=True, exist_ok=True)
        gate = Path(__file__).with_name('process_gate.py')
        gate_python = sys.executable
        if not Path(gate_python).is_file():
            raise WorkerDiagnostic('CFD_RESOURCE_CAP_UNAVAILABLE', 'The native Python gate interpreter is unavailable; do not launch the solver.')
        with log.open('wb') as stream:
            self.process = subprocess.Popen([gate_python, '-B', str(gate)], cwd=cwd, stdin=subprocess.PIPE, stdout=stream, stderr=subprocess.STDOUT, env=env, shell=False, start_new_session=sys.platform != 'win32')
            try:
                if self.cgroup:
                    (self.cgroup / 'cgroup.procs').write_text(str(self.process.pid))
                self.process.stdin.write((json.dumps(argv) + '\n').encode())
                self.process.stdin.close()
                while self.process.poll() is None:
                    reason = None
                    if self.cancel_file.exists():
                        reason = ('CFD_CANCELLED', 'The study was cancelled; reload the preserved input or inspect the last complete checkpoint.')
                    elif time.monotonic() >= self.deadline:
                        reason = ('CFD_TIMEOUT', 'The entire setup/mesh/solve/export job reached its time limit.')
                    elif log.stat().st_size > self.limits.log_bytes:
                        reason = ('CFD_LOG_BUDGET_EXCEEDED', 'The solver log exceeded the declared budget; inspect convergence and setup.')
                    elif folder_size(self.workspace) > self.limits.disk_bytes:
                        reason = ('CFD_DISK_BUDGET_EXCEEDED', 'The output exceeded its local disk budget; preserve input and select an explicit supported case.')
                    if reason:
                        self.stop()
                        raise WorkerDiagnostic(*reason)
                    time.sleep(0.01)
                code = self.process.returncode
                self.stop()
                if log.stat().st_size > self.limits.log_bytes or folder_size(self.workspace) > self.limits.disk_bytes:
                    raise WorkerDiagnostic('CFD_OUTPUT_BUDGET_EXCEEDED', 'Output exceeded the declared budget during the last process interval.')
                if code != 0:
                    raise WorkerDiagnostic('CFD_SOLVER_PROCESS_STOPPED', 'Solver stage stopped; inspect its bounded local log and memory/license/model prerequisites.')
                return {'exitCode': code, 'log': log.relative_to(self.workspace).as_posix()}
            except BaseException:
                self.stop()
                raise
