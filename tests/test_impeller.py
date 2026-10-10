"""Official-source, engineering frame and inert export contracts, NOT CFD results."""

from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "services/cfd_worker/src").is_dir())
sys.path.insert(0, str(ROOT / "services/cfd_worker/src"))
from untirta_cfd_worker.contracts import Job, WorkerDiagnostic, digest, file_digest
from untirta_cfd_worker.profiles import benchmark_reference, IMPELLER_PROFILES, IMPELLER_PROVENANCE_SHA256
from untirta_cfd_worker import impeller, openfoam
from untirta_cfd_worker.checkpoint import build_checkpoint, validate_checkpoint_descriptor
from untirta_cfd_worker.artifacts import create_artifact_bytes, inspect_artifact
from untirta_cfd_worker.runner import execute_job

FIXTURES = ROOT / "tools/cfd_worker/fixtures/openfoam12-impeller"
TEST_TMP = ROOT / "tmp/cfd-worker-tests"
TEST_TMP.mkdir(parents=True, exist_ok=True)


def job_for(profile="impeller-transient-v1"):
    reference = benchmark_reference(profile)
    source = {"schemaVersion": "untirta.cfd-worker-job.v1", "studyId": "impeller-contract-only",
              "inputRevision": "test-v1", "adapter": "openfoam", "caseProfile": profile,
              "referenceSha256": digest(reference), "limits": {}, "parameters": {}}
    return Job.parse(source, reference)


def points_text(rows, declared=None):
    return ('FoamFile { format ascii; class vectorField; object points; }\n' +
            str(len(rows) if declared is None else declared) + '\n(\n' +
            '\n'.join('(' + ' '.join(format(v, '.15g') for v in row) + ')' for row in rows) + '\n)\n').encode()


