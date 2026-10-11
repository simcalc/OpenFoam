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
sys.modules[spec.name] = campaign
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


def solver_log(dt=0.001, profile=campaign.LEGACY):
    text = ["Build : " + campaign.OPENFOAM_PACKAGE_BUILD, "Create mesh for time = 0"]
    for i in range(round(profile.end_time / dt)):
        text.extend(["Courant Number mean: 0.01 max: 0.2", f"Time = {(i+1)*dt:.12g}s"])
        for name in (("Ux", "Uy", "p") if profile.laminar else ("Ux", "Uy", "p", "epsilon", "k")):
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

    def test_laminar_derivative_is_distinct_fixed_six_run_experiment(self):
        profile = campaign.LAMINAR
        self.assertNotEqual(profile.id, campaign.LEGACY.id)
        self.assertEqual(len(profile.runs), 6)
        self.assertEqual(profile.runs[:5], campaign.RUNS)
        self.assertEqual(profile.end_time, 0.5)
        self.assertEqual(profile.output_interval, 0.1)
        self.assertEqual(profile.runs[5][1:], profile.runs[2][1:])
        for _, n, dt in profile.runs:
            self.assertEqual(profile.end_time / dt, round(profile.end_time / dt))
            self.assertEqual(profile.output_interval / dt, round(profile.output_interval / dt))
            self.assertLessEqual(dt / (0.1 / n), 0.8 + 1e-14)
        with self.assertRaises(Exception): profile.end_time = 5

    def test_laminar_physics_and_linear_tolerances_are_explicit_source_untouched(self):
        fixture = ROOT / "tools/cfd_worker/fixtures/openfoam12"
        source = fixture / "transient-cavity-v1"
        original = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()}
        before = campaign.verified_source(fixture)
        base = self.root / "laminar-base"; tight = self.root / "laminar-tight"
        prepared = campaign.prepare_case(source, base, 80, 0.00025, campaign.LAMINAR)
        repeated = campaign.prepare_case(source, tight, 80, 0.00025, campaign.LAMINAR, tight=True)
        self.assertTrue(prepared["physicsOrBoundaryConditionsChanged"])
        self.assertFalse(prepared["boundaryConditionsChanged"])
        self.assertFalse(prepared["geometryChanged"])
        self.assertRegex((base / "constant/momentumTransport").read_text(), r"simulationType\s+laminar;")
        self.assertRegex((base / "constant/physicalProperties").read_text(), r"nu\s+0.001 \[m\^2/s\];")
        self.assertEqual((base / "system/fvSolution").read_text().count("1e-9;"), 3)
        self.assertEqual((tight / "system/fvSolution").read_text().count("1e-11;"), 3)
        self.assertNotIn("relTol          0.1;", (base / "system/fvSolution").read_text())
        for name, raw in original.items():
            self.assertEqual((source / name).read_bytes(), raw)
            if name != "system/fvSolution": self.assertEqual((base / name).read_bytes(), (tight / name).read_bytes())
        self.assertNotEqual(prepared["derivedFileSha256"]["system/fvSolution"], repeated["derivedFileSha256"]["system/fvSolution"])
        after = campaign.verified_source(fixture)
        self.assertEqual(before, after)
        derived = campaign.profile_identity(before, campaign.LAMINAR)
        self.assertNotEqual(before["physicsSettingsSha256"], derived["physicsSettingsSha256"])
        self.assertEqual(before["geometrySha256"], derived["geometrySha256"])
        self.assertEqual(before["boundaryConditionsSha256"], derived["boundaryConditionsSha256"])
        with self.assertRaises(campaign.WorkerDiagnostic): campaign.prepare_case(source, self.root / "bad-tight", 40, 0.00025, campaign.LAMINAR, tight=True)

    def test_laminar_logs_require_every_pressure_velocity_courant_continuity_step(self):
        log = solver_log(0.001, campaign.LAMINAR)
        parsed = campaign.diagnostic_history(log, 0.001, campaign.LAMINAR)
        self.assertEqual(parsed["physicalStepCount"], 500)
        self.assertEqual(set(parsed["maxLastCorrectionLinearResidual"]), {"p", "Ux", "Uy"})
        self.assertIsNone(parsed["numericalQualitySatisfied"])
        for invalid in (log.replace("Time = 0.25s", "Time = 0.2501s"), log.replace("Solving for Uy", "Solving for Uz"), log.replace("Courant Number mean:", "No courant mean:")):
            with self.assertRaises(campaign.WorkerDiagnostic): campaign.diagnostic_history(invalid, 0.001, campaign.LAMINAR)

    def test_laminar_collected_quantity_preserves_exact_verifier_schema(self):
        """Synthetic parser-boundary evidence only; no solver execution claim."""
        from untirta_cfd_worker.numerical_verification import REPORT_KEYS, _quantity_report
        folder = self.root / "runs/schema-contract"; case = folder / "native-case"
        (case / "constant/polyMesh").mkdir(parents=True)
        (case / "constant/polyMesh/points").write_text(point_field(20))
        (case / "system").mkdir(); (case / "system/blockMeshDict").write_text("// contract geometry descriptor")
        (folder / "logs").mkdir()
        (folder / "logs/checkMesh.log").write_text(mesh_log(20))
        (folder / "logs/foamRun.log").write_text(solver_log(0.00025, campaign.LAMINAR))
        for time in ("0", "0.1", "0.2", "0.3", "0.4", "0.5"):
            (case / time).mkdir()
            (case / time / "U").write_text("dimensions [0 1 -1 0 0 0 0]; internalField uniform (1 0 0);")
            (case / time / "p").write_text("dimensions [0 2 -2 0 0 0 0]; internalField uniform 0;")
        identity = campaign.profile_identity(campaign.verified_source(ROOT / "tools/cfd_worker/fixtures/openfoam12"), campaign.LAMINAR)
        _, report, history = campaign.collect_run(case, self.root, "schema-contract", 20, 0.00025, identity, {}, campaign.LAMINAR)
        self.assertEqual(set(report), REPORT_KEYS)
        self.assertEqual(_quantity_report(report)["quantity"]["value"], 0.5)
        self.assertEqual(history["samples"][-1]["timeSeconds"], 0.5)
        self.assertNotIn("numericalControlsSha256", report)

    def test_split_diagnostic_history_preserves_every_step_without_raising_archive_caps(self):
        path = self.root / "runs/budget-contract"; path.mkdir(parents=True)
        raw = campaign.diagnostic_history(solver_log(0.00025, campaign.LAMINAR), 0.00025, campaign.LAMINAR)
        row = {"id": "budget-contract", "diagnostics": raw}
        campaign.persist_run_diagnostics(row, self.root)
        saved = json.loads((path / "diagnostic-history.json").read_text())
        self.assertEqual(saved["samples"], raw["samples"])
        self.assertEqual(saved["physicalStepCount"], 2000)
        self.assertNotIn("samples", row["diagnostics"])
        item = row["diagnostics"]["sampleHistory"]
        self.assertEqual(item["sha256"], campaign.file_digest(self.root / item["path"]))
        self.assertLess(item["bytes"], 4*1024**2)
        self.assertLess(len(json.dumps(row).encode()), 4000)
        self.assertEqual(campaign.MAX_ARCHIVE, 25*1024**2)
        with self.assertRaises(campaign.WorkerDiagnostic): campaign.persist_run_diagnostics({"id": "budget-contract", "diagnostics": raw}, self.root)

    def linear_repeat(self, tight_value=1.0001):
        base = {"id": "base", "cellCount": 6400, "meshSha256": "a"*64, "timeStepSeconds": 0.00025,
                "physicalEndTimeSeconds": 0.5, "quantity": {"id": campaign.QUANTITY, "unit": "m2/s2", "value": 1.0}}
        tight = {**base, "id": "tight", "quantity": {**base["quantity"], "value": tight_value}}
        return base, tight

    def test_tighter_linear_repeat_compares_raw_quantity_without_gci_or_physical_promotion(self):
        base, tight = self.linear_repeat()
        assessed = campaign.assess_linear_repeat(base, tight, {"fineRelativeChange": 0.02}, {"fineRelativeChange": 0.005})
        self.assertTrue(assessed["nominalCriterionSatisfied"])
        self.assertAlmostEqual(assessed["relativeChange"], 0.0001)
        self.assertAlmostEqual(assessed["relativeChangeThreshold"], 0.0005)
        self.assertIsNone(assessed["numericalQualitySatisfied"])
        self.assertFalse(assessed["engineeringValidated"])
        self.assertFalse(assessed["nonlinearCouplingErrorAssessed"])
        self.assertFalse(assessed["mainApplicationWrites"])
        base, tight = self.linear_repeat(1.5)
        assessed = campaign.assess_linear_repeat(base, tight, {"fineRelativeChange": 0.02}, {"fineRelativeChange": 0.005})
        self.assertFalse(assessed["nominalCriterionSatisfied"])
        self.assertEqual(assessed["relativeChange"], 0.5)
        import xml.etree.ElementTree as ET
        for key in ("inputMathML", "equationMathML", "substitutionMathML", "resultMathML"):
            ET.fromstring(assessed["equationStages"][0][key])

    def test_linear_repeat_rejects_other_mesh_time_units_nonfinite_or_zero_operands(self):
        base, tight = self.linear_repeat()
        for key, value in (("meshSha256", "b"*64), ("timeStepSeconds", 0.001), ("physicalEndTimeSeconds", 0.1), ("cellCount", 400)):
            changed = {**tight, key: value}
            with self.assertRaises(campaign.WorkerDiagnostic): campaign.assess_linear_repeat(base, changed, {"fineRelativeChange": 0.02}, {"fineRelativeChange": 0.005})
        for value in (float("nan"), float("inf"), 0, -1):
            changed = {**tight, "quantity": {**tight["quantity"], "value": value}}
            with self.assertRaises(campaign.WorkerDiagnostic): campaign.assess_linear_repeat(base, changed, {"fineRelativeChange": 0.02}, {"fineRelativeChange": 0.005})

    def test_laminar_cannot_start_solver_on_failed_mesh_or_overwrite_original_profile(self):
        fixture = ROOT / "tools/cfd_worker/fixtures/openfoam12"
        with patch.object(campaign.sys, "platform", "linux"), patch.dict(campaign.os.environ, {"WM_PROJECT_VERSION": "12"}), patch.object(campaign.shutil, "which", return_value="/usr/bin/foamRun"), patch.object(campaign, "ProcessRunner") as runner:
            runner.return_value.run.return_value = {"exitCode": 0, "log": "test"}
            with patch.object(campaign, "verify_uniform_mesh", side_effect=campaign.WorkerDiagnostic("BAD_MESH", "Preserve the failed native mesh.")):
                result, _ = campaign.execute_campaign(self.root, fixture, campaign.LAMINAR)
        self.assertEqual(result["campaign"], campaign.LAMINAR.id)
        self.assertEqual(result["status"], "EducationalExperimentReviewRequired")
        self.assertFalse(result["actualSolverExecutionCompleted"])
        self.assertFalse(result["convergenceQualified"])
        self.assertFalse(result["mainApplicationWrites"])

    def test_refined_is_two_new_mesh160_solves_without_old_profile_or_cap_change(self):
        self.assertEqual(campaign.REFINED.id, "cavity-laminar-re100-discretisation-v2")
        self.assertEqual(campaign.REFINED.runs, (("mesh160-dt00025",160,0.00025),("mesh160-dt00025-tight",160,0.00025)))
        self.assertEqual(campaign.REFINED.end_time,0.5)
        self.assertEqual(len(campaign.LAMINAR.runs),6)
        self.assertEqual(len(campaign.LEGACY.runs),5)
        fixture=ROOT/"tools/cfd_worker/fixtures/openfoam12"
        base=campaign.prepare_case(fixture/"transient-cavity-v1",self.root/"refined",160,0.00025,campaign.REFINED)
        self.assertEqual(base["linearTolerance"],"1e-9")
        self.assertEqual(campaign.profile_identity(campaign.verified_source(fixture),campaign.REFINED),campaign.profile_identity(campaign.verified_source(fixture),campaign.LAMINAR))
        for n,dt in ((80,0.00025),(160,0.001),(160,0.000125)):
            with self.assertRaises(campaign.WorkerDiagnostic):campaign.prepare_case(fixture/"transient-cavity-v1",self.root/f"bad-{n}-{dt}",n,dt,campaign.REFINED)

    def prior_association(self):
        return {"repository":campaign.PRIOR_REPOSITORY,"repositoryPrivate":False,"runId":campaign.PRIOR_RUN_ID,
            "headSha":campaign.PRIOR_COMMIT,"event":"workflow_dispatch","workflow":".github/workflows/cavity-convergence-validation.yml",
            "status":"completed","conclusion":"success","artifactId":campaign.PRIOR_ARTIFACT_ID,
            "artifactName":"cavity-discretisation-development-evidence","artifactExpired":False,
            "artifactBytes":campaign.PRIOR_OUTER_BYTES,"artifactDigest":"sha256:"+campaign.PRIOR_OUTER_SHA256,
            "artifactRunId":campaign.PRIOR_RUN_ID,"artifactHeadSha":campaign.PRIOR_COMMIT,"headersAndRedirectCredentialsStored":False}

    def test_prior_source_run_requires_exact_public_authenticated_association(self):
        association=self.prior_association();campaign.validate_prior_association(association)
        for key,value in (("headSha","a"*40),("runId",1),("repositoryPrivate",True),("conclusion","failure"),("artifactDigest","sha256:"+"a"*64),("headersAndRedirectCredentialsStored",True)):
            with self.assertRaises(campaign.WorkerDiagnostic):campaign.validate_prior_association({**association,key:value})
        with self.assertRaises(campaign.WorkerDiagnostic):campaign.validate_prior_association({**association,"genericUserReport":True})

    def test_generic_prior_report_or_modified_archive_cannot_supply_reused_quantity(self):
        with self.assertRaises(campaign.WorkerDiagnostic) as error:
            campaign.verify_fixed_prior(b'{"quantity":1}',self.prior_association(),{})
        self.assertEqual(error.exception.code,"CFD_PRIOR_OUTER_CHECKSUM_MISMATCH")
        with patch.object(campaign,"PRIOR_OUTER_BYTES",1),self.assertRaises(campaign.WorkerDiagnostic):
            association=self.prior_association();campaign.verify_fixed_prior(b"x",association,{})

    def test_durable_release_requires_exact_asset_target_url_size_and_digest(self):
        release={"id":campaign.PRIOR_RELEASE_ID,"tag_name":campaign.PRIOR_RELEASE_TAG,"target_commitish":campaign.PRIOR_COMMIT,
            "assets":[{"id":campaign.PRIOR_RELEASE_ASSET_ID,"name":campaign.PRIOR_RELEASE_ASSET_NAME,"size":campaign.PRIOR_OUTER_BYTES,
                "digest":"sha256:"+campaign.PRIOR_OUTER_SHA256,"browser_download_url":campaign.PRIOR_RELEASE_URL}]}
        campaign.validate_prior_release(release)
        for key,value in (("target_commitish","main"),("id",1),("assets",release["assets"]*2)):
            with self.assertRaises(campaign.WorkerDiagnostic):campaign.validate_prior_release({**release,key:value})
        changed={**release,"assets":[{**release["assets"][0],"digest":"sha256:"+"a"*64}]}
        with self.assertRaises(campaign.WorkerDiagnostic):campaign.validate_prior_release(changed)

    def test_prior_archive_rejects_unsafe_paths_and_executable_payload(self):
        for name,mode in (("../escape",0o100644),("safe/evil",0o100755),("safe!evil",0o100644)):
            output=io.BytesIO()
            with zipfile.ZipFile(output,"w") as archive:
                item=zipfile.ZipInfo(name);item.external_attr=mode<<16;archive.writestr(item,b"x")
            payload=output.getvalue().replace(b"safe!evil",b"safe\\evil")
            with self.assertRaises(campaign.WorkerDiagnostic):campaign.checked_archive(payload)

    def test_refined_prior_failure_prevents_every_mesher_solver_and_preserves_diagnostic(self):
        fixture=ROOT/"tools/cfd_worker/fixtures/openfoam12"
        with patch.object(campaign.sys,"platform","linux"),patch.dict(campaign.os.environ,{"WM_PROJECT_VERSION":"12"}),patch.object(campaign.shutil,"which",return_value="/usr/bin/foamRun"),patch.object(campaign,"fetch_fixed_prior",side_effect=campaign.WorkerDiagnostic("BAD_PRIOR","Restore exact retained prior evidence.")),patch.object(campaign,"ProcessRunner") as runner:
            result,_=campaign.execute_campaign(self.root,fixture,campaign.REFINED)
        runner.assert_not_called()
        self.assertEqual(result["diagnostics"][0]["reasonCode"],"BAD_PRIOR")
        self.assertEqual(result["stages"],[])
        self.assertFalse(result["actualSolverExecutionCompleted"])
        self.assertFalse(result["mainApplicationWrites"])

    def test_refined_linear_repeat_uses_spatial_only_without_borrowed_temporal_claim(self):
        base,tight=self.linear_repeat()
        result=campaign.assess_linear_repeat(base,tight,{"fineRelativeChange":0.02},None)
        self.assertAlmostEqual(result["relativeChangeThreshold"],0.002)
        self.assertIn("mesh160 temporal sensitivity is unassessed",result["declaredCriterionBasis"])
        self.assertIsNone(result["numericalQualitySatisfied"])
        self.assertFalse(result["nonlinearCouplingErrorAssessed"])

    def test_temporal_v3_only_two_mesh160_steps_no_additional_fine_or_tight_solve(self):
        self.assertEqual(campaign.TEMPORAL.id, "cavity-laminar-re100-discretisation-v3")
        self.assertEqual(campaign.TEMPORAL.runs, (("mesh160-dt001",160,0.001),("mesh160-dt0005",160,0.0005)))
        self.assertEqual(sum(round(campaign.TEMPORAL.end_time/dt) for _,_,dt in campaign.TEMPORAL.runs),1500)
        fixture=ROOT/"tools/cfd_worker/fixtures/openfoam12"
        source=fixture/"transient-cavity-v1"
        original=campaign.verified_source(fixture)
        fine=campaign.prepare_case(source,self.root/"existing-fine-settings",160,0.00025,campaign.REFINED)
        for name,n,dt in campaign.TEMPORAL.runs:
            current=campaign.prepare_case(source,self.root/name,n,dt,campaign.TEMPORAL)
            for path in fine["derivedFileSha256"]:
                if path != "system/controlDict":
                    self.assertEqual(current["derivedFileSha256"][path],fine["derivedFileSha256"][path])
            self.assertEqual(current["controls"]["endTime"],"0.5")
            self.assertEqual(current["controls"]["writeInterval"],str(round(0.1/dt)))
            self.assertEqual(current["linearTolerance"],"1e-9")
        self.assertEqual(campaign.profile_identity(original,campaign.TEMPORAL),campaign.profile_identity(original,campaign.REFINED))
        for n,dt,tight in ((80,0.001,False),(160,0.00025,False),(160,0.0005,True)):
            with self.assertRaises(campaign.WorkerDiagnostic):
                campaign.prepare_case(source,self.root/f"invalid-temporal-{n}-{dt}-{tight}",n,dt,campaign.TEMPORAL,tight=tight)
        self.assertEqual(campaign.verified_source(fixture),original)

    def v2_association(self):
        pins=campaign.prior_pins(campaign.REFINED)
        return {"repository":campaign.PRIOR_REPOSITORY,"repositoryPrivate":False,"runId":pins["runId"],
            "headSha":pins["commit"],"event":"workflow_dispatch","workflow":".github/workflows/cavity-convergence-validation.yml",
            "status":"completed","conclusion":"success","artifactId":pins["artifactId"],
            "artifactName":"cavity-discretisation-development-evidence","artifactExpired":False,
            "artifactBytes":pins["outerBytes"],"artifactDigest":"sha256:"+pins["outerSha256"],
            "artifactRunId":pins["runId"],"artifactHeadSha":pins["commit"],"headersAndRedirectCredentialsStored":False,
            "retrievalMethod":"PinnedDurableRelease","originalArtifactAssociationIsHistorical":True}

    def test_temporal_v3_requires_exact_v2_not_v1_or_generic_run_association(self):
        association=self.v2_association()
        campaign.validate_prior_association(association,campaign.REFINED)
        for altered in (self.prior_association(),{**association,"headSha":"a"*40},{**association,"originalArtifactAssociationIsHistorical":False}):
            with self.assertRaises(campaign.WorkerDiagnostic):campaign.validate_prior_association(altered,campaign.REFINED)
        for profile in (campaign.LEGACY,campaign.TEMPORAL):
            with self.assertRaises(campaign.WorkerDiagnostic):campaign.prior_pins(profile)

    def test_temporal_v3_release_pins_reject_modified_asset_or_draft(self):
        pins=campaign.prior_pins(campaign.REFINED)
        release={"id":pins["releaseId"],"tag_name":pins["releaseTag"],"target_commitish":pins["commit"],"draft":False,"prerelease":False,
            "assets":[{"id":pins["releaseAssetId"],"name":"outer-github-artifact.zip","size":pins["outerBytes"],
                "digest":"sha256:"+pins["outerSha256"],"browser_download_url":pins["releaseUrl"]}]}
        campaign.validate_prior_release(release,campaign.REFINED)
        for altered in ({**release,"draft":True},{**release,"target_commitish":campaign.PRIOR_COMMIT},
                        {**release,"assets":[{**release["assets"][0],"browser_download_url":"https://example.com/user-report.zip"}]}):
            with self.assertRaises(campaign.WorkerDiagnostic):campaign.validate_prior_release(altered,campaign.REFINED)
        with self.assertRaises(campaign.WorkerDiagnostic) as error:
            campaign.verify_fixed_prior(b"untrusted modified prior",self.v2_association(),{},campaign.REFINED)
        self.assertEqual(error.exception.code,"CFD_PRIOR_OUTER_CHECKSUM_MISMATCH")

    def test_reused_temporal_history_retains_original_binding_samples_and_sources(self):
        source={"binding":{"studyId":"sealed-v2","inputRevision":"source-v1","inputSha256":"1"*64,"referenceSha256":"2"*64},
            "samples":[{"timeSeconds":0.5,"values":{"specific-kinetic-energy":0.033}}],
            "sourceFiles":[{"path":"runs/mesh160-dt00025/native-case/0.5/U","sha256":"3"*64,"bytes":10}]}
        original=json.dumps(source,sort_keys=True)
        binding={"studyId":"new-v3","inputRevision":"campaign-v1","inputSha256":"4"*64,"referenceSha256":"2"*64}
        derived,projection=campaign.bind_reused_history(source,binding)
        self.assertEqual(json.dumps(source,sort_keys=True),original)
        self.assertEqual(derived["samples"],source["samples"])
        self.assertEqual(derived["sourceFiles"],source["sourceFiles"])
        self.assertEqual(projection["originalBinding"],source["binding"])
        self.assertEqual(projection["derivedBinding"],binding)
        self.assertNotEqual(projection["originalHistoryDigest"],projection["derivedHistoryDigest"])
        self.assertFalse(projection["newSolverExecution"])
        with self.assertRaises(campaign.WorkerDiagnostic):campaign.bind_reused_history(source,{**binding,"referenceSha256":"5"*64})
        derived["samples"][0]["values"]["specific-kinetic-energy"]=9
        self.assertEqual(source["samples"][0]["values"]["specific-kinetic-energy"],0.033)

    def test_temporal_prior_rejection_prevents_all_new_mesh_or_solver_work(self):
        fixture=ROOT/"tools/cfd_worker/fixtures/openfoam12"
        with patch.object(campaign.sys,"platform","linux"),patch.dict(campaign.os.environ,{"WM_PROJECT_VERSION":"12"}),patch.object(campaign.shutil,"which",return_value="/usr/bin/foamRun"),patch.object(campaign,"fetch_fixed_prior",side_effect=campaign.WorkerDiagnostic("BAD_V2_NATIVE","Restore exact sealed v2 native evidence.")),patch.object(campaign,"ProcessRunner") as runner:
            result,_=campaign.execute_campaign(self.root,fixture,campaign.TEMPORAL)
        runner.assert_not_called()
        self.assertEqual(result["diagnostics"][0]["reasonCode"],"BAD_V2_NATIVE")
        self.assertEqual(result["stages"],[])
        self.assertFalse(result["actualSolverExecutionCompleted"])
        self.assertFalse(result["convergenceQualified"])

    def test_temporal_transport_assembles_correct_three_steps_and_never_qualifies_quality(self):
        """Synthetic first-order transport contract only; no actual solver evidence."""
        fixture=ROOT/"tools/cfd_worker/fixtures/openfoam12"
        source=campaign.verified_source(fixture)
        sealed_binding={"studyId":campaign.REFINED.id,"inputRevision":"campaign-v1","inputSha256":"1"*64,
            "referenceSha256":campaign.digest(campaign.profile_identity(source,campaign.REFINED))}
        def synthetic(name,dt,binding,tight=False):
            value=0.04+dt+(1e-9 if tight else 0)
            report={"schemaVersion":"untirta.cfd-numerical-quantity.v1","evidenceRole":"actual-solver",
                **campaign.profile_identity(source,campaign.TEMPORAL,tight=tight),
                "solverIdentity":{"distribution":"OpenFOAM-Foundation","version":"12"},"physicalTimeSeconds":0.5,
                "timeStepSeconds":dt,"quantity":{"id":campaign.QUANTITY,"unit":"m2/s2","value":value},
                "numericalQualitySatisfied":None,"sourceFiles":[{"path":"contract-only/U","sha256":"3"*64,"bytes":10}]}
            history={"schemaVersion":"untirta.cfd-time-history.v1","binding":binding,"evidenceRole":"actual-solver","timeUnit":"s",
                "quantities":[{"id":campaign.QUANTITY,"unit":"m2/s2"}],
                "samples":[{"timeSeconds":t,"values":{campaign.QUANTITY:value}} for t in (0.1,0.2,0.3,0.4,0.5)],"sourceFiles":report["sourceFiles"]}
            row={"id":name,"divisions":160,"cellCount":25600,"timeStepSeconds":dt,"physicalEndTimeSeconds":0.5,
                "meshSha256":"4"*64,"quantity":report["quantity"],"reportPath":f"runs/{name}/numerical-quantity.json",
                "historyPath":f"runs/{name}/time-history.json","diagnostics":{"physicalStepCount":round(0.5/dt),"samples":[]}}
            return row,report,history
        reused=[]
        for name,_,dt in campaign.REFINED.runs:
            row,report,history=synthetic(name,dt,sealed_binding,name.endswith("-tight"))
            reused.append({"row":row,"report":report,"history":history,"nativeSources":[]})
        prior={"temporalRuns":reused,"priorSpatialAssessment":{"fineRelativeChange":0.007},
            "priorTemporalAssessment":{"status":"NotAssessedOnRefinedMesh"},"priorMesh80TemporalAssessment":{"qualifiesMesh160":False}}
        class ContractRunner:
            def __init__(self,*args):pass
            def close(self):pass
            def run(self,args,cwd,log):
                log.parent.mkdir(parents=True,exist_ok=True);log.write_text("synthetic transport; not a real solver log\n")
                return {"exitCode":0,"log":"contract-only"}
        def collected(case,workspace,name,n,dt,identity,binding,profile):return synthetic(name,dt,binding)
        with patch.object(campaign.sys,"platform","linux"),patch.dict(campaign.os.environ,{"WM_PROJECT_VERSION":"12"}),patch.object(campaign.shutil,"which",return_value="/usr/bin/foamRun"),patch.object(campaign,"fetch_fixed_prior",return_value=(b"contract-only",{})),patch.object(campaign,"verify_fixed_prior",return_value=prior),patch.object(campaign,"ProcessRunner",ContractRunner),patch.object(campaign,"verify_uniform_mesh"),patch.object(campaign,"collect_run",side_effect=collected):
            result,workspace=campaign.execute_campaign(self.root,fixture,campaign.TEMPORAL)
        self.assertEqual(len(result["runs"]),2)
        self.assertEqual(len(result["stages"]),6)
        self.assertEqual([run["timeStepSeconds"] for run in result["temporalEvidence"]["runs"]],[0.001,0.0005,0.00025])
        self.assertTrue(result["temporalAssessment"]["valid"])
        self.assertAlmostEqual(result["temporalAssessment"]["observedOrder"],1)
        self.assertTrue(result["temporalAssessment"]["nominalRelativeChangeCriterionSatisfied"])
        self.assertIsNone(result["temporalAssessment"]["numericalQualitySatisfied"])
        self.assertIsNone(result["temporalAssessment"]["criterionSatisfied"])
        self.assertFalse(result["convergenceQualified"])
        self.assertFalse(result["mainApplicationWrites"])
        self.assertFalse(result["reusedTemporalRuns"][0]["newSolverExecution"])
        original=json.loads((workspace/"reference/prior/mesh160-dt00025-original-history.json").read_text())
        bound=json.loads((workspace/"reference/prior/mesh160-dt00025-bound-history.json").read_text())
        self.assertEqual(original["binding"],sealed_binding)
        self.assertEqual(bound["binding"],result["binding"])
        self.assertEqual(original["samples"],bound["samples"])

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
