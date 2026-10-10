"""Version-pinned official development fixtures; never substitutes a Pump mesh."""
from __future__ import annotations
import math
from pathlib import Path
import re
import shutil
from .contracts import Job, WorkerDiagnostic, file_digest, write_json
from .checkpoint import build_checkpoint
from .resources import folder_size

def _descriptor(path: Path, workspace: Path, *, role=None):
    item = {'path': path.relative_to(workspace).as_posix(), 'sha256': file_digest(path), 'bytes': path.stat().st_size}
    if role:
        item['role'] = role
    return item

def save_initial_state(job: Job, workspace: Path, case: Path) -> None:
    """Save supplied native initial fields after meshing/initial potential flow.

    OpenFOAM initializes from its 0 directory; this snapshot is saved before the
    first physical step. It is not a solved initial-state or convergence claim.
    """
    names = ('U', 'p') if job.profile == 'transient-cavity-v1' else ('U', 'p_rgh', 'alpha.water')
    required = [case / '0' / name for name in names]
    if any((not path.is_file() or path.is_symlink() for path in required)):
        raise WorkerDiagnostic('CFD_INITIAL_STATE_REQUIRED', 'Provide every pinned native initial field before beginning physical time integration.')
    paths = sorted((path for path in (case / '0').iterdir() if path.is_file() and (not path.name.endswith('.orig'))))
    if folder_size(workspace) + sum((path.stat().st_size for path in paths)) > job.limits.disk_bytes:
        raise WorkerDiagnostic('CFD_DISK_LIMIT', 'The separate initialized-field snapshot would exceed the study disk budget.')
    destination = workspace / 'output' / 'native-initial-state' / '0'
    destination.mkdir(parents=True, exist_ok=False)
    for path in paths:
        shutil.copyfile(path, destination / path.name)

def _logged_seconds(line: str, label: str) -> float | None:
    """Foundation 12 logs SI seconds bare or with `s`; no other units convert."""
    prefix = '\\s*' + re.escape(label) + '\\s*=\\s*'
    if not re.match(prefix, line):
        return None
    number = '([+-]?(?:\\d+(?:\\.\\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?)'
    matched = re.fullmatch(prefix + number + '\\s*(?:s)?\\s*', line)
    if not matched:
        raise WorkerDiagnostic('CFD_NATIVE_TIME_TOKEN_INVALID', "Use a finite SI-second log value with an optional exact 's' suffix; unknown units and trailing tokens are rejected, not converted.")
    value = float(matched[1])
    if not math.isfinite(value):
        raise WorkerDiagnostic('CFD_NATIVE_TIME_TOKEN_INVALID', 'Native logged physical time must be finite SI seconds.')
    return value

def _time_states(solver_log: str, case: Path, profile: str, step_offset=0) -> list[dict]:
    fixed = None
    if profile == 'transient-cavity-v1':
        entry = re.findall('(?m)^\\s*deltaT\\s+([0-9.eE+-]+)\\s*;', (case / 'system' / 'controlDict').read_text())
        if len(entry) == 1:
            fixed = float(entry[0])
    current_dt, samples = (fixed, [])
    for line in solver_log.splitlines():
        dt_value = _logged_seconds(line, 'deltaT')
        value = _logged_seconds(line, 'Time')
        if dt_value is not None:
            if dt_value <= 0:
                raise WorkerDiagnostic('CFD_NATIVE_TIME_STEP_REQUIRED', 'The actual logged physical time step must be positive SI seconds.')
            current_dt = dt_value
        if value is not None:
            if current_dt is None or not math.isfinite(current_dt) or current_dt <= 0 or (not math.isfinite(value)) or (value <= 0):
                raise WorkerDiagnostic('CFD_NATIVE_TIME_STEP_REQUIRED', 'Use actual positive logged adaptive time steps or the pinned fixed time step; do not infer adaptive dt from output interval.')
            if samples and value <= samples[-1]['physicalTimeSeconds']:
                raise WorkerDiagnostic('CFD_NATIVE_TIME_SEQUENCE_INVALID', 'Each actual physical step must advance strictly beyond the previous logged time.')
            samples.append({'interpretation': 'physical', 'physicalTimeSeconds': value, 'timeStepSeconds': current_dt, 'step': step_offset + len(samples) + 1, 'iteration': None})
    return samples