def contract_archive(profile="impeller-transient-v1", *, motion_bytes=None, extra=None):
    """Two-point synthetic native schema data; no mesh or solver validation claim."""
    job = job_for(profile)
    physical = profile == "impeller-transient-v1"
    point_path = "output/native-case/0.01/polyMesh/points" if physical else "output/native-case/constant/polyMesh/points"
    files = {
        "input/job.json": (json.dumps(job.source).encode(), "input"),
        "reference/snapshot.json": (json.dumps(job.reference).encode(), "reference"),
        "output/native-case/0.01/U" if physical else "output/native-case/50/U": (b"internalField uniform (0 0 0);", "native"),
        "output/native-case/0.01/p" if physical else "output/native-case/50/p": (b"internalField uniform 0;", "native"),
        point_path: (points_text([(0, 1, 0), (0, 0, 1)]), "native"),
        "output/native-case/system/createNonConformalCouplesDict": ((FIXTURES / "native/system/createNonConformalCouplesDict").read_bytes(), "native"),
        "output/native-initial-state/0/U": ((FIXTURES / "native/0/U").read_bytes(), "native"),
        "output/native-initial-state/0/p": ((FIXTURES / "native/0/p").read_bytes(), "native"),
    }
    law_path = "output/native-case/constant/dynamicMeshDict" if physical else "output/native-case/constant/MRFProperties"
    law = motion_bytes if motion_bytes is not None else (FIXTURES / ("native/constant/dynamicMeshDict" if physical else "mrf-reference/constant/MRFProperties")).read_bytes()
    files[law_path] = (law, "native")
    if extra:
        files.update(extra)
    def descriptor(path, role=None):
        raw = files[path][0]
        value = {"path": path, "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}
        if role:
            value["role"] = role
        return value
    fields = [descriptor(path, "field") for path in files if path.rsplit('/', 1)[-1] in ("U", "p") and '/native-case/' in path]
    state = {"interpretation": "physical" if physical else "steady-iteration", "physicalTimeSeconds": 0.01 if physical else None,
             "timeStepSeconds": 0.001 if physical else None, "step": 10 if physical else None, "iteration": None if physical else 50}
    method = {"id": profile, "rotationModel": "sliding-mesh" if physical else "mrf", "physicalModel": "single-phase-incompressible"}
    cp = build_checkpoint(binding=job.binding(), solver={"adapter": "openfoam", "distribution": "OpenFOAM-Foundation", "version": "12"},
        method=method, time_state=state,
        motion={"mode": method["rotationModel"], "rotorAngleRad": impeller.OMEGA * 0.01 if physical else None,
                "angularSpeedRadS": impeller.OMEGA, "axis": [1, 0, 0], "origin": [0, 0, 0], "motionLawSha256": sha256(law).hexdigest()},
        interface={"type": "NCC", "identifiers": impeller.INTERFACE_NAMES,
                   "configurationSha256": sha256(files["output/native-case/system/createNonConformalCouplesDict"][0]).hexdigest()},
        native_files=fields, mesh_files=[descriptor(point_path)],
        settings_files=[descriptor(law_path), descriptor("output/native-case/system/createNonConformalCouplesDict")],
        setup_state={"initialStateSavedBeforePreview": True, "initialStateFiles": [descriptor("output/native-initial-state/0/U"), descriptor("output/native-initial-state/0/p")],
                     "sequence": ["setup-saved", "initialized", "initial-state-saved", "solved", "checkpoint-saved"]},
        evidence_role="contract-test", checkpoint_id="synthetic-impeller-contract")
    files["checkpoints/contract.json"] = (json.dumps(cp).encode(), "checkpoint")
    return create_artifact_bytes(artifact_id="impeller-synthetic-contract-only", binding=job.binding(), solver=cp["solver"], method=method,
        evidence_role="contract-test", provenance={"kind": "educational-contract", "sourceApplication": "synthetic schema regression",
            "claim": "Two synthetic points and placeholder fields for schema safety tests ONLY; no executed CFD."},
        lifecycle={"state": "contract-test", "actualCfdRunClaimed": False, "engineeringValidated": False, "calculationFreshness": "Imported Evidence"}, files=files), cp, job


class OfficialImpellerFixtureTests(unittest.TestCase):
    def test_exact_package_native_reference_and_license_bytes(self):
        data = impeller._verified_fixture(FIXTURES)
        self.assertEqual(file_digest(FIXTURES / "provenance.json"), IMPELLER_PROVENANCE_SHA256)
        self.assertEqual(len(data["files"]), 20)
        self.assertEqual(sum(row["bytes"] for row in data["files"]), 61172)
        blocks = (FIXTURES / "native/system/blockMeshDict").read_text()
        self.assertEqual(3 * 11 * 11 * 17 + 2 * 10 * 10 * 16, impeller.NATIVE_CELL_COUNT)
        self.assertIn("convertToMeters 0.01;", blocks)
        self.assertIn("rotating (11 11 17)", blocks)
        self.assertEqual(data["license"], "GPL-3.0-or-later")
        self.assertFalse(any(row["path"].endswith(("Allrun", "Allclean")) for row in data["files"]))

    def test_source_package_profile_and_revision_are_exact_not_user_overrides(self):
        for profile in IMPELLER_PROFILES:
            job = job_for(profile)
            for key in ("sourcePackageSha256", "sourcePackageVersion", "sourceBuild", "sourceCase"):
                reference = dict(job.reference, **{key: "other"})
                with self.subTest(profile=profile, key=key), self.assertRaises(WorkerDiagnostic):
                    Job.parse(dict(job.source, referenceSha256=digest(reference)), reference)
            with self.assertRaises(WorkerDiagnostic):
                Job.parse(dict(job.source, parameters={"rpm": 123}), job.reference)
        job = job_for()
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp, patch('untirta_cfd_worker.runner.capability_report', return_value={'adapters': {'openfoam': {'available': True}}}), patch('untirta_cfd_worker.runner.shutil.which', return_value=None):
            # Capability transport only; no fake solver/mesh is created.
            result = execute_job(job.source, job.reference, Path(temp), ROOT / 'tools/cfd_worker/fixtures/openfoam12')
            self.assertEqual(result['reasonCode'], 'CFD_NCC_EXECUTABLE_REQUIRED')
            self.assertFalse(result['actualCfdRun'])
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_prepare_preserves_all_source_bytes_and_orders_coupling_before_strict_gate(self):
        before = {path.relative_to(FIXTURES).as_posix(): file_digest(path) for path in FIXTURES.rglob('*') if path.is_file()}
        for profile in IMPELLER_PROFILES:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
                case, stages, meta = openfoam.prepare(job_for(profile), Path(temp), ROOT / "tools/cfd_worker/fixtures/openfoam12")
                self.assertEqual([row[0] for row in stages], ["blockMesh", "createNonConformalCouples", "checkMesh", "foamRun"])
                self.assertIn("-allGeometry", stages[2])
                self.assertIn("-allTopology", stages[2])
                self.assertEqual(file_digest(case / "system/blockMeshDict"), file_digest(FIXTURES / "native/system/blockMeshDict"))
                self.assertEqual(file_digest(case / "system/createNonConformalCouplesDict"), file_digest(FIXTURES / "native/system/createNonConformalCouplesDict"))
                self.assertEqual(meta["interfaceModel"], "Foundation-12-NCC")
                self.assertIn("NPSH3", meta["quantityClaim"])
                self.assertIsNone(meta["fluid"]["densityKgM3"])
                self.assertIsNone(meta["fluid"]["vaporPressurePa"])
        self.assertEqual(before, {path.relative_to(FIXTURES).as_posix(): file_digest(path) for path in FIXTURES.rglob('*') if path.is_file()})

    def test_mrf_fixed_geometry_uses_rotating_frame_walls_and_steady_equations(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            case, _, meta = openfoam.prepare(job_for("impeller-mrf-v1"), Path(temp), ROOT / "tools/cfd_worker/fixtures/openfoam12")
            self.assertFalse((case / "constant/dynamicMeshDict").exists())
            mrf = (case / "constant/MRFProperties").read_text()
            self.assertIn("cellZone    rotating;", mrf)
            self.assertIn("axis        (1 0 0);", mrf)
            self.assertIn("omega       60 [rpm];", mrf)
            self.assertIn("MRFnoSlip", (case / "0/U").read_text())
            self.assertIn("MRFslip", (case / "0/U").read_text())
            self.assertNotIn("movingWallVelocity", (case / "0/U").read_text())
            self.assertIn("steadyState", (case / "system/fvSchemes").read_text())
            self.assertIn("SIMPLE", (case / "system/fvSolution").read_text())
            self.assertEqual(meta["timeInterpretation"], "steady-iteration")
            self.assertEqual([row['id'] for row in meta['equations']], ['impeller.mesh-cell-count', 'impeller.angular-speed'])

    def test_native_transient_uses_same_pinned_motion_and_ten_physical_steps(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            case, _, meta = openfoam.prepare(job_for(), Path(temp), ROOT / "tools/cfd_worker/fixtures/openfoam12")
            self.assertEqual(file_digest(case / 'constant/dynamicMeshDict'), file_digest(FIXTURES / 'native/constant/dynamicMeshDict'))
            self.assertIn('Euler', (case / 'system/fvSchemes').read_text())
            states = openfoam._time_states('\n'.join('Time = ' + str(i / 1000) + 's' for i in range(1, 11)), case, 'impeller-transient-v1')
            self.assertEqual(len(states), 10)
            self.assertEqual(states[-1]['timeStepSeconds'], 0.001)
            self.assertEqual(states[-1]['physicalTimeSeconds'], 0.01)
            self.assertEqual(meta['timeInterpretation'], 'physical')
            self.assertAlmostEqual(impeller.equation_traces('sliding-mesh', 0.01)[-1]['result'], 0.06283185307179587)

    def test_changed_source_and_rewritten_manifest_are_both_rejected(self):
        import shutil
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            copy = Path(temp) / 'fixture'
            shutil.copytree(FIXTURES, copy)
            path = copy / 'native/system/blockMeshDict'
            path.write_bytes(path.read_bytes() + b'\n//changed\n')
            with self.assertRaises(WorkerDiagnostic) as caught:
                impeller._verified_fixture(copy)
            self.assertEqual(caught.exception.code, 'CFD_FIXTURE_CHECKSUM_INVALID')
            data = json.loads((copy / 'provenance.json').read_text())
            row = next(row for row in data['files'] if row['path'] == 'native/system/blockMeshDict')
            row.update(sha256=file_digest(path), bytes=path.stat().st_size)
            (copy / 'provenance.json').write_text(json.dumps(data))
            with self.assertRaises(WorkerDiagnostic) as caught:
                impeller._verified_fixture(copy)
            self.assertEqual(caught.exception.code, 'CFD_FIXTURE_PROVENANCE_CHANGED')


class ImpellerNativeEngineeringContracts(unittest.TestCase):
    def test_exact_saved_rotation_and_stationary_points_are_both_required(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            initial, saved = Path(temp) / 'before', Path(temp) / 'after'
            initial.write_bytes(points_text([(0.01, 0.05, 0), (0.06, 0.08, 0)]))
            angle = impeller.OMEGA * 0.01
            saved.write_bytes(points_text([(0.01, 0.05 * math.cos(angle), 0.05 * math.sin(angle)), (0.06, 0.08, 0)]))
            report = impeller.verify_motion(initial, saved, 0.01)
            self.assertEqual(report['rotatingPoints'], 1)
            self.assertEqual(report['stationaryPoints'], 1)
            zone_report = impeller.verify_motion(initial, saved, 0.01, rotor_points={0})
            self.assertTrue(zone_report['rotorZonePointSelectionVerified'])
            with self.assertRaises(WorkerDiagnostic) as caught:
                impeller.verify_motion(initial, saved, 0.01, rotor_points={1})
            self.assertEqual(caught.exception.code, 'CFD_ROTOR_ZONE_MOTION_MISMATCH')
            with self.assertRaises(WorkerDiagnostic):
                impeller.verify_motion(initial, saved, 0.02)
            saved.write_bytes(initial.read_bytes())
            with self.assertRaises(WorkerDiagnostic) as caught:
                impeller.verify_motion(initial, saved, 0.01)
            self.assertEqual(caught.exception.code, 'CFD_MOVED_MESH_REQUIRED')

    def test_points_declared_cardinality_unknown_tokens_and_nonfinite_fail(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            path = Path(temp) / 'points'
            for data in (points_text([(0, 1, 0)], declared=2), points_text([(0, 1, 0)]) + b'extra',
                         points_text([(0, 1, 0)]).replace(b'(0 1 0)', b'(nan 1 0)')):
                path.write_bytes(data)
                with self.assertRaises(WorkerDiagnostic):
                    impeller.points(path)

    def test_field_cardinality_and_kinematic_pressure_units_are_not_guessed(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            path = Path(temp) / 'p'
            path.write_text('dimensions [0 2 -2 0 0 0 0];\ninternalField nonuniform List<scalar> 2 (0 1);')
            self.assertEqual(impeller.checked_field(path, '0 2 -2 0 0 0 0', 1, 2), [0.0, 1.0])
            with self.assertRaises(WorkerDiagnostic) as caught:
                impeller.checked_field(path, '0 2 -2 0 0 0 0', 1, 3)
            self.assertEqual(caught.exception.code, 'CFD_FIELD_CELL_COUNT_MISMATCH')
            with self.assertRaises(WorkerDiagnostic) as caught:
                impeller.checked_field(path, '1 -1 -2 0 0 0 0', 1, 2)
            self.assertEqual(caught.exception.code, 'CFD_FIELD_UNIT_MISMATCH')
            for field in ('dimensions [0 1 -1 0 0 0 0]; internalField nonuniform List<vector> 2 ((1 2) (3 4 5 6));',
                          'dimensions [0 1 -1 0 0 0 0]; internalField uniform (1 2 3) garbage;',
                          'dimensions [0 1 -1 0 0 0 0]; internalField uniform (1 nan 3);'):
                path.write_text(field)
                with self.subTest(field=field), self.assertRaises(WorkerDiagnostic):
                    impeller.checked_field(path, '0 1 -1 0 0 0 0', 3, 2)

    def test_rotor_point_selection_follows_actual_cell_face_topology(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as temp:
            mesh = Path(temp)
            def native(content):
                return 'FoamFile { format ascii; class labelList; object owner; }\n' + content
            (mesh / 'cellZones').write_text('FoamFile { format ascii; class regIOobject; object cellZones; }\n1 (rotating {type cellZone; cellLabels List<label> 1 (0);})')
            (mesh / 'faces').write_text(native('2 (3(0 1 2) 3(3 4 5))'))
            (mesh / 'owner').write_text(native('2 (0 1)'))
            (mesh / 'neighbour').write_text(native('0 ()'))
            self.assertEqual(impeller.rotating_point_labels(mesh, 6, cell_count=2, expected_rotor_cells=1), {0, 1, 2})
            (mesh / 'faces').write_text(native('2 (3(0 1 9) 3(3 4 5))'))
            with self.assertRaises(WorkerDiagnostic):
                impeller.rotating_point_labels(mesh, 6, cell_count=2, expected_rotor_cells=1)

    def test_ncc_contract_export_is_integrity_only_and_never_current_or_solver_verified(self):
        for profile in IMPELLER_PROFILES:
            raw, cp, job = contract_archive(profile)
            result = inspect_artifact(raw, expected_binding=job.binding())
            self.assertTrue(result['valid'], result)
            self.assertFalse(result['actualCfdRunVerified'])
            self.assertEqual(result['evidenceRole'], 'contract-test')
            self.assertEqual(cp['interface']['type'], 'NCC')
            self.assertEqual(cp['timeState']['interpretation'], 'physical' if profile.endswith('transient-v1') else 'steady-iteration')
            self.assertIsNone(cp['timeState']['physicalTimeSeconds'] if profile.endswith('mrf-v1') else None)
            wrong = deepcopy(cp)
            wrong['solver']['adapter'] = 'pyfluent'
            wrong.pop('checkpointChecksum')
            wrong['checkpointChecksum'] = digest(wrong)
            self.assertFalse(validate_checkpoint_descriptor(wrong, binding=job.binding())['valid'])

    def test_builtin_library_requires_exact_full_dictionary_bytes_path_and_profile(self):
        native = (FIXTURES / 'native/constant/dynamicMeshDict').read_bytes()
        for changed in (native.replace(b'libfvMotionSolvers.so', b'custom.so'), native + b'\nlibs ("custom.so");\n', native + b'\n// changed bytes\n'):
            with self.subTest(changed=sha256(changed).hexdigest()), self.assertRaises(WorkerDiagnostic) as caught:
                contract_archive(motion_bytes=changed)
            self.assertEqual(caught.exception.code, 'CFD_ARTIFACT_ACTIVE_DICTIONARY_PROHIBITED')
        for profile, path in (('impeller-mrf-v1', 'output/native-case/constant/dynamicMeshDict'),
                              ('impeller-transient-v1', 'output/other/constant/dynamicMeshDict')):
            with self.subTest(profile=profile, path=path), self.assertRaises(WorkerDiagnostic):
                contract_archive(profile, extra={path: (native, 'native')})

    def test_unknown_native_dictionary_or_external_include_remains_rejected(self):
        for path, data in (('output/native-case/system/customCouplingDict', b'patches (A B);'),
                           ('output/native-case/0.01/U', b'#include "../../secret"\ninternalField uniform (0 0 0);')):
            with self.subTest(path=path), self.assertRaises(WorkerDiagnostic):
                contract_archive(extra={path: (data, 'native')})
        for profile, path in (('impeller-transient-v1', 'output/native-case/0.01/Uf'),
                              ('impeller-mrf-v1', 'output/native-case/50/Uf')):
            raw, _, job = contract_archive(profile, extra={path: (b'FoamFile {format ascii; class surfaceVectorField; object Uf;} dimensions [0 1 -1 0 0 0 0]; internalField uniform (0 0 0);', 'native')})
            self.assertTrue(inspect_artifact(raw, expected_binding=job.binding())['valid'])
        with self.assertRaises(WorkerDiagnostic):
            contract_archive(extra={'output/arbitrary/Uf': (b'internalField uniform (0 0 0);', 'native')})
        for profile, path in (('impeller-transient-v1', 'output/native-case/50/Uf'),
                              ('impeller-mrf-v1', 'output/native-case/0.01/Uf'),
                              ('impeller-mrf-v1', 'output/native-case/0.01/fvMesh/polyFaces')):
            with self.subTest(profile=profile, path=path), self.assertRaises(WorkerDiagnostic):
                contract_archive(profile, extra={path: (b'internalField uniform (0 0 0);', 'native')})


if __name__ == '__main__':
    unittest.main()
