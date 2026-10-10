"""Inspect actual local capabilities without launching/installing a solver."""
import os
from pathlib import Path
import shutil
import sys
from .profiles import IMPELLER_PROFILES

def linux_cgroup_limits() -> dict:
    try:
        line = next((line for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::')))
        folder = Path('/sys/fs/cgroup') / line[3:].lstrip('/')
        memory = (folder / 'memory.max').read_text().strip()
        cpu = (folder / 'cpu.max').read_text().strip().split()
        tasks = (folder / 'pids.max').read_text().strip()
        return {'supported': memory != 'max' and cpu[0] != 'max' and os.access(folder, os.W_OK) and (tasks != 'max') and (0 < int(tasks) <= 32) and os.access(folder / 'cgroup.procs', os.W_OK) and (folder / 'cgroup.kill').is_file(), 'memoryBytes': int(memory) if memory != 'max' else None, 'maximumTasks': int(tasks) if tasks != 'max' else None, 'cpuCores': int(cpu[0]) / int(cpu[1]) if cpu[0] != 'max' else None, 'cgroup': str(folder)}
    except (OSError, StopIteration, ValueError):
        return {'supported': False}

def capability_report() -> dict:
    native_windows = False
    cgroup = linux_cgroup_limits() if sys.platform == 'linux' else {'supported': False}
    resource_supported = native_windows or cgroup['supported']
    executables = {name: shutil.which(name) for name in ('blockMesh', 'checkMesh', 'foamRun', 'snappyHexMesh', 'potentialFoam')}
    openfoam = sys.platform == 'linux' and all(executables.values()) and (os.environ.get('WM_PROJECT_VERSION') == '12')
    return {'schemaVersion': 'untirta.cfd-worker-capabilities.v1', 'mainApplicationDependency': False, 'resourceEnforcement': {'available': resource_supported, 'mode': 'windows-job-object' if native_windows else 'inherited-cgroup-v2' if cgroup['supported'] else 'unavailable', 'aggregateMemoryHardCap': resource_supported, 'singleJob': True, 'threads': 1, 'diskCap': '10-ms-watchdog-not-filesystem-hard-quota', 'maximumProcessesOrTasks': 32 if native_windows else cgroup.get('maximumTasks'), 'linuxInheritedLimits': cgroup}, 'adapters': {'openfoam': {'available': bool(openfoam and resource_supported), 'solverDetected': bool(openfoam), 'requiredVersion': 'Foundation-12', 'detectedVersion': os.environ.get('WM_PROJECT_VERSION'), 'reasonCode': 'CFD_READY_FOR_DEVELOPMENT_BENCHMARK' if openfoam and resource_supported else 'CFD_RESOURCE_CAP_REQUIRED' if openfoam else 'CFD_SOLVER_UNAVAILABLE', 'profiles': ['transient-cavity-v1', 'cavitation-bullet-v1', *IMPELLER_PROFILES], 'profileExecutables': {profile: {'createNonConformalCouples': shutil.which('createNonConformalCouples')} for profile in IMPELLER_PROFILES}, 'pumpCfdValidated': False}}, 'actualCfdRunVerified': False}
