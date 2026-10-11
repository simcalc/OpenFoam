"""Fixed Foundation 12 cavity discretisation experiment; no Pump input surface.

The legacy RAS profile retains five fixed solves to0.05s. An explicit laminar
Re100 derivative runs six fixed solves to0.5s, including a tighter linear-solver
repeat. The finest mesh / finest time step is shared by the spatial and temporal
series. Neither profile asserts steady-state, experimental, Pump, NPSHr or
qualified asymptotic convergence acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/cfd_worker/src"))
from untirta_cfd_worker.artifacts import sanitize_log
from untirta_cfd_worker.contracts import Limits, WorkerDiagnostic, digest, file_digest, write_json
from untirta_cfd_worker.impeller import checked_field, points
from untirta_cfd_worker.openfoam import _logged_seconds, replace_entry, verify_mesh_log
from untirta_cfd_worker.resources import ProcessRunner
from untirta_cfd_worker.runner import single_job
from untirta_cfd_worker.profiles import OPENFOAM_PACKAGE_BUILD, OPENFOAM_PACKAGE_SHA256

SOURCE_COMMIT = "0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd"
PROVENANCE_SHA256 = "2345ea9afeb49935ad406ec0da2d5f14a072d9ae7941c42c69fb7f659eee9414"
SOURCE_URL = "https://github.com/OpenFOAM/OpenFOAM-12/tree/" + SOURCE_COMMIT + "/tutorials/incompressibleFluid/cavity"
CAMPAIGN = "cavity-discretisation-v1"
END_TIME = 0.05
OUTPUT_INTERVAL = 0.01
TOLERANCE = 0.01  # Declared educational sensitivity criterion, not physical accuracy.
QUANTITY = "specific-kinetic-energy"
MAX_ARCHIVE = 25 * 1024**2
RUNS = (("mesh20-dt00025", 20, 0.00025), ("mesh40-dt00025", 40, 0.00025),
        ("mesh80-dt00025", 80, 0.00025), ("mesh80-dt001", 80, 0.001),
        ("mesh80-dt0005", 80, 0.0005))
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


@dataclass(frozen=True)
class CampaignProfile:
    id: str
    end_time: float
    output_interval: float
    runs: tuple
    laminar: bool = False


LEGACY = CampaignProfile(CAMPAIGN, END_TIME, OUTPUT_INTERVAL, RUNS)
LAMINAR = CampaignProfile("cavity-laminar-re100-discretisation-v1", 0.5, 0.1,
    RUNS + (("mesh80-dt00025-tight", 80, 0.00025),), True)
PROFILES = {"ras-early": LEGACY, "laminar-re100": LAMINAR}
LINEAR_TOLERANCE = "1e-9"
TIGHT_LINEAR_TOLERANCE = "1e-11"
ITERATIVE_SENSITIVITY_FRACTION = 0.1
REFERENCES = [
    {"title": "OpenFOAM v10 User Guide, 2.1 Lid-driven cavity flow",
     "url": "https://doc.cfd.direct/openfoam/user-guide-v10/cavity",
     "pages": "Web section 2.1.1.3 and 2.1.7.1; no printed page numbering",
     "equation": "2.1, Re=L*U/nu; prescribed laminar viscosity1e-3 m2/s yields Re100 for L=.1m,U=1m/s",
     "scope": "Educational physical parameter choice only; runtime uses pinned Foundation12 foamRun, not v10 icoFoam."},
    {"title": "OpenFOAM v12 User Guide, 4.6 Solution and algorithm control",
     "url": "https://doc.cfd.direct/openfoam/user-guide-v12/fvsolution",
     "pages": "Web sections4.6.2 and4.6.7; no printed page numbering",
     "scope": "Absolute versus relative linear tolerance and PIMPLE correctors; tightening linear solves does not prove nonlinear convergence."},
    {"title": "NASA NPARC Alliance: Examining Spatial (Grid) Convergence",
     "url": "https://www.grc.nasa.gov/www/wind/valid/tutorial/spatconv.html",
     "pages": "Grid considerations, observed order and GCI; no printed page numbering",
     "scope": "Systematic refinement must separate iterative error; numerical convergence is distinct from physical validation."}]


def profile_identity(source_identity: dict, profile: CampaignProfile, *, tight: bool = False) -> dict:
    """Distinguish an explicit laminar derivative from the immutable RAS source."""
    if not profile.laminar:
        return source_identity
    return {**source_identity,
        "physicsSettingsSha256": digest({"immutableSourceSettingsSha256": source_identity["physicsSettingsSha256"],
            "simulationType": "laminar", "kinematicViscosityM2S": 0.001,
            "temporalScheme": "Euler", "linearTolerance": TIGHT_LINEAR_TOLERANCE if tight else LINEAR_TOLERANCE, "relativeLinearTolerance": 0,
            "pressureCorrectors": 2, "outerCorrectors": 1})}


def descriptor(path: Path, workspace: Path) -> dict:
    return {"path": path.relative_to(workspace).as_posix(), "sha256": file_digest(path), "bytes": path.stat().st_size}


def verified_source(fixture: Path) -> dict:
    provenance_path = fixture / "provenance.json"
    if file_digest(provenance_path) != PROVENANCE_SHA256:
        raise WorkerDiagnostic("CFD_FIXTURE_PROVENANCE_CHANGED", "Restore the exact reviewed immutable official tutorial provenance.")
    provenance = json.loads(provenance_path.read_text())
    if provenance.get("commit") != SOURCE_COMMIT:
        raise WorkerDiagnostic("CFD_FIXTURE_SOURCE_INVALID", "Use the exact Foundation 12 source revision.")
    rows = [item for item in provenance["files"] if item["path"].startswith("transient-cavity-v1/")]
    expected = set()
    for item in rows:
        path = fixture / item["path"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != item["bytes"] or file_digest(path) != item["sha256"]:
            raise WorkerDiagnostic("CFD_FIXTURE_CHECKSUM_INVALID", "Restore every immutable cavity source byte before creating a derivative.")
        expected.add(path.relative_to(fixture / "transient-cavity-v1").as_posix())
    actual = {p.relative_to(fixture / "transient-cavity-v1").as_posix() for p in (fixture / "transient-cavity-v1").rglob("*") if p.is_file()}
    if not rows or actual != expected:
        raise WorkerDiagnostic("CFD_FIXTURE_FILE_SET_INVALID", "The exact reviewed cavity subset is required; arbitrary case files are prohibited.")
    source = fixture / "transient-cavity-v1"
    return {"sourceIdentity": {"sourceId": SOURCE_URL, "revision": SOURCE_COMMIT, "sha256": PROVENANCE_SHA256},
        "geometrySha256": digest({"sourceBlockMeshSha256": file_digest(source / "system/blockMeshDict"), "dimensionsM": [0.1, 0.1, 0.01], "emptyDirection": "z"}),
        "physicsSettingsSha256": digest({p: file_digest(source / p) for p in ("constant/physicalProperties", "constant/momentumTransport", "system/fvSchemes", "system/fvSolution")}),
        "boundaryConditionsSha256": digest({p.relative_to(source).as_posix(): file_digest(p) for p in sorted((source / "0").iterdir())}),
        "samplingDefinitionSha256": digest({"quantity": QUANTITY, "method": "0.5*sum(cellVolume*magSqr(U))/sum(cellVolume)", "domain": "every native fluid cell", "uniformVolumesRequireNativeMeshProof": True, "unit": "m2/s2"})}


def prepare_case(source: Path, case: Path, divisions: int, dt: float,
                 profile: CampaignProfile = LEGACY, *, tight: bool = False) -> dict:
    if profile not in (LEGACY, LAMINAR) or (divisions, dt) not in {(n, step) for _, n, step in profile.runs} or (tight and (profile != LAMINAR or (divisions, dt) != (80, 0.00025))):
        raise WorkerDiagnostic("CFD_CAMPAIGN_PROFILE_INVALID", "Only the fixed reviewed profile grid/time-step/tolerance combinations may run.")
    shutil.copytree(source, case)
    block = case / "system/blockMeshDict"
    text, count = re.subn(r"hex \(0 1 2 3 4 5 6 7\) \(20 20 1\) simpleGrading \(1 1 1\)",
                        f"hex (0 1 2 3 4 5 6 7) ({divisions} {divisions} 1) simpleGrading (1 1 1)", block.read_text())
    if count != 1:
        raise WorkerDiagnostic("CFD_FIXTURE_SETUP_INVALID", "Use the exact one-block uniform official square cavity geometry.")
    block.write_text(text)
    controls = {"endTime": str(profile.end_time), "deltaT": str(dt), "writeControl": "timeStep",
                "writeInterval": str(round(profile.output_interval / dt)), "writePrecision": "12", "writeFormat": "ascii",
                "writeCompression": "off", "purgeWrite": "0", "runTimeModifiable": "false"}
    for key, value in controls.items():
        replace_entry(case / "system/controlDict", key, value)
    if profile.laminar:
        replace_entry(case / "constant/momentumTransport", "simulationType", "laminar")
        replace_entry(case / "constant/physicalProperties", "nu", "0.001 [m^2/s]")
        solution = case / "system/fvSolution"
        tolerance = TIGHT_LINEAR_TOLERANCE if tight else LINEAR_TOLERANCE
        text, count = re.subn(r"(\btolerance\s+)(?:1e-06|1e-05)(;)", rf"\g<1>{tolerance}\2", solution.read_text())
        text, relative_count = re.subn(r"(\brelTol\s+)0\.1(;)", r"\g<1>0\2", text)
        if count != 3 or relative_count != 1:
            raise WorkerDiagnostic("CFD_FIXTURE_SETUP_INVALID", "All three original linear tolerances and the original relative pressure tolerance must be identified exactly.")
        solution.write_text(text)
    return {"divisions": [divisions, divisions, 1], "controls": controls, "geometryChanged": False,
            "physicsOrBoundaryConditionsChanged": profile.laminar, "boundaryConditionsChanged": False,
            "temporalScheme": "Euler", "turbulenceModel": "laminar" if profile.laminar else "kEpsilon-RAS",
            "kinematicViscosityM2S": 0.001 if profile.laminar else 1e-5,
            "linearTolerance": (TIGHT_LINEAR_TOLERANCE if tight else LINEAR_TOLERANCE) if profile.laminar else None,
            "pressureCorrectors": 2, "outerCorrectors": 1, "tightLinearRepeat": tight,
            "derivedFileSha256": {p: file_digest(case / p) for p in ("system/controlDict", "system/blockMeshDict", "constant/momentumTransport", "constant/physicalProperties", "system/fvSolution")}}


def verify_uniform_mesh(case: Path, divisions: int, log: str) -> dict:
    mesh = verify_mesh_log(log)
    if mesh["cellCount"] != divisions**2:
        raise WorkerDiagnostic("CFD_CAMPAIGN_MESH_COUNT_MISMATCH", "The actual checkMesh count must equal the declared N by N by one grid.")
    parsed = points(case / "constant/polyMesh/points")
    expected = {(i, j, k) for i in range(divisions + 1) for j in range(divisions + 1) for k in (0, 1)}
    actual = set()
    for x, y, z in parsed:
        indices = (round(x * divisions / 0.1), round(y * divisions / 0.1), round(z / 0.01))
        reconstructed = (indices[0] * 0.1 / divisions, indices[1] * 0.1 / divisions, indices[2] * 0.01)
        if any(not math.isclose(a, b, abs_tol=1e-10, rel_tol=1e-10) for a, b in zip((x, y, z), reconstructed)):
            raise WorkerDiagnostic("CFD_CAMPAIGN_NONUNIFORM_MESH", "Native points must occupy the complete uniform original square lattice; point aliases cannot inflate grid resolution.")
        actual.add(indices)
    if len(parsed) != 2 * (divisions + 1)**2 or actual != expected or len(actual) != len(parsed):
        raise WorkerDiagnostic("CFD_CAMPAIGN_NONUNIFORM_MESH", "Use every distinct native lattice vertex once with the exact original 0.1 by 0.1 by 0.01 m domain.")
    values = re.findall(r"Min volume = (" + NUMBER + r")\. Max volume = (" + NUMBER + r")\.\s+Total volume = (" + NUMBER + r")\.\s+Cell volumes OK\.", log)
    volume = 0.1 * 0.1 * 0.01
    if len(values) != 1 or any(not math.isclose(float(a), b, rel_tol=2e-5, abs_tol=1e-15) for a, b in zip(values[0], (volume / divisions**2, volume / divisions**2, volume))):
        raise WorkerDiagnostic("CFD_CAMPAIGN_CELL_VOLUME_INVALID", "Actual checkMesh min/max/total volumes must verify the uniform-cell quadrature and unchanged domain volume.")
    mesh.update({"uniformNativeLatticeVerified": True, "uniformCellVolumesVerified": True,
                 "cellVolumeM3": volume / divisions**2, "totalVolumeM3": volume,
                 "representativeSpacingM": 0.1 / divisions, "gridDimension": 2})
    return mesh


def kinetic_energy(path: Path, cell_count: int, cell_volume: float) -> tuple[float, dict]:
    values = checked_field(path, "0 1 -1 0 0 0 0", 3, cell_count)
    vectors = list(zip(values[::3], values[1::3], values[2::3]))
    if len(vectors) == 1:  # Native uniform declaration applies to every cell.
        sum_squares = cell_count * math.fsum(v * v for v in vectors[0])
    else:
        sum_squares = math.fsum(math.fsum(v * v for v in row) for row in vectors)
    total_volume = cell_count * cell_volume
    integrated = 0.5 * cell_volume * sum_squares
    value = integrated / total_volume
    if not all(math.isfinite(v) for v in (sum_squares, integrated, value)):
        raise WorkerDiagnostic("CFD_CAMPAIGN_QUANTITY_NONFINITE", "Preserve the raw native field and inspect overflow/nonfinite quadrature; do not replace values.")
    return value, {"nativeDeclarationVectors": len(vectors), "evaluatedCellCount": cell_count,
                   "sumSpeedSquaredM2S2": sum_squares, "cellVolumeM3": cell_volume, "totalVolumeM3": total_volume}


def diagnostic_history(log: str, dt: float, profile: CampaignProfile = LEGACY) -> dict:
    if re.findall(r"(?m)^Build\s*:\s*([^\r\n]+)\s*$", log) != [OPENFOAM_PACKAGE_BUILD]:
        raise WorkerDiagnostic("CFD_CAMPAIGN_SOLVER_BUILD_MISMATCH", "Use the exact checksum-pinned Foundation 12 package build, recorded by the actual solver log.")
    samples, current, pending_courant = [], None, None
    for line in log.splitlines():
        courant = re.fullmatch(r"\s*Courant Number mean:\s*(" + NUMBER + r")\s+max:\s*(" + NUMBER + r")\s*", line)
        if courant:
            pending_courant = {"mean": float(courant[1]), "max": float(courant[2])}
        time = _logged_seconds(line, "Time")
        if time is not None:
            current = {"timeSeconds": time, "timeStepSeconds": dt, "courant": pending_courant,
                       "linearSolves": [], "continuity": []}
            samples.append(current)
        residual = re.search(r"Solving for (Ux|Uy|p|k|epsilon), Initial residual = (" + NUMBER + r"), Final residual = (" + NUMBER + r"), No Iterations (\d+)\s*$", line)
        continuity = re.fullmatch(r"\s*time step continuity errors\s*:\s*sum local = (" + NUMBER + r"), global = (" + NUMBER + r")(?:, cumulative = (" + NUMBER + r"))?\s*", line)
        if residual and current:
            current["linearSolves"].append({"field": residual[1], "initialResidual": float(residual[2]), "finalResidual": float(residual[3]), "iterations": int(residual[4])})
        if continuity and current:
            current["continuity"].append({"sumLocal": float(continuity[1]), "global": float(continuity[2]), "cumulative": float(continuity[3]) if continuity[3] else None})
    times = [row["timeSeconds"] for row in samples]
    expected = round(profile.end_time / dt)
    if len(samples) != expected or any(not math.isclose(t, (i + 1) * dt, abs_tol=1e-10, rel_tol=1e-10) for i, t in enumerate(times)) or not re.search(r"(?m)^\s*End\s*$", log):
        raise WorkerDiagnostic("CFD_CAMPAIGN_TIME_SEQUENCE_INVALID", "The real solver must complete every declared fixed physical step to the profile end time; steady iteration or missing time samples cannot substitute.")
    final_residuals, last_continuity = [], []
    for row in samples:
        last = {item["field"]: item["finalResidual"] for item in row["linearSolves"]}
        fields = {"Ux", "Uy", "p"} if profile.laminar else {"Ux", "Uy", "p", "k", "epsilon"}
        if set(last) != fields or row["courant"] is None or not row["continuity"]:
            raise WorkerDiagnostic("CFD_CAMPAIGN_DIAGNOSTIC_REQUIRED", "Every actual physical step requires velocity/pressure/turbulence linear residuals, continuity and logged Courant numbers.")
        final_residuals.append(last)
        last_continuity.append(row["continuity"][-1])
    json.dumps(samples, allow_nan=False)  # Reject overflow without replacing a sample.
    if any(row["courant"]["mean"] < 0 or row["courant"]["max"] < 0 for row in samples) or any(v < 0 for row in final_residuals for v in row.values()):
        raise WorkerDiagnostic("CFD_CAMPAIGN_DIAGNOSTIC_INVALID", "Logged Courant numbers and normalized linear residuals must remain finite nonnegative values.")
    return {"samples": samples, "physicalStepCount": expected, "maximumCourantObserved": max(row["courant"]["max"] for row in samples),
            "maxLastCorrectionLinearResidual": {name: max(row[name] for row in final_residuals) for name in sorted(fields)},
            "maximumLastCorrectionContinuitySumLocal": max(abs(row["sumLocal"]) for row in last_continuity),
            "maximumLastCorrectionContinuityGlobalAbsolute": max(abs(row["global"]) for row in last_continuity),
            "continuityBasis": "Native OpenFOAM time-step continuity diagnostics; not open-Pump inlet/outlet mass-imbalance ratio.",
            "numericalQualitySatisfied": None, "claim": "Observed linear/time-integration diagnostics only; iterative error insignificance and physical accuracy are unqualified."}


def collect_run(case: Path, workspace: Path, run_id: str, divisions: int, dt: float, identities: dict, binding: dict,
                profile: CampaignProfile = LEGACY) -> tuple[dict, dict, dict]:
    folder = case.parent
    mesh = verify_uniform_mesh(case, divisions, (folder / "logs/checkMesh.log").read_text())
    diagnostics = diagnostic_history((folder / "logs/foamRun.log").read_text(), dt, profile)
    mesh_files = sorted(p for p in (case / "constant/polyMesh").iterdir() if p.is_file())
    mesh_hash = digest([descriptor(p, case) for p in mesh_files])
    samples, native_files, final_quadrature = [], [], None
    initial_value, _ = kinetic_energy(case / "0/U", divisions**2, mesh["cellVolumeM3"])
    samples.append({"timeSeconds": 0, "values": {QUANTITY: initial_value}})
    for index in range(1, round(profile.end_time / profile.output_interval) + 1):
        t = round(index * profile.output_interval, 10)
        time_folders = [p for p in case.iterdir() if p.is_dir() and re.fullmatch(NUMBER, p.name) and math.isclose(float(p.name), t, abs_tol=1e-12)]
        if len(time_folders) != 1:
            raise WorkerDiagnostic("CFD_CAMPAIGN_NATIVE_TIME_REQUIRED", "Each common physical monitor time requires its own saved actual native U field; no interpolation/last-valid alias.")
        native = time_folders[0] / "U"
        value, quadrature = kinetic_energy(native, divisions**2, mesh["cellVolumeM3"])
        samples.append({"timeSeconds": t, "values": {QUANTITY: value}})
        native_files.append(descriptor(native, workspace))
        final_quadrature = quadrature
    fields = (("p", "0 2 -2 0 0 0 0"),) if profile.laminar else (("p", "0 2 -2 0 0 0 0"), ("k", "0 2 -2 0 0 0 0"), ("epsilon", "0 2 -3 0 0 0 0"), ("nut", "0 2 -1 0 0 0 0"))
    for name, dimensions in fields:
        native = time_folders[0] / name
        values = checked_field(native, dimensions, 1, divisions**2)
        if name != "p" and min(values) < 0:
            raise WorkerDiagnostic("CFD_CAMPAIGN_TURBULENCE_DOMAIN_INVALID", "Preserve native fields; negative turbulent energy/dissipation/viscosity cannot be accepted.")
        native_files.append(descriptor(native, workspace))
    native_files.extend(descriptor(p, workspace) for p in mesh_files)
    native_files.append(descriptor(case / "system/blockMeshDict", workspace))
    report = {"schemaVersion": "untirta.cfd-numerical-quantity.v1", "evidenceRole": "actual-solver", **identities,
              "solverIdentity": {"distribution": "OpenFOAM-Foundation", "version": "12"},
              "physicalTimeSeconds": profile.end_time, "timeStepSeconds": dt, "quantity": {"id": QUANTITY, "unit": "m2/s2", "value": samples[-1]["values"][QUANTITY]},
              "numericalQualitySatisfied": None, "sourceFiles": native_files}
    history = {"schemaVersion": "untirta.cfd-time-history.v1", "binding": binding, "evidenceRole": "actual-solver", "timeUnit": "s",
               "quantities": [{"id": QUANTITY, "unit": "m2/s2"}], "samples": samples,
               "sourceFiles": [descriptor(case / "0/U", workspace)] + native_files}
    row = {"id": run_id, "divisions": divisions, "cellCount": divisions**2, "timeStepSeconds": dt, "physicalEndTimeSeconds": profile.end_time,
           "meshSha256": mesh_hash, "mesh": mesh, "status": "ActualNativeSamplesCollected", "diagnostics": diagnostics,
           "quantity": report["quantity"], "quadrature": final_quadrature,
           "sourceLogSha256": {name: file_digest(folder / "logs" / name) for name in ("checkMesh.log", "foamRun.log")},
           "reportPath": (folder / "numerical-quantity.json").relative_to(workspace).as_posix(),
           "historyPath": (folder / "time-history.json").relative_to(workspace).as_posix()}
    row["equations"] = [{"id": "cavity.specific-kinetic-energy", "input": {"cellCount": divisions**2, **final_quadrature},
        "inputMathMl": '<math><mrow><mi>N</mi><mo>=</mo><mn>' + str(divisions**2) + '</mn><mtext> cells; Vc=' + str(mesh["cellVolumeM3"]) + ' m3; sum |U|2=' + str(final_quadrature["sumSpeedSquaredM2S2"]) + ' m2/s2</mtext></mrow></math>',
        "formulaMathMl": '<math><mrow><mi>K</mi><mo>=</mo><mfrac><mrow><mn>1</mn><mo>×</mo><munderover><mo>∑</mo><mi>c</mi><mi>N</mi></munderover><msub><mi>V</mi><mi>c</mi></msub><msup><mrow><mo>|</mo><msub><mi>U</mi><mi>c</mi></msub><mo>|</mo></mrow><mn>2</mn></msup></mrow><mrow><mn>2</mn><mo>×</mo><munderover><mo>∑</mo><mi>c</mi><mi>N</mi></munderover><msub><mi>V</mi><mi>c</mi></msub></mrow></mfrac></mrow></math>',
        "substitutionMathMl": '<math><mrow><mi>K</mi><mo>=</mo><mfrac><mrow><mn>0.5</mn><mo>×</mo><mn>' + str(mesh["cellVolumeM3"]) + '</mn><mo>×</mo><mn>' + str(final_quadrature["sumSpeedSquaredM2S2"]) + '</mn></mrow><mn>' + str(mesh["totalVolumeM3"]) + '</mn></mfrac></mrow></math>',
        "result": report["quantity"]["value"], "unit": "m2/s2", "source": SOURCE_URL,
        "authority": "Python native-field volume quadrature; every actual cell; no frontend recalculation"}]
    return row, report, history


def write_archive(workspace: Path, output: Path, profile: CampaignProfile = LEGACY) -> dict:
    """Bounded evidence-only ZIP. It is deliberately not a Pump native importer."""
    if output.exists() or output.resolve().is_relative_to(workspace.resolve()):
        raise WorkerDiagnostic("CFD_CAMPAIGN_ARCHIVE_PATH_INVALID", "Write a fresh archive outside the immutable campaign run directory.")
    selected = sorted(p for p in workspace.rglob("*") if p.is_file() and p.name != "cancel.request")
    descriptors, total = [], 0
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        raise WorkerDiagnostic("CFD_CAMPAIGN_ARCHIVE_PATH_INVALID", "Preserve an existing temporary artifact; choose a fresh output path.")
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in selected:
                if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()) or path.stat().st_size > 4 * 1024**2:
                    raise WorkerDiagnostic("CFD_CAMPAIGN_ARCHIVE_SOURCE_INVALID", "Only bounded regular campaign native/settings/report/log files may enter the evidence archive.")
                raw = path.read_bytes()
                if path.suffix == ".log": raw = sanitize_log(raw)
                total += len(raw)
                if total > 64 * 1024**2:
                    raise WorkerDiagnostic("CFD_CAMPAIGN_ARCHIVE_BUDGET", "Preserve raw local results; the campaign source evidence exceeds its explicit archive budget.")
                relative = path.relative_to(workspace).as_posix()
                archive.writestr(relative, raw)
                descriptors.append({"path": relative, "sha256": sha256(raw).hexdigest(), "bytes": len(raw),
                                    "sourceSha256": file_digest(path), "sourceBytes": path.stat().st_size,
                                    "sanitized": path.suffix == ".log"})
            manifest = {"schemaVersion": "untirta.educational-cavity-campaign-artifact.v1", "campaign": profile.id,
                        "files": descriptors, "nativeImportCompatible": False, "actualCfdRunVerified": False,
                        "mainApplicationWrites": False, "claim": "Fixed educational discretisation evidence only; no Current/Pump/physical validation or restart assertion."}
            archive.writestr("artifact-manifest.json", json.dumps(manifest, indent=2, allow_nan=False).encode())
        if temporary.stat().st_size > MAX_ARCHIVE:
            raise WorkerDiagnostic("CFD_CAMPAIGN_ARCHIVE_BUDGET", "The evidence ZIP exceeds 25 MiB; raw native files and failure report remain preserved locally.")
        temporary.replace(output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {"path": output.name, "sha256": file_digest(output), "bytes": output.stat().st_size, "maximumBytes": MAX_ARCHIVE}


def assess_linear_repeat(base: dict, tight: dict, spatial: dict, temporal: dict) -> dict:
    """A controlled linear-tolerance experiment; no nonlinear-error qualification."""
    if (base["cellCount"] != tight["cellCount"] or base["meshSha256"] != tight["meshSha256"]
            or base["timeStepSeconds"] != tight["timeStepSeconds"] or base["physicalEndTimeSeconds"] != tight["physicalEndTimeSeconds"]
            or base["quantity"]["id"] != tight["quantity"]["id"] or base["quantity"]["unit"] != tight["quantity"]["unit"]):
        raise WorkerDiagnostic("CFD_CAMPAIGN_REPEAT_MISMATCH", "Repeat the same actual native mesh, physical time, time step and quantity with only the declared linear-tolerance change.")
    baseline, repeated = base["quantity"]["value"], tight["quantity"]["value"]
    changes = (spatial["fineRelativeChange"], temporal["fineRelativeChange"])
    if not all(math.isfinite(v) for v in (baseline, repeated, *changes)) or baseline <= 0 or repeated <= 0 or min(changes) < 0:
        raise WorkerDiagnostic("CFD_CAMPAIGN_REPEAT_QUANTITY_INVALID", "Preserve native quantities; this relative kinetic-energy comparison requires positive finite operands and finite nonnegative sensitivity changes.")
    delta = abs(repeated - baseline) / abs(baseline)
    threshold = ITERATIVE_SENSITIVITY_FRACTION * min(changes)
    return {"status": "LinearToleranceSensitivityAssessed", "baselineRunId": base["id"], "tightRunId": tight["id"],
        "baselineTolerance": LINEAR_TOLERANCE, "tightTolerance": TIGHT_LINEAR_TOLERANCE,
        "relativeChange": delta, "declaredMaximumFractionOfDiscretisationChange": ITERATIVE_SENSITIVITY_FRACTION,
        "declaredCriterionBasis": "Educational target: linear-tolerance sensitivity at most10% of the smaller observed spatial/temporal fine-relative change; not a physical-error bound.",
        "relativeChangeThreshold": threshold, "nominalCriterionSatisfied": delta <= threshold,
        "nonlinearCouplingErrorAssessed": False, "numericalQualitySatisfied": None,
        "engineeringValidated": False, "mainApplicationWrites": False,
        "limitations": ["Only one fine-grid linear-tolerance repeat is included; this does not bound iterative error on every mesh.",
                        "PIMPLE outer iteration and coupling sensitivity have not been assessed; qualified GCI remains withheld."],
        "equationStages": [{"identifier": "CFD_CAVITY_LINEAR_TOLERANCE_SENSITIVITY", "calculationAuthority": "Python",
            "input": {"baselineValue": baseline, "tightValue": repeated, "quantityUnit": "m2/s2"},
            "inputMathML": f'<math><mrow><msub><mi>K</mi><mi>b</mi></msub><mo>=</mo><mn>{baseline}</mn><mo>;</mo><msub><mi>K</mi><mi>t</mi></msub><mo>=</mo><mn>{repeated}</mn></mrow></math>',
            "equationMathML": '<math><mrow><msub><mi>ε</mi><mi>linear</mi></msub><mo>=</mo><mfrac><mrow><mo>|</mo><msub><mi>K</mi><mi>t</mi></msub><mo>−</mo><msub><mi>K</mi><mi>b</mi></msub><mo>|</mo></mrow><mrow><mo>|</mo><msub><mi>K</mi><mi>b</mi></msub><mo>|</mo></mrow></mfrac></mrow></math>',
            "substitutionMathML": f'<math><mfrac><mrow><mo>|</mo><mn>{repeated}</mn><mo>−</mo><mn>{baseline}</mn><mo>|</mo></mrow><mn>{baseline}</mn></mfrac></math>',
            "resultMathML": f'<math><mn>{delta}</mn></math>', "result": delta, "unit": "1", "source": REFERENCES[1]}]}


def execute_campaign(workspace_root: Path, fixture: Path, profile: CampaignProfile = LEGACY) -> tuple[dict, Path]:
    if profile not in (LEGACY, LAMINAR):
        raise WorkerDiagnostic("CFD_CAMPAIGN_PROFILE_INVALID", "Only an immutable reviewed campaign profile may execute.")
    source = verified_source(fixture)
    identities = profile_identity(source, profile)
    inputs = {"schemaVersion": "untirta.educational-cavity-campaign-input.v1", "campaign": profile.id,
              "runs": [{"id": name, "divisions": n, "timeStepSeconds": dt} for name, n, dt in profile.runs],
              "endTimeSeconds": profile.end_time, "monitorIntervalSeconds": profile.output_interval,
              "relativeTolerance": TOLERANCE, "criterionBasis": "Declared educational sensitivity tolerance; not physical-error bound.",
              "quantity": {"id": QUANTITY, "unit": "m2/s2"}, "limits": {"memoryBytes": 2147483648, "timeoutSeconds": 1200, "diskBytes": 268435456, "logBytes": 2097152, "threads": 1}}
    if profile.laminar:
        inputs["explicitDerivative"] = {"simulationType": "laminar", "kinematicViscosityM2S": 0.001,
            "lidSpeedMS": 1, "characteristicLengthM": 0.1, "targetReynoldsNumber": 100,
            "temporalScheme": "Euler", "linearTolerance": LINEAR_TOLERANCE, "relativeLinearTolerance": 0,
            "tightRepeatTolerance": TIGHT_LINEAR_TOLERANCE, "iterativeSensitivityFraction": ITERATIVE_SENSITIVITY_FRACTION,
            "references": REFERENCES, "notAContinuationOfOriginalRas": True}
    binding = {"studyId": profile.id, "inputRevision": "campaign-v1", "inputSha256": digest(inputs), "referenceSha256": digest(identities)}
    result = {"schemaVersion": "untirta.educational-cavity-campaign.v1", "campaign": profile.id, "binding": binding,
              "status": "Preparing", "source": identities, "runs": [], "stages": [], "diagnostics": [],
              "runtimePackage": {"version": "20250206", "sha256": OPENFOAM_PACKAGE_SHA256, "requiredSolverBuild": OPENFOAM_PACKAGE_BUILD},
              "actualSolverExecutionCompleted": False, "actualCfdRunVerified": False, "engineeringValidated": False,
              "physicalValidation": False, "convergenceQualified": False, "pumpStatusWrites": False, "mainApplicationWrites": False,
              "scope": "Controlled laminar Re100 cavity transient discretisation and linear-tolerance sensitivity at0.5s; explicit nu1e-3m2/s derivative, Euler, lid1m/s. No stationary, turbulence, exact-Pump, NPSHr or physical-validation claim." if profile.laminar else "Early transient single-phase kEpsilon-RAS cavity discretisation sensitivity at0.05s; Euler scheme, source nu1e-5m2/s and lid1m/s. No steady-state, wall-model, exact-Pump or NPSHr claim.",
              "immutableSource": source, "references": REFERENCES if profile.laminar else []}
    if profile.laminar:
        result["physicalParameterAssessment"] = {"characteristicReynoldsNumber": 0.1 * 1 / 0.001,
            "unit": "1", "calculationAuthority": "Python", "source": REFERENCES[0],
            "inputMathML": '<math><mrow><mi>L</mi><mo>=</mo><mn>0.1</mn><mtext>m;</mtext><mi>U</mi><mo>=</mo><mn>1</mn><mtext>m/s;</mtext><mi>ν</mi><mo>=</mo><mn>0.001</mn><mtext>m²/s</mtext></mrow></math>',
            "equationMathML": '<math><mrow><mi>Re</mi><mo>=</mo><mfrac><mrow><mi>L</mi><mi>U</mi></mrow><mi>ν</mi></mfrac></mrow></math>',
            "substitutionMathML": '<math><mfrac><mrow><mn>0.1</mn><mo>×</mo><mn>1</mn></mrow><mn>0.001</mn></mfrac></math>',
            "resultMathML": '<math><mi>Re</mi><mo>=</mo><mn>100</mn></math>'}
    with single_job(workspace_root):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        workspace = workspace_root.resolve() / (profile.id + "-" + stamp)
        workspace.mkdir()
        write_json(workspace / "input/campaign.json", inputs)
        write_json(workspace / "reference/source.json", identities)
        runner, reports, histories = None, {}, {}
        try:
            if sys.platform != "linux" or os.environ.get("WM_PROJECT_VERSION") != "12":
                raise WorkerDiagnostic("CFD_SOLVER_UNAVAILABLE", "Use only the checksum-pinned Foundation 12 Linux runtime in its bounded delegated scope.")
            programs = {name: shutil.which(name) for name in ("blockMesh", "checkMesh", "foamRun")}
            if not all(programs.values()):
                raise WorkerDiagnostic("CFD_SOLVER_UNAVAILABLE", "The complete pinned mesher/checker/solver executables are required.")
            runner = ProcessRunner(workspace, Limits.parse(inputs["limits"]), workspace / "cancel.request")
            for name, n, dt in profile.runs:
                folder = workspace / "runs" / name
                case = folder / "native-case"
                prepared = prepare_case(fixture / "transient-cavity-v1", case, n, dt, profile, tight=name.endswith("-tight"))
                for stage in ("blockMesh", "checkMesh", "foamRun"):
                    args = [programs[stage], "-case", str(case)]
                    if stage == "checkMesh": args.extend(["-allGeometry", "-allTopology"])
                    done = runner.run(args, case, folder / "logs" / (stage + ".log"))
                    result["stages"].append({"runId": name, "name": stage, **done})
                    if stage == "checkMesh": verify_uniform_mesh(case, n, (folder / "logs/checkMesh.log").read_text())
                run_identities = profile_identity(source, profile, tight=name.endswith("-tight"))
                row, report, history = collect_run(case, workspace, name, n, dt, run_identities, binding, profile)
                row["derivedSettings"] = prepared
                report["numericalControlsSha256"] = prepared["derivedFileSha256"]["system/fvSolution"]
                write_json(workspace / row["reportPath"], report)
                write_json(workspace / row["historyPath"], history)
                reports[row["reportPath"]], histories[row["historyPath"]] = report, history
                result["runs"].append(row)
                write_json(workspace / "campaign-report.json", result)
            result["actualSolverExecutionCompleted"] = True
            result["status"] = "EducationalNumericalExperimentCompleted"
            rows = {row["id"]: row for row in result["runs"]}
            result["spatialEvidence"] = {"schemaVersion": "untirta.cfd-spatial-verification.v1", "gridDimension": 2,
                "quantityId": QUANTITY, "relativeTolerance": TOLERANCE,
                "runs": [{"reportPath": rows[name]["reportPath"], "representativeSpacingM": 0.1 / n,
                          "cellCount": n**2, "meshSha256": rows[name]["meshSha256"]} for name, n, _ in profile.runs[:3]]}
            from untirta_cfd_worker.numerical_verification import assess_spatial_evidence, assess_temporal_quantity_evidence
            result["spatialAssessment"] = assess_spatial_evidence(result["spatialEvidence"], reports=reports)
            result["temporalEvidence"] = {"schemaVersion": "untirta.cfd-temporal-evidence.v1", "binding": binding,
                "quantityId": QUANTITY, "evaluationTimeSeconds": profile.end_time, "relativeTolerance": TOLERANCE,
                "runs": [{"historyPath": rows[name]["historyPath"], "timeStepSeconds": dt,
                          "meshSha256": rows[name]["meshSha256"], "physicsSettingsSha256": identities["physicsSettingsSha256"]}
                         for name, _, dt in (profile.runs[3], profile.runs[4], profile.runs[2])]}
            temporal_reports = {row["historyPath"]: reports[row["reportPath"]] for row in result["runs"]}
            result["temporalAssessment"] = assess_temporal_quantity_evidence(result["temporalEvidence"], binding=binding, histories=histories, reports=temporal_reports)
            result["spatialPacket"] = {"kind": "spatial", "data": result["spatialEvidence"],
                "reports": {r["reportPath"]: reports[r["reportPath"]] for r in result["spatialEvidence"]["runs"]}}
            result["temporalPacket"] = {"kind": "temporal", "data": result["temporalEvidence"], "binding": binding,
                "histories": {r["historyPath"]: histories[r["historyPath"]] for r in result["temporalEvidence"]["runs"]},
                "reports": {r["historyPath"]: temporal_reports[r["historyPath"]] for r in result["temporalEvidence"]["runs"]}}
            result["sharedSpatialTemporalRunId"] = profile.runs[2][0]
            if profile.laminar:
                result["linearToleranceAssessment"] = assess_linear_repeat(rows[profile.runs[2][0]], rows[profile.runs[5][0]], result["spatialAssessment"], result["temporalAssessment"])
            result["qualificationLimitations"] = ["The 1% criterion is an explicitly declared educational sensitivity target, not manufacturer or physical accuracy acceptance.",
                "Native linear residual/continuity/Courant histories are observations; iterative-error insignificance has not been established, so numericalQualitySatisfied remains null and qualified GCI is withheld.",
                "Temporal sensitivity at the declared finite physical time does not establish stationary flow, turbulence/wall-model adequacy, experimental agreement or real Pump behavior."]
        except (WorkerDiagnostic, OSError, ValueError, KeyError, ImportError) as exc:
            result["status"] = "EducationalExperimentReviewRequired"
            result["diagnostics"].append({"reasonCode": exc.code if isinstance(exc, WorkerDiagnostic) else "CFD_CAMPAIGN_OUTPUT_INVALID",
                                          "action": exc.action if isinstance(exc, WorkerDiagnostic) else "Inspect bounded saved stage logs and native outputs; no result was substituted."})
        finally:
            if runner:
                try:
                    runner.close()
                except (WorkerDiagnostic, OSError) as exc:
                    result["status"] = "EducationalExperimentReviewRequired"
                    result["diagnostics"].append({"reasonCode": exc.code if isinstance(exc, WorkerDiagnostic) else "CFD_CAMPAIGN_CLEANUP_REQUIRED",
                        "action": exc.action if isinstance(exc, WorkerDiagnostic) else "Verify the dedicated solver process tree has stopped before another experiment."})
            result["completedAt"] = datetime.now(timezone.utc).isoformat()
            write_json(workspace / "campaign-report.json", result)
    return result, workspace


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--profile", choices=tuple(PROFILES), default="ras-early")
    args = parser.parse_args(argv)
    try:
        targets = (args.workspace.absolute(), args.report.absolute(), args.archive.absolute())
        if any(any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (path, *path.parents) if p.exists()) for path in targets):
            raise WorkerDiagnostic("CFD_CAMPAIGN_OUTPUT_PATH_INVALID", "Use regular dedicated output paths without symlinks or junctions.")
        if (args.report.exists() or args.archive.exists() or args.report.resolve() == args.archive.resolve()
                or not args.report.resolve().is_relative_to(args.workspace.resolve())
                or not args.archive.resolve().is_relative_to(args.workspace.resolve())
                or args.workspace.resolve() == ROOT or ROOT.is_relative_to(args.workspace.resolve())
                or any(path.resolve().is_relative_to(ROOT) and path.resolve().relative_to(ROOT).parts[0] not in {"outputs", "tmp", "test-results"} for path in targets)):
            raise WorkerDiagnostic("CFD_CAMPAIGN_OUTPUT_PATH_INVALID", "Choose fresh dedicated output report/archive paths; original fixture/source evidence cannot be overwritten.")
        profile = PROFILES[args.profile]
        result, workspace = execute_campaign(args.workspace, ROOT / "tools/cfd_worker/fixtures/openfoam12", profile)
        try:
            result["artifact"] = write_archive(workspace, args.archive, profile)
        except (WorkerDiagnostic, OSError, ValueError) as exc:
            result["status"] = "EducationalExperimentReviewRequired"
            result["diagnostics"].append({"reasonCode": exc.code if isinstance(exc, WorkerDiagnostic) else "CFD_CAMPAIGN_ARCHIVE_INVALID", "action": exc.action if isinstance(exc, WorkerDiagnostic) else "Inspect the preserved raw campaign output; archive preparation did not complete."})
        write_json(args.report, result)
        print(json.dumps({key: result[key] for key in ("schemaVersion", "status", "actualSolverExecutionCompleted", "convergenceQualified", "diagnostics")}, allow_nan=False))
        return 0 if result["status"] == "EducationalNumericalExperimentCompleted" else 2
    except (WorkerDiagnostic, OSError, ValueError) as exc:
        result = {"schemaVersion": "untirta.educational-cavity-campaign.v1", "status": "EducationalExperimentReviewRequired", "actualSolverExecutionCompleted": False,
                  "mainApplicationWrites": False, "diagnostics": [{"reasonCode": exc.code if isinstance(exc, WorkerDiagnostic) else "CFD_CAMPAIGN_INPUT_INVALID", "action": exc.action if isinstance(exc, WorkerDiagnostic) else "Use fresh dedicated output paths and the exact pinned official fixture."}]}
        print(json.dumps(result)); return 2


if __name__ == "__main__":
    raise SystemExit(main())
