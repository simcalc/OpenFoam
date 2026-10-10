"""At most 1 MiB of sanitized failure diagnostics; never native CFD evidence."""

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/cfd_worker/src"))
from untirta_cfd_worker.artifacts import sanitize_log
from untirta_cfd_worker.contracts import WorkerDiagnostic

MAXIMUM_BYTES = 1024**2
LOG_BYTES = 64 * 1024


def _regular(path: Path, root: Path) -> None:
    if not path.resolve().is_relative_to(root.resolve()) or any(
        parent.is_symlink() or getattr(parent, "is_junction", lambda: False)()
        for parent in [path, *path.parents] if parent.is_relative_to(root)):
        raise WorkerDiagnostic("CFD_DIAGNOSTIC_PATH_INVALID", "Read only regular report/log files inside the dedicated output directory.")
    if not path.is_file():
        raise WorkerDiagnostic("CFD_DIAGNOSTIC_SOURCE_REQUIRED", "Choose regular local diagnostic report/log files.")


def _text(value, maximum=2000):
    return sanitize_log(str(value).encode()).decode()[:maximum]


def diagnostic_bytes(root: Path, report: Path) -> bytes:
    root, report = root.resolve(), report.absolute()
    if not report.resolve().is_relative_to(root):
        raise WorkerDiagnostic("CFD_DIAGNOSTIC_PATH_INVALID", "The source report must belong to the independent output directory.")
    source = {"status": "SourceReportUnavailable", "reasonCode": "CFD_DIAGNOSTIC_REPORT_UNAVAILABLE",
              "action": "Inspect the bounded scope startup log; the worker did not produce an accessible report.", "actualCfdRunClaimed": False}
    if report.exists():
        _regular(report, root)
        if report.stat().st_size <= 128 * 1024:
            try:
                data = json.loads(report.read_text(encoding="utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
                if isinstance(data, dict):
                    source = {"status": _text(data.get("status", "Unknown"), 128),
                              "reasonCode": _text(data.get("reasonCode", "CFD_DEVELOPMENT_REVIEW_REQUIRED"), 128),
                              "action": _text(data.get("action", "Review the bounded development stage logs.")),
                              "actualCfdRunClaimed": data.get("actualCfdRun") is True}
            except (ValueError, UnicodeError):
                source["reasonCode"] = "CFD_DIAGNOSTIC_REPORT_INVALID"
    members = {"output/diagnostic-report.json": json.dumps(source, allow_nan=False).encode()}
    descriptors = []
    # One wrapper log plus at most nine stage logs; no source input/reference or
    # arbitrary output/native files can enter this failure-only archive.
    paths = sorted(root.glob("*-run.log")) + sorted((root / "cfd-worker").glob("*/output/logs/*.log"))
    for index, path in enumerate(paths[:10]):
        _regular(path, root)
        source_bytes = path.stat().st_size
        # Sanitize complete bounded content before retaining a tail. Cutting an
        # assignment's prefix first could conceal its credential marker.
        if source_bytes > 4 * 1024**2:
            clean = b"[LOG EXCEEDS SANITIZATION BUDGET; CONTENT OMITTED]\n"
        else:
            with path.open("rb") as stream:
                raw = stream.read(4 * 1024**2 + 1)
            clean = sanitize_log(raw) if len(raw) <= 4 * 1024**2 else b"[LOG EXCEEDS SANITIZATION BUDGET; CONTENT OMITTED]\n"
        truncated = source_bytes > LOG_BYTES or len(clean) > LOG_BYTES
        marker = b"[TRUNCATED DIAGNOSTIC LOG TAIL]\n" if truncated else b""
        clean = marker + clean[-(LOG_BYTES - len(marker)):]
        relative = "output/logs/" + str(index) + "-" + path.name
        members[relative] = clean
        descriptors.append({"path": relative, "bytes": len(clean), "sha256": sha256(clean).hexdigest(),
                            "truncated": truncated, "sourceBytes": source_bytes})
    manifest = {"schemaVersion": "untirta.cfd-development-diagnostic-artifact.v1", "kind": "development-diagnostics-only",
        "status": "DevelopmentReviewRequired", "sourceReport": source, "actualCfdRunVerified": False,
        "engineeringValidated": False, "nativeImportCompatible": False, "includesNativeRestartFiles": False,
        "mainApplicationWrites": False, "maximumBytes": MAXIMUM_BYTES, "omittedLogCount": max(0, len(paths) - 10),
        "logs": descriptors, "claim": "Sanitized bounded failure logs only; no Current, native restart, Pump or CFD engineering acceptance."}
    members["manifest.json"] = json.dumps(manifest, allow_nan=False).encode()
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for relative, raw in members.items():
            archive.writestr(relative, raw)
    result = output.getvalue()
    if len(result) > MAXIMUM_BYTES:
        raise WorkerDiagnostic("CFD_DIAGNOSTIC_BUDGET_EXCEEDED", "Diagnostic ZIP cannot exceed 1 MiB; no partial artifact is exported.")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.resolve() == args.report.resolve() or args.output.exists():
            raise WorkerDiagnostic("CFD_DIAGNOSTIC_OVERWRITE_PROHIBITED", "Choose a new diagnostic ZIP distinct from the source report.")
        raw = diagnostic_bytes(args.root, args.report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(raw)
    except WorkerDiagnostic as exc:
        print(json.dumps({"status": "InputRequired", "reasonCode": exc.code, "action": exc.action, "actualCfdRunVerified": False}))
        return 2
    except (OSError, ValueError, TypeError):
        print(json.dumps({"status": "InputRequired", "reasonCode": "CFD_DIAGNOSTIC_EXPORT_UNAVAILABLE",
            "action": "Use accessible independent report/log files and a new writable diagnostic path.", "actualCfdRunVerified": False}))
        return 2
    print(json.dumps({"status": "DiagnosticsExported", "bytes": len(raw), "maximumBytes": MAXIMUM_BYTES,
                      "actualCfdRunVerified": False, "nativeImportCompatible": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