def prepare_restart(job: Job, workspace: Path, case: Path) -> tuple[Path, dict]:
    """Clone an exact fixed development checkpoint; preserve parent native files."""
    if job.profile != 'transient-cavity-v1':
        raise WorkerDiagnostic('CFD_RESTART_PROFILE_INVALID', 'Only the pinned transient cavity has an approved fixed restart development profile.')
    times = [float(path.name) for path in case.iterdir() if path.is_dir() and re.fullmatch('\\d+(?:\\.\\d+)?(?:e[-+]?\\d+)?', path.name)]
    if not times or max(times) != 0.05:
        raise WorkerDiagnostic('CFD_RESTART_STATE_REQUIRED', 'Select the exact completed 0.05 s native cavity checkpoint.')
    fields = [case / '0.05' / name for name in ('U', 'p')]
    if any((not path.is_file() for path in fields)):
        raise WorkerDiagnostic('CFD_RESTART_STATE_REQUIRED', 'Restore the paired U/p native fields saved at exactly 0.05 s.')
    if folder_size(workspace) + folder_size(case) > job.limits.disk_bytes:
        raise WorkerDiagnostic('CFD_DISK_LIMIT', 'The separate restart copy would exceed the declared disk budget.')
    destination = workspace / 'output' / 'native-restart-case'
    shutil.copytree(case, destination)
    changes = {'startFrom': 'startTime', 'startTime': '0.05', 'endTime': '0.055', 'writeInterval': '1'}
    for name, value in changes.items():
        replace_entry(destination / 'system/controlDict', name, value)
    for path in fields:
        if file_digest(path) != file_digest(destination / '0.05' / path.name):
            raise WorkerDiagnostic('CFD_RESTART_PAIR_MISMATCH', 'The copied restart state must match the exact original paired field checksums.')
    original_files = [path for path in case.rglob('*') if path.is_file() and path.relative_to(case).as_posix() != 'system/controlDict']
    if any((file_digest(path) != file_digest(destination / path.relative_to(case)) for path in original_files)):
        raise WorkerDiagnostic('CFD_RESTART_SETUP_MISMATCH', 'Reuse the unchanged mesh, physics, initial and native fields; only documented restart time/write controls may change.')
    return (destination, {'sourceTimeSeconds': 0.05, 'sourceNativeFiles': [_descriptor(path, workspace) for path in fields], 'originalControlSha256': file_digest(case / 'system/controlDict'), 'restartControlSha256': file_digest(destination / 'system/controlDict'), 'restartOverrides': changes, 'source': 'https://doc.cfd.direct/openfoam/user-guide-v12/controldict', 'mainApplicationWrites': False})

def verify_restart_log(workspace: Path, start_time: float, end_time: float):
    text = (workspace / 'output/logs/restart-foamRun.log').read_text(errors='replace')
    created, times = ([], [])
    for line in text.splitlines():
        mesh_time = _logged_seconds(line, 'Create mesh for time')
        step_time = _logged_seconds(line, 'Time')
        _logged_seconds(line, 'deltaT')
        if mesh_time is not None:
            created.append(mesh_time)
        if step_time is not None:
            times.append(step_time)
    if len(created) != 1 or not math.isclose(created[0], start_time, abs_tol=1e-12) or (not times) or (min(times) <= start_time) or any((b <= a for a, b in zip(times, times[1:]))) or (not math.isclose(times[-1], end_time, abs_tol=1e-12)):
        raise WorkerDiagnostic('CFD_NATIVE_RESTART_UNVERIFIED', 'The actual solver must log loading the exact 0.05 s mesh/state and advancing beyond it to 0.055 s.')

def replace_entry(path: Path, name: str, value: str) -> None:
    content = path.read_text()
    content, count = re.subn('(?m)^(\\s*' + re.escape(name) + '\\s+)[^;]+;', lambda m: m[1] + value + ';', content)
    if count != 1:
        raise WorkerDiagnostic('CFD_FIXTURE_SETUP_INVALID', 'The pinned fixture setting is missing or duplicated.')
    path.write_text(content)

def verify_mesh_log(log: str) -> dict:
    """Require completed all-geometry/all-topology checks before fluid solve."""
    failed = re.search('(?m)^\\s*Failed\\s+\\d+\\s+mesh checks?\\.\\s*$', log)
    concave_cells = re.search('Concave cells[^\\n]*number of cells:\\s*[1-9]\\d*', log)
    concave_faces = re.search('There are\\s+[1-9]\\d*\\s+faces with concave angles', log)
    complete = len(re.findall('(?m)^\\s*Mesh OK\\.\\s*$', log)) == 1 and bool(re.search('(?m)^\\s*End\\s*$', log))
    full_checks = bool(re.search('(?m)^\\s*Enabling all \\(cell, face, edge, point\\) topology checks\\.\\s*$', log)) and bool(re.search('(?m)^\\s*Enabling all geometry checks\\.\\s*$', log))
    cells = re.findall('(?m)^\\s*cells:\\s*(\\d+)\\s*$', log)
    if failed or concave_cells or concave_faces or (not complete) or (not full_checks) or (len(cells) != 1) or (int(cells[0]) <= 0):
        raise WorkerDiagnostic('CFD_MESH_CHECK_FAILED', 'Repair the derived mesh and rerun checkMesh -allGeometry -allTopology to a complete Mesh OK result without concave cells/faces; no potential-flow or physical solver stage may follow a failed mesh.')
    return {'passed': True, 'cellCount': int(cells[0]), 'checks': ['allGeometry', 'allTopology'], 'meshCheck': 'Mesh OK'}

