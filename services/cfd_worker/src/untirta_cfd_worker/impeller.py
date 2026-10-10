"""Reviewed Foundation 12 radial impeller tutorial, not a manufactured Pump.

Original case and MRF example dictionaries remain immutable fixture files.
This adapter records every derivative, distinguishes steady iteration from
physical time, and verifies saved rotating points before writing checkpoints.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import shutil

from .contracts import Job, WorkerDiagnostic, file_digest, write_json
from .checkpoint import build_checkpoint
from .profiles import OPENFOAM_PACKAGE_SHA256, OPENFOAM_PACKAGE_BUILD, IMPELLER_PROVENANCE_SHA256

NATIVE_CELL_COUNT = 9371
RPM = 60.0
OMEGA = 2 * math.pi * RPM / 60
END_TIME = 0.01
TIME_STEP = 0.001
MRF_ITERATIONS = 50
INTERFACE_NAMES = ["nonConformalCouple" + side + str(angle)
                   for side in ("In", "Out") for angle in range(0, 360, 30)]
SOURCES = {"geometry": "https://github.com/OpenFOAM/OpenFOAM-12/tree/master/tutorials/incompressibleFluid/impeller",
           "ncc": "https://cfd.direct/openfoam/free-software/using-non-conformal-coupling/",
           "mrf": "https://cpp.openfoam.org/v12/MRFZone_8C_source.html",
           "wall": "https://cpp.openfoam.org/v12/classFoam_1_1MRFnoSlipFvPatchVectorField.html"}


def _descriptor(path: Path, workspace: Path, role=None) -> dict:
    item = {"path": path.relative_to(workspace).as_posix(), "sha256": file_digest(path), "bytes": path.stat().st_size}
    if role:
        item["role"] = role
    return item


def _verified_fixture(root: Path) -> dict:
    if file_digest(root / "provenance.json") != IMPELLER_PROVENANCE_SHA256:
        raise WorkerDiagnostic("CFD_FIXTURE_PROVENANCE_CHANGED", "Restore the immutable reviewed package-derived fixture manifest; replacing both source bytes and their listed checksums is prohibited.")
    data = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    package = data.get("sourcePackage", {})
    if (data.get("schemaVersion") != "untirta.cfd-package-fixture-provenance.v1" or
            package.get("sha256") != OPENFOAM_PACKAGE_SHA256 or package.get("build") != OPENFOAM_PACKAGE_BUILD or
            package.get("version") != "20250206" or data.get("sourceCase") != "tutorials/incompressibleFluid/impeller"):
        raise WorkerDiagnostic("CFD_FIXTURE_SOURCE_INVALID", "Restore the exact checksum-bound Foundation 12 package-derived impeller fixture.")
    listed = set()
    for item in data["files"]:
        path = root / item["path"]
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink() or not path.is_file() or path.stat().st_size != item["bytes"] or file_digest(path) != item["sha256"]:
            raise WorkerDiagnostic("CFD_FIXTURE_CHECKSUM_INVALID", "Restore the immutable original impeller and MRF-reference bytes before preparing a derived case.")
        listed.add(item["path"])
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and path.name != "provenance.json"}
    if actual != listed or len(listed) != 20:
        raise WorkerDiagnostic("CFD_FIXTURE_FILE_SET_INVALID", "Use the exact reviewed 20-file native/reference/license subset; additional source files are not executable inputs.")
    return data


def prepare(job: Job, workspace: Path, root: Path):
    from .openfoam import replace_entry
    source = _verified_fixture(root)
    case = workspace / "output/native-case"
    shutil.copytree(root / "native", case)
    # Native functions includes are auxiliary plotting/report configuration.
    # Omit them from this first fixed native-field/motion smoke, not from source.
    (case / "system/functions").unlink()
    control = case / "system/controlDict"
    overrides = {"writeFormat": "ascii", "writePrecision": "12", "runTimeModifiable": "false",
                 "adjustTimeStep": "no", "writeControl": "timeStep", "purgeWrite": "0"}
    derived = []
    if job.profile == "impeller-transient-v1":
        overrides.update({"endTime": "0.01", "deltaT": "0.001", "writeInterval": "10"})
        interpretation, rotation = "physical", "sliding-mesh"
    else:
        overrides.update({"endTime": str(MRF_ITERATIONS), "deltaT": "1", "writeInterval": str(MRF_ITERATIONS)})
        (case / "constant/dynamicMeshDict").unlink()
        for name in ("fvSchemes", "fvSolution"):
            shutil.copyfile(root / "mrf-reference/system" / name, case / "system" / name)
        mrf = (root / "mrf-reference/constant/MRFProperties").read_text()
        if mrf.count("cellZone    rotor;") != 1 or mrf.count("axis        (0 0 1);") != 1:
            raise WorkerDiagnostic("CFD_FIXTURE_SETUP_INVALID", "The exact reviewed MRF reference zone and axis must be available.")
        mrf = mrf.replace("cellZone    rotor;", "cellZone    rotating;").replace("axis        (0 0 1);", "axis        (1 0 0);")
        (case / "constant/MRFProperties").write_text(mrf)
        velocity = (case / "0/U").read_text()
        velocity, wall_count = re.subn(r"\btype\s+movingWallVelocity\s*;", "type            MRFnoSlip;", velocity)
        velocity, slip_count = re.subn(r"\btype\s+movingWallSlipVelocity\s*;", "type            MRFslip;", velocity)
        if wall_count != 1 or slip_count != 1:
            raise WorkerDiagnostic("CFD_FIXTURE_SETUP_INVALID", "The exact native rotor-wall and noncouple slip groups are required for the derived MRF frame.")
        (case / "0/U").write_text(velocity)
        interpretation, rotation = "steady-iteration", "mrf"
        derived = ["Remove dynamicMeshDict: geometry remains fixed; NCC connects stationary overlapping interface faces.",
                   "Use pinned mixerVessel2DMRF steadyState/SIMPLE numerical dictionaries.",
                   "MRF zone=rotating, origin=(0,0,0)m, axis=(1,0,0), omega=60rpm from native impeller motion.",
                   "Native rotor wall group movingWallVelocity becomes MRFnoSlip; nonCouple slip group becomes MRFslip."]
    for name, value in overrides.items():
        replace_entry(control, name, value)
    stages = [["blockMesh", "-case", str(case)],
              ["createNonConformalCouples", "-overwrite", "-case", str(case)],
              ["checkMesh", "-allGeometry", "-allTopology", "-case", str(case)],
              ["foamRun", "-case", str(case)]]
    metadata = {"distribution": "OpenFOAM-Foundation", "version": "12", "profile": job.profile,
        "sourcePackage": source["sourcePackage"], "sourceCase": source["sourceCase"], "sourceLinks": SOURCES,
        "physicalModel": "single-phase-incompressible-radial-impeller-educational-smoke",
        "rotationModel": rotation, "timeInterpretation": interpretation, "interfaceModel": "Foundation-12-NCC",
        "benchmarkOverrides": overrides, "derivedModelChanges": derived,
        "geometry": {"type": "official-parametric-blockMesh-30-degree-radial-impeller-passage", "lengthUnit": "m",
                     "nativeCellCount": NATIVE_CELL_COUNT, "rotatingCellZone": "rotating", "axis": [1, 0, 0], "originM": [0, 0, 0]},
        "fluid": {"kinematicViscosityM2S": 1e-5, "densityKgM3": None, "vaporPressurePa": None,
                  "claim": "Native nu-only single-phase tutorial; no inferred liquid identity or absolute pressure datum."},
        "quantityClaim": "Official educational impeller passage only; no exact manufactured Pump, converged head, cavitation, NPSHR or NPSH3 claim.",
        "limitations": ["No volute or exact manufactured pump identity; native idealized sector geometry is retained.",
                        "Bounded 10-step transient or 50-iteration MRF execution is a software smoke, not period/convergence validation.",
                        "Pressure is kinematic p [m2/s2]; density, absolute pressure and vapor pressure are unspecified.",
                        "Liquid-vapour pressure sweep and valid 3% head crossing remain separate unqualified engineering tasks."],
        "equations": equation_traces(rotation)}
    return case, stages, metadata


def equation_traces(rotation: str, time_seconds=None):
    traces = [{"id": "impeller.mesh-cell-count", "title": "Native structured-cell count",
        "inputMathMl": '<math><mrow><mtext>3 rotor blocks: </mtext><mn>11</mn><mo>×</mo><mn>11</mn><mo>×</mo><mn>17</mn><mtext> cells; 2 stator blocks: </mtext><mn>10</mn><mo>×</mo><mn>10</mn><mo>×</mo><mn>16</mn><mtext> cells</mtext></mrow></math>',
        "mathMl": '<math><mrow><mi>N</mi><mo>=</mo><munderover><mo>∑</mo><mi>b</mi><mi>B</mi></munderover><msub><mi>N</mi><mtext>x,b</mtext></msub><msub><mi>N</mi><mtext>y,b</mtext></msub><msub><mi>N</mi><mtext>z,b</mtext></msub></mrow></math>',
        "substitutionMathMl": '<math><mrow><mi>N</mi><mo>=</mo><mn>3</mn><mo>×</mo><mn>11</mn><mo>×</mo><mn>11</mn><mo>×</mo><mn>17</mn><mo>+</mo><mn>2</mn><mo>×</mo><mn>10</mn><mo>×</mo><mn>10</mn><mo>×</mo><mn>16</mn></mrow></math>',
        "result": NATIVE_CELL_COUNT, "unit": "cells", "source": SOURCES["geometry"], "authority": "Python, immutable native blockMesh subdivisions; actual checkMesh must match"},
        {"id": "impeller.angular-speed", "title": "Angular speed from native rotational speed",
        "inputMathMl": '<math><mrow><mi>n</mi><mo>=</mo><mn>60</mn><mtext> rpm</mtext></mrow></math>',
        "mathMl": '<math><mrow><mi>ω</mi><mo>=</mo><mfrac><mrow><mn>2</mn><mi>π</mi><mi>n</mi></mrow><mn>60</mn></mfrac></mrow></math>',
        "substitutionMathMl": '<math><mrow><mi>ω</mi><mo>=</mo><mfrac><mrow><mn>2</mn><mi>π</mi><mo>×</mo><mn>60</mn></mrow><mn>60</mn></mfrac></mrow></math>',
        "result": OMEGA, "unit": "rad/s", "source": SOURCES["geometry"], "authority": "Python, immutable native omega 60 [rpm]"}]
    if rotation == "sliding-mesh" and time_seconds is not None:
        traces.append({"id": "impeller.rotor-angle", "title": "Physical rotor position from saved time",
            "inputMathMl": '<math><mtable><mtr><mtd><mi>ω</mi><mo>=</mo><mn>' + str(OMEGA) + '</mn><mtext> rad/s</mtext></mtd></mtr><mtr><mtd><mi>t</mi><mo>=</mo><mn>' + str(time_seconds) + '</mn><mtext> s</mtext></mtd></mtr></mtable></math>',
            "mathMl": '<math><mrow><mi>θ</mi><mo>=</mo><mi>ω</mi><mi>t</mi></mrow></math>',
            "substitutionMathMl": '<math><mrow><mi>θ</mi><mo>=</mo><mn>' + str(OMEGA) + '</mn><mo>×</mo><mn>' + str(time_seconds) + '</mn></mrow></math>',
            "result": OMEGA * time_seconds, "unit": "rad", "source": SOURCES["ncc"],
            "authority": "Python, actual logged physical time plus checksum-bound native motion law; saved points checked independently"})
    return traces


def _body(text: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", text, flags=re.DOTALL)


def points(path: Path) -> list[tuple[float, float, float]]:
    text = _body(path.read_text(encoding="utf-8"))
    matched = re.fullmatch(r"\s*FoamFile\s*\{[^{}]*\}\s*(\d+)\s*\((.*)\)\s*", text, re.DOTALL)
    if not matched or not re.search(r"\bclass\s+vectorField\s*;", text):
        raise WorkerDiagnostic("CFD_MOVED_POINTS_FORMAT_INVALID", "Use the actual finite ASCII native vectorField mesh-points list.")
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    token = r"\(\s*(" + number + r")\s+(" + number + r")\s+(" + number + r")\s*\)"
    values = [tuple(float(v) for v in row) for row in re.findall(token, matched[2])]
    remainder = re.sub(token, "", matched[2]).strip()
    if remainder or len(values) != int(matched[1]) or not values or not all(math.isfinite(v) for row in values for v in row):
        raise WorkerDiagnostic("CFD_MOVED_POINTS_FORMAT_INVALID", "Native mesh-point count and every finite SI coordinate must match its declared list.")
    return values


def _native_list(path: Path):
    text = _body(path.read_text(encoding="utf-8"))
    matched = re.fullmatch(r"\s*FoamFile\s*\{[^{}]*\}\s*(\d+)\s*\((.*)\)\s*;?\s*", text, re.DOTALL)
    if not matched or int(matched[1]) > 1000000:
        raise WorkerDiagnostic("CFD_NATIVE_TOPOLOGY_INVALID", "Use bounded actual ASCII native topology lists with their complete declared counts.")
    return int(matched[1]), matched[2]


def rotating_point_labels(mesh: Path, point_count: int, *, cell_count=NATIVE_CELL_COUNT, expected_rotor_cells=6171) -> set[int]:
    """Select actual rotor points through source zone->cell->face->point topology."""
    text = _body((mesh / "cellZones").read_text(encoding="utf-8"))
    zones = re.findall(r"\brotating\s*\{\s*type\s+cellZone\s*;\s*cellLabels\s+(?:List<label>\s+)?(\d+)\s*\(([^()]*)\)\s*;\s*\}", text, re.DOTALL)
    if len(zones) != 1 or not re.fullmatch(r"\s*\d+(?:\s+\d+)*\s*", zones[0][1]):
        raise WorkerDiagnostic("CFD_ROTOR_CELL_ZONE_INVALID", "Bind the actual named rotating cellZone and complete finite cell-label list from the generated native mesh.")
    rotor = {int(value) for value in zones[0][1].split()}
    if len(rotor) != int(zones[0][0]) or len(rotor) != expected_rotor_cells or not rotor or min(rotor) < 0 or max(rotor) >= cell_count:
        raise WorkerDiagnostic("CFD_ROTOR_CELL_ZONE_INVALID", "The source rotating zone must contain exactly its 6171 native rotor cells, without duplicate/out-of-range labels.")
    def labels(name):
        count, content = _native_list(mesh / name)
        values = [int(value) for value in content.split()] if re.fullmatch(r"\s*(?:\d+\s*)*", content) else []
        if len(values) != count or any(value >= cell_count for value in values):
            raise WorkerDiagnostic("CFD_NATIVE_TOPOLOGY_INVALID", "Each native face owner/neighbour must match its declared count and bounded actual cell label.")
        return values
    owners, neighbours = labels("owner"), labels("neighbour")
    count, content = _native_list(mesh / "faces")
    faces = re.findall(r"(\d+)\s*\(([^()]*)\)", content)
    if len(faces) != count or count != len(owners) or len(neighbours) > count or re.sub(r"\d+\s*\([^()]*\)", "", content).strip() or set(owners + neighbours) != set(range(cell_count)):
        raise WorkerDiagnostic("CFD_NATIVE_TOPOLOGY_INVALID", "Pair all complete native faces and owner/neighbour cells, with a contiguous accepted cell set.")
    selected = set()
    for i, (declared, values) in enumerate(faces):
        if not re.fullmatch(r"\s*\d+(?:\s+\d+)*\s*", values):
            raise WorkerDiagnostic("CFD_NATIVE_TOPOLOGY_INVALID", "Every native face must contain complete nonnegative point indices.")
        indices = [int(value) for value in values.split()]
        if len(indices) != int(declared) or len(indices) < 3 or len(set(indices)) != len(indices) or max(indices) >= point_count:
            raise WorkerDiagnostic("CFD_NATIVE_TOPOLOGY_INVALID", "Each face point-list count and vertex reference must match the exact native point topology.")
        if owners[i] in rotor or i < len(neighbours) and neighbours[i] in rotor:
            selected.update(indices)
    if not selected or len(selected) >= point_count:
        raise WorkerDiagnostic("CFD_ROTOR_POINT_SELECTION_INVALID", "Use both actual rotor and stationary-stator point sets selected from the named cellZone topology.")
    return selected


def verify_motion(initial_path: Path, saved_path: Path, time_seconds: float, *, rotor_points=None) -> dict:
    if isinstance(time_seconds, bool) or not isinstance(time_seconds, (int, float)) or not math.isfinite(time_seconds) or time_seconds <= 0:
        raise WorkerDiagnostic("CFD_MOVED_MESH_TIME_INVALID", "Verify saved rotor geometry at an explicit finite positive physical time in seconds.")
    original, moved = points(initial_path), points(saved_path)
    if len(original) != len(moved):
        raise WorkerDiagnostic("CFD_MOVED_MESH_POINT_COUNT_MISMATCH", "Saved physical mesh points must retain the exact initial mesh topology/order.")
    angle, rotating, stationary = OMEGA * time_seconds, 0, 0
    sine, cosine = math.sin(angle), math.cos(angle)
    def close(a, b):
        return all(math.isclose(x, y, rel_tol=1e-7, abs_tol=1e-9) for x, y in zip(a, b))
    for index, (before, after) in enumerate(zip(original, moved)):
        expected = (before[0], before[1] * cosine - before[2] * sine, before[1] * sine + before[2] * cosine)
        if rotor_points is not None:
            if index in rotor_points and not close(expected, after) or index not in rotor_points and not close(before, after):
                raise WorkerDiagnostic("CFD_ROTOR_ZONE_MOTION_MISMATCH", "Only points selected from the native rotating cellZone may follow the exact rotor motion; stator points must remain fixed.")
        if close(before, after):
            stationary += 1
        elif close(expected, after):
            rotating += 1
        else:
            raise WorkerDiagnostic("CFD_MOVED_MESH_POSITION_MISMATCH", "Each saved point must remain stationary or follow the exact native +x-axis 60rpm rotation at this physical time.")
    if not rotating or not stationary:
        raise WorkerDiagnostic("CFD_MOVED_MESH_REQUIRED", "Saved native points must demonstrate both actual rotor motion and a stationary stator; metadata alone is insufficient.")
    return {"savedPhysicalTimeSeconds": time_seconds, "rotorAngleRad": angle, "rotatingPoints": rotating,
            "stationaryPoints": stationary, "nativePointCount": len(original), "actualMeshPositionChecked": True,
            "rotorZonePointSelectionVerified": rotor_points is not None,
            "sourceInitialPointsSha256": file_digest(initial_path), "savedPointsSha256": file_digest(saved_path)}


def checked_field(path: Path, expected_dimensions: str, components: int, cell_count: int) -> list[float]:
    from .openfoam import field_dimensions
    text = path.read_text(encoding="utf-8")
    if field_dimensions(text) != expected_dimensions:
        raise WorkerDiagnostic("CFD_FIELD_UNIT_MISMATCH", "Use the actual native kinematic-pressure/velocity/turbulence SI dimensions; no absolute-pressure reinterpretation.")
    body = _body(text)
    declarations = re.findall(r"\binternalField\s+(.*?);", body, re.DOTALL)
    if len(declarations) != 1 or len(re.findall(r"\binternalField\b", body)) != 1:
        raise WorkerDiagnostic("CFD_NATIVE_FIELD_INVALID", "Use exactly one native internal-field declaration at the saved state.")
    declaration = declarations[0].strip()
    count = 1
    if declaration.startswith("nonuniform"):
        match = re.fullmatch(r"nonuniform\s+List<(scalar|vector)>\s+(\d+)\s*\((.*)\)", declaration, re.DOTALL)
        if not match or int(match[2]) != cell_count or match[1] != ("vector" if components == 3 else "scalar"):
            raise WorkerDiagnostic("CFD_FIELD_CELL_COUNT_MISMATCH", "Each native list type/count must match the accepted mesh-cell count and scalar/vector components.")
        count, content = cell_count, match[3].strip()
    else:
        match = re.fullmatch(r"uniform\s+(.+)", declaration, re.DOTALL)
        if not match:
            raise WorkerDiagnostic("CFD_NATIVE_FIELD_INVALID", "Use the supported explicit uniform or nonuniform native scalar/vector field format.")
        content = match[1].strip()
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    if components == 3:
        vector = r"\(\s*(" + number + r")\s+(" + number + r")\s+(" + number + r")\s*\)"
        rows = re.findall(vector, content)
        valid = len(rows) == count and not re.sub(vector, "", content).strip()
        values = [float(value) for row in rows for value in row]
    else:
        valid = bool(re.fullmatch(number + r"(?:\s+" + number + r")*", content))
        values = [float(value) for value in re.findall(number, content)]
        valid = valid and len(values) == count
    if not valid or not all(math.isfinite(value) for value in values):
        raise WorkerDiagnostic("CFD_NATIVE_FIELD_INVALID", "Every declared native scalar/vector element must be complete and finite; truncated tuples, unknown tokens and extra values cannot be accepted.")
    return values


def collect(job: Job, workspace: Path, case: Path) -> dict:
    from .openfoam import _logged_seconds, _time_states, verify_mesh_log
    log = (workspace / "output/logs/foamRun.log").read_text(errors="replace")
    mesh = verify_mesh_log((workspace / "output/logs/checkMesh.log").read_text(errors="replace"))
    if mesh["cellCount"] != NATIVE_CELL_COUNT or not re.search(r"(?m)^\s*End\s*$", log):
        raise WorkerDiagnostic("CFD_IMPELLER_RUN_INCOMPLETE", "The unchanged official 9371-cell coupled mesh and actual solver must both complete their strict smoke scope.")
    physical = job.profile == "impeller-transient-v1"
    initial_points = workspace / "output/native-initial-state/constant/polyMesh/points"
    point_count = len(points(initial_points))
    rotor_points = rotating_point_labels(case / "constant/polyMesh", point_count)
    target = END_TIME if physical else MRF_ITERATIONS
    folder = case / ("0.01" if physical else str(MRF_ITERATIONS))
    if not folder.is_dir():
        raise WorkerDiagnostic("CFD_NATIVE_CHECKPOINT_REQUIRED", "Save the exact completed physical 0.01s state or steady iteration50, without substituting earlier outputs.")
    if physical:
        states = _time_states(log, case, job.profile)
        if len(states) != 10 or any(not math.isclose(row["physicalTimeSeconds"], (i + 1) * TIME_STEP, abs_tol=1e-12) or
                                    not math.isclose(row["timeStepSeconds"], TIME_STEP, abs_tol=1e-12) for i, row in enumerate(states)):
            raise WorkerDiagnostic("CFD_PHYSICAL_TIME_INCOMPLETE", "Complete all ten actual fixed 0.001s steps through 0.01s; a saved folder alone does not verify time integration.")
        state = states[-1]
        motion_check = verify_motion(initial_points, folder / "polyMesh/points", target, rotor_points=rotor_points)
    else:
        samples = [value for line in log.splitlines() if (value := _logged_seconds(line, "Time")) is not None]
        if samples != list(range(1, MRF_ITERATIONS + 1)):
            raise WorkerDiagnostic("CFD_STEADY_ITERATION_INCOMPLETE", "Complete the exact fifty steady iterations; native pseudo-time folder labels are not physical seconds.")
        if (folder / "polyMesh/points").exists():
            raise WorkerDiagnostic("CFD_MRF_MESH_MOTION_MISMATCH", "MRF geometry must remain fixed; moved physical points cannot be relabeled steady iteration.")
        state = {"interpretation": "steady-iteration", "physicalTimeSeconds": None, "timeStepSeconds": None,
                 "step": None, "iteration": MRF_ITERATIONS}
        motion_check = None
    definitions = {"U": ("0 1 -1 0 0 0 0", 3, "m/s"), "p": ("0 2 -2 0 0 0 0", 1, "m2/s2"),
                   "k": ("0 2 -2 0 0 0 0", 1, "m2/s2"), "epsilon": ("0 2 -3 0 0 0 0", 1, "m2/s3"), "nut": ("0 2 -1 0 0 0 0", 1, "m2/s")}
    summaries = {}
    for name, (dims, components, unit) in definitions.items():
        path = folder / name
        if not path.is_file():
            raise WorkerDiagnostic("CFD_NATIVE_CHECKPOINT_REQUIRED", "Include the exact paired U/p/k/epsilon/nut solution fields at this saved native state.")
        values = checked_field(path, dims, components, NATIVE_CELL_COUNT)
        summaries[name] = {"minimum": min(values), "maximum": max(values), "scalarComponentCount": len(values), "unit": unit}
    native_paths = sorted(path for path in folder.iterdir() if path.is_file())
    mesh_paths = sorted(path for path in (case / "constant/polyMesh").rglob("*") if path.is_file() and (not physical or path.name != "points"))
    mesh_paths += sorted(path for path in (case / "constant/fvMesh").rglob("*") if path.is_file())
    mesh_paths += sorted(path for path in (folder / "fvMesh").rglob("*") if path.is_file())
    if not physical and not (folder / "fvMesh/polyFaces").exists():
        # Stationary NCC may retain the initial coupling instance. Preserve
        # its actual path/time instead of inventing a final-iteration copy.
        mesh_paths += sorted(path for path in (case / "0/fvMesh").rglob("*") if path.is_file())
    if physical:
        mesh_paths += sorted(path for path in (folder / "polyMesh").rglob("*") if path.is_file())
    settings_paths = sorted(path for path in case.rglob("*") if path.is_file() and
        ("system" in path.relative_to(case).parts or "constant" in path.relative_to(case).parts) and "polyMesh" not in path.relative_to(case).parts)
    settings_paths += sorted(path for path in (folder / "uniform").rglob("*") if path.is_file())
    initial_paths = sorted(path for path in (workspace / "output/native-initial-state").rglob("*") if path.is_file())
    initial_points = workspace / "output/native-initial-state/constant/polyMesh/points"
    if not initial_points.is_file() or file_digest(initial_points) != file_digest(case / "constant/polyMesh/points"):
        raise WorkerDiagnostic("CFD_INITIAL_MESH_POSITION_MISMATCH", "The native unadvanced mesh must have been saved before solve and remain paired with this case; do not create it retrospectively.")
    rotation = "sliding-mesh" if physical else "mrf"
    law = case / ("constant/dynamicMeshDict" if physical else "constant/MRFProperties")
    native = build_checkpoint(binding=job.binding(), solver={"adapter": "openfoam", "distribution": "OpenFOAM-Foundation", "version": "12"},
        method={"id": job.profile, "rotationModel": rotation, "physicalModel": "single-phase-incompressible"},
        time_state=state, motion={"mode": rotation, "rotorAngleRad": OMEGA * target if physical else None,
            "angularSpeedRadS": OMEGA, "axis": [1, 0, 0], "origin": [0, 0, 0], "motionLawSha256": file_digest(law)},
        interface={"type": "NCC", "identifiers": INTERFACE_NAMES, "configurationSha256": file_digest(case / "system/createNonConformalCouplesDict")},
        native_files=[_descriptor(path, workspace, "field") for path in native_paths],
        mesh_files=[_descriptor(path, workspace) for path in mesh_paths], settings_files=[_descriptor(path, workspace) for path in settings_paths],
        setup_state={"initialStateSavedBeforePreview": True, "initialStateFiles": [_descriptor(path, workspace) for path in initial_paths],
                     "sequence": ["setup-saved", "initialized", "initial-state-saved", "solved", "checkpoint-saved"]},
        checkpoint_id="native-impeller-" + folder.name)
    path = workspace / "checkpoints" / ("native-impeller-" + folder.name + ".json")
    write_json(path, native)
    return {"actualCfdRun": True, "numericalSmokePassed": True, "engineeringValidated": False,
            "timeInterpretation": state["interpretation"], "physicalTimeSeconds": target if physical else None,
            "iteration": None if physical else MRF_ITERATIONS, "checkpoints": [],
            "nativeCheckpointFiles": [path.relative_to(workspace).as_posix()], "fieldSummaries": summaries,
            "meshMotionEvidence": motion_check, "equations": equation_traces(rotation, target if physical else None),
            "acceptanceScope": "Actual strict coupled mesh/finite native fields/physical motion or steady iteration software smoke only",
            "remainingEngineeringChecks": ["conservation and residual convergence", "periodic-transient convergence", "mesh and time independence",
                "defined liquid/absolute pressure basis", "liquid-vapour pressure sweep", "valid 3% head crossing", "exact Pump conditions"]}
