"""Native CFD restart bindings; file integrity never establishes solver authority.

OpenFOAM time directories and Fluent case/data pairs are inspected without running
their contents.  All public validators return bounded, JSON-safe diagnostics.
"""
from __future__ import annotations
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import PurePosixPath
import re
from typing import Any
from xml.sax.saxutils import escape
from .contracts import WorkerDiagnostic, digest
SCHEMA = 'untirta.cfd-native-checkpoint.v2'
BINDING_KEYS = {'studyId', 'inputRevision', 'inputSha256', 'referenceSha256'}
SHA = re.compile('[0-9a-f]{64}')
ID = re.compile('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}')

def fail(code: str, action: str) -> None:
    raise WorkerDiagnostic(code, action)

def diagnostic(error: WorkerDiagnostic) -> dict:
    return {'status': 'InputRequired', 'valid': False, 'reasonCode': error.code, 'diagnostics': [{'reasonCode': error.code, 'correctiveAction': error.action}], 'restartCompatible': False, 'actualCfdRunVerified': False, 'actualRestartExecutionVerified': False, 'calculationFreshness': 'Unavailable', 'mainApplicationWrites': False}

def strict_json(raw: bytes) -> Any:

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                fail('CFD_JSON_DUPLICATE_KEY', 'Use JSON with unique object keys.')
            result[key] = value
        return result
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=lambda _: fail('CFD_JSON_NONFINITE', 'Use finite JSON numbers.'))
    except WorkerDiagnostic:
        raise
    except (ValueError, UnicodeError, RecursionError):
        fail('CFD_JSON_INVALID', 'Import a bounded UTF-8 JSON document with finite numbers.')

def validate_binding(binding: Any, expected: dict | None=None) -> dict:
    if not isinstance(binding, dict) or not BINDING_KEYS.issubset(binding) or len(binding) > 16:
        fail('CFD_BINDING_REQUIRED', 'Bind the artifact to the independent study ID, input revision and input/reference checksums.')
    for key, value in binding.items():
        if key in ('studyId', 'inputRevision'):
            if not isinstance(value, str) or not ID.fullmatch(value):
                fail('CFD_BINDING_INVALID', 'Use bounded study and revision identifiers without path characters.')
        elif key.endswith('Sha256'):
            if not isinstance(value, str) or not SHA.fullmatch(value):
                fail('CFD_BINDING_INVALID', 'Provide exact lowercase SHA-256 binding values.')
        else:
            fail('CFD_BINDING_INVALID', 'Use only study/revision identifiers and explicitly named SHA-256 bindings.')
    if expected is not None:
        validate_binding(expected)
        if binding != expected:
            fail('CFD_BINDING_MISMATCH', 'Import results from the exact current independent input revision and reference snapshot.')
    return deepcopy(binding)

