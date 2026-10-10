"""Bounded, inert CFD ZIP inspection/import into an independent Lab workspace.

No solver is run, no archive member is executed and no Main/database credentials
are used. Checksums establish integrity, not provenance authenticity or Current.
"""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
import shutil
import stat
import unicodedata
import uuid
import zipfile
import zlib
from .checkpoint import assess_temporal_evidence, diagnostic, fail, file_descriptors, safe_path, strict_json, validate_binding, validate_checkpoint_descriptor, validate_time_history, ID, SHA
from .contracts import WorkerDiagnostic, digest
SCHEMA = 'untirta.cfd-artifact.v1'
ROLES = {'input', 'reference', 'checkpoint', 'native', 'time-history', 'temporal-evidence', 'result', 'log'}
NATIVE_NAMES = {'U', 'p', 'p_rgh', 'alpha.water', 'alpha.vapour', 'alphaPhi.water', 'phi', 'Phi', 'rho', 'rho.water', 'rho.vapour', 'T', 'k', 'epsilon', 'omega', 'nut', 'nuTilda', 'alphat', 'points', 'faces', 'owner', 'neighbour', 'boundary', 'cellZones', 'faceZones', 'pointZones', 'controlDict', 'fvSchemes', 'fvSolution', 'blockMeshDict', 'snappyHexMeshDict', 'decomposeParDict', 'dynamicMeshDict', 'motionProperties', 'transportProperties', 'physicalProperties', 'momentumTransport', 'physicalProperties.water', 'physicalProperties.vapour', 'phaseProperties', 'thermophysicalProperties', 'turbulenceProperties', 'MRFProperties', 'g', 'fvModels', 'fvConstraints', 'pointDisplacement', 'cellDisplacement', 'cellLevel', 'pointLevel', 'level0Edge', 'refinementHistory', 'surfaceIndex', 'uniform', 'time'}

@dataclass(frozen=True)
class ArtifactLimits:
    archive_bytes: int = 25 * 1024 ** 2
    total_bytes: int = 128 * 1024 ** 2
    file_bytes: int = 64 * 1024 ** 2
    json_bytes: int = 16 * 1024 ** 2
    log_bytes: int = 4 * 1024 ** 2
    entries: int = 2048
    compression_ratio: int = 200

    def validate(self):
        caps = ArtifactLimits()
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or (not 0 < value <= getattr(caps, name)):
                fail('CFD_ARTIFACT_LIMIT_INVALID', 'Use positive artifact budgets within the independent worker profile.')

def sanitize_log(raw: bytes) -> bytes:
    """Remove credentials, credential URLs, absolute user paths and terminal controls."""
    text = raw.decode('utf-8', errors='replace')
    text = re.sub('\\x1b(?:\\[[0-?]*[ -/]*[@-~]|\\][^\\x07]*(?:\\x07|\\x1b\\\\))', '', text)
    text = ''.join((c for c in text if c in '\n\r\t' or (ord(c) >= 32 and ord(c) != 127)))
    text = re.sub('(?im)^.*(?:authorization|cookie|password|passwd|secret|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|service[_ -]?role|gh[pousr]_[A-Za-z0-9]|github_pat_|eyJ[A-Za-z0-9_-]+\\.).*$', '[REDACTED SENSITIVE LOG LINE]', text)
    text = re.sub('https?://[^\\s<>\\"\']+', '[REDACTED URL]', text)
    text = re.sub('(?i)\\b[A-Z]:[\\\\/][^\\r\\n\\t\\"\']+', '[REDACTED LOCAL PATH]', text)
    text = re.sub('(?:/home/|/Users/|/tmp/)[^\\s\\"\']+', '[REDACTED LOCAL PATH]', text)
    return text.encode('utf-8')

def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, 'is_junction', lambda: False)())

