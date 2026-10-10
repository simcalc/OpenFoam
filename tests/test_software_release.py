"""Synthetic software packaging/transport contracts; never publishes or solves."""
from hashlib import sha256
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "tools/cfd_worker/software_release.py").is_file())
TEST_TMP = ROOT / "tmp/software-release-contract-tests"
TEST_TMP.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location("software_release_contract", ROOT / "tools/cfd_worker/software_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def synthetic_tree(root):
    """Small declared contract fixtures, not OpenFOAM binaries or a release."""
    paths = ("COPYING", "Allwmake", "wmake/wmake", "etc/bashrc", "src/OpenFOAM/example.C",
        "applications/modules/example.C", "applications/solvers/example.C", "applications/utilities/example.C",
        "thirdparty/example.C", "tutorials/example/controlDict", "bin/example", "doc/example", "build-stamp", ".build",
        "platforms/linux64GccDPInt32Opt/bin/foamRun", "platforms/linux64GccDPInt32Opt/bin/blockMesh",
        "platforms/linux64GccDPInt32Opt/bin/checkMesh", "platforms/linux64GccDPInt32Opt/bin/snappyHexMesh",
        "platforms/linux64GccDPInt32Opt/bin/potentialFoam", "platforms/linux64GccDPInt32Opt/lib/libOpenFOAM.so",
        "platforms/linux64GccDPInt32Opt/lib/libblockMesh.so")
    for name in paths:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"GNU GENERAL PUBLIC LICENSE\nVersion 3\nCONTRACT ONLY\n" if name == "COPYING" else b"CONTRACT ONLY; NO SOFTWARE CLAIM\n")


