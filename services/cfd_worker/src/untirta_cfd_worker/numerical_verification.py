"""Controlled grid sensitivity from declared quantities, never solver authenticity.

Three systematically refined grids may support an observed order and a GCI
estimate.  Checksums identify supplied operands, not a physical experiment or
an actual solver execution.  Temporal arithmetic remains in checkpoint.py.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any
from xml.sax.saxutils import escape

from .checkpoint import SHA, assess_temporal_evidence, file_descriptors, safe_path
from .contracts import WorkerDiagnostic

SCHEMA = "untirta.cfd-spatial-verification.v1"
QUANTITY_SCHEMA = "untirta.cfd-numerical-quantity.v1"
SOURCE = {
    "title": "NASA NPARC Alliance: Examining Spatial (Grid) Convergence",
    "authors": "NASA NPARC Alliance / J. W. Slater",
    "url": "https://www.grc.nasa.gov/www/wind/valid/tutorial/spatconv.html",
    "pages": "Observed order and Grid Convergence Index; web page has no printed page numbering",
    "equation": "Three common-ratio grids; GCI uses a safety factor of 1.25. Estimates do not prove an asymptotic range or physical accuracy.",
}
REPORT_KEYS = {"schemaVersion", "evidenceRole", "sourceIdentity", "solverIdentity", "geometrySha256",
               "physicsSettingsSha256", "boundaryConditionsSha256", "samplingDefinitionSha256",
               "physicalTimeSeconds", "timeStepSeconds", "quantity", "numericalQualitySatisfied", "sourceFiles"}
IDENTITY_KEYS = ("sourceIdentity", "solverIdentity", "geometrySha256", "physicsSettingsSha256",
                 "boundaryConditionsSha256", "samplingDefinitionSha256", "physicalTimeSeconds", "evidenceRole")


def _fail(code: str, action: str) -> None:
    raise WorkerDiagnostic(code, action)


def _number(value: Any, *, positive=False, nonnegative=False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("CFD_VERIFICATION_NUMBER_INVALID", "Use typed finite numerical operands in the declared SI units.")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or positive and value <= 0 or nonnegative and value < 0:
        _fail("CFD_VERIFICATION_NUMBER_INVALID", "Use finite quantities, positive spacings/time steps and nonnegative physical time.")
    return float(value)


def _text(value: Any, limit=240) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        _fail("CFD_VERIFICATION_IDENTITY_INVALID", "Provide bounded explicit source, solver and quantity identities without control characters.")
    return value


def _hash(value: Any) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        _fail("CFD_VERIFICATION_HASH_INVALID", "Bind each source, geometry, physical setting, boundary, sample and mesh to its lowercase SHA-256.")
    return value


def _quantity_report(data: Any) -> dict:
    if not isinstance(data, dict) or set(data) != REPORT_KEYS or data["schemaVersion"] != QUANTITY_SCHEMA:
        _fail("CFD_VERIFICATION_REPORT_INVALID", "Supply the exact bounded numerical-quantity report schema; do not attach results or authority flags.")
    if data["evidenceRole"] not in ("actual-solver", "contract-test"):
        _fail("CFD_EVIDENCE_ROLE_INVALID", "Distinguish declared actual-solver operands from synthetic contract-test operands.")
    source, solver, quantity = data["sourceIdentity"], data["solverIdentity"], data["quantity"]
    if not isinstance(source, dict) or set(source) != {"sourceId", "revision", "sha256"}:
        _fail("CFD_VERIFICATION_IDENTITY_INVALID", "Declare the unchanged source ID, revision and source checksum.")
    if not isinstance(solver, dict) or set(solver) != {"distribution", "version"}:
        _fail("CFD_VERIFICATION_IDENTITY_INVALID", "Keep the actual solver distribution and version explicit.")
    if not isinstance(quantity, dict) or set(quantity) != {"id", "unit", "value"}:
        _fail("CFD_VERIFICATION_QUANTITY_INVALID", "Declare the sampled quantity ID, unit and finite value.")
    _text(source["sourceId"]); _text(source["revision"]); _hash(source["sha256"])
    _text(solver["distribution"]); _text(solver["version"])
    _text(quantity["id"], 80); _text(quantity["unit"], 40); _number(quantity["value"])
    for key in ("geometrySha256", "physicsSettingsSha256", "boundaryConditionsSha256", "samplingDefinitionSha256"):
        _hash(data[key])
    _number(data["physicalTimeSeconds"], nonnegative=True)
    _number(data["timeStepSeconds"], positive=True)
    if data["numericalQualitySatisfied"] is not None and type(data["numericalQualitySatisfied"]) is not bool:
        _fail("CFD_VERIFICATION_QUALITY_INVALID", "Declare numerical quality as true, false or unknown (null); a residual value alone is not qualification.")
    file_descriptors(data["sourceFiles"], None)
    return deepcopy(data)


def _math(inner: str) -> str:
    return '<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">' + inner + '</math>'


def _mn(value: Any) -> str:
    return "<mn>" + escape(str(value)) + "</mn>"


def _stage(identifier: str, title: str, operands: dict, equation: str, substitution: str, result: Any, unit="1") -> dict:
    inputs = "<mrow>" + "<mo>;</mo>".join("<mtext>" + escape(key) + " = " + escape(str(value)) + "</mtext>" for key, value in operands.items()) + "</mrow>"
    return {"identifier": identifier, "title": title, "calculationAuthority": "Python",
            "input": deepcopy(operands), "inputMathML": _math(inputs), "equationMathML": _math(equation),
            "substitutionMathML": _math(substitution), "resultMathML": _math(_mn(result) + "<mtext> " + escape(unit) + "</mtext>"),
            "result": result, "unit": unit, "source": deepcopy(SOURCE)}


def _base() -> dict:
    return {"schemaVersion": "untirta.cfd-spatial-verification-report.v1", "valid": False,
            "status": "NumericalReviewRequired", "reasonCode": "CFD_SPATIAL_VERIFICATION_REQUIRED",
            "rawObservations": [], "diagnostics": [], "equationStages": [],
            "observedOrder": None, "fineRelativeChange": None, "nominalRelativeChangeCriterionSatisfied": None,
            "criterionSatisfied": None, "gci": None, "asymptoticRatioIndicator": None,
            "asymptoticRangeEstablished": False, "engineeringValidated": False, "actualCfdRunVerified": False,
            "authenticityVerified": False, "mainApplicationWrites": False,
            "claim": "Declared numerical sensitivity and conditional GCI estimates only; solver execution, source authenticity, physical accuracy and engineering acceptance remain separate."}


def _issue(output: dict, code: str, action: str) -> None:
    output["diagnostics"].append({"reasonCode": code, "correctiveAction": action})


def assess_spatial_evidence(data: Any, *, reports: dict[str, dict]) -> dict:
    """Inspect three common-ratio grids with unchanged physics and sampling.

    ``numericalQualitySatisfied`` is supplied evidence, not derived from a native
    linear-solver tolerance.  Unknown/failed quality retains order and relative
    differences but withholds GCI and numerical criterion acceptance.
    """
    output = _base()
    try:
        keys = {"schemaVersion", "gridDimension", "quantityId", "relativeTolerance", "runs"}
        if not isinstance(data, dict) or set(data) != keys or data["schemaVersion"] != SCHEMA:
            _fail("CFD_SPATIAL_SCHEMA_INVALID", "Provide three explicit systematically refined grids in the spatial-verification schema.")
        if type(data["gridDimension"]) is not int or data["gridDimension"] not in (2, 3):
            _fail("CFD_SPATIAL_DIMENSION_INVALID", "Declare whether the unchanged grid is refined in two or three dimensions.")
        quantity_id = _text(data["quantityId"], 80)
        tolerance = data["relativeTolerance"]
        if tolerance is not None and not 0 < _number(tolerance, positive=True) < 1:
            _fail("CFD_SPATIAL_TOLERANCE_INVALID", "Declare a relative criterion between zero and one, or null if no criterion exists.")
        if not isinstance(reports, dict) or len(reports) > 64 or not isinstance(data["runs"], list) or len(data["runs"]) != 3:
            _fail("CFD_SPATIAL_REFINEMENT_REQUIRED", "Supply three distinct bounded grid reports; repeated runs are not grid refinements.")
        rows, paths, meshes = [], set(), set()
        for run in data["runs"]:
            if not isinstance(run, dict) or set(run) != {"reportPath", "representativeSpacingM", "cellCount", "meshSha256"}:
                _fail("CFD_SPATIAL_RUN_INVALID", "Bind each grid to its report, explicit representative spacing in metres, cell count and mesh checksum.")
            path, mesh = safe_path(run["reportPath"]), _hash(run["meshSha256"])
            count = run["cellCount"]
            if type(count) is not int or not 1 <= count <= 1000000000:
                _fail("CFD_SPATIAL_CELL_COUNT_INVALID", "Use the actual positive bounded grid cell count.")
            spacing = _number(run["representativeSpacingM"], positive=True)
            if path.casefold() in paths or mesh in meshes:
                _fail("CFD_SPATIAL_REFINEMENT_REQUIRED", "Use three distinct report paths and mesh checksums; preserve each original mesh.")
            paths.add(path.casefold()); meshes.add(mesh)
            report = _quantity_report(reports.get(path))
            rows.append({**deepcopy(run), "report": report})
            output["rawObservations"].append({**deepcopy(run), "quantity": deepcopy(report["quantity"]),
                "numericalQualitySatisfied": report["numericalQualitySatisfied"], "sourceFiles": deepcopy(report["sourceFiles"])})
        first = rows[0]["report"]
        if any(any(row["report"][key] != first[key] for key in IDENTITY_KEYS + ("timeStepSeconds",)) or
               row["report"]["quantity"]["id"] != quantity_id or row["report"]["quantity"]["unit"] != first["quantity"]["unit"] for row in rows):
            _fail("CFD_SPATIAL_IDENTITY_MISMATCH", "Keep source revision, solver, geometry, physics, boundaries, sampling, physical time, time step, quantity unit and evidence role identical across the three grids.")
        rows.sort(key=lambda row: row["representativeSpacingM"])
        (fine, medium, coarse) = rows
        h1, h2, h3 = (row["representativeSpacingM"] for row in rows)
        if not (h1 < h2 < h3 and fine["cellCount"] > medium["cellCount"] > coarse["cellCount"]):
            _fail("CFD_SPATIAL_REFINEMENT_REQUIRED", "Decrease representative spacing and increase the actual cell count systematically.")
        ratio = h2 / h1
        if not math.isfinite(ratio) or not math.isclose(ratio, h3/h2, rel_tol=1e-10):
            _fail("CFD_SPATIAL_RATIO_INVALID", "Use a common systematic refinement ratio; the constant-ratio order formula does not support these spacings.")
        f1, f2, f3 = (_number(row["report"]["quantity"]["value"]) for row in rows)
        d21, d32 = f2-f1, f3-f2
        if not all(math.isfinite(v) for v in (d21, d32)):
            _fail("CFD_SPATIAL_DIFFERENCE_NONFINITE", "Reduce numerical scale and preserve finite raw grid differences.")
        output.update(valid=True, status="SpatialSensitivityAssessed", reasonCode="CFD_SPATIAL_SENSITIVITY_ASSESSED",
                      evidenceRole=first["evidenceRole"], quantityId=quantity_id, quantityUnit=first["quantity"]["unit"],
                      relativeTolerance=tolerance, refinementRatio=ratio, gridDimension=data["gridDimension"],
                      mediumMinusFine=d21, coarseMinusMedium=d32, matchedIdentity={key: deepcopy(first[key]) for key in IDENTITY_KEYS + ("timeStepSeconds",)})
        output["rawObservations"].sort(key=lambda row: row["representativeSpacingM"])
        unit = first["quantity"]["unit"]
        output["equationStages"].append(_stage("CFD_SPATIAL_REFINEMENT_RATIO", "Common grid refinement ratio",
            {"fineSpacingM": h1, "mediumSpacingM": h2, "coarseSpacingM": h3},
            "<mi>r</mi><mo>=</mo><mfrac><msub><mi>h</mi><mn>2</mn></msub><msub><mi>h</mi><mn>1</mn></msub></mfrac>",
            "<mi>r</mi><mo>=</mo><mfrac>"+_mn(h2)+_mn(h1)+"</mfrac>", ratio))
        if f1 == 0:
            _issue(output, "CFD_SPATIAL_ZERO_NORMALIZATION", "Fine-grid quantity is zero; report absolute differences without a relative change or GCI.")
        else:
            relative = abs(d21)/abs(f1)
            if not math.isfinite(relative):
                _fail("CFD_SPATIAL_DIFFERENCE_NONFINITE", "Preserve finite normalization operands; relative change overflows at this scale.")
            output["fineRelativeChange"] = relative
            output["nominalRelativeChangeCriterionSatisfied"] = relative <= tolerance if tolerance is not None else None
            output["equationStages"].append(_stage("CFD_SPATIAL_FINE_RELATIVE_CHANGE", "Medium-to-fine relative change",
                {"fineValue": f1, "mediumValue": f2, "quantityUnit": unit, "relativeTolerance": tolerance},
                "<mi>ε</mi><mo>=</mo><mfrac><mrow><mo>|</mo><msub><mi>f</mi><mn>2</mn></msub><mo>−</mo><msub><mi>f</mi><mn>1</mn></msub><mo>|</mo></mrow><mrow><mo>|</mo><msub><mi>f</mi><mn>1</mn></msub><mo>|</mo></mrow></mfrac>",
                "<mi>ε</mi><mo>=</mo><mfrac><mrow><mo>|</mo>"+_mn(f2)+"<mo>−</mo>"+_mn(f1)+"<mo>|</mo></mrow><mrow><mo>|</mo>"+_mn(f1)+"<mo>|</mo></mrow></mfrac>", relative))
        monotone = d21 != 0 and d32 != 0 and (d21 > 0) == (d32 > 0)
        if not monotone:
            _issue(output, "CFD_SPATIAL_ORDER_UNRESOLVED", "Equal or oscillating grid differences do not resolve observed order; retain raw values and extend refinement.")
        else:
            order = (math.log(abs(d32))-math.log(abs(d21)))/math.log(ratio)
            if not math.isfinite(order):
                _fail("CFD_SPATIAL_ORDER_UNRESOLVED", "Inspect finite systematic differences and refinement ratios.")
            output["observedOrder"] = order
            output["equationStages"].append(_stage("CFD_SPATIAL_OBSERVED_ORDER", "Observed spatial order",
                {"fineValue": f1, "mediumValue": f2, "coarseValue": f3, "quantityUnit": unit, "refinementRatio": ratio},
                "<mi>p</mi><mo>=</mo><mfrac><mrow><mi>ln</mi><mo>(</mo><mfrac><mrow><mo>|</mo><msub><mi>f</mi><mn>3</mn></msub><mo>−</mo><msub><mi>f</mi><mn>2</mn></msub><mo>|</mo></mrow><mrow><mo>|</mo><msub><mi>f</mi><mn>2</mn></msub><mo>−</mo><msub><mi>f</mi><mn>1</mn></msub><mo>|</mo></mrow></mfrac><mo>)</mo></mrow><mrow><mi>ln</mi><mi>r</mi></mrow></mfrac>",
                "<mi>p</mi><mo>=</mo><mfrac><mrow><mi>ln</mi><mo>(</mo><mfrac><mrow><mo>|</mo>"+_mn(d32)+"<mo>|</mo></mrow><mrow><mo>|</mo>"+_mn(d21)+"<mo>|</mo></mrow></mfrac><mo>)</mo></mrow><mrow><mi>ln</mi>"+_mn(ratio)+"</mrow></mfrac>", order))
            if order <= 0:
                _issue(output, "CFD_SPATIAL_ORDER_NONPOSITIVE", "Observed differences do not decrease with refinement; a positive-order GCI estimate is unavailable.")
        quality = [row["report"]["numericalQualitySatisfied"] for row in rows]
        qualified = all(value is True for value in quality)
        output["numericalQualitySatisfied"] = True if qualified else False if False in quality else None
        if not qualified:
            _issue(output, "CFD_NUMERICAL_QUALITY_UNQUALIFIED", "Demonstrate iterative-error and conservation adequacy relative to grid differences; finite linear residuals alone do not establish this.")
        if tolerance is None:
            _issue(output, "CFD_VERIFICATION_CRITERION_UNDECLARED", "Declare a justified relative criterion before claiming it satisfied.")
        if ratio < 1.1:
            _issue(output, "CFD_SPATIAL_REFINEMENT_TOO_CLOSE", "Separate grids with refinement ratio at least 1.1 for this documented GCI assessment; retain nominal sensitivity.")
        if f2 == 0:
            _issue(output, "CFD_SPATIAL_ZERO_MEDIUM_NORMALIZATION", "Medium-grid quantity is zero; the normalized medium GCI and asymptotic ratio are unavailable.")
        order = output["observedOrder"]
        if qualified and tolerance is not None:
            output["criterionSatisfied"] = bool(order is not None and order > 0 and output["fineRelativeChange"] is not None and output["fineRelativeChange"] <= tolerance)
        if qualified and ratio >= 1.1 and order is not None and order > 0 and f1 != 0 and f2 != 0:
            denominator = math.expm1(order*math.log(ratio))
            fine_gci = 1.25 * output["fineRelativeChange"] / denominator
            medium_gci = 1.25 * (abs(d32)/abs(f2)) / denominator
            indicator = medium_gci/(math.exp(order*math.log(ratio))*fine_gci)
            if not all(math.isfinite(value) for value in (fine_gci, medium_gci, indicator)):
                _fail("CFD_SPATIAL_GCI_NONFINITE", "Inspect normalization and observed-order scale; preserve raw sensitivity without nonfinite estimates.")
            output["gci"] = {"safetyFactor": 1.25, "fineFraction": fine_gci, "mediumFraction": medium_gci,
                "interpretation": "Conditional discretization-error estimate, not a confidence interval or physical-model validation."}
            output["asymptoticRatioIndicator"] = indicator
            output["equationStages"].append(_stage("CFD_SPATIAL_GCI_FINE", "Conditional fine-grid GCI estimate",
                {"fineRelativeChange": output["fineRelativeChange"], "refinementRatio": ratio, "observedOrder": order, "safetyFactor": 1.25},
                "<msub><mi>GCI</mi><mn>12</mn></msub><mo>=</mo><mfrac><mrow><msub><mi>F</mi><mi>s</mi></msub><mi>ε</mi></mrow><mrow><msup><mi>r</mi><mi>p</mi></msup><mo>−</mo><mn>1</mn></mrow></mfrac>",
                "<msub><mi>GCI</mi><mn>12</mn></msub><mo>=</mo><mfrac><mrow><mn>1.25</mn><mo>×</mo>"+_mn(output["fineRelativeChange"])+"</mrow><mrow><msup>"+_mn(ratio)+_mn(order)+"</msup><mo>−</mo><mn>1</mn></mrow></mfrac>", fine_gci))
            output["equationStages"].append(_stage("CFD_SPATIAL_GCI_MEDIUM", "Conditional medium-grid GCI estimate",
                {"mediumValue": f2, "coarseValue": f3, "quantityUnit": unit, "refinementRatio": ratio, "observedOrder": order, "safetyFactor": 1.25},
                "<msub><mi>GCI</mi><mn>23</mn></msub><mo>=</mo><mfrac><mrow><msub><mi>F</mi><mi>s</mi></msub><mo>|</mo><msub><mi>f</mi><mn>3</mn></msub><mo>−</mo><msub><mi>f</mi><mn>2</mn></msub><mo>|</mo></mrow><mrow><mo>|</mo><msub><mi>f</mi><mn>2</mn></msub><mo>|</mo><mo>(</mo><msup><mi>r</mi><mi>p</mi></msup><mo>−</mo><mn>1</mn><mo>)</mo></mrow></mfrac>",
                "<msub><mi>GCI</mi><mn>23</mn></msub><mo>=</mo><mfrac><mrow><mn>1.25</mn><mo>×</mo><mo>|</mo>"+_mn(d32)+"<mo>|</mo></mrow><mrow><mo>|</mo>"+_mn(f2)+"<mo>|</mo><mo>(</mo><msup>"+_mn(ratio)+_mn(order)+"</msup><mo>−</mo><mn>1</mn><mo>)</mo></mrow></mfrac>", medium_gci))
            output["equationStages"].append(_stage("CFD_SPATIAL_ASYMPTOTIC_RATIO_INDICATOR", "Asymptotic ratio indicator (not proof)",
                {"mediumGciFraction": medium_gci, "fineGciFraction": fine_gci, "refinementRatio": ratio, "observedOrder": order},
                "<mi>A</mi><mo>=</mo><mfrac><msub><mi>GCI</mi><mn>23</mn></msub><mrow><msup><mi>r</mi><mi>p</mi></msup><msub><mi>GCI</mi><mn>12</mn></msub></mrow></mfrac>",
                "<mi>A</mi><mo>=</mo><mfrac>"+_mn(medium_gci)+"<mrow><msup>"+_mn(ratio)+_mn(order)+"</msup><mo>×</mo>"+_mn(fine_gci)+"</mrow></mfrac>", indicator))
        return output
    except WorkerDiagnostic as error:
        output.update(valid=False, status="NumericalReviewRequired", reasonCode=error.code, gci=None,
                      asymptoticRatioIndicator=None, criterionSatisfied=None)
        _issue(output, error.code, error.action)
        return output
    except (TypeError, ValueError, ZeroDivisionError, OverflowError, RecursionError):
        output.update(valid=False, status="NumericalReviewRequired", reasonCode="CFD_SPATIAL_EVIDENCE_INVALID", gci=None,
                      asymptoticRatioIndicator=None, criterionSatisfied=None)
        _issue(output, "CFD_SPATIAL_EVIDENCE_INVALID", "Supply bounded finite controlled grid operands; retain raw reports for diagnosis.")
        return output


def assess_temporal_quantity_evidence(data: Any, *, binding: dict, histories: dict[str, dict], reports: dict[str, dict]) -> dict:
    """Strong campaign identity gate, then reuse existing temporal arithmetic.

    ``reports`` is keyed by each existing temporal run's ``historyPath``.  Each
    quantity report must identify that run's exact evaluation sample and dt.
    Geometry, boundaries, source and sampling are matched in addition to the
    existing checkpoint temporal mesh/physics/unit checks.  No temporal GCI is
    introduced by this wrapper.
    """
    raw = []
    try:
        if not isinstance(data, dict) or not isinstance(data.get("runs"), list) or len(data["runs"]) != 3 or not isinstance(reports, dict) or len(reports) > 64 or not isinstance(histories, dict) or len(histories) > 64:
            _fail("CFD_TEMPORAL_IDENTITY_REQUIRED", "Provide the three exact temporal histories and matched numerical-quantity identity reports.")
        validated = []
        for run in data["runs"]:
            if not isinstance(run, dict):
                _fail("CFD_TEMPORAL_IDENTITY_REQUIRED", "Describe each controlled temporal run with an exact history path and numerical report.")
            path = safe_path(run.get("historyPath"))
            report = _quantity_report(reports.get(path))
            raw.append({"historyPath": path, "quantity": deepcopy(report["quantity"]), "numericalQualitySatisfied": report["numericalQualitySatisfied"]})
            history = histories.get(path)
            if not isinstance(history, dict) or not isinstance(history.get("samples"), list):
                _fail("CFD_TEMPORAL_HISTORY_INVALID", "Restore bounded exact temporal histories.")
            samples = [row for row in history["samples"] if isinstance(row, dict) and row.get("timeSeconds") == data.get("evaluationTimeSeconds")]
            units = [q.get("unit") for q in history.get("quantities", []) if isinstance(q, dict) and q.get("id") == data.get("quantityId")]
            if len(samples) != 1 or not isinstance(samples[0].get("values"), dict) or units != [report["quantity"]["unit"]] or report["quantity"]["id"] != data.get("quantityId") or report["physicalTimeSeconds"] != data.get("evaluationTimeSeconds") or report["timeStepSeconds"] != run.get("timeStepSeconds") or report["physicsSettingsSha256"] != run.get("physicsSettingsSha256") or report["quantity"]["value"] != samples[0]["values"].get(data.get("quantityId")) or report["evidenceRole"] != history.get("evidenceRole"):
                _fail("CFD_TEMPORAL_IDENTITY_MISMATCH", "Match each report to its exact history value, evaluation time, time step, physics and evidence role.")
            validated.append(report)
        first = validated[0]
        if any(any(row[key] != first[key] for key in IDENTITY_KEYS) or row["quantity"]["unit"] != first["quantity"]["unit"] for row in validated):
            _fail("CFD_TEMPORAL_IDENTITY_MISMATCH", "Keep source revision, solver, geometry, physics, boundaries, sampling, physical time, quantity unit and role unchanged in temporal refinement.")
        result = assess_temporal_evidence(data, binding=binding, histories=histories)
        result["rawQuantityReports"] = raw
        result["asymptoticRangeEstablished"] = False
        result["matchedIdentity"] = {key: deepcopy(first[key]) for key in IDENTITY_KEYS}
        if result["valid"]:
            # Presentation only: checkpoint.py remains the temporal authority.
            for stage in result["equationStages"]:
                stage["inputMathML"] = _math("<mrow>" + "<mo>;</mo>".join("<mtext>" + escape(key) + " = " + escape(str(value)) + "</mtext>" for key, value in stage["input"].items()) + "</mrow>")
                stage["calculationAuthority"] = "Python"
                stage["unit"] = "1"
            nominal = result["criterionSatisfied"]
            quality = [row["numericalQualitySatisfied"] for row in validated]
            qualified = all(value is True for value in quality)
            result["nominalRelativeChangeCriterionSatisfied"] = nominal
            result["numericalQualitySatisfied"] = True if qualified else False if False in quality else None
            result["criterionSatisfied"] = nominal if qualified else None
            result.setdefault("diagnostics", [])
            if not qualified:
                _issue(result, "CFD_NUMERICAL_QUALITY_UNQUALIFIED", "Demonstrate iterative-error and conservation adequacy relative to time-step differences before numerical qualification.")
        return result
    except (WorkerDiagnostic, TypeError, ValueError, OverflowError, RecursionError) as error:
        code = error.code if isinstance(error, WorkerDiagnostic) else "CFD_TEMPORAL_IDENTITY_INVALID"
        action = error.action if isinstance(error, WorkerDiagnostic) else "Supply bounded matched temporal quantities and histories."
        return {"valid": False, "status": "NumericalReviewRequired", "reasonCode": code,
                "rawQuantityReports": raw, "diagnostics": [{"reasonCode": code, "correctiveAction": action}],
                "criterionSatisfied": None, "engineeringValidated": False, "actualCfdRunVerified": False,
                "asymptoticRangeEstablished": False}