def prepare(job: Job, workspace: Path, fixture_root: Path) -> tuple[Path, list[list[str]], dict]:
    import json
    provenance = json.loads((fixture_root / 'provenance.json').read_text())
    source = fixture_root / job.profile
    for item in provenance['files']:
        if item['path'].startswith(job.profile + '/') and file_digest(fixture_root / item['path']) != item['sha256']:
            raise WorkerDiagnostic('CFD_FIXTURE_CHECKSUM_INVALID', 'Restore the pinned official development fixture before execution.')
    case = workspace / 'output' / 'native-case'
    shutil.copytree(source, case)
    control = case / 'system' / 'controlDict'
    changes = {'writeFormat': 'ascii', 'runTimeModifiable': 'false', 'purgeWrite': '0'}
    stages = [['blockMesh', '-case', str(case)]]
    if job.profile == 'transient-cavity-v1':
        changes.update({'endTime': '0.05', 'deltaT': '0.005', 'writeInterval': '5'})
        physical_label = 'physical-transient-single-phase-development-benchmark'
    else:
        changes.update({'endTime': '0.00001', 'writeInterval': '0.000005', 'maxCo': '1', 'maxAlphaCo': '0.5'})
        snappy = case / 'system' / 'snappyHexMeshDict'
        replace_entry(snappy, 'maxGlobalCells', '100000')
        replace_entry(snappy, 'maxLocalCells', '100000')
        replace_entry(snappy, 'snap', 'false')
        replace_entry(snappy, 'addLayers', 'false')
        original = snappy.read_text()
        changed = original.replace('level (4 4);', 'level (0 0);').replace('levels ((0.0 3));', 'levels ((0.0 0));')
        if changed == original:
            raise WorkerDiagnostic('CFD_FIXTURE_SETUP_INVALID', 'The expected development mesh profile was not found.')
        snappy.write_text(changed)
        for name in ('U', 'p_rgh'):
            shutil.copyfile(case / '0' / (name + '.orig'), case / '0' / name)
        stages += [['snappyHexMesh', '-overwrite', '-case', str(case)]]
        physical_label = 'physical-transient-cavitation-development-benchmark'
    for name, value in changes.items():
        replace_entry(control, name, value)
    stages += [['checkMesh', '-allGeometry', '-allTopology', '-case', str(case)]]
    if job.profile == 'cavitation-bullet-v1':
        stages += [['potentialFoam', '-pName', 'p_rgh', '-case', str(case)]]
    stages += [['foamRun', '-case', str(case)]]
    metadata = {'distribution': 'OpenFOAM-Foundation', 'version': '12', 'sourceCommit': provenance['commit'], 'profile': job.profile, 'physicalModel': physical_label, 'benchmarkOverrides': changes, 'meshOverride': {'surfaceLevel': 0, 'regionLevel': 0, 'maxCells': 100000, 'snap': False, 'addLayers': False, 'meshProfileRevision': 'castellated-staircase-uniform-v3', 'geometryApproximation': 'castellated-staircase'} if job.profile == 'cavitation-bullet-v1' else None, 'meshLimitations': ['Castellated staircase cells approximate the immutable STL boundary; no exact smooth surface geometry claim.', 'Unchanged native 15 x 15 x 50 background, no local refinement: approximately five cells across the source 0.01 m bullet diameter.', 'No snapping or boundary layers; coarse development mesh is not mesh-independent or suitable for quantitative geometry, cavitation-model or Pump acceptance claims.'] if job.profile == 'cavitation-bullet-v1' else [], 'quantityClaim': 'development-only; no Pump performance, NPSHR, NPSH3 or engineering acceptance claim'}
    return (case, stages, metadata)

