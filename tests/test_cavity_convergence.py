"""Campaign contracts/quadrature/transport only; no local CFD solve claim."""
from __future__ import annotations

from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "tools/cfd_worker/run_cavity_convergence.py").is_file())
spec = importlib.util.spec_from_file_location("cavity_campaign_contract", ROOT / "tools/cfd_worker/run_cavity_convergence.py")
campaign = importlib.util.module_from_spec(spec)
spec.loader.exec_module(campaign)


def point_field(n, *, duplicate=False):
    values = [(i * 0.1 / n, j * 0.1 / n, k * 0.01) for i in range(n+1) for j in range(n+1) for k in (0, 1)]
    if duplicate: values[-1] = values[0]
    rows = "\n".join("(" + " ".join(str(v) for v in row) + ")" for row in values)
    return "FoamFile { class vectorField; object points; }\n" + str(len(values)) + "\n(\n" + rows + "\n)\n"


def mesh_log(n):
    volume = 0.1 * 0.1 * 0.01
    return ("Enabling all (cell, face, edge, point) topology checks.\nEnabling all geometry checks.\n"
            + f"cells: {n*n}\nMin volume = {volume/(n*n)}. Max volume = {volume/(n*n)}.  Total volume = {volume}.  Cell volumes OK.\nMesh OK.\nEnd\n")


def solver_log(dt=0.001):
    text = ["Build : " + campaign.OPENFOAM_PACKAGE_BUILD, "Create mesh for time = 0"]
    for i in range(round(campaign.END_TIME / dt)):
        text.extend(["Courant Number mean: 0.01 max: 0.2", f"Time = {(i+1)*dt:.12g}s"])
        for name in ("Ux", "Uy", "p", "epsilon", "k"):
            text.append(f"linearSolver: Solving for {name}, Initial residual = 0.5, Final residual = 1e-7, No Iterations 4")
        text.append("time step continuity errors : sum local = 1e-9, global = -1e-20, cumulative = -1e-20")
    return "\n".join(text) + "\nEnd\n"