def _regular_source(path: Path) -> None:
    candidate = path.absolute()
    for part in (candidate, *candidate.parents):
        if _is_link(part):
            fail('CFD_ARTIFACT_SOURCE_INVALID', 'Use a regular local archive without symbolic-link ancestors.')
    if not candidate.is_file():
        fail('CFD_ARTIFACT_SOURCE_INVALID', 'Choose a regular local CFD ZIP file.')

def _read_archive(archive: bytes | Path, limits: ArtifactLimits) -> bytes:
    if isinstance(archive, bytes):
        raw = archive
    elif isinstance(archive, Path):
        _regular_source(archive)
        if archive.stat().st_size > limits.archive_bytes:
            fail('CFD_ARTIFACT_BUDGET_EXCEEDED', 'Use a CFD archive within the 25 MiB application limit or a smaller local export.')
        with archive.open('rb') as source:
            raw = source.read(limits.archive_bytes + 1)
    else:
        fail('CFD_ARTIFACT_SOURCE_INVALID', 'Supply ZIP bytes or a local Path; URL fetching is not supported.')
    if len(raw) > limits.archive_bytes:
        fail('CFD_ARTIFACT_BUDGET_EXCEEDED', 'Reduce the ZIP to the declared independent artifact budget.')
    if not raw.startswith(b'PK\x03\x04'):
        fail('CFD_ARTIFACT_SIGNATURE_INVALID', 'Import an ordinary ZIP archive with a strict CFD manifest.')
    return raw

def _members(raw: bytes, limits: ArtifactLimits) -> dict[str, bytes]:
    data, seen = ({}, set())
    with zipfile.ZipFile(BytesIO(raw), 'r') as archive:
        entries = archive.infolist()
        if not entries or len(entries) > limits.entries:
            fail('CFD_ARTIFACT_BUDGET_EXCEEDED', 'Reduce the artifact file count to the declared budget.')
        total = 0
        for entry in entries:
            path = safe_path(entry.orig_filename)
            if entry.filename != path:
                fail('CFD_ARTIFACT_PATH_INVALID', 'Use unambiguous unchanged archive filenames.')
            normalized = unicodedata.normalize('NFC', path).casefold()
            if normalized in seen or path != unicodedata.normalize('NFC', path):
                fail('CFD_ARTIFACT_DUPLICATE_PATH', 'Use unique normalized paths, including case-insensitive filenames.')
            seen.add(normalized)
            mode = entry.external_attr >> 16
            if entry.is_dir() or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG)) or mode & 73:
                fail('CFD_ARTIFACT_SPECIAL_FILE_PROHIBITED', 'Import only inert regular files without executables, symbolic links or directory entries.')
            if entry.flag_bits & 1 or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                fail('CFD_ARTIFACT_COMPRESSION_INVALID', 'Use an unencrypted stored/deflated ZIP archive.')
            if entry.file_size > limits.file_bytes or entry.file_size > max(1, entry.compress_size) * limits.compression_ratio:
                fail('CFD_ARTIFACT_BUDGET_EXCEEDED', 'Reduce oversized files or excessive archive compression ratios.')
            total += entry.file_size
            if total > limits.total_bytes:
                fail('CFD_ARTIFACT_BUDGET_EXCEEDED', 'Reduce total uncompressed CFD output to the declared budget.')
            with archive.open(entry) as source:
                content = source.read(min(limits.file_bytes, entry.file_size) + 1)
            if len(content) != entry.file_size:
                fail('CFD_ARTIFACT_SIZE_MISMATCH', 'Restore an intact archive with matching file sizes and checksums.')
            data[path] = content
        paths = set(data)
        if any(('/'.join(path.split('/')[:i]) in paths for path in paths for i in range(1, len(path.split('/'))))):
            fail('CFD_ARTIFACT_DUPLICATE_PATH', 'Remove file/directory path collisions from the archive.')
    return data

