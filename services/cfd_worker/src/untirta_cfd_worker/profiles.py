"""Fixed educational profiles and exact public source bindings; no Pump defaults."""

OPENFOAM_COMMIT = "0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd"
IMPELLER_PROFILES = ("impeller-transient-v1", "impeller-mrf-v1")
OPENFOAM_PACKAGE_SHA256 = "b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1"
OPENFOAM_PACKAGE_BUILD = "12-86e126a7bc4d"
IMPELLER_PROVENANCE_SHA256 = "d8fe70bf0f11ac4dbbdb1f8eb5402bf03f99a45f51ff2dfc93117cc64700481c"
IMPELLER_MOTION_DICT_SHA256 = "296b91a86de32230c7316b7e87b8d5ba7be15aa6d0b3fb4a55ae5913d46d462e"
IMPELLER_NCC_DICT_SHA256 = "1d26944e7563c884cb3e8dcbbe1540c1f311a974a43f48281a762b96e9e863d1"


def benchmark_reference(profile: str) -> dict:
    if profile in IMPELLER_PROFILES:
        return {"schemaVersion": "untirta.cfd-benchmark-reference.v2", "kind": "development-benchmark",
                "caseProfile": profile, "sourcePackageSha256": OPENFOAM_PACKAGE_SHA256,
                "sourcePackageVersion": "20250206", "sourceBuild": OPENFOAM_PACKAGE_BUILD,
                "sourceCase": "tutorials/incompressibleFluid/impeller"}
    if profile in ("transient-cavity-v1", "cavitation-bullet-v1"):
        return {"schemaVersion": "untirta.cfd-benchmark-reference.v1", "kind": "development-benchmark",
                "caseProfile": profile, "sourceCommit": OPENFOAM_COMMIT}
    raise ValueError("Unsupported fixed educational profile")
