"""File-based independent runner with immutable inputs and controlled diagnostics."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any
from uuid import uuid4
from .capabilities import capability_report
from .contracts import Job, WorkerDiagnostic, digest, file_digest, write_json
from . import openfoam
from .resources import ProcessRunner, folder_size
from .checkpoint import validate_checkpoint_descriptor

@contextmanager
def single_job(workspace_root: Path):
    workspace_root.mkdir(parents=True, exist_ok=True)
    lock = workspace_root / '.worker-active.lock'
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 384)
    except FileExistsError:
        raise WorkerDiagnostic('CFD_WORKER_BUSY', 'One job is already active. Recover a stale lock only after verifying that its process has stopped.') from None
    try:
        os.write(handle, json.dumps({'pid': os.getpid(), 'startedAt': datetime.now(timezone.utc).isoformat()}).encode())
        os.close(handle)
        yield
    finally:
        lock.unlink(missing_ok=True)

def validate_checkpoint(workspace: Path, checkpoint: Path, binding: dict) -> dict:
    try:
        data = json.loads(checkpoint.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise WorkerDiagnostic('CFD_CHECKPOINT_FILE_INVALID', 'Provide an accessible UTF-8 JSON checkpoint descriptor.') from None
    if not isinstance(data, dict):
        raise WorkerDiagnostic('CFD_CHECKPOINT_BINDING_INVALID', 'Provide a structured checkpoint bound to the exact input/reference revision.')
    if data.get('schemaVersion') == 'untirta.cfd-native-checkpoint.v2':
        checked = validate_checkpoint_descriptor(data, binding=binding)
        if not checked['valid']:
            raise WorkerDiagnostic(checked['reasonCode'], checked['diagnostics'][0]['correctiveAction'])
        descriptors = data['nativeFiles'] + data['meshFiles'] + data['settingsFiles'] + data['setupState']['initialStateFiles']
        for item in descriptors:
            original = workspace / item['path']
            path = original.resolve()
            if not path.is_relative_to(workspace.resolve()) or any((parent.is_symlink() for parent in [original, *original.parents] if parent.is_relative_to(workspace))) or (not path.is_file()) or (path.stat().st_size != item['bytes']) or (file_digest(path) != item['sha256']):
                raise WorkerDiagnostic('CFD_CHECKPOINT_FILE_INVALID', 'Restore the checksum-bound native solution, mesh, settings and initial-state files in this workspace.')
        return data
    if data.get('schemaVersion') != 'untirta.cfd-native-checkpoint.v1' or any((data.get(key) != value for key, value in binding.items())):
        raise WorkerDiagnostic('CFD_CHECKPOINT_BINDING_INVALID', 'Use a checkpoint from the exact input/reference revision.')
    if data.get('timeInterpretation') not in {'physical', 'steady-iteration'}:
        raise WorkerDiagnostic('CFD_CHECKPOINT_TIME_INVALID', 'Declare physical time or steady iteration without conflating them.')
    if data.get('timeInterpretation') == 'physical' and (isinstance(data.get('physicalTimeSeconds'), bool) or not isinstance(data.get('physicalTimeSeconds'), (int, float)) or (not math.isfinite(data['physicalTimeSeconds'])) or (data['physicalTimeSeconds'] < 0)):
        raise WorkerDiagnostic('CFD_CHECKPOINT_TIME_INVALID', 'A physical checkpoint needs its actual physical time.')
    if not isinstance(data.get('files'), list) or not isinstance(data.get('setupFiles', []), list):
        raise WorkerDiagnostic('CFD_CHECKPOINT_INCOMPLETE', 'Native output/setup files must be explicit checksum-bound lists.')
    for item in data.get('files', []) + data.get('setupFiles', []):
        if not isinstance(item, dict) or not isinstance(item.get('path'), str) or (not isinstance(item.get('sha256'), str)) or (not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
            raise WorkerDiagnostic('CFD_CHECKPOINT_FILE_INVALID', 'Each native file requires its explicit local path and SHA-256 digest.')
        original_path = workspace / item['path']
        path = original_path.resolve()
        if not path.is_relative_to(workspace.resolve()) or original_path.is_symlink() or (not path.is_file()) or (file_digest(path) != item['sha256']):
            raise WorkerDiagnostic('CFD_CHECKPOINT_FILE_INVALID', 'The paired native field/case/data or mesh setup is missing or changed.')
        if data.get('timeInterpretation') == 'physical' and item in data.get('files', []):
            try:
                native_time = float(path.parent.name)
            except ValueError:
                native_time = None
            if native_time != data['physicalTimeSeconds']:
                raise WorkerDiagnostic('CFD_CHECKPOINT_TIME_PAIR_MISMATCH', 'Native fields and mesh checkpoint must belong to the same physical time.')
    if not data.get('files') or not data.get('meshPosition'):
        raise WorkerDiagnostic('CFD_CHECKPOINT_INCOMPLETE', 'The checkpoint requires paired native outputs and a declared mesh position.')
    return data

def execute_job(source: dict[str, Any], reference: dict[str, Any], workspace_root: Path, fixture_root: Path, *, restart_smoke: bool=False) -> dict:
    job = Job.parse(source, reference)
    if restart_smoke and (job.adapter != 'openfoam' or job.profile != 'transient-cavity-v1'):
        raise WorkerDiagnostic('CFD_RESTART_PROFILE_INVALID', 'The fixed restart smoke is only available for the pinned single-phase transient cavity development case.')
    capabilities = capability_report()
    if not capabilities['adapters'][job.adapter]['available']:
        return {'schemaVersion': 'untirta.cfd-worker-result.v1', **job.binding(), 'status': 'InputRequired', 'actualCfdRun': False, 'reasonCode': capabilities['adapters'][job.adapter]['reasonCode'], 'action': 'Provide the declared solver/runtime and whole-tree resource enforcement; independent valid methods remain available.', 'capabilities': capabilities}
    with single_job(workspace_root.resolve()):
        run_id = job.study_id + '-' + uuid4().hex[:12]
        workspace = workspace_root.resolve() / run_id
        for name in ('input', 'reference', 'output', 'checkpoints'):
            (workspace / name).mkdir(parents=True)
        write_json(workspace / 'input' / 'job.json', source)
        write_json(workspace / 'reference' / 'snapshot.json', reference)
        manifest = {'schemaVersion': 'untirta.cfd-worker-manifest.v1', **job.binding(), 'runId': run_id, 'startedAt': datetime.now(timezone.utc).isoformat(), 'adapter': job.adapter, 'status': 'Preparing', 'caseProfile': job.profile, 'actualCfdRun': False, 'mainApplicationWrites': False, 'inputPersistence': 'input-only', 'resourceEnforcement': capabilities['resourceEnforcement'], 'stages': []}
        runner = None
        try:
            runner = ProcessRunner(workspace, job.limits, workspace / 'cancel.request')
            case, stages, metadata = openfoam.prepare(job, workspace, fixture_root)
            manifest['solver'] = metadata
            for stage in stages:
                executable = shutil.which(stage[0])
                if not executable:
                    raise WorkerDiagnostic('CFD_SOLVER_UNAVAILABLE', 'The selected versioned mesher/solver executable is unavailable.')
                manifest['status'] = 'Running'
                write_json(workspace / 'output' / 'manifest.json', manifest)
                if stage[0] == 'foamRun':
                    openfoam.save_initial_state(job, workspace, case)
                result = runner.run([executable] + stage[1:], case, workspace / 'output' / 'logs' / (stage[0] + '.log'))
                manifest['stages'].append({'name': stage[0], **result})
            result = openfoam.collect(job, workspace, case)
            if restart_smoke:
                source_states = [validate_checkpoint(workspace, workspace / path, job.binding()) for path in result['nativeCheckpointFiles']]
                source_state = next((item for item in source_states if item['timeState']['physicalTimeSeconds'] == 0.05))
                restarted, lineage = openfoam.prepare_restart(job, workspace, case)
                stage_result = runner.run([shutil.which('foamRun'), '-case', str(restarted)], restarted, workspace / 'output' / 'logs' / 'restart-foamRun.log')
                manifest['stages'].append({'name': 'restart-foamRun', **stage_result})
                restart_result = openfoam.collect(job, workspace, restarted, log_name='restart-foamRun.log', target_time=0.055, minimum_time=0.05, checkpoint_prefix='restart-', step_offset=10)
                openfoam.verify_restart_log(workspace, 0.05, 0.055)
                restart_state = validate_checkpoint(workspace, workspace / restart_result['nativeCheckpointFiles'][-1], job.binding())
                result['restart'] = {**lineage, 'actualRestartExecutionCompleted': True, 'actualRestartExecutionVerified': False, 'restartEquivalenceVerified': False, 'sourceCheckpointChecksum': source_state['checkpointChecksum'], 'sourceSettingsSha256': source_state['settingsSha256'], 'restartCheckpointChecksum': restart_state['checkpointChecksum'], 'restartSettingsSha256': restart_state['settingsSha256'], 'physicalTimeSeconds': restart_result['physicalTimeSeconds'], 'fieldSummaries': restart_result['fieldSummaries'], 'claim': 'Native checkpoint loaded and one actual finite physical step completed; no continuous-run equivalence or pump-physics validation claim.'}
                result['checkpoints'] += restart_result['checkpoints']
                result['nativeCheckpointFiles'] += restart_result['nativeCheckpointFiles']
            for item in result.get('checkpoints', []):
                if isinstance(item, str):
                    validate_checkpoint(workspace, workspace / item, job.binding())
            for item in result.get('nativeCheckpointFiles', []):
                validate_checkpoint(workspace, workspace / item, job.binding())
            manifest.update(result)
            manifest['status'] = 'DevelopmentBenchmarkComplete' if result.get('actualCfdRun') else 'NumericalReviewRequired'
        except WorkerDiagnostic as exc:
            manifest.update({'status': 'Cancelled' if exc.code == 'CFD_CANCELLED' else 'InputRequired' if exc.code.endswith('REQUIRED') else 'Stopped', 'reasonCode': exc.code, 'action': exc.action, 'actualCfdRun': False})
        except Exception as exc:
            manifest.update({'status': 'Stopped', 'reasonCode': 'CFD_WORKER_STAGE_STOPPED', 'actualCfdRun': False, 'action': 'Inspect the bounded local stage log and verify solver/version/model inputs.', 'exceptionType': type(exc).__name__})
        finally:
            if runner:
                runner.close()
            manifest['finishedAt'] = datetime.now(timezone.utc).isoformat()
            manifest['sourceInputUnchanged'] = digest(json.loads((workspace / 'input' / 'job.json').read_text())) == job.binding()['inputSha256']
            manifest['referenceUnchanged'] = digest(json.loads((workspace / 'reference' / 'snapshot.json').read_text())) == job.binding()['referenceSha256']
            if not manifest['sourceInputUnchanged'] or not manifest['referenceUnchanged']:
                manifest.update({'status': 'Stopped', 'actualCfdRun': False, 'reasonCode': 'CFD_IMMUTABLE_INPUT_CHANGED'})
            write_json(workspace / 'output' / 'manifest.json', manifest)
        return {'schemaVersion': 'untirta.cfd-worker-result.v1', **manifest, 'workspace': str(workspace), 'outputBytes': folder_size(workspace), 'manifestPath': str(workspace / 'output' / 'manifest.json')}