def _role_path(path: str, role: str, limits: ArtifactLimits, raw: bytes) -> None:
    prefixes = {'input': 'input/', 'reference': 'reference/', 'checkpoint': 'checkpoints/', 'native': 'output/', 'time-history': 'output/', 'temporal-evidence': 'output/', 'result': 'output/', 'log': 'output/logs/'}
    if role not in ROLES or not path.startswith(prefixes[role]):
        fail('CFD_ARTIFACT_ROLE_PATH_INVALID', 'Keep independent input, immutable reference, checkpoints and native outputs in their designated folders.')
    name = path.rsplit('/', 1)[-1]
    if role == 'native':
        if name not in NATIVE_NAMES and (not name.endswith(('.cas.h5', '.dat.h5', '.msh.h5', '.stl', '.obj'))):
            fail('CFD_ARTIFACT_NATIVE_TYPE_PROHIBITED', 'Include supported inert native fields, mesh/settings dictionaries or Fluent HDF5 pairs; scripts/executables are prohibited.')
        if name.endswith(('.cas.h5', '.dat.h5', '.msh.h5')) and (not raw.startswith(b'\x89HDF\r\n\x1a\n')):
            fail('CFD_NATIVE_SIGNATURE_INVALID', 'Provide actual HDF5 native solver files rather than renamed placeholders.')
        if raw.startswith((b'MZ', b'\x7fELF', b'#!')):
            fail('CFD_ARTIFACT_NATIVE_TYPE_PROHIBITED', 'Executable content cannot be imported as native CFD evidence.')
        if not name.endswith(('.cas.h5', '.dat.h5', '.msh.h5')):
            text = raw.decode('utf-8', errors='replace')
            if re.search('(?im)#\\s*(?:include\\w*|codeStream|calc|eval)\\b|\\btype\\s+coded\\w*\\s*;|\\b(?:codeExecute|codeWrite|codeInclude|codeOptions|codeLibs)\\b|\\blibs\\s*\\(', text):
                fail('CFD_ARTIFACT_ACTIVE_DICTIONARY_PROHIBITED', 'Remove dynamic code, external includes and library loading from imported native dictionaries; imported contents are never executed.')
    elif role == 'log':
        if not name.endswith('.log') or len(raw) > limits.log_bytes:
            fail('CFD_ARTIFACT_LOG_INVALID', 'Include bounded UTF-8 log files; credentials are sanitized before persistence.')
    elif not name.endswith('.json') or len(raw) > limits.json_bytes:
        fail('CFD_ARTIFACT_JSON_BUDGET_EXCEEDED', 'Use bounded JSON engineering documents with explicit schemas.')