def number(value: Any, *, positive=False, nonnegative=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or (not math.isfinite(value)):
        fail('CFD_CHECKPOINT_NUMBER_INVALID', 'Provide finite checkpoint quantities in the declared SI units.')
    if positive and value <= 0 or (nonnegative and value < 0):
        fail('CFD_CHECKPOINT_NUMBER_INVALID', 'Use positive time steps and nonnegative physical time or iteration counts.')
    return float(value)

def integer(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        fail('CFD_CHECKPOINT_STEP_INVALID', 'Supply the actual nonnegative native solver step or iteration.')
    return value

def safe_path(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 240 or (not value) or ('\\' in value) or (':' in value):
        fail('CFD_ARTIFACT_PATH_INVALID', 'Use relative POSIX paths inside the independent artifact.')
    if value.startswith('/') or any((ord(c) < 32 or ord(c) == 127 for c in value)):
        fail('CFD_ARTIFACT_PATH_INVALID', 'Remove absolute paths and control characters from the artifact.')
    parts = value.split('/')
    if any((not part or part in ('.', '..') or part.endswith(('.', ' ')) or re.search('[<>"|?*]', part) or re.fullmatch('(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\\..*)?', part, re.I) for part in parts)):
        fail('CFD_ARTIFACT_PATH_INVALID', 'Remove path traversal, reserved filenames and ambiguous path components.')
    return value

def file_descriptors(items: Any, files: dict[str, bytes] | None, *, roles=False) -> list[dict]:
    if not isinstance(items, list) or not items or len(items) > 2048:
        fail('CFD_CHECKPOINT_FILES_REQUIRED', 'Include the exact native, mesh, settings and initial-state file descriptors.')
    seen, output = (set(), [])
    for item in items:
        keys = {'path', 'sha256', 'bytes'} | ({'role'} if roles else set())
        if not isinstance(item, dict) or set(item) != keys:
            fail('CFD_CHECKPOINT_FILE_INVALID', 'Describe every native file with its relative path, byte count, SHA-256 and native role.')
        path = safe_path(item['path'])
        if path.casefold() in seen or not isinstance(item['sha256'], str) or (not SHA.fullmatch(item['sha256'])):
            fail('CFD_CHECKPOINT_FILE_INVALID', 'Use unique exact file descriptors with lowercase SHA-256 checksums.')
        seen.add(path.casefold())
        integer(item['bytes'])
        if roles and item['role'] not in ('case', 'data', 'field'):
            fail('CFD_CHECKPOINT_FILE_INVALID', 'Identify native solution files as case, data or field.')
        if files is not None:
            raw = files.get(path)
            if raw is None or len(raw) != item['bytes'] or sha256(raw).hexdigest() != item['sha256']:
                fail('CFD_CHECKPOINT_CHECKSUM_MISMATCH', 'Restore the exact paired native files from this checkpoint; do not mix runs or mesh positions.')
        output.append(deepcopy(item))
    return output

def build_checkpoint(*, binding: dict, solver: dict, method: dict, time_state: dict, motion: dict, interface: dict, native_files: list[dict], mesh_files: list[dict], settings_files: list[dict], setup_state: dict, evidence_role='actual-solver', checkpoint_id='native-checkpoint') -> dict:
    """Build exact metadata from adapter-owned native operands; does not invent them."""
    data = {'schemaVersion': SCHEMA, 'checkpointId': checkpoint_id, 'binding': deepcopy(binding), 'solver': deepcopy(solver), 'method': deepcopy(method), 'timeState': deepcopy(time_state), 'motion': deepcopy(motion), 'interface': deepcopy(interface), 'nativeFiles': deepcopy(native_files), 'meshFiles': deepcopy(mesh_files), 'settingsFiles': deepcopy(settings_files), 'setupState': deepcopy(setup_state), 'evidenceRole': evidence_role, 'nativePairSha256': digest(native_files), 'meshSha256': digest(mesh_files), 'settingsSha256': digest(settings_files)}
    data['meshPositionSha256'] = digest({'meshSha256': data['meshSha256'], 'motion': motion})
    data['checkpointChecksum'] = digest(data)
    _validate(data, binding=binding, files=None, expected_restart=None)
    return data

def _validate(data: Any, *, binding: dict, files: dict[str, bytes] | None, expected_restart: dict | None) -> dict:
    if files is not None and (not isinstance(files, dict) or any((not isinstance(k, str) or not isinstance(v, bytes) for k, v in files.items()))):
        fail('CFD_CHECKPOINT_FILES_REQUIRED', 'Supply a bounded map of relative native file paths to inert bytes.')
    if isinstance(data, dict) and data.get('schemaVersion') == 'untirta.cfd-native-checkpoint.v1':
        fail('CFD_CHECKPOINT_LEGACY_REVIEW_REQUIRED', 'Re-export a v2 checkpoint with exact solver, time-step, mesh-position, motion/interface and initial-state bindings.')
    keys = {'schemaVersion', 'checkpointId', 'binding', 'solver', 'method', 'timeState', 'motion', 'interface', 'nativeFiles', 'meshFiles', 'settingsFiles', 'setupState', 'evidenceRole', 'nativePairSha256', 'meshSha256', 'settingsSha256', 'meshPositionSha256', 'checkpointChecksum'}
    if not isinstance(data, dict) or set(data) != keys or data.get('schemaVersion') != SCHEMA:
        fail('CFD_CHECKPOINT_SCHEMA_INVALID', 'Import a complete untirta.cfd-native-checkpoint.v2 descriptor.')
    validate_binding(data['binding'], binding)
    if not isinstance(data['checkpointId'], str) or not ID.fullmatch(data['checkpointId']):
        fail('CFD_CHECKPOINT_ID_INVALID', 'Use a bounded native checkpoint identifier.')
    if data['evidenceRole'] not in ('actual-solver', 'contract-test'):
        fail('CFD_EVIDENCE_ROLE_INVALID', 'Distinguish real solver evidence from educational contract tests.')
    checksum = dict(data)
    checksum.pop('checkpointChecksum')
    if data['checkpointChecksum'] != digest(checksum):
        fail('CFD_CHECKPOINT_DESCRIPTOR_CHECKSUM_MISMATCH', 'Restore the unchanged native checkpoint descriptor.')
    solver, method = (data['solver'], data['method'])
    if any((not isinstance(solver[key], str) or not solver[key] or len(solver[key]) > 100 for key in solver)):
        fail('CFD_CHECKPOINT_SOLVER_INVALID', 'Provide bounded solver identity and version strings.')
    if not isinstance(method, dict) or set(method) != {'id', 'rotationModel', 'physicalModel'} or method['rotationModel'] not in ('stationary', 'mrf', 'sliding-mesh'):
        fail('CFD_CHECKPOINT_METHOD_INVALID', 'Declare stationary, MRF or physical sliding-mesh treatment explicitly.')
    if any((not isinstance(method[k], str) or not method[k] or len(method[k]) > 160 for k in method)):
        fail('CFD_CHECKPOINT_METHOD_INVALID', 'Provide bounded method and physical-model identifiers.')
    native = file_descriptors(data['nativeFiles'], files, roles=True)
    mesh = file_descriptors(data['meshFiles'], files)
    settings = file_descriptors(data['settingsFiles'], files)
    for key, values in (('nativePairSha256', native), ('meshSha256', mesh), ('settingsSha256', settings)):
        if data[key] != digest(values):
            fail('CFD_CHECKPOINT_PAIR_MISMATCH', 'Keep native solution, mesh and settings descriptor groups unchanged and paired.')
    motion, interface, time_state = (data['motion'], data['interface'], data['timeState'])
    motion_keys = {'mode', 'rotorAngleRad', 'angularSpeedRadS', 'axis', 'origin', 'motionLawSha256'}
    if not isinstance(motion, dict) or set(motion) != motion_keys or motion['mode'] != method['rotationModel']:
        fail('CFD_CHECKPOINT_MOTION_REQUIRED', 'Bind the exact rotor position, motion law and mesh treatment to this native state.')
    if motion['mode'] == 'stationary':
        if any((motion[key] is not None for key in motion_keys - {'mode'})):
            fail('CFD_CHECKPOINT_MOTION_MISMATCH', 'A stationary mesh must not carry an undeclared rotor motion.')
    else:
        number(motion['angularSpeedRadS'])
        if not isinstance(motion['motionLawSha256'], str) or not SHA.fullmatch(motion['motionLawSha256']):
            fail('CFD_CHECKPOINT_MOTION_REQUIRED', 'Bind the exact rotor motion law checksum.')
        for name in ('axis', 'origin'):
            if not isinstance(motion[name], list) or len(motion[name]) != 3:
                fail('CFD_CHECKPOINT_MOTION_REQUIRED', 'Provide the exact three-component rotor axis and origin in SI coordinates.')
            for value in motion[name]:
                number(value)
        if not math.isclose(sum((v * v for v in motion['axis'])), 1, rel_tol=1e-08, abs_tol=1e-08):
            fail('CFD_CHECKPOINT_AXIS_INVALID', 'Supply a unit rotor-axis vector from the saved solver setup.')
        if motion['mode'] == 'sliding-mesh':
            number(motion['rotorAngleRad'])
        elif motion['rotorAngleRad'] is not None:
            fail('CFD_CHECKPOINT_MRF_TIME_MISMATCH', 'MRF has a fixed mesh; do not label a rotor angle as physical mesh motion.')
    if data['meshPositionSha256'] != digest({'meshSha256': data['meshSha256'], 'motion': motion}):
        fail('CFD_CHECKPOINT_MESH_POSITION_MISMATCH', 'Use the mesh and rotor position saved at this exact native time step.')
    if not isinstance(interface, dict) or set(interface) != {'type', 'identifiers', 'configurationSha256'} or interface['type'] not in ('none', 'AMI', 'NCC', 'sliding', 'turbo'):
        fail('CFD_CHECKPOINT_INTERFACE_REQUIRED', 'Declare the exact rotor/stator interface configuration.')
    if not isinstance(interface['identifiers'], list) or len(interface['identifiers']) > 64 or any((not isinstance(v, str) or not v or len(v) > 120 for v in interface['identifiers'])):
        fail('CFD_CHECKPOINT_INTERFACE_REQUIRED', 'Use bounded actual interface identifiers.')
    if interface['type'] == 'NCC':
        from .profiles import IMPELLER_PROFILES, IMPELLER_NCC_DICT_SHA256
        names = ['nonConformalCouple' + side + str(angle) for side in ('In', 'Out') for angle in range(0, 360, 30)]
        expected_mode = 'sliding-mesh' if method['id'] == 'impeller-transient-v1' else 'mrf'
        if solver != {'adapter': 'openfoam', 'distribution': 'OpenFOAM-Foundation', 'version': '12'} or method['id'] not in IMPELLER_PROFILES or method['rotationModel'] != expected_mode or (interface['identifiers'] != names) or (interface['configurationSha256'] != IMPELLER_NCC_DICT_SHA256):
            fail('CFD_CHECKPOINT_NCC_PROFILE_MISMATCH', 'NCC currently requires the exact reviewed Foundation 12 impeller method, source coupling configuration and rotor frame; it cannot be relabeled as AMI or an arbitrary solver interface.')
    if interface['type'] == 'none':
        if interface['identifiers'] or interface['configurationSha256'] is not None or motion['mode'] == 'sliding-mesh':
            fail('CFD_CHECKPOINT_INTERFACE_REQUIRED', 'Physical sliding mesh needs a saved rotor/stator interface binding.')
    elif not interface['identifiers'] or not isinstance(interface['configurationSha256'], str) or (not SHA.fullmatch(interface['configurationSha256'])):
        fail('CFD_CHECKPOINT_INTERFACE_REQUIRED', 'Include exact interface identifiers and its configuration checksum.')
    if not isinstance(time_state, dict) or set(time_state) != {'interpretation', 'physicalTimeSeconds', 'timeStepSeconds', 'step', 'iteration'}:
        fail('CFD_CHECKPOINT_TIME_REQUIRED', 'Supply explicit native physical time, time step and step; distinguish steady iterations.')
    if time_state['interpretation'] == 'physical':
        physical_time = number(time_state['physicalTimeSeconds'], nonnegative=True)
        number(time_state['timeStepSeconds'], positive=True)
        integer(time_state['step'])
        if time_state['iteration'] is not None:
            fail('CFD_CHECKPOINT_TIME_MISMATCH', 'Physical step and steady iteration must not be mixed.')
        if solver['adapter'] == 'openfoam':
            if any((item['role'] != 'field' for item in native)) or len(native) < 2:
                fail('CFD_CHECKPOINT_PAIR_REQUIRED', 'Include every required OpenFOAM native solution field from the same physical-time directory.')
            parents = {str(PurePosixPath(item['path']).parent) for item in native}
            try:
                matching_time = len(parents) == 1 and float(PurePosixPath(next(iter(parents))).name) == physical_time
            except ValueError:
                matching_time = False
            if not matching_time:
                fail('CFD_CHECKPOINT_TIME_MISMATCH', 'Pair OpenFOAM fields from the exact same physical-time directory.')
    elif time_state['interpretation'] == 'steady-iteration':
        integer(time_state['iteration'])
        if any((time_state[k] is not None for k in ('physicalTimeSeconds', 'timeStepSeconds', 'step'))) or motion['mode'] == 'sliding-mesh':
            fail('CFD_CHECKPOINT_TIME_MISMATCH', 'Pseudo-time/steady iterations are not physical transient time or sliding mesh.')
    else:
        fail('CFD_CHECKPOINT_TIME_REQUIRED', 'Identify physical time or steady iteration explicitly.')
    if motion['mode'] == 'sliding-mesh':
        points = [item for item in mesh if PurePosixPath(item['path']).name == 'points']
        try:
            exact_position = len(points) == 1 and float(PurePosixPath(points[0]['path']).parent.parent.name) == time_state['physicalTimeSeconds']
        except ValueError:
            exact_position = False
        if not exact_position:
            fail('CFD_CHECKPOINT_MOVED_MESH_REQUIRED', 'Include the OpenFOAM moved mesh points from this exact physical-time directory; initial constant points cannot substitute.')
    setup = data['setupState']
    if not isinstance(setup, dict) or set(setup) != {'initialStateSavedBeforePreview', 'initialStateFiles', 'sequence'} or setup['initialStateSavedBeforePreview'] is not True:
        fail('CFD_INITIAL_STATE_SAVE_REQUIRED', 'Save the initialized native state before any mesh-motion preview, then save each solve checkpoint.')
    initial_files = file_descriptors(setup['initialStateFiles'], files)
    sequence = setup['sequence']
    allowed_events = {'setup-saved', 'initialized', 'initial-state-saved', 'mesh-preview', 'solved', 'checkpoint-saved', 'native-reopened'}
    if not isinstance(sequence, list) or len(sequence) > 32 or any((v not in allowed_events for v in sequence)):
        fail('CFD_CHECKPOINT_LIFECYCLE_INVALID', 'Record bounded ordered native setup, initialization, initial save, preview and solve events.')
    try:
        if not sequence.index('setup-saved') < sequence.index('initialized') < sequence.index('initial-state-saved') < sequence.index('solved') < sequence.index('checkpoint-saved'):
            raise ValueError()
        if 'mesh-preview' in sequence and sequence.index('mesh-preview') < sequence.index('initial-state-saved'):
            raise ValueError()
    except ValueError:
        fail('CFD_INITIAL_STATE_SAVE_REQUIRED', 'Save setup, initialize, save initial state, optionally preview, solve and save the checkpoint in that order.')
    restart = {'binding': data['binding'], 'solver': solver, 'method': method, 'timeState': time_state, 'meshSha256': data['meshSha256'], 'settingsSha256': data['settingsSha256'], 'nativePairSha256': data['nativePairSha256'], 'meshPositionSha256': data['meshPositionSha256'], 'motion': motion, 'interface': interface}
    if expected_restart is not None and expected_restart != restart:
        fail('CFD_RESTART_STATE_MISMATCH', 'Select the exact saved time, step, rotor position, paired solution, mesh, solver and settings; do not combine checkpoints.')
    return {'status': 'CheckpointIntegrityVerified', 'valid': True, 'reasonCode': 'CFD_CHECKPOINT_INTEGRITY_VERIFIED', 'diagnostics': [], 'checkpoint': deepcopy(data), 'restartBinding': deepcopy(restart), 'restartCompatible': files is not None, 'restartExecutionPermitted': False, 'nativeFormatParsed': False, 'actualCfdRunVerified': False, 'actualRestartExecutionVerified': False, 'authenticityVerified': False, 'calculationFreshness': 'Imported Evidence', 'mainApplicationWrites': False, 'claim': 'Integrity and restart-state compatibility only; native reopen/restart and engineering convergence require solver execution.'}

def validate_checkpoint_descriptor(descriptor: Any, *, binding: dict, files: dict[str, bytes] | None=None, expected_restart: dict | None=None) -> dict:
    try:
        return _validate(descriptor, binding=binding, files=files, expected_restart=expected_restart)
    except WorkerDiagnostic as error:
        return diagnostic(error)
    except (ValueError, TypeError, OverflowError, RecursionError):
        return diagnostic(WorkerDiagnostic('CFD_CHECKPOINT_SCHEMA_INVALID', 'Import a complete bounded checkpoint with finite native operands.'))

def validate_time_history(data: Any, *, binding: dict, files: dict[str, bytes] | None=None) -> dict:
    """Inspect physical samples, units and source files; never substitute steady iterations."""
    try:
        keys = {'schemaVersion', 'binding', 'evidenceRole', 'timeUnit', 'quantities', 'samples', 'sourceFiles'}
        if not isinstance(data, dict) or set(data) != keys or data['schemaVersion'] != 'untirta.cfd-time-history.v1' or (data['timeUnit'] != 's'):
            fail('CFD_TIME_HISTORY_SCHEMA_INVALID', 'Provide a physical CFD time history in seconds with explicit quantity units and native source checksums.')
        validate_binding(data['binding'], binding)
        if data['evidenceRole'] not in ('actual-solver', 'contract-test'):
            fail('CFD_EVIDENCE_ROLE_INVALID', 'Distinguish actual solver time samples from contract-test values.')
        quantities = data['quantities']
        if not isinstance(quantities, list) or not 1 <= len(quantities) <= 64:
            fail('CFD_TIME_HISTORY_QUANTITY_REQUIRED', 'Declare each physical quantity and its unit.')
        ids = set()
        for q in quantities:
            if not isinstance(q, dict) or set(q) != {'id', 'unit'} or (not isinstance(q['id'], str)) or (not ID.fullmatch(q['id'])) or (q['id'] in ids) or (not isinstance(q['unit'], str)) or (not 1 <= len(q['unit']) <= 40):
                fail('CFD_TIME_HISTORY_QUANTITY_REQUIRED', 'Use unique bounded quantity names and explicit units.')
            ids.add(q['id'])
        if not isinstance(data['samples'], list) or not 2 <= len(data['samples']) <= 100000:
            fail('CFD_TIME_HISTORY_SAMPLES_REQUIRED', 'Supply at least two bounded physical-time samples.')
        previous = -1.0
        for row in data['samples']:
            if not isinstance(row, dict) or set(row) != {'timeSeconds', 'values'} or (not isinstance(row['values'], dict)) or (set(row['values']) != ids):
                fail('CFD_TIME_HISTORY_SAMPLE_INVALID', 'Bind every sample to the declared quantity set.')
            t = number(row['timeSeconds'], nonnegative=True)
            if t <= previous:
                fail('CFD_TIME_HISTORY_TIME_INVALID', 'Use strictly increasing actual physical times; do not mix checkpoints or steady iterations.')
            previous = t
            for value in row['values'].values():
                number(value)
        file_descriptors(data['sourceFiles'], files)
        return {'status': 'TimeHistoryIntegrityVerified', 'valid': True, 'reasonCode': 'CFD_TIME_HISTORY_INTEGRITY_VERIFIED', 'sampleCount': len(data['samples']), 'physicalStartTimeSeconds': data['samples'][0]['timeSeconds'], 'physicalEndTimeSeconds': previous, 'quantities': deepcopy(quantities), 'evidenceRole': data['evidenceRole'], 'actualCfdRunVerified': False, 'authenticityVerified': False}
    except WorkerDiagnostic as error:
        return diagnostic(error)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return diagnostic(WorkerDiagnostic('CFD_TIME_HISTORY_SCHEMA_INVALID', 'Supply a bounded finite physical time history.'))

def assess_temporal_evidence(data: Any, *, binding: dict, histories: dict[str, dict]) -> dict:
    """Three-step observed order, at an exact shared physical time; no interpolation.

    This is a numerical sensitivity assessment, not an authenticity, continuum
    accuracy or full CFD engineering acceptance decision.
    """
    try:
        if not isinstance(histories, dict):
            fail('CFD_TEMPORAL_HISTORY_INVALID', 'Supply a bounded map of exact physical-time history documents.')
        keys = {'schemaVersion', 'binding', 'quantityId', 'evaluationTimeSeconds', 'relativeTolerance', 'runs'}
        if not isinstance(data, dict) or set(data) != keys or data['schemaVersion'] != 'untirta.cfd-temporal-evidence.v1':
            fail('CFD_TEMPORAL_EVIDENCE_REQUIRED', 'Provide three controlled time-step refinements with unchanged physics/mesh and a shared physical evaluation time.')
        validate_binding(data['binding'], binding)
        t = number(data['evaluationTimeSeconds'], nonnegative=True)
        tolerance = number(data['relativeTolerance'], positive=True)
        if tolerance >= 1 or not isinstance(data['runs'], list) or len(data['runs']) != 3:
            fail('CFD_TEMPORAL_REFINEMENT_REQUIRED', 'Use three refinement runs and an explicit relative tolerance below one.')
        rows, units, roles, mesh_hashes, physics_hashes, paths = ([], set(), set(), set(), set(), set())
        for run in data['runs']:
            if not isinstance(run, dict) or set(run) != {'historyPath', 'timeStepSeconds', 'meshSha256', 'physicsSettingsSha256'}:
                fail('CFD_TEMPORAL_BINDING_INVALID', 'Bind each time-step run to its exact history, mesh and unchanged physical settings.')
            path = safe_path(run['historyPath'])
            if path in paths:
                fail('CFD_TEMPORAL_REFINEMENT_REQUIRED', 'Use three distinct solver runs; repeated history files are not independent refinements.')
            paths.add(path)
            history = histories.get(path)
            report = validate_time_history(history, binding=binding)
            if not report['valid']:
                fail('CFD_TEMPORAL_HISTORY_INVALID', 'Restore valid exact-revision physical histories for every refinement.')
            matches = [q for q in history['quantities'] if q['id'] == data['quantityId']]
            samples = [sample for sample in history['samples'] if sample['timeSeconds'] == t]
            if len(matches) != 1 or len(samples) != 1:
                fail('CFD_TEMPORAL_TIME_MISMATCH', 'Evaluate the same quantity at the exact shared physical sample time in all three runs.')
            if any((not isinstance(run[k], str) or not SHA.fullmatch(run[k]) for k in ('meshSha256', 'physicsSettingsSha256'))):
                fail('CFD_TEMPORAL_BINDING_INVALID', 'Use exact mesh and physical-settings SHA-256 values.')
            units.add(matches[0]['unit'])
            roles.add(history['evidenceRole'])
            mesh_hashes.add(run['meshSha256'])
            physics_hashes.add(run['physicsSettingsSha256'])
            rows.append((number(run['timeStepSeconds'], positive=True), samples[0]['values'][data['quantityId']]))
        if len(units) != 1 or len(mesh_hashes) != 1 or len(physics_hashes) != 1 or (len(roles) != 1):
            fail('CFD_TEMPORAL_BINDING_MISMATCH', 'Keep units, mesh, physical settings and evidence role identical across temporal refinements.')
        rows.sort(reverse=True)
        (dt1, f1), (dt2, f2), (dt3, f3) = rows
        ratio = dt1 / dt2
        if ratio <= 1 or not math.isclose(ratio, dt2 / dt3, rel_tol=1e-10):
            fail('CFD_TEMPORAL_REFINEMENT_RATIO_INVALID', 'Use strictly decreasing time steps with a common refinement ratio.')
        if f3 == 0 or f2 == f3 or f1 == f2 or ((f1 - f2) * (f2 - f3) <= 0):
            fail('CFD_TEMPORAL_ORDER_UNRESOLVED', 'Observed order is unresolved for zero, identical or oscillating operands; retain raw histories and extend verification.')
        order = math.log(abs((f1 - f2) / (f2 - f3))) / math.log(ratio)
        relative = abs(f2 - f3) / abs(f3)
        if not math.isfinite(order) or not math.isfinite(relative):
            fail('CFD_TEMPORAL_ORDER_UNRESOLVED', 'Inspect finite temporal refinement differences and denominators.')

        def mn(value):
            return '<mn>' + escape(str(value)) + '</mn>'

        def mathml(inner):
            return '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">' + inner + '</math>'
        fsub = lambda n: '<msub><mi>f</mi><mn>' + str(n) + '</mn></msub>'
        equation_stages = [{'identifier': 'CFD_TEMPORAL_OBSERVED_ORDER', 'title': 'Observed temporal order', 'input': {'timeStepsSeconds': [dt1, dt2, dt3], 'sampleValues': [f1, f2, f3], 'quantityUnit': next(iter(units)), 'physicalTimeSeconds': t}, 'equationMathML': mathml('<mi>p</mi><mo>=</mo><mfrac><mrow><mi>ln</mi><mo>(</mo><mfrac><mrow>' + fsub(1) + '<mo>−</mo>' + fsub(2) + '</mrow><mrow>' + fsub(2) + '<mo>−</mo>' + fsub(3) + '</mrow></mfrac><mo>)</mo></mrow><mrow><mi>ln</mi><mo>(</mo><mi>r</mi><mo>)</mo></mrow></mfrac>'), 'substitutionMathML': mathml('<mi>p</mi><mo>=</mo><mfrac><mrow><mi>ln</mi><mo>(</mo><mfrac><mrow>' + mn(f1) + '<mo>−</mo>' + mn(f2) + '</mrow><mrow>' + mn(f2) + '<mo>−</mo>' + mn(f3) + '</mrow></mfrac><mo>)</mo></mrow><mrow><mi>ln</mi><mo>(</mo>' + mn(ratio) + '<mo>)</mo></mrow></mfrac>'), 'resultMathML': mathml('<mi>p</mi><mo>=</mo>' + mn(order)), 'result': order}, {'identifier': 'CFD_TEMPORAL_FINE_RELATIVE_CHANGE', 'title': 'Relative change from medium to fine', 'input': {'mediumValue': f2, 'fineValue': f3, 'relativeTolerance': tolerance}, 'equationMathML': mathml('<mi>ε</mi><mo>=</mo><mfrac><mrow><mo>|</mo>' + fsub(2) + '<mo>−</mo>' + fsub(3) + '<mo>|</mo></mrow><mrow><mo>|</mo>' + fsub(3) + '<mo>|</mo></mrow></mfrac>'), 'substitutionMathML': mathml('<mi>ε</mi><mo>=</mo><mfrac><mrow><mo>|</mo>' + mn(f2) + '<mo>−</mo>' + mn(f3) + '<mo>|</mo></mrow><mrow><mo>|</mo>' + mn(f3) + '<mo>|</mo></mrow></mfrac>'), 'resultMathML': mathml('<mi>ε</mi><mo>=</mo>' + mn(relative)), 'result': relative}]
        for stage in equation_stages:
            stage['source'] = {'title': 'NASA NPARC Alliance: Examining Spatial (Grid) Convergence', 'authors': 'NASA NPARC Alliance / J. W. Slater', 'pages': 'Observed order / Richardson extrapolation; web page has no printed page numbering', 'url': 'https://www.grc.nasa.gov/www/wind/valid/tutorial/spatconv.html', 'equation': 'Systematic-refinement order identity applied to declared Δt at fixed mesh/physics; observed sensitivity, not proven asymptotic convergence or GCI.'}
        return {'status': 'TemporalSensitivityAssessed', 'valid': True, 'reasonCode': 'CFD_TEMPORAL_SENSITIVITY_ASSESSED', 'observedOrder': order, 'fineRelativeChange': relative, 'criterionSatisfied': order > 0 and relative <= tolerance, 'relativeTolerance': tolerance, 'evaluationTimeSeconds': t, 'evidenceRole': next(iter(roles)), 'engineeringValidated': False, 'actualCfdRunVerified': False, 'equationStages': equation_stages, 'claim': 'Three-step observed numerical sensitivity only; imported provenance, conservation, mesh independence and physical-model acceptance remain separate.'}
    except WorkerDiagnostic as error:
        return diagnostic(error)
    except (TypeError, ValueError, ZeroDivisionError, OverflowError, RecursionError):
        return diagnostic(WorkerDiagnostic('CFD_TEMPORAL_EVIDENCE_INVALID', 'Supply bounded finite temporal refinement evidence.'))