class CavityCampaignContracts(unittest.TestCase):
    def setUp(self):
        base = ROOT / "outputs"; base.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def mesh(self, n=20, duplicate=False):
        case = self.root / "case"
        (case / "constant/polyMesh").mkdir(parents=True, exist_ok=True)
        (case / "constant/polyMesh/points").write_text(point_field(n, duplicate=duplicate))
        return case

    def test_fixed_five_solves_form_two_three_member_series_same_geometry_endpoint(self):
        self.assertEqual(len(campaign.RUNS), 5)
        self.assertEqual([n for _,n,_ in campaign.RUNS[:3]], [20,40,80])
        self.assertEqual([dt for _,_,dt in (campaign.RUNS[3],campaign.RUNS[4],campaign.RUNS[2])], [0.001,0.0005,0.00025])
        self.assertEqual({dt for _,_,dt in campaign.RUNS[:3]}, {0.00025})
        self.assertEqual(campaign.END_TIME, 0.05)
        self.assertEqual(campaign.MAX_ARCHIVE, 25*1024**2)
        for _,n,dt in campaign.RUNS:
            self.assertEqual(campaign.END_TIME/dt, round(campaign.END_TIME/dt))
            self.assertEqual(campaign.OUTPUT_INTERVAL/dt, round(campaign.OUTPUT_INTERVAL/dt))
            self.assertLessEqual(1.0*dt/(0.1/n), 0.8 + 1e-14)

    def test_derivative_changes_grid_time_output_only_original_source_immutable(self):
        fixture = ROOT / "tools/cfd_worker/fixtures/openfoam12"
        source = fixture / "transient-cavity-v1"
        original = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        identities = campaign.verified_source(fixture)
        case = self.root / "derived"
        result = campaign.prepare_case(source, case, 80, 0.0005)
        self.assertFalse(result["geometryChanged"])
        self.assertFalse(result["physicsOrBoundaryConditionsChanged"])
        self.assertIn("(80 80 1)", (case/"system/blockMeshDict").read_text())
        self.assertRegex((case/"system/controlDict").read_text(), r"writeInterval\s+20;")
        for name, raw in original.items():
            self.assertEqual((source/name).read_bytes(), raw)
            if name not in ("system/controlDict", "system/blockMeshDict"):
                self.assertEqual((case/name).read_bytes(), raw)
        self.assertEqual(identities, campaign.verified_source(fixture))
        with self.assertRaises(campaign.WorkerDiagnostic): campaign.prepare_case(source, self.root/"unreviewed", 100, 0.01)

    def test_source_checksum_rejects_tampered_fixture_before_copy(self):
        target = ROOT/"tools/cfd_worker/fixtures/openfoam12/transient-cavity-v1/0/U"
        original = campaign.file_digest
        def changed(path): return "0"*64 if path == target else original(path)
        with patch.object(campaign, "file_digest", side_effect=changed), self.assertRaises(campaign.WorkerDiagnostic) as error:
            campaign.verified_source(ROOT/"tools/cfd_worker/fixtures/openfoam12")
        self.assertEqual(error.exception.code, "CFD_FIXTURE_CHECKSUM_INVALID")

    def test_native_lattice_and_volumes_verify_every_cell_not_point_aliases(self):
        checked = campaign.verify_uniform_mesh(self.mesh(), 20, mesh_log(20))
        self.assertEqual(checked["cellCount"], 400)
        self.assertTrue(checked["uniformNativeLatticeVerified"])
        self.assertTrue(checked["uniformCellVolumesVerified"])
        self.assertAlmostEqual(checked["representativeSpacingM"], 0.005)
        self.assertAlmostEqual(checked["cellVolumeM3"], 2.5e-7)
        self.mesh(20, duplicate=True)
        with self.assertRaises(campaign.WorkerDiagnostic) as error: campaign.verify_uniform_mesh(self.root/"case",20,mesh_log(20))
        self.assertEqual(error.exception.code,"CFD_CAMPAIGN_NONUNIFORM_MESH")

    def test_uniform_quadrature_rejects_wrong_volume_and_failed_mesh_exit_zero(self):
        case = self.mesh()
        for text in (mesh_log(20).replace("Total volume = 0.00010000000000000002", "Total volume = 0.0002"),
                     mesh_log(20)+"Failed 1 mesh checks.\n"):
            with self.assertRaises(campaign.WorkerDiagnostic): campaign.verify_uniform_mesh(case,20,text)

    def test_vector_field_uniform_and_nonuniform_exact_volume_weighted_energy(self):
        native=self.root/"U"
        native.write_text("dimensions [0 1 -1 0 0 0 0];\ninternalField uniform (1 2 0);")
        value, quadrature=campaign.kinetic_energy(native,400,2.5e-7)
        self.assertAlmostEqual(value,2.5)
        self.assertEqual(quadrature['evaluatedCellCount'],400)
        self.assertEqual(quadrature['nativeDeclarationVectors'],1)
        native.write_text("dimensions [0 1 -1 0 0 0 0];\ninternalField nonuniform List<vector> 2 ((1 0 0)(0 3 0));")
        value,_=campaign.kinetic_energy(native,2,0.25)
        self.assertAlmostEqual(value,2.5)
        native.write_text("dimensions [0 1 -1 0 0 0 0];\ninternalField nonuniform List<vector> 2 ((1 0 0));")
        with self.assertRaises(campaign.WorkerDiagnostic): campaign.kinetic_energy(native,2,0.25)

    def test_real_log_grammar_preserves_physical_steps_signed_continuity_and_raw_residuals(self):
        result=campaign.diagnostic_history(solver_log(),0.001)
        self.assertEqual(result['physicalStepCount'],50)
        self.assertAlmostEqual(result['maximumCourantObserved'],0.2)
        self.assertEqual(result['samples'][0]['continuity'][0]['global'],-1e-20)
        self.assertIsNone(result['numericalQualitySatisfied'])
        self.assertEqual(set(result['maxLastCorrectionLinearResidual']),{'p','Ux','Uy','k','epsilon'})

    def test_time_units_missing_steps_and_missing_residual_do_not_gain_acceptance(self):
        for text in (solver_log().replace('Time = 0.001s','Time = 1ms'),
                     solver_log().replace('Time = 0.05s','Time = 0.049s'),
                     solver_log().replace('Solving for epsilon','Solving for undefined')):
            with self.assertRaises(campaign.WorkerDiagnostic): campaign.diagnostic_history(text,0.001)

    def test_failed_checkmesh_prevents_physical_solver_and_retains_controlled_report(self):
        calls=[]
        class TransportOnlyRunner:
            def __init__(self,*args): pass
            def close(self): pass
            def run(self, argv, cwd, log):
                calls.append(Path(argv[0]).name)
                log.parent.mkdir(parents=True,exist_ok=True)
                log.write_text("Failed 1 mesh checks.\nEnd\n")
                return {'exitCode':0,'log':'contract-only'}
        with patch.object(campaign.sys,'platform','linux'), patch.dict(campaign.os.environ,{'WM_PROJECT_VERSION':'12'}), patch.object(campaign.shutil,'which',side_effect=lambda name:'/reviewed/'+name), patch.object(campaign,'ProcessRunner',TransportOnlyRunner):
            result,workspace=campaign.execute_campaign(self.root/'runs',ROOT/'tools/cfd_worker/fixtures/openfoam12')
        self.assertEqual(calls,['blockMesh','checkMesh'])
        self.assertFalse(result['actualSolverExecutionCompleted'])
        self.assertEqual(result['status'],'EducationalExperimentReviewRequired')
        self.assertEqual(result['diagnostics'][0]['reasonCode'],'CFD_MESH_CHECK_FAILED')
        self.assertTrue((workspace/'campaign-report.json').is_file())
        self.assertFalse((self.root/'runs/.worker-active.lock').exists())

    def test_archive_sanitizes_copy_preserves_raw_source_and_explicit_nonimportable_scope(self):
        workspace=self.root/'run';workspace.mkdir()
        raw=b'Time = 0.05s\nACCESS_TOKEN=contract-placeholder\nhttps://host/path?token=example\n'
        log=workspace/'foamRun.log';log.write_bytes(raw)
        (workspace/'campaign-report.json').write_text('{"evidenceRole":"contract-test"}')
        output=self.root/'artifact.zip'
        meta=campaign.write_archive(workspace,output)
        self.assertEqual(meta['bytes'],output.stat().st_size)
        self.assertEqual(log.read_bytes(),raw)
        with zipfile.ZipFile(output) as archive:
            packed=archive.read('foamRun.log')
            self.assertIn(b'Time = 0.05s',packed)
            self.assertNotIn(b'contract-placeholder',packed)
            manifest=json.loads(archive.read('artifact-manifest.json'))
            self.assertFalse(manifest['nativeImportCompatible'])
            item=next(v for v in manifest['files'] if v['path']=='foamRun.log')
            self.assertEqual(item['sourceSha256'],sha256(raw).hexdigest())
            self.assertEqual(item['sha256'],sha256(packed).hexdigest())

    def test_archive_budget_rejection_does_not_erase_sources_or_leave_partial_zip(self):
        workspace=self.root/'run';workspace.mkdir()
        source=workspace/'raw-native';source.write_bytes(bytes(range(256))*8)
        output=self.root/'too-small.zip'
        with patch.object(campaign,'MAX_ARCHIVE',32),self.assertRaises(campaign.WorkerDiagnostic): campaign.write_archive(workspace,output)
        self.assertTrue(source.exists())
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix('.zip.tmp').exists())

    def test_cli_rejects_main_source_output_and_report_archive_alias_before_execution(self):
        with patch.object(campaign,'execute_campaign') as execute,patch('sys.stdout',new=io.StringIO()):
            code=campaign.main(['--workspace',str(ROOT/'apps/frontend'),'--report',str(ROOT/'apps/frontend/forbidden-campaign.json'),'--archive',str(ROOT/'apps/frontend/forbidden.zip')])
        self.assertEqual(code,2);execute.assert_not_called()
        with patch.object(campaign,'execute_campaign') as execute,patch('sys.stdout',new=io.StringIO()):
            code=campaign.main(['--workspace',str(self.root),'--report',str(self.root/'same'),'--archive',str(self.root/'same')])
        self.assertEqual(code,2);execute.assert_not_called()


if __name__ == '__main__': unittest.main()