def _input_only(value) -> None:
    prohibited = {'results', 'output', 'outputs', 'checkpoints', 'equationStages', 'currentResult', 'actualCfdRunVerified', 'password', 'secret', 'accessToken', 'refreshToken', 'serviceRoleKey', 'apiKey', 'authorization', 'cookie'}
    if not isinstance(value, dict) or value.get('schemaVersion') not in ('untirta.cfd-worker-job.v1',):
        fail('CFD_ARTIFACT_INPUT_SCHEMA_INVALID', 'Include an independent input-only CFD job/study document.')

    def walk(item, depth=0):
        if depth > 32:
            fail('CFD_ARTIFACT_INPUT_SCHEMA_INVALID', 'Use a bounded input-only document.')
        if isinstance(item, dict):
            for key, child in item.items():
                if key in prohibited:
                    fail('CFD_ARTIFACT_INPUT_ONLY_REQUIRED', 'Keep computed results, native outputs and credentials outside input persistence.')
                walk(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 100000:
                fail('CFD_ARTIFACT_INPUT_SCHEMA_INVALID', 'Use bounded input arrays.')
            for child in item:
                walk(child, depth + 1)
        elif isinstance(item, float) and (not math.isfinite(item)):
            fail('CFD_JSON_NONFINITE', 'Use finite input quantities.')
    walk(value)

def _inspect(archive: bytes | Path, expected_binding: dict | None, limits: ArtifactLimits):
    limits.validate()
    raw = _read_archive(archive, limits)
    files = _members(raw, limits)
    if 'manifest.json' not in files or len(files['manifest.json']) > limits.json_bytes:
        fail('CFD_ARTIFACT_MANIFEST_REQUIRED', 'Include a bounded manifest.json with untirta.cfd-artifact.v1 schema and exact file descriptors.')
    manifest = strict_json(files['manifest.json'])
    keys = {'schemaVersion', 'artifactId', 'binding', 'solver', 'method', 'evidenceRole', 'provenance', 'lifecycle', 'files', 'checkpoints', 'timeHistories', 'temporalEvidence', 'manifestChecksum'}
    if not isinstance(manifest, dict) or set(manifest) != keys or manifest.get('schemaVersion') != SCHEMA:
        fail('CFD_ARTIFACT_SCHEMA_INVALID', 'Re-export a strict untirta.cfd-artifact.v1 archive; legacy ZIPs do not carry native restart/Current evidence.')
    if not isinstance(manifest['artifactId'], str) or not ID.fullmatch(manifest['artifactId']):
        fail('CFD_ARTIFACT_ID_INVALID', 'Use a bounded independent artifact identifier.')
    binding = validate_binding(manifest['binding'], expected_binding)
    checksum = dict(manifest)
    checksum.pop('manifestChecksum')
    if manifest['manifestChecksum'] != digest(checksum):
        fail('CFD_ARTIFACT_MANIFEST_CHECKSUM_MISMATCH', 'Restore the unchanged complete artifact manifest.')
    if manifest['evidenceRole'] not in ('actual-solver', 'contract-test'):
        fail('CFD_EVIDENCE_ROLE_INVALID', 'Declare actual solver evidence or educational contract-test evidence explicitly.')
    provenance = manifest['provenance']
    if not isinstance(provenance, dict) or set(provenance) != {'kind', 'sourceApplication', 'claim'} or provenance['kind'] not in ('local-worker', 'github-actions-worker', 'educational-contract'):
        fail('CFD_ARTIFACT_PROVENANCE_INVALID', 'Declare independent worker or educational-contract provenance; checksums do not authenticate it.')
    if any((not isinstance(provenance[k], str) or not provenance[k] or len(provenance[k]) > 300 for k in provenance)):
        fail('CFD_ARTIFACT_PROVENANCE_INVALID', 'Use bounded non-secret provenance labels.')
    if (manifest['evidenceRole'] == 'contract-test') != (provenance['kind'] == 'educational-contract'):
        fail('CFD_ARTIFACT_PROVENANCE_MISMATCH', 'Educational contract samples must be labeled contract-test, never actual-solver.')
    lifecycle = manifest['lifecycle']
    if not isinstance(lifecycle, dict) or set(lifecycle) != {'state', 'actualCfdRunClaimed', 'engineeringValidated', 'calculationFreshness'} or lifecycle['state'] not in ('checkpoint-saved', 'solver-completed', 'numerical-review-required', 'contract-test') or (type(lifecycle['actualCfdRunClaimed']) is not bool) or (lifecycle['engineeringValidated'] is not False) or (lifecycle['calculationFreshness'] != 'Imported Evidence'):
        fail('CFD_ARTIFACT_LIFECYCLE_INVALID', 'Import independent evidence requiring review; file presence cannot establish Current or engineering acceptance.')
    if manifest['evidenceRole'] == 'contract-test' and (lifecycle['actualCfdRunClaimed'] or lifecycle['state'] != 'contract-test'):
        fail('CFD_ARTIFACT_PROVENANCE_MISMATCH', 'Contract-test fixtures cannot claim that a solver ran.')
    descriptors = manifest['files']
    if not isinstance(descriptors, list) or not descriptors or len(descriptors) > limits.entries:
        fail('CFD_ARTIFACT_FILES_REQUIRED', 'List each exact independent input/reference/native/output file.')
    declared, role_paths, documents = (set(), {role: [] for role in ROLES}, {})
    for item in descriptors:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256', 'bytes', 'role'}:
            fail('CFD_ARTIFACT_FILE_INVALID', 'Describe each artifact member by path, byte count, SHA-256 and persistence role.')
        file_descriptors([{k: item[k] for k in ('path', 'sha256', 'bytes')}], files)
        path = item['path']
        if path in declared or path == 'manifest.json':
            fail('CFD_ARTIFACT_DUPLICATE_PATH', 'List each archive member exactly once, excluding manifest.json.')
        declared.add(path)
        _role_path(path, item['role'], limits, files[path])
        role_paths[item['role']].append(path)
        if item['role'] not in ('native', 'log'):
            documents[path] = strict_json(files[path])
    if declared != set(files) - {'manifest.json'}:
        fail('CFD_ARTIFACT_UNDECLARED_FILE', 'Remove undeclared members; every imported byte needs an exact manifest role and checksum.')
    for role in ('input', 'reference'):
        if len(role_paths[role]) != 1:
            fail('CFD_ARTIFACT_REFERENCE_REQUIRED', 'Include exactly one input-only document and its immutable reference snapshot.')
    source = documents[role_paths['input'][0]]
    reference = documents[role_paths['reference'][0]]
    _input_only(source)
    if not isinstance(reference, dict) or digest(source) != binding['inputSha256'] or digest(reference) != binding['referenceSha256']:
        fail('CFD_ARTIFACT_SOURCE_BINDING_MISMATCH', 'Bind artifacts to the exact canonical input and immutable reference JSON, separately from derived outputs.')
    if source.get('studyId') != binding['studyId'] or source.get('inputRevision') != binding['inputRevision']:
        fail('CFD_ARTIFACT_SOURCE_BINDING_MISMATCH', 'Use the exact study ID and input revision represented by the archived input-only document.')
    if source['schemaVersion'] == 'untirta.cfd-worker-job.v1':
        from .contracts import Job
        Job.parse(source, reference)
    else:
        fail('CFD_ARTIFACT_INPUT_SCHEMA_INVALID', 'Only fixed educational worker input/reference documents are accepted; Pump studies and private snapshots are prohibited.')
    for path in role_paths['result']:
        data = documents[path]
        supported = {'untirta.cfd-worker-manifest.v1', 'untirta.cfd-worker-result.v1'}
        if not isinstance(data, dict) or data.get('schemaVersion') not in supported:
            fail('CFD_ARTIFACT_RESULT_SCHEMA_INVALID', 'Include supported explicitly versioned independent CFD reports; arbitrary output JSON is not accepted as solver evidence.')
        declared_binding = data.get('binding', data)
        for key in ('studyId', 'inputRevision', 'inputSha256', 'referenceSha256'):
            if key in declared_binding and declared_binding[key] != binding[key]:
                fail('CFD_ARTIFACT_RESULT_BINDING_MISMATCH', 'Keep output reports bound to the same exact input/reference revision as their native files.')
        if data.get('calculationFreshness') == 'Current' or data.get('actualCfdRunVerified') is True or data.get('engineeringValidated') is True:
            fail('CFD_ARTIFACT_LIFECYCLE_INVALID', 'Import raw independent solver evidence for review; uploaded JSON cannot self-authorize Current or verified engineering acceptance.')
    for key, role in (('checkpoints', 'checkpoint'), ('timeHistories', 'time-history'), ('temporalEvidence', 'temporal-evidence')):
        if not isinstance(manifest[key], list) or len(manifest[key]) > limits.entries or manifest[key] != role_paths[role]:
            fail('CFD_ARTIFACT_EVIDENCE_INDEX_INVALID', 'Index every checkpoint/history/temporal document once in manifest file order.')
    if not manifest['checkpoints']:
        fail('CFD_ARTIFACT_CHECKPOINT_REQUIRED', 'Include at least one complete native checkpoint and all referenced native files.')
    checkpoint_reports = []
    for path in manifest['checkpoints']:
        report = validate_checkpoint_descriptor(documents[path], binding=binding, files=files)
        if not report['valid']:
            issue = report['diagnostics'][0]
            fail(issue['reasonCode'], issue['correctiveAction'])
        checkpoint = report['checkpoint']
        if checkpoint['solver'] != manifest['solver'] or checkpoint['method'] != manifest['method'] or checkpoint['evidenceRole'] != manifest['evidenceRole']:
            fail('CFD_ARTIFACT_SOLVER_METHOD_MISMATCH', 'Keep exact solver/version, physical method and evidence role identical in the manifest and all checkpoints.')
        checkpoint_reports.append({'path': path, 'checkpointId': checkpoint['checkpointId'], 'timeState': checkpoint['timeState'], 'restartBinding': report['restartBinding'], 'restartCompatible': report['restartCompatible'], 'restartExecutionPermitted': False, 'nativeFormatParsed': False, 'actualRestartExecutionVerified': False})
    histories, history_reports = ({}, [])
    for path in manifest['timeHistories']:
        data = documents[path]
        report = validate_time_history(data, binding=binding, files=files)
        if not report['valid']:
            issue = report['diagnostics'][0]
            fail(issue['reasonCode'], issue['correctiveAction'])
        if data['evidenceRole'] != manifest['evidenceRole']:
            fail('CFD_ARTIFACT_PROVENANCE_MISMATCH', 'Keep time-history evidence role identical to its artifact source.')
        histories[path] = data
        history_reports.append({'path': path, **report})
    temporal = [assess_temporal_evidence(documents[path], binding=binding, histories=histories) for path in manifest['temporalEvidence']]
    issues = [issue for report in temporal if not report['valid'] for issue in report['diagnostics']]
    if any((cp['timeState']['interpretation'] == 'physical' for cp in checkpoint_reports)):
        if not history_reports:
            issues.append({'reasonCode': 'CFD_TIME_HISTORY_EVIDENCE_REQUIRED', 'correctiveAction': 'Import physical time-history samples and their native source checksums before assessing transient response.'})
        if not temporal:
            issues.append({'reasonCode': 'CFD_TEMPORAL_EVIDENCE_REQUIRED', 'correctiveAction': 'Run/import three controlled time-step refinements; saved native checkpoints alone do not establish temporal convergence.'})
    result = {'schemaVersion': 'untirta.cfd-artifact-inspection.v1', 'status': 'ArtifactIntegrityVerified', 'valid': True, 'reasonCode': 'CFD_ARTIFACT_INTEGRITY_VERIFIED', 'artifactId': manifest['artifactId'], 'artifactSha256': sha256(raw).hexdigest(), 'archiveBytes': len(raw), 'uncompressedBytes': sum(map(len, files.values())), 'binding': binding, 'bindingMatched': expected_binding is not None, 'solver': manifest['solver'], 'method': manifest['method'], 'evidenceRole': manifest['evidenceRole'], 'provenance': provenance, 'solverExecutionClaimed': lifecycle['actualCfdRunClaimed'], 'actualCfdRunVerified': False, 'authenticityVerified': False, 'engineeringValidated': False, 'calculationFreshness': 'Imported Evidence', 'checkpoints': checkpoint_reports, 'timeHistories': history_reports, 'temporalEvidence': temporal, 'diagnostics': issues, 'mainApplicationWrites': False, 'inputPersistence': 'input-only', 'claim': 'Imported integrity evidence only. Run provenance, native reopen/restart and engineering acceptance remain unverified; L1/L2 remain independent.'}
    return (result, files, manifest)

def inspect_artifact(archive: bytes | Path, *, expected_binding: dict | None=None, limits: ArtifactLimits | None=None) -> dict:
    try:
        return _inspect(archive, expected_binding, limits or ArtifactLimits())[0]
    except WorkerDiagnostic as error:
        return diagnostic(error)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError, zipfile.BadZipFile, RuntimeError, NotImplementedError):
        return diagnostic(WorkerDiagnostic('CFD_ARTIFACT_INVALID', 'Choose an intact bounded CFD artifact ZIP with exact checksums and supported schemas.'))

