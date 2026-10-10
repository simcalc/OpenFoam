import os
from pathlib import Path
import shutil
import sys

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

def capability_report():
    cgroup=linux_cgroup_limits() if sys.platform=='linux' else {'supported':False}
    executables={name:shutil.which(name) for name in ('blockMesh','checkMesh','foamRun','snappyHexMesh','potentialFoam')}
    solver=sys.platform=='linux' and all(executables.values()) and os.environ.get('WM_PROJECT_VERSION')=='12'
    bounded=bool(cgroup.get('supported'))
    return {'schemaVersion':'untirta.cfd-worker-capabilities.v1','mainApplicationDependency':False,
      'resourceEnforcement':{'available':bounded,'mode':'inherited-cgroup-v2' if bounded else 'unavailable',
        'aggregateMemoryHardCap':bounded,'singleJob':True,'threads':1,'diskCap':'10-ms-watchdog-not-filesystem-hard-quota',
        'maximumProcessesOrTasks':cgroup.get('maximumTasks'),'linuxInheritedLimits':cgroup},
      'adapters':{'openfoam':{'available':bool(solver and bounded),'solverDetected':bool(solver),
        'requiredVersion':'Foundation-12','detectedVersion':os.environ.get('WM_PROJECT_VERSION'),
        'reasonCode':'CFD_READY_FOR_DEVELOPMENT_BENCHMARK' if solver and bounded else 'CFD_RESOURCE_CAP_REQUIRED' if solver else 'CFD_SOLVER_UNAVAILABLE',
        'profiles':['transient-cavity-v1','cavitation-bullet-v1'],'pumpCfdValidated':False}},'actualCfdRunVerified':False}
