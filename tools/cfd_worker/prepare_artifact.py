"""Bounded development evidence or an explicit strict native restart artifact."""

import argparse
import json
import os
from pathlib import Path
import sys
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/cfd_worker/src"))
from untirta_cfd_worker.contracts import WorkerDiagnostic, digest, file_digest, write_json
from untirta_cfd_worker.artifacts import create_artifact_bytes, sanitize_log
from untirta_cfd_worker.runner import validate_checkpoint


def native_artifact_bytes(workspace: Path, report: dict) -> bytes:
    """Build a strict archive of exact local files; declared execution is not verified authority."""
    binding = {key: report[key] for key in ("studyId", "inputRevision", "inputSha256", "referenceSha256")}
    members = {}
    budget = 0

    def add(relative: str, role: str):
        nonlocal budget
        original = workspace / relative
        path = original.resolve()
        if not path.is_relative_to(workspace.resolve()) or any(parent.is_symlink() for parent in [original, *original.parents] if parent.is_relative_to(workspace)) or not path.is_file():
            raise WorkerDiagnostic("CFD_ARTIFACT_PATH_INVALID", "Export only checksum-bound regular files from this isolated worker workspace.")
        if relative in members: return
        # Limit uncompressed read memory as well as final uploaded ZIP size.
        budget += path.stat().st_size
        if budget > 25 * 1024**2:
            raise WorkerDiagnostic("CFD_ARTIFACT_BUDGET_EXCEEDED", "Complete native export exceeds the conservative 25 MiB read/upload budget; retain the native study locally or use the smaller development evidence.")
        raw = path.read_bytes()
        members[relative] = (sanitize_log(raw) if role == "log" else raw, role)

    add("input/job.json", "input")
    add("reference/snapshot.json", "reference")
    add("output/manifest.json", "result")
    checkpoints = report.get("nativeCheckpointFiles", [])
    if not checkpoints:
        raise WorkerDiagnostic("CFD_NATIVE_CHECKPOINT_REQUIRED", "Produce a v2 checkpoint with exact native solution, mesh, settings and initial state before native export.")
    identity = None
    for relative in checkpoints:
        checkpoint = validate_checkpoint(workspace, workspace / relative, binding)
        if checkpoint["schemaVersion"] != "untirta.cfd-native-checkpoint.v2":
            raise WorkerDiagnostic("CFD_NATIVE_CHECKPOINT_REQUIRED", "Native export requires a v2 checkpoint; legacy development descriptors remain non-importable.")
        current = {key: checkpoint[key] for key in ("solver", "method", "evidenceRole")}
        if identity is not None and identity != current:
            raise WorkerDiagnostic("CFD_ARTIFACT_SOLVER_METHOD_MISMATCH", "Export one exact solver/method/evidence role per artifact.")
        identity = current
        add(relative, "checkpoint")
        for item in checkpoint["nativeFiles"] + checkpoint["meshFiles"] + checkpoint["settingsFiles"] + checkpoint["setupState"]["initialStateFiles"]:
            add(item["path"], "native")
    if report.get("actualCfdRun") and identity["evidenceRole"] != "actual-solver":
        raise WorkerDiagnostic("CFD_ARTIFACT_EVIDENCE_ROLE_MISMATCH", "Contract files cannot be exported as an actual-solver run.")
    for path in sorted((workspace / "output/logs").glob("*.log")):
        add(path.relative_to(workspace).as_posix(), "log")
    # Job studyId supports 80 characters, and runId adds a 13-character nonce.
    # Keep that original runId in output/manifest.json; use an opaque bounded ID
    # for the archive namespace without truncating source identity or binding.
    artifact_id = "cfd-" + digest({"runId": report["runId"], "binding": binding})
    raw = create_artifact_bytes(artifact_id=artifact_id, binding=binding,
        solver=identity["solver"], method=identity["method"], evidence_role=identity["evidenceRole"],
        provenance={"kind": ("github-actions-worker" if os.environ.get("GITHUB_ACTIONS") == "true" else "local-worker") if identity["evidenceRole"] == "actual-solver" else "educational-contract",
            "sourceApplication": "independent CFD worker", "claim": "Native development restart files; no Pump/NPSHR/NPSH3 engineering or authenticity claim."},
        lifecycle={"state": "solver-completed" if identity["evidenceRole"] == "actual-solver" else "contract-test",
            "actualCfdRunClaimed": report.get("actualCfdRun", False), "engineeringValidated": False, "calculationFreshness": "Imported Evidence"},
        files=members)
    if len(raw) > 25 * 1024**2:
        raise WorkerDiagnostic("CFD_ARTIFACT_BUDGET_EXCEEDED", "The strict native archive exceeds the declared 25 MiB upload cap.")
    return raw


def enforce_upload_budget(archive: Path, manifest: Path) -> None:
    if archive.stat().st_size + manifest.stat().st_size > 25 * 1024**2:
        archive.unlink()
        manifest.unlink()
        raise WorkerDiagnostic("CFD_ARTIFACT_BUDGET_EXCEEDED", "ZIP plus evidence manifest exceed the combined 25 MiB upload cap.")