def field_dimensions(text: str) -> str | None:
    """Decode one explicit native dimension set without changing field values.

    Foundation 12 dimensionSetIO.C writes the dimensionless set as []; the
    other reviewed field quantities retain seven SI base exponents. Empty
    brackets mean zero exponents, never missing or inferred pressure units.
    """
    body = re.sub('/\\*.*?\\*/|//[^\\n]*', '', text, flags=re.DOTALL)
    declarations = re.findall('(?m)^\\s*dimensions\\s+\\[([^\\[\\];\\r\\n]*)\\]\\s*;', body)
    if len(declarations) != 1 or len(re.findall('\\bdimensions\\b', body)) != 1:
        return None
    value = ' '.join(declarations[0].split())
    if len(value) > 128:
        return None
    if not value:
        return '0 0 0 0 0 0 0'
    if not re.fullmatch('[+-]?\\d+(?:\\s+[+-]?\\d+){6}', value):
        return None
    return ' '.join((str(int(exponent)) for exponent in value.split()))

def field_values(path: Path) -> list[float]:
    """Read finite ASCII internal field numbers, excluding header/dimensions."""
    text = path.read_text(encoding='utf-8')
    if re.search('\\b(?:nan|inf|infinity)\\b', text, re.IGNORECASE):
        raise WorkerDiagnostic('CFD_NONFINITE_FIELD', 'The solver field contains a nonfinite value; check numerics and physical setup.')
    match = re.search('internalField\\s+(?:nonuniform\\s+List<\\w+>\\s+\\d+\\s*\\((.*?)\\)\\s*;|uniform\\s+([^;]+);)', text, re.DOTALL)
    if not match:
        raise WorkerDiagnostic('CFD_NATIVE_FIELD_INVALID', 'Expected a supported finite ASCII native field.')
    values = [float(value) for value in re.findall('[-+]?(?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][-+]?\\d+)?', match[1] or match[2])]
    if not values or not all((math.isfinite(value) for value in values)):
        raise WorkerDiagnostic('CFD_NATIVE_FIELD_INVALID', 'The native field does not contain finite solution values.')
    return values

