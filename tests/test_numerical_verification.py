"""Synthetic numerical-contract tests; no OpenFOAM or physical CFD is executed."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "services/cfd_worker/src").is_dir())
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "services/cfd_worker/src"))
from untirta_cfd_worker.numerical_verification import assess_spatial_evidence, assess_temporal_quantity_evidence


def quantity(value, *, quality=True, dt=0.00025):
    return {"schemaVersion": "untirta.cfd-numerical-quantity.v1", "evidenceRole": "contract-test",
        "sourceIdentity": {"sourceId": "synthetic-matched-cavity", "revision": "contract-v1", "sha256": "a"*64},
        "solverIdentity": {"distribution": "not-executed", "version": "contract-v1"},
        "geometrySha256": "b"*64, "physicsSettingsSha256": "c"*64, "boundaryConditionsSha256": "d"*64,
        "samplingDefinitionSha256": "e"*64, "physicalTimeSeconds": 0.05, "timeStepSeconds": dt,
        "quantity": {"id": "specific-kinetic-energy", "unit": "m2/s2", "value": value},
        "numericalQualitySatisfied": quality, "sourceFiles": [{"path": "synthetic/U", "sha256": "f"*64, "bytes": 123}]}


def spatial(values=(1.01, 1.04, 1.16), quality=True):
    data = {"schemaVersion": "untirta.cfd-spatial-verification.v1", "gridDimension": 2,
        "quantityId": "specific-kinetic-energy", "relativeTolerance": 0.05, "runs": []}
    reports = {}
    for index, (h, count, value) in enumerate(zip((0.00125, 0.0025, 0.005), (6400, 1600, 400), values)):
        path = "runs/contract-" + str(index) + "/numerical-quantity.json"
        data["runs"].append({"reportPath": path, "representativeSpacingM": h, "cellCount": count, "meshSha256": str(index)*64})
        reports[path] = quantity(value, quality=quality)
    return data, reports


def temporal():
    binding = {"studyId": "contract-campaign", "inputRevision": "contract-v1", "inputSha256": "1"*64, "referenceSha256": "2"*64}
    data = {"schemaVersion": "untirta.cfd-temporal-evidence.v1", "binding": binding,
            "quantityId": "specific-kinetic-energy", "evaluationTimeSeconds": 0.05, "relativeTolerance": 0.05, "runs": []}
    histories, reports = {}, {}
    for index, (dt, value) in enumerate(zip((0.001, 0.0005, 0.00025), (1.16, 1.04, 1.01))):
        path = "runs/contract-dt-" + str(index) + "/history.json"
        data["runs"].append({"historyPath": path, "timeStepSeconds": dt, "meshSha256": "b"*64, "physicsSettingsSha256": "c"*64})
        histories[path] = {"schemaVersion": "untirta.cfd-time-history.v1", "binding": binding, "evidenceRole": "contract-test",
            "timeUnit": "s", "quantities": [{"id": "specific-kinetic-energy", "unit": "m2/s2"}],
            "samples": [{"timeSeconds": 0.01, "values": {"specific-kinetic-energy": value/2}},
                        {"timeSeconds": 0.05, "values": {"specific-kinetic-energy": value}}],
            "sourceFiles": [{"path": "synthetic/U", "sha256": "f"*64, "bytes": 123}]}
        reports[path] = quantity(value, dt=dt)
    return data, binding, histories, reports


class NumericalVerificationTests(unittest.TestCase):
    def test_exact_quadratic_grid_error_and_gci_match_independent_operands(self):
        data, reports = spatial()
        data["runs"].reverse()  # input ordering cannot change grid identity
        result = assess_spatial_evidence(data, reports=reports)
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["observedOrder"], 2)
        self.assertAlmostEqual(result["fineRelativeChange"], 0.03/1.01)
        self.assertAlmostEqual(result["gci"]["fineFraction"], 1.25*(0.03/1.01)/3)
        self.assertAlmostEqual(result["gci"]["mediumFraction"], 1.25*(0.12/1.04)/3)
        self.assertAlmostEqual(result["asymptoticRatioIndicator"], 1.01/1.04)
        self.assertTrue(result["criterionSatisfied"])
        for key in ("engineeringValidated", "actualCfdRunVerified", "authenticityVerified", "asymptoticRangeEstablished", "mainApplicationWrites"):
            self.assertFalse(result[key])
        for stage in result["equationStages"]:
            for key in ("inputMathML", "equationMathML", "substitutionMathML", "resultMathML"):
                ElementTree.fromstring(stage[key])
            self.assertIn("unit", stage)
            self.assertIn("grc.nasa.gov", stage["source"]["url"])

    def test_unknown_or_failed_numerical_quality_keeps_raw_order_not_gci_or_acceptance(self):
        for quality in (None, False):
            with self.subTest(quality=quality):
                data, reports = spatial(quality=quality)
                result = assess_spatial_evidence(data, reports=reports)
                self.assertTrue(result["valid"])
                self.assertAlmostEqual(result["observedOrder"], 2)
                self.assertEqual(len(result["rawObservations"]), 3)
                self.assertIsNone(result["gci"])
                self.assertIsNone(result["criterionSatisfied"])
                self.assertEqual(result["numericalQualitySatisfied"], quality)
                self.assertIn("CFD_NUMERICAL_QUALITY_UNQUALIFIED", [d["reasonCode"] for d in result["diagnostics"]])

    def test_missing_declared_tolerance_does_not_invent_an_acceptance_threshold(self):
        data, reports = spatial()
        data["relativeTolerance"] = None
        result = assess_spatial_evidence(data, reports=reports)
        self.assertTrue(result["valid"])
        self.assertIsNone(result["criterionSatisfied"])
        self.assertIsNone(result["nominalRelativeChangeCriterionSatisfied"])
        self.assertIsNotNone(result["gci"])

    def test_actual_solver_metadata_is_not_execution_or_engineering_authority(self):
        data, reports = spatial()
        for report in reports.values(): report["evidenceRole"] = "actual-solver"
        result = assess_spatial_evidence(data, reports=reports)
        self.assertTrue(result["valid"])
        self.assertEqual(result["evidenceRole"], "actual-solver")
        self.assertFalse(result["actualCfdRunVerified"])
        self.assertFalse(result["engineeringValidated"])
        self.assertFalse(result["authenticityVerified"])

    def test_zero_equal_oscillating_and_nonpositive_order_are_honest_raw_diagnostics(self):
        for values, code in (((0, .03, .15), "CFD_SPATIAL_ZERO_NORMALIZATION"),
                             ((1, 1, 1), "CFD_SPATIAL_ORDER_UNRESOLVED"),
                             ((1, 1.2, 1.1), "CFD_SPATIAL_ORDER_UNRESOLVED"),
                             ((1, 1.2, 1.3), "CFD_SPATIAL_ORDER_NONPOSITIVE"),
                             ((-1, 0, 4), "CFD_SPATIAL_ZERO_MEDIUM_NORMALIZATION")):
            with self.subTest(values=values):
                data, reports = spatial(values)
                result = assess_spatial_evidence(data, reports=reports)
                self.assertTrue(result["valid"])
                self.assertIsNone(result["gci"])
                self.assertEqual([r["quantity"]["value"] for r in result["rawObservations"]], list(values))
                self.assertIn(code, [d["reasonCode"] for d in result["diagnostics"]])

    def test_mismatched_source_solver_geometry_physics_boundary_sampling_time_unit_or_role_is_rejected(self):
        changes = {"sourceIdentity": {"sourceId": "another-source", "revision": "contract-v1", "sha256": "a"*64},
            "solverIdentity": {"distribution": "another-solver", "version": "contract-v1"}, "geometrySha256": "0"*64,
            "physicsSettingsSha256": "0"*64, "boundaryConditionsSha256": "0"*64, "samplingDefinitionSha256": "0"*64,
            "physicalTimeSeconds": .051, "timeStepSeconds": .0005, "evidenceRole": "actual-solver",
            "quantity": {"id": "specific-kinetic-energy", "unit": "m/s", "value": 1.04}}
        for key, value in changes.items():
            with self.subTest(key=key):
                data, reports = spatial()
                reports[data["runs"][1]["reportPath"]][key] = value
                result = assess_spatial_evidence(data, reports=reports)
                self.assertFalse(result["valid"])
                self.assertEqual(result["reasonCode"], "CFD_SPATIAL_IDENTITY_MISMATCH")
                self.assertEqual(len(result["rawObservations"]), 3)
                self.assertEqual(result["equationStages"], [])

    def test_distinct_systematic_grids_required_and_no_cellcount_only_inference(self):
        for mutation in ("duplicate-path", "duplicate-mesh", "nonuniform-ratio", "count-reversal", "missing-spacing"):
            with self.subTest(mutation=mutation):
                data, reports = spatial()
                if mutation == "duplicate-path": data["runs"][1]["reportPath"] = data["runs"][0]["reportPath"]
                if mutation == "duplicate-mesh": data["runs"][1]["meshSha256"] = data["runs"][0]["meshSha256"]
                if mutation == "nonuniform-ratio": data["runs"][2]["representativeSpacingM"] *= 1.1
                if mutation == "count-reversal": data["runs"][1]["cellCount"] = 6401
                if mutation == "missing-spacing": del data["runs"][1]["representativeSpacingM"]
                result = assess_spatial_evidence(data, reports=reports)
                self.assertFalse(result["valid"])
                self.assertIsNone(result["gci"])

    def test_close_grid_ratio_does_not_claim_gci(self):
        data, reports = spatial()
        for row, spacing in zip(data["runs"], (.005, .00525, .0055125)):
            row["representativeSpacingM"] = spacing
        result = assess_spatial_evidence(data, reports=reports)
        self.assertTrue(result["valid"])
        self.assertIsNone(result["gci"])
        self.assertIn("CFD_SPATIAL_REFINEMENT_TOO_CLOSE", [d["reasonCode"] for d in result["diagnostics"]])

    def test_untrusted_metadata_cannot_add_authority_flags_or_inject_mathml(self):
        data, reports = spatial()
        unit = '<img src=x onerror="alert(1)">'
        for report in reports.values(): report["quantity"]["unit"] = unit
        result = assess_spatial_evidence(data, reports=reports)
        self.assertTrue(result["valid"])
        for stage in result["equationStages"]:
            for key in ("inputMathML", "equationMathML", "substitutionMathML", "resultMathML"):
                tree = ElementTree.fromstring(stage[key])
                self.assertFalse(any(node.tag.endswith("img") for node in tree.iter()))
        reports[data["runs"][0]["reportPath"]]["actualCfdRunVerified"] = True
        result = assess_spatial_evidence(data, reports=reports)
        self.assertFalse(result["valid"])
        self.assertFalse(result["actualCfdRunVerified"])

    def test_nonfinite_boolean_oversized_and_unsafe_operands_fail_closed(self):
        for value in (True, float("nan"), float("inf"), 10**1000):
            with self.subTest(valueType=type(value).__name__):
                data, reports = spatial()
                reports[data["runs"][1]["reportPath"]]["quantity"]["value"] = value
                self.assertFalse(assess_spatial_evidence(data, reports=reports)["valid"])
        data, reports = spatial()
        reports[data["runs"][1]["reportPath"]]["sourceFiles"][0]["path"] = "../../private.json"
        self.assertFalse(assess_spatial_evidence(data, reports=reports)["valid"])

    def test_temporal_wrapper_reuses_order_and_adds_quality_source_identity_gates(self):
        data, binding, histories, reports = temporal()
        before = deepcopy((data, histories, reports))
        result = assess_temporal_quantity_evidence(data, binding=binding, histories=histories, reports=reports)
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["observedOrder"], 2)
        self.assertTrue(result["criterionSatisfied"])
        self.assertFalse(result["asymptoticRangeEstablished"])
        self.assertEqual(before, (data, histories, reports))
        for stage in result["equationStages"]:
            for key in ("inputMathML", "equationMathML", "substitutionMathML", "resultMathML"):
                ElementTree.fromstring(stage[key])
            self.assertEqual(stage["calculationAuthority"], "Python")
        for report in reports.values(): report["numericalQualitySatisfied"] = None
        result = assess_temporal_quantity_evidence(data, binding=binding, histories=histories, reports=reports)
        self.assertTrue(result["valid"])
        self.assertIsNone(result["criterionSatisfied"])
        self.assertTrue(result["nominalRelativeChangeCriterionSatisfied"])
        self.assertEqual(len(result["rawQuantityReports"]), 3)

    def test_temporal_mismatched_boundary_history_value_unit_time_or_binding_is_rejected(self):
        for mutation in ("boundary", "value", "unit", "report-time", "sample-time", "binding"):
            with self.subTest(mutation=mutation):
                data, binding, histories, reports = temporal()
                path = data["runs"][1]["historyPath"]
                if mutation == "boundary": reports[path]["boundaryConditionsSha256"] = "0"*64
                if mutation == "value": reports[path]["quantity"]["value"] = 1.05
                if mutation == "unit": reports[path]["quantity"]["unit"] = "m/s"
                if mutation == "report-time": reports[path]["physicalTimeSeconds"] = .051
                if mutation == "sample-time": histories[path]["samples"][1]["timeSeconds"] = .051
                if mutation == "binding": histories[path]["binding"] = {**binding, "inputRevision": "contract-other"}
                result = assess_temporal_quantity_evidence(data, binding=binding, histories=histories, reports=reports)
                self.assertFalse(result["valid"])
                self.assertFalse(result["actualCfdRunVerified"])


if __name__ == "__main__":
    unittest.main()