def _export(args):
    sidecar = args.output.with_suffix(".manifest.json")
    if args.report.resolve() in {args.output.resolve(), sidecar.resolve()}:
        raise WorkerDiagnostic("CFD_ARTIFACT_SOURCE_OVERWRITE_PROHIBITED", "Choose archive and sidecar paths distinct from the source report.")
    if args.output.exists() or sidecar.exists():
        raise WorkerDiagnostic("CFD_ARTIFACT_OUTPUT_EXISTS", "Choose new archive and sidecar paths; existing evidence is never overwritten.")
    report = json.loads(args.report.read_text(encoding="utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
    if not isinstance(report, dict):
        raise WorkerDiagnostic("CFD_ARTIFACT_REPORT_INVALID", "Provide a finite structured independent worker report.")
    if not report.get("actualCfdRun") or report.get("status") != "DevelopmentBenchmarkComplete":
        raise WorkerDiagnostic("CFD_ARTIFACT_NUMERICAL_RUN_REQUIRED", "Only completed real development benchmark outputs can be published as CFD artifacts.")
    workspace = Path(report["workspace"]).resolve()
    if args.native_export:
        raw = native_artifact_bytes(workspace, report)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(raw)
        artifact_manifest = args.output.with_suffix(".manifest.json")
        write_json(artifact_manifest, {"schemaVersion": "untirta.cfd-development-artifact.v1", "inputSha256": report["inputSha256"],
            "referenceSha256": report["referenceSha256"], "artifactSha256": file_digest(args.output), "bytes": len(raw), "retentionDays": 7,
            "includesNativeRestartFiles": True, "nativeImportSchema": "untirta.cfd-artifact.v1", "actualCfdRunVerified": False,
            "claim": "Exact native restart integrity evidence only; imported checksums do not verify actual solver execution or Pump physics."})
        enforce_upload_budget(args.output, artifact_manifest)
        return
    files = [workspace / "output" / "manifest.json"] + sorted((workspace / "checkpoints").glob("*.json"))
    files += sorted((workspace / "output" / "logs").glob("*.log"))
    if sum(path.stat().st_size for path in files) > 25 * 1024**2:
        raise WorkerDiagnostic("CFD_ARTIFACT_BUDGET_EXCEEDED", "The development artifact exceeds the 25 MiB limit; upload is disabled.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            if path.is_symlink() or not path.resolve().is_relative_to(workspace):
                raise WorkerDiagnostic("CFD_ARTIFACT_PATH_INVALID", "Artifacts must contain regular files from this isolated output workspace.")
            if path.suffix == ".log":
                archive.writestr(path.relative_to(workspace).as_posix(), sanitize_log(path.read_bytes()))
            else:
                archive.write(path, path.relative_to(workspace).as_posix())
    if args.output.stat().st_size > 25 * 1024**2:
        args.output.unlink()
        raise WorkerDiagnostic("CFD_ARTIFACT_BUDGET_EXCEEDED", "Compressed artifact exceeds the declared cap.")
    artifact_manifest = args.output.with_suffix(".manifest.json")
    write_json(artifact_manifest, {"schemaVersion": "untirta.cfd-development-artifact.v1",
               "inputSha256": report["inputSha256"], "referenceSha256": report["referenceSha256"],
               "artifactSha256": file_digest(args.output), "bytes": args.output.stat().st_size,
               "retentionDays": 7, "includesNativeRestartFiles": False,
               "nativeImportCompatible": False,
               "claim": "numerical-development evidence only; native restart files remain local to the runner"})
    enforce_upload_budget(args.output, artifact_manifest)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-export", action="store_true", help="Strict untirta.cfd-artifact.v1 with native restart files, capped at 25 MiB; separate benchmark binding, no Pump claim")
    args = parser.parse_args(argv)
    try:
        _export(args)
    except WorkerDiagnostic as exc:
        result = {"schemaVersion": "untirta.cfd-development-artifact-result.v1", "status": "InputRequired",
                  "reasonCode": exc.code, "action": exc.action, "actualCfdRunVerified": False, "mainApplicationWrites": False}
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        result = {"schemaVersion": "untirta.cfd-development-artifact-result.v1", "status": "InputRequired",
                  "reasonCode": "CFD_ARTIFACT_EXPORT_UNAVAILABLE", "action": "Use an accessible finite worker report and new writable output paths; source reports and existing artifacts remain unchanged.",
                  "actualCfdRunVerified": False, "mainApplicationWrites": False}
    else:
        print(json.dumps({"schemaVersion": "untirta.cfd-development-artifact-result.v1", "status": "ArtifactExported",
            "archivePath": str(args.output.resolve()), "sidecarPath": str(args.output.with_suffix(".manifest.json").resolve()),
            "includesNativeRestartFiles": args.native_export, "actualCfdRunVerified": False, "mainApplicationWrites": False}))
        return 0
    print(json.dumps(result, allow_nan=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