def collect(job: Job, workspace: Path, case: Path, *, log_name='foamRun.log', target_time=None, minimum_time=0, checkpoint_prefix='', step_offset=0) -> dict:
    solver_log = (workspace / 'output' / 'logs' / log_name).read_text(errors='replace')
    mesh_log = (workspace / 'output' / 'logs' / 'checkMesh.log').read_text(errors='replace')
    verify_mesh_log(mesh_log)
    if not re.search('(?m)^End\\s*$', solver_log):
        raise WorkerDiagnostic('CFD_NUMERICAL_RUN_INCOMPLETE', 'The solver or mesh acceptance did not complete; inspect stage logs.')
    folders = sorted((p for p in case.iterdir() if p.is_dir() and re.fullmatch('\\d+(?:\\.\\d+)?(?:e[-+]?\\d+)?', p.name)), key=lambda p: float(p.name))
    solved = [folder for folder in folders if float(folder.name) > minimum_time]
    target = target_time if target_time is not None else 0.05 if job.profile == 'transient-cavity-v1' else 1e-05
    if not solved or not math.isclose(float(solved[-1].name), target, rel_tol=1e-05, abs_tol=1e-12):
        raise WorkerDiagnostic('CFD_PHYSICAL_TIME_INCOMPLETE', "The final native checkpoint did not reach the profile's physical end time.")
    fields = ('U', 'p') if job.profile == 'transient-cavity-v1' else ('U', 'p_rgh', 'alpha.water')
    units = {'U': 'm/s (vector components)', 'p': 'm2/s2 (kinematic pressure)', 'p_rgh': 'Pa (hydrostatic-reduced pressure)', 'alpha.water': '1 (liquid fraction)'}
    dimensions = {'U': '0 1 -1 0 0 0 0', 'p': '0 2 -2 0 0 0 0', 'p_rgh': '1 -1 -2 0 0 0 0', 'alpha.water': '0 0 0 0 0 0 0'}
    checkpoints, native_checkpoint_files, summaries = ([], [], {})
    time_states = _time_states(solver_log, case, job.profile, step_offset)
    times = [item['physicalTimeSeconds'] for item in time_states]
    for folder in solved:
        files = []
        for name in fields:
            path = folder / name
            if not path.is_file():
                raise WorkerDiagnostic('CFD_CHECKPOINT_INCOMPLETE', 'A physical-time checkpoint is missing required paired native fields.')
            values = field_values(path)
            if field_dimensions(path.read_text()) != dimensions[name]:
                raise WorkerDiagnostic('CFD_FIELD_UNIT_MISMATCH', 'Native ' + name + ' dimensions must match the selected solver quantity; pressure and kinematic pressure are distinct, and [] is dimensionless only.')
            if name == 'alpha.water' and (min(values) < -1e-06 or max(values) > 1 + 1e-06):
                raise WorkerDiagnostic('CFD_PHASE_FRACTION_OUTSIDE_DOMAIN', 'Liquid fraction must remain inside [0,1] within the declared tolerance.')
            files.append({'path': path.relative_to(workspace).as_posix(), 'sha256': file_digest(path), 'bytes': path.stat().st_size})
            if folder == solved[-1]:
                summaries[name] = {'minimum': min(values), 'maximum': max(values), 'scalarComponentCount': len(values), 'unit': units[name]}
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.name not in fields:
                files.append(_descriptor(path, workspace))
        checkpoint = {'schemaVersion': 'untirta.cfd-native-checkpoint.v1', **job.binding(), 'physicalTimeSeconds': float(folder.name), 'timeInterpretation': 'physical', 'meshPosition': 'stationary', 'step': next((step_offset + i + 1 for i, value in enumerate(times) if math.isclose(value, float(folder.name), rel_tol=1e-08, abs_tol=1e-12)), None), 'files': files, 'setupFiles': [{'path': path.relative_to(workspace).as_posix(), 'sha256': file_digest(path)} for path in sorted(case.rglob('*')) if path.is_file() and ('system' in path.relative_to(case).parts or 'constant' in path.relative_to(case).parts)]}
        destination = workspace / 'checkpoints' / (checkpoint_prefix + folder.name + '.json')
        write_json(destination, checkpoint)
        checkpoints.append(destination.relative_to(workspace).as_posix())
        state = next((item for item in time_states if math.isclose(item['physicalTimeSeconds'], float(folder.name), rel_tol=1e-08, abs_tol=1e-12)), None)
        if state is None:
            raise WorkerDiagnostic('CFD_NATIVE_TIME_STEP_REQUIRED', 'Pair each saved output with the exact logged physical time, step and time step.')
        mesh_paths = sorted((path for path in (case / 'constant' / 'polyMesh').rglob('*') if path.is_file()))
        settings_paths = sorted((path for path in case.rglob('*') if path.is_file() and ('system' in path.relative_to(case).parts or 'constant' in path.relative_to(case).parts) and ('polyMesh' not in path.relative_to(case).parts) and ('geometry' not in path.relative_to(case).parts)))
        settings_paths += sorted((path for path in (folder / 'uniform').rglob('*') if path.is_file()))
        initial_paths = sorted((workspace / 'output' / 'native-initial-state' / '0').glob('*'))
        native = build_checkpoint(binding=job.binding(), solver={'adapter': 'openfoam', 'distribution': 'OpenFOAM-Foundation', 'version': '12'}, method={'id': job.profile, 'rotationModel': 'stationary', 'physicalModel': 'single-phase-incompressible' if job.profile == 'transient-cavity-v1' else 'liquid-vapour-cavitation'}, time_state=state, motion={'mode': 'stationary', 'rotorAngleRad': None, 'angularSpeedRadS': None, 'axis': None, 'origin': None, 'motionLawSha256': None}, interface={'type': 'none', 'identifiers': [], 'configurationSha256': None}, native_files=[dict(item, role='field') for item in files], mesh_files=[_descriptor(path, workspace) for path in mesh_paths], settings_files=[_descriptor(path, workspace) for path in settings_paths], setup_state={'initialStateSavedBeforePreview': True, 'initialStateFiles': [_descriptor(path, workspace) for path in initial_paths], 'sequence': ['setup-saved', 'initialized', 'initial-state-saved', 'solved', 'checkpoint-saved']}, checkpoint_id='native-' + checkpoint_prefix + folder.name)
        native_path = workspace / 'checkpoints' / ('native-' + checkpoint_prefix + folder.name + '.json')
        write_json(native_path, native)
        native_checkpoint_files.append(native_path.relative_to(workspace).as_posix())
    return {'actualCfdRun': True, 'numericalSmokePassed': True, 'engineeringValidated': False, 'physicalTimeSeconds': float(solved[-1].name), 'checkpoints': checkpoints, 'nativeCheckpointFiles': native_checkpoint_files, 'fieldSummaries': summaries, 'acceptanceScope': 'real mesh/solve/finite-field/time-bound-checkpoint development smoke only', 'remainingEngineeringChecks': ['mesh independence', 'temporal convergence', 'conservation convergence', 'exact Pump geometry and conditions', 'NPSH head-drop criterion']}
