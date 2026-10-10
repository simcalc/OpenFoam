"""Input-only jobs and immutable output/reference binding (stdlib only)."""
from __future__ import annotations
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any

class WorkerDiagnostic(ValueError):

    def __init__(self, code: str, action: str):
        self.code, self.action = (code, action)
        super().__init__(action)

def digest(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def file_digest(path: Path) -> str:
    result = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()

def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)

def finite(value: Any, positive: bool=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (not math.isfinite(value)) or (positive and value <= 0):
        raise WorkerDiagnostic('CFD_INPUT_VALUE_INVALID', 'Supply a finite engineering value in the declared SI unit.')
    return float(value)

@dataclass(frozen=True)
class Limits:
    memory_bytes: int = 2 * 1024 ** 3
    timeout_seconds: float = 900
    disk_bytes: int = 256 * 1024 ** 2
    log_bytes: int = 2 * 1024 ** 2
    threads: int = 1

    @classmethod
    def parse(cls, data: dict[str, Any]) -> 'Limits':
        if not isinstance(data, dict) or set(data) - {'memoryBytes', 'timeoutSeconds', 'diskBytes', 'logBytes', 'threads'}:
            raise WorkerDiagnostic('CFD_LIMITS_INVALID', 'Use only the supported CPU, memory, time, log and disk limits.')
        for key in ('memoryBytes', 'diskBytes', 'logBytes', 'threads'):
            if key in data and (isinstance(data[key], bool) or not isinstance(data[key], int)):
                raise WorkerDiagnostic('CFD_LIMITS_INVALID', 'Resource byte counts and thread count must be integers.')
        values = cls(int(finite(data.get('memoryBytes', 2 * 1024 ** 3), True)), finite(data.get('timeoutSeconds', 900), True), int(finite(data.get('diskBytes', 256 * 1024 ** 2), True)), int(finite(data.get('logBytes', 2 * 1024 ** 2), True)), int(finite(data.get('threads', 1), True)))
        if not (32 * 1024 ** 2 <= values.memory_bytes <= 2 * 1024 ** 3 and values.timeout_seconds <= 1500 and (1024 <= values.disk_bytes <= 512 * 1024 ** 2) and (1024 <= values.log_bytes <= 4 * 1024 ** 2) and (values.threads == 1)):
            raise WorkerDiagnostic('CFD_LIMITS_OUTSIDE_PROFILE', 'Use one thread, <=2 GiB memory, <=1500 seconds and bounded local output.')
        return values

@dataclass(frozen=True)
class Job:
    study_id: str
    revision: str
    adapter: str
    profile: str
    source: dict[str, Any]
    reference: dict[str, Any]
    limits: Limits

    @classmethod
    def parse(cls, source: dict[str, Any], reference: dict[str, Any]) -> 'Job':
        expected = {'schemaVersion', 'studyId', 'inputRevision', 'adapter', 'caseProfile', 'referenceSha256', 'limits', 'parameters'}
        if not isinstance(source, dict) or set(source) != expected or source.get('schemaVersion') != 'untirta.cfd-worker-job.v1':
            raise WorkerDiagnostic('CFD_JOB_SCHEMA_INVALID', 'Import an input-only untirta.cfd-worker-job.v1 package.')
        for key in ('studyId', 'inputRevision'):
            if not isinstance(source[key], str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', source[key]):
                raise WorkerDiagnostic('CFD_JOB_ID_INVALID', 'Use a bounded study ID and revision without path characters.')
        try:
            reference_hash = digest(reference)
        except (ValueError, TypeError):
            raise WorkerDiagnostic('CFD_REFERENCE_BINDING_INVALID', 'Reference fields must be finite JSON values.') from None
        if not isinstance(reference, dict) or source['referenceSha256'] != reference_hash:
            raise WorkerDiagnostic('CFD_REFERENCE_BINDING_INVALID', 'Re-export the exact reference package for this input revision.')
        if not isinstance(source['parameters'], dict):
            raise WorkerDiagnostic('CFD_PARAMETERS_INVALID', 'Supply the parameters required by the selected profile.')
        adapter, profile = (source['adapter'], source['caseProfile'])
        if set(reference) != {'schemaVersion', 'kind', 'caseProfile', 'sourceCommit'} or reference.get('schemaVersion') != 'untirta.cfd-benchmark-reference.v1':
            raise WorkerDiagnostic('CFD_PUBLIC_REFERENCE_SCHEMA_INVALID', 'Only the generated fixed tutorial reference is allowed; no student/Main snapshots.')
        allowed = {'openfoam': {'transient-cavity-v1', 'cavitation-bullet-v1'}}
        if not isinstance(adapter, str) or not isinstance(profile, str) or adapter not in allowed or (profile not in allowed[adapter]):
            raise WorkerDiagnostic('CFD_PROFILE_UNSUPPORTED', 'Select an approved versioned adapter profile; arbitrary commands are not accepted.')
        if adapter == 'openfoam' and source['parameters']:
            raise WorkerDiagnostic('CFD_BENCHMARK_PARAMETERS_LOCKED', 'Development fixtures use fixed parameters; they are not user Pump studies.')
        if adapter == 'openfoam' and reference.get('kind') != 'development-benchmark':
            raise WorkerDiagnostic('CFD_BENCHMARK_REFERENCE_INVALID', 'These fixture profiles are limited to project numerical development benchmarks.')
        if adapter == 'openfoam' and (reference.get('caseProfile') != profile or reference.get('sourceCommit') != '0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd'):
            raise WorkerDiagnostic('CFD_BENCHMARK_REFERENCE_INVALID', 'Use the exact approved benchmark profile and pinned source commit reference.')
        return cls(source['studyId'], source['inputRevision'], adapter, profile, source, reference, Limits.parse(source['limits']))

    def binding(self) -> dict[str, str]:
        return {'studyId': self.study_id, 'inputRevision': self.revision, 'inputSha256': digest(self.source), 'referenceSha256': digest(self.reference)}