class SoftwareReleaseTests(unittest.TestCase):
    def test_exact_manual_public_standard_context_rejects_other_repositories(self):
        environment = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux", "RUNNER_ENVIRONMENT": "github-hosted", "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": release.REPOSITORY}
        event = {"repository": {"full_name": release.REPOSITORY, "private": False, "visibility": "public"}}
        release.validate_public_context(environment, event)
        for key, value in (("RUNNER_OS", "Windows"), ("RUNNER_ENVIRONMENT", "self-hosted"), ("GITHUB_EVENT_NAME", "push"), ("GITHUB_REPOSITORY", "simcalc/npsh")):
            with self.subTest(key=key), self.assertRaises(release.ReleaseError):
                release.validate_public_context(dict(environment, **{key: value}), event)
        for repository in ({"private": True, "full_name": release.REPOSITORY}, {"private": False, "full_name": "simcalc/npsh"}):
            with self.assertRaises(release.ReleaseError):
                release.validate_public_context(environment, {"repository": repository})

    def test_https_downloader_keeps_pin_rejects_downgrade_and_never_authenticates(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            target = Path(directory) / "software.deb"
            raw = b"TRANSPORT ONLY"
            target.write_bytes(raw)
            with patch.object(release, "command", return_value=b"") as command:
                release.download(release.PACKAGE_URL, target, expected_sha=sha256(raw).hexdigest(), expected_bytes=len(raw))
            argv = command.call_args.args[0]
            self.assertEqual(argv[:2], ["curl", "--disable"])
            self.assertEqual(argv[argv.index("--proto") + 1], "=https")
            self.assertEqual(argv[argv.index("--proto-redir") + 1], "=https")
            self.assertIn("--tlsv1.2", argv)
            self.assertNotIn("--insecure", argv)
            self.assertNotIn("--header", argv)
            with patch.object(release, "command", return_value=b""), self.assertRaises(release.ReleaseError):
                release.download(release.PACKAGE_URL, target, expected_sha="0" * 64)
            with self.assertRaises(release.ReleaseError):
                release.download("http://example.invalid/software.deb", target)

    def test_control_identity_has_exact_package_version_architecture_and_size(self):
        control = "Package: openfoam12\nVersion: 20250206\nArchitecture: amd64\nInstalled-Size: 555188\nDepends: compiler, runtime\n"
        self.assertEqual(release.inspect_control(control)["Version"], "20250206")
        for old, new in (("20250206", "20240709"), ("amd64", "arm64"), ("555188", "1"), ("openfoam12", "other")):
            with self.assertRaises(release.ReleaseError):
                release.inspect_control(control.replace(old, new))

    def test_source_archive_is_reproducible_and_preserves_all_nonplatform_bytes(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            tree = Path(directory) / "openfoam12"
            synthetic_tree(tree)
            inventory = release.inventory_tree(tree)
            first, second = Path(directory) / "first.gz", Path(directory) / "second.gz"
            release.source_archive(tree, inventory, first)
            release.source_archive(tree, inventory, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(fileobj=BytesIO(first.read_bytes()), mode="r:gz") as archive:
                names = archive.getnames()
                for item in inventory["members"]:
                    if item["type"] == "file" and not item["path"].startswith("platforms/"):
                        self.assertEqual(archive.extractfile("openfoam12/" + item["path"]).read(), (tree / item["path"]).read_bytes())
                self.assertFalse(any(name.startswith("openfoam12/platforms") for name in names))
                self.assertIn("openfoam12/thirdparty/example.C", names)
                self.assertIn("openfoam12/Allwmake", names)
                self.assertIn("openfoam12/.build", names)
            self.assertTrue(inventory["correspondingSourceIncluded"])
            self.assertGreater(inventory["areas"]["thirdparty"]["regularFiles"], 0)

    def test_missing_source_build_runtime_or_license_refuses_complete_claim(self):
        for missing in ("Allwmake", "wmake/wmake", "thirdparty/example.C", "src/OpenFOAM/example.C", "platforms/linux64GccDPInt32Opt/bin/foamRun", "COPYING"):
            with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
                tree = Path(directory) / "openfoam12"
                synthetic_tree(tree)
                (tree / missing).unlink()
                with self.subTest(missing=missing), self.assertRaises(release.ReleaseError):
                    release.inventory_tree(tree)

    def test_draft_lookup_is_idempotent_and_public_asset_verification_is_anonymous(self):
        draft = {"tag_name": release.TAG, "draft": True, "id": 1, "assets": []}
        with patch.object(release, "gh_json", side_effect=[None, [draft]]):
            self.assertEqual(release.find_release(), draft)
        with patch.object(release, "gh_json", side_effect=[None, [draft, draft]]), self.assertRaises(release.ReleaseError):
            release.find_release()
        with patch.object(release, "gh_json", side_effect=[None, [], None, [draft]]) as api, patch.object(release.time, "sleep") as delay:
            self.assertEqual(release.find_release(), draft)
            self.assertEqual(api.call_count, 4)
            delay.assert_called_once_with(2)
            self.assertTrue(all("Cache-Control: no-cache" in call.args[0] for call in api.call_args_list))
        with patch.object(release, "gh_json", side_effect=[None, draft]), patch.object(release.time, "sleep") as delay:
            self.assertEqual(release.read_release(1), draft)
            delay.assert_called_once_with(2)
        with patch.object(release, "verify_prepared", return_value={}), patch.dict(os.environ, {"GH_TOKEN": "TRANSPORT_ONLY"}), patch.object(release, "gh_json", return_value=None) as api, patch.object(release.time, "sleep"), patch.object(release, "create_draft") as create, patch.object(release, "find_release") as find, self.assertRaisesRegex(release.ReleaseError, "SOFTWARE_RELEASE_KNOWN_ID_UNAVAILABLE"):
            release.publish(ROOT, resume_release_id=408858191)
        self.assertEqual(api.call_count, 3)
        create.assert_not_called()
        find.assert_not_called()
        for identifier in (0, -1, True, "1", 2**63):
            with self.subTest(identifier=identifier), patch.object(release, "gh_json") as api, self.assertRaises(release.ReleaseError):
                release.read_release(identifier)
            api.assert_not_called()
        with patch.object(release, "gh_json", return_value=dict(draft, tag_name="different")), self.assertRaises(release.ReleaseError):
            release.read_release(1)
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as directory:
            output = Path(directory)
            (output / "RELEASE-NOTES.md").write_text("DECLARED TRANSPORT CONTRACT ONLY", encoding="utf-8")
            def post_response(argv):
                self.assertEqual(argv[:3], ["--method", "POST", f"repos/{release.REPOSITORY}/releases"])
                request = json.loads(Path(argv[argv.index("--input") + 1]).read_text(encoding="utf-8"))
                self.assertEqual(request["target_commitish"], "a" * 40)
                self.assertEqual(request["tag_name"], release.TAG)
                self.assertIs(request["draft"], True)
                return draft
            with patch.object(release, "gh_json", side_effect=post_response) as api, patch.object(release, "find_release") as find, patch.object(release, "command") as command:
                self.assertEqual(release.create_draft(output, "a" * 40), draft)
                self.assertEqual(api.call_count, 1)
            find.assert_not_called()
            command.assert_not_called()
            local, target = Path(directory) / release.PACKAGE_NAME, Path(directory) / "verified.deb"
            local.write_bytes(b"TRANSPORT ONLY")
            with patch.object(release, "download") as download:
                release.verify_asset_bytes({"size": local.stat().st_size, "state": "uploaded"}, local, target)
            self.assertEqual(download.call_args.args[0], f"https://github.com/{release.REPOSITORY}/releases/download/{release.TAG}/{release.PACKAGE_NAME}")
            self.assertEqual(download.call_args.kwargs["expected_sha"], release.file_sha256(local))
            with self.assertRaises(release.ReleaseError):
                release.verify_asset_bytes({"size": 0, "state": "uploaded"}, local, target)
        workflow = (ROOT / ".github/workflows/openfoam-software-release.yml").read_text(encoding="utf-8")
        self.assertIn("existing_release_id:", workflow)
        self.assertIn("RESUME_RELEASE_ID: ${{ inputs.existing_release_id }}", workflow)
        self.assertIn('args+=(--resume-release-id "$RESUME_RELEASE_ID")', workflow)


if __name__ == "__main__":
    unittest.main()