def _workspace(root: Path) -> Path:
    if not isinstance(root, Path):
        fail('CFD_ARTIFACT_WORKSPACE_INVALID', 'Use a dedicated independent Lab workspace Path.')
    absolute = root.absolute()
    for part in (absolute, *absolute.parents):
        if _is_link(part):
            fail('CFD_ARTIFACT_WORKSPACE_INVALID', 'Use an independent artifact workspace without symbolic-link ancestors.')
    resolved = absolute.resolve()
    repository = Path(__file__).resolve().parents[4]
    protected = [repository / name for name in ('apps', 'services', 'contracts', 'architecture', 'deployment', '.git', 'tests', 'tools', 'data', 'database', 'supabase')]
    if resolved == repository or any((resolved == path or resolved.is_relative_to(path) for path in protected)):
        fail('CFD_ARTIFACT_WORKSPACE_INVALID', 'Persist artifacts only in an independent output workspace, outside Main source, contracts and deployment/database files.')
    return resolved

def create_artifact_bytes(*, artifact_id: str, binding: dict, solver: dict, method: dict, evidence_role: str, provenance: dict, lifecycle: dict, files: dict[str, tuple[bytes, str]]) -> bytes:
    """Export inert declared files; suitable for adapters and labeled contract fixtures."""
    unsupported = sorted({path.rsplit('/', 1)[-1] for path, (_, role) in files.items() if role == 'native' and path.rsplit('/', 1)[-1] not in NATIVE_NAMES and (not path.endswith(('.cas.h5', '.dat.h5', '.msh.h5', '.stl', '.obj')))})
    if unsupported:
        names = ', '.join((name for name in unsupported if re.fullmatch('[A-Za-z0-9_.-]{1,80}', name)))[:512]
        fail('CFD_ARTIFACT_NATIVE_TYPE_PROHIBITED', 'Review the actual unsupported native basenames before export: ' + (names or 'invalid basename') + '. Keep scripts/executables prohibited.')
    manifest = {'schemaVersion': SCHEMA, 'artifactId': artifact_id, 'binding': binding, 'solver': solver, 'method': method, 'evidenceRole': evidence_role, 'provenance': provenance, 'lifecycle': lifecycle, 'files': [{'path': path, 'sha256': sha256(raw).hexdigest(), 'bytes': len(raw), 'role': role} for path, (raw, role) in files.items()], 'checkpoints': [p for p, (_, role) in files.items() if role == 'checkpoint'], 'timeHistories': [p for p, (_, role) in files.items() if role == 'time-history'], 'temporalEvidence': [p for p, (_, role) in files.items() if role == 'temporal-evidence']}
    manifest['manifestChecksum'] = digest(manifest)
    limits = ArtifactLimits()
    if any((len(raw) > limits.file_bytes for raw, _ in files.values())) or sum((len(raw) for raw, _ in files.values())) > limits.total_bytes:
        fail('CFD_ARTIFACT_BUDGET_EXCEEDED', 'Reduce native evidence to the existing per-file and total byte budgets before packaging.')
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:

        def write_member(path, content):
            compressor = zlib.compressobj(wbits=-15)
            compressed_bytes = len(compressor.compress(content) + compressor.flush())
            method = zipfile.ZIP_STORED if len(content) > max(1, compressed_bytes) * limits.compression_ratio else zipfile.ZIP_DEFLATED
            archive.writestr(path, content, compress_type=method)
        write_member('manifest.json', json.dumps(manifest, allow_nan=False).encode())
        for path, (raw, _) in files.items():
            write_member(path, raw)
    raw = output.getvalue()
    report = inspect_artifact(raw, expected_binding=binding)
    if not report['valid']:
        issue = report['diagnostics'][0]
        fail(issue['reasonCode'], issue['correctiveAction'])
    return raw
