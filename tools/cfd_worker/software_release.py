"""Fixed official OpenFOAM software release; independent of Main and solvers."""
from __future__ import annotations

import argparse
from email.parser import Parser
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "simcalc/OpenFoam"
TAG = "openfoam12-20250206"
PACKAGE_NAME = "openfoam12_20250206_amd64.deb"
PACKAGE_SHA256 = "b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1"
PACKAGE_BYTES = 112877506
PACKAGE_URL = "https://downloads.sourceforge.net/project/foam/foam/ubuntu/dists/noble/main/binary-amd64/" + PACKAGE_NAME
SOURCE_NAME = "openfoam12_20250206-corresponding-source.tar.gz"
ASSETS = (PACKAGE_NAME, SOURCE_NAME, "COPYING", "INSTALL.md", "INVENTORY.json", "INVENTORY.md", "RELEASE.json", "SHA256SUMS", "RELEASE-NOTES.md")
MAXIMUM_OUTPUT_BYTES = 1024**3


class ReleaseError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    result = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            result.update(block)
    return result.hexdigest()


def write_json(path: Path, value: dict):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def validate_public_context(environment=None, event=None):
    environment = os.environ if environment is None else environment
    if (environment.get("GITHUB_ACTIONS") != "true" or environment.get("RUNNER_OS") != "Linux" or
        environment.get("RUNNER_ENVIRONMENT") != "github-hosted" or environment.get("GITHUB_EVENT_NAME") != "workflow_dispatch" or
        environment.get("GITHUB_REPOSITORY") != REPOSITORY):
        raise ReleaseError("SOFTWARE_PUBLIC_STANDARD_RUNNER_REQUIRED")
    if event is None:
        event = json.loads(Path(environment["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    repository = event.get("repository", {}) if isinstance(event, dict) else {}
    if repository.get("private") is not False or repository.get("full_name") != REPOSITORY or repository.get("visibility") not in (None, "public"):
        raise ReleaseError("SOFTWARE_PUBLIC_REPOSITORY_REQUIRED")


def command(argv, *, timeout=120, stdout=None):
    result = subprocess.run(argv, stdout=stdout if stdout is not None else subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, check=False)
    if result.returncode:
        # Raw command errors can contain redirect signatures or credentials.
        raise ReleaseError("SOFTWARE_COMMAND_FAILED:" + Path(argv[0]).name)
    return result.stdout if stdout is None else None


def download(url: str, target: Path, *, expected_sha=None, expected_bytes=None):
    if not url.startswith("https://"):
        raise ReleaseError("SOFTWARE_HTTPS_REQUIRED")
    command(["curl", "--disable", "--fail", "--silent", "--show-error", "--location", "--proto", "=https", "--proto-redir", "=https",
             "--tlsv1.2", "--retry", "3", "--max-time", "300", url, "--output", str(target)], timeout=330)
    if (expected_bytes is not None and target.stat().st_size != expected_bytes) or (expected_sha is not None and file_sha256(target) != expected_sha):
        raise ReleaseError("SOFTWARE_DOWNLOAD_INTEGRITY_FAILED")


def inspect_control(raw: str) -> dict:
    control = Parser().parsestr(raw)
    result = {key: control.get(key, "") for key in ("Package", "Version", "Architecture", "Installed-Size", "Depends", "Recommends", "Homepage")}
    if (result["Package"], result["Version"], result["Architecture"], result["Installed-Size"]) != ("openfoam12", "20250206", "amd64", "555188"):
        raise ReleaseError("SOFTWARE_PACKAGE_IDENTITY_MISMATCH")
    return result


def inventory_tree(tree: Path) -> dict:
    """Inventory exact regular bytes and link targets; never follow source links."""
    required_files = ("COPYING", "Allwmake", "wmake/wmake", "etc/bashrc",
                      "platforms/linux64GccDPInt32Opt/bin/foamRun", "platforms/linux64GccDPInt32Opt/bin/blockMesh",
                      "platforms/linux64GccDPInt32Opt/bin/checkMesh", "platforms/linux64GccDPInt32Opt/bin/snappyHexMesh",
                      "platforms/linux64GccDPInt32Opt/bin/potentialFoam", "platforms/linux64GccDPInt32Opt/lib/libOpenFOAM.so",
                      "platforms/linux64GccDPInt32Opt/lib/libblockMesh.so")
    required_directories = ("src/OpenFOAM", "applications/modules", "applications/solvers", "applications/utilities", "thirdparty", "wmake", "etc", "tutorials")
    if any(not (tree / name).is_file() for name in required_files) or any(not (tree / name).is_dir() for name in required_directories):
        raise ReleaseError("SOFTWARE_COMPLETE_SOURCE_OR_RUNTIME_REQUIRED")
    license_text = (tree / "COPYING").read_text(encoding="utf-8")
    if "GNU GENERAL PUBLIC LICENSE" not in license_text or "Version 3" not in license_text:
        raise ReleaseError("SOFTWARE_GPL_COPYING_REQUIRED")
    members = []
    total = 0
    for path in sorted(tree.rglob("*"), key=lambda item: item.relative_to(tree).as_posix()):
        relative = path.relative_to(tree).as_posix()
        if path.is_symlink():
            if not path.resolve().is_relative_to(tree.resolve()):
                raise ReleaseError("SOFTWARE_EXTERNAL_SOURCE_LINK_PROHIBITED")
            members.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
        elif path.is_file():
            size = path.stat().st_size
            total += size
            if total > MAXIMUM_OUTPUT_BYTES:
                raise ReleaseError("SOFTWARE_INVENTORY_BUDGET_EXCEEDED")
            members.append({"path": relative, "type": "file", "bytes": size, "sha256": file_sha256(path)})
        elif path.is_dir():
            members.append({"path": relative, "type": "directory"})
        else:
            raise ReleaseError("SOFTWARE_UNSUPPORTED_FILE_TYPE")
    source_files = [item for item in members if item["type"] == "file" and item["path"].startswith(("src/", "applications/")) and item["path"].endswith((".C", ".H"))]
    if not source_files:
        raise ReleaseError("SOFTWARE_CORRESPONDING_SOURCE_REQUIRED")
    areas = {}
    for area in ("src", "applications/modules", "applications/solvers", "applications/utilities", "thirdparty", "wmake", "etc", "bin", "tutorials", "doc", "platforms/linux64GccDPInt32Opt/bin", "platforms/linux64GccDPInt32Opt/lib"):
        files = [item for item in members if item["type"] == "file" and item["path"].startswith(area + "/")]
        areas[area] = {"regularFiles": len(files), "bytes": sum(item["bytes"] for item in files)}
    if any(areas[area]["regularFiles"] == 0 for area in ("src", "applications/modules", "applications/solvers", "applications/utilities", "thirdparty", "wmake", "etc", "tutorials")):
        raise ReleaseError("SOFTWARE_COMPLETE_SOURCE_AREA_REQUIRED")
    return {"schemaVersion": "untirta.openfoam-software-inventory.v1", "members": members, "areas": areas,
            "regularBytes": total, "sourceCodeFiles": len(source_files), "correspondingSourceIncluded": True,
            "sourceBasis": "Exact source/build tree included in checksum-pinned official binary package; no tutorial subset substituted."}


def source_archive(tree: Path, inventory: dict, target: Path):
    """Deterministic archive of every non-platform member, with original bytes."""
    with target.open("wb") as output, gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0, compresslevel=6) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
        root_info = tarfile.TarInfo("openfoam12/")
        root_info.type, root_info.mode = tarfile.DIRTYPE, 0o755
        archive.addfile(root_info)
        for item in inventory["members"]:
            relative = item["path"]
            if relative == "platforms" or relative.startswith("platforms/"):
                continue
            path = tree / relative
            info = tarfile.TarInfo("openfoam12/" + relative)
            info.mode = path.lstat().st_mode & 0o777
            if item["type"] == "directory":
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            elif item["type"] == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, item["target"]
                archive.addfile(info)
            else:
                if file_sha256(path) != item["sha256"]:
                    raise ReleaseError("SOFTWARE_SOURCE_CHANGED_DURING_ARCHIVE")
                info.size = item["bytes"]
                with path.open("rb") as stream:
                    archive.addfile(info, stream)


def template_path():
    for path in (ROOT / "docs/software-release-INSTALL.md", ROOT / "deployment/cfd-public/software-release-INSTALL.md"):
        if path.is_file():
            return path
    raise ReleaseError("SOFTWARE_INSTALL_TEMPLATE_REQUIRED")


def prepare(output: Path):
    if output.exists() and any(output.iterdir()):
        raise ReleaseError("SOFTWARE_NEW_OUTPUT_DIRECTORY_REQUIRED")
    output.mkdir(parents=True, exist_ok=True)
    package = output / PACKAGE_NAME
    download(PACKAGE_URL, package, expected_sha=PACKAGE_SHA256, expected_bytes=PACKAGE_BYTES)
    control = inspect_control(command(["dpkg-deb", "--field", str(package)]).decode("utf-8"))
    with tempfile.TemporaryDirectory(prefix="verified-official-tree-", dir=output) as temporary:
        extracted = Path(temporary)
        command(["dpkg-deb", "--extract", str(package), str(extracted)], timeout=180)
        tree = extracted / "opt/openfoam12"
        inventory = inventory_tree(tree)
        source_archive(tree, inventory, output / SOURCE_NAME)
        (output / "COPYING").write_bytes((tree / "COPYING").read_bytes())
    inventory.update({"package": {"name": PACKAGE_NAME, "sha256": PACKAGE_SHA256, "bytes": PACKAGE_BYTES, "metadata": control,
        "url": PACKAGE_URL, "officialChecksumIndex": "https://dl.openfoam.org/ubuntu/dists/noble/main/binary-amd64/Packages"},
        "sourceArchive": {"name": SOURCE_NAME, "sha256": file_sha256(output / SOURCE_NAME), "bytes": (output / SOURCE_NAME).stat().st_size,
            "exclusion": "Generated platforms/ binaries only; all other OpenFOAM tree members retained."},
        "binaryRepackaged": False, "solverExecuted": False, "engineeringValidated": False})
    write_json(output / "INVENTORY.json", inventory)
    rows = ["# Official OpenFOAM package inventory", "", "Package: openfoam12 / 20250206 / amd64; original binary package bytes unchanged.", "",
        "Installed-Size: 555188 KiB for OpenFOAM itself; APT dependencies add separate machine-dependent space.", "",
        "| Area | Regular files | Bytes |", "| --- | ---: | ---: |"]
    rows += [f"| {area} | {counts['regularFiles']} | {counts['bytes']} |" for area, counts in inventory["areas"].items()]
    rows += ["", f"Source-code files inventoried: {inventory['sourceCodeFiles']}.", "", "INVENTORY.json lists every member, regular-file checksum and symbolic-link target.", "The source archive is taken from this exact verified package, with generated platforms/ binaries excluded.", "No local installation, source rebuild, solver run or CFD engineering acceptance is claimed."]
    (output / "INVENTORY.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (output / "INSTALL.md").write_bytes(template_path().read_bytes())
    notes = "# OpenFOAM Foundation 12 official package (20250206)\n\nComplete unchanged official Ubuntu 24.04 amd64 .deb, matching source/build archive, GPL COPYING, inventory and installation instructions. SHA256SUMS verifies each asset.\n\nSource and build files are included in the original package and separately archived from its exact bytes. This is software availability; it does not establish CFD cavitation, exact Pump or NPSHr/NPSH3 validation.\n"
    (output / "RELEASE-NOTES.md").write_text(notes, encoding="utf-8")
    write_json(output / "RELEASE.json", {"schemaVersion": "untirta.openfoam-software-release.v1", "repository": REPOSITORY, "tag": TAG,
        "assets": list(ASSETS), "packageSha256": PACKAGE_SHA256, "packageBytes": PACKAGE_BYTES,
        "correspondingSourceIncluded": True, "sourceArchiveSha256": inventory["sourceArchive"]["sha256"],
        "runtimeInstallationPerformed": False, "solverExecuted": False, "engineeringValidated": False})
    lines = [file_sha256(output / name) + "  " + name for name in ASSETS if name != "SHA256SUMS"]
    (output / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if sum((output / name).stat().st_size for name in ASSETS) > MAXIMUM_OUTPUT_BYTES:
        raise ReleaseError("SOFTWARE_RELEASE_BUDGET_EXCEEDED")
    return {"status": "SoftwareAssetsPrepared", "tag": TAG, "assets": list(ASSETS), "packageSha256": PACKAGE_SHA256, "correspondingSourceIncluded": True}


def gh_json(argv, *, allow_missing=False):
    result = subprocess.run(["gh", "api", *argv], capture_output=True, timeout=120, check=False)
    if result.returncode:
        if allow_missing and b"HTTP 404" in result.stderr:
            return None
        raise ReleaseError("SOFTWARE_RELEASE_API_UNAVAILABLE")
    if len(result.stdout) > 1024**2:
        raise ReleaseError("SOFTWARE_RELEASE_API_BUDGET_EXCEEDED")
    return json.loads(result.stdout)


def verify_prepared(output: Path):
    expected = {}
    for line in (output / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        matched = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not matched or matched[2] not in ASSETS or matched[2] in expected:
            raise ReleaseError("SOFTWARE_ASSET_MANIFEST_INVALID")
        expected[matched[2]] = matched[1]
    if set(expected) != set(ASSETS) - {"SHA256SUMS"}:
        raise ReleaseError("SOFTWARE_ASSET_MANIFEST_INCOMPLETE")
    for name, checksum in expected.items():
        path = output / name
        if not path.is_file() or path.is_symlink() or file_sha256(path) != checksum:
            raise ReleaseError("SOFTWARE_ASSET_INTEGRITY_FAILED")
    if expected[PACKAGE_NAME] != PACKAGE_SHA256 or (output / PACKAGE_NAME).stat().st_size != PACKAGE_BYTES:
        raise ReleaseError("SOFTWARE_OFFICIAL_PACKAGE_REQUIRED")
    manifest = json.loads((output / "RELEASE.json").read_text())
    if manifest.get("repository") != REPOSITORY or manifest.get("tag") != TAG or manifest.get("correspondingSourceIncluded") is not True:
        raise ReleaseError("SOFTWARE_RELEASE_BINDING_INVALID")
    expected["SHA256SUMS"] = file_sha256(output / "SHA256SUMS")
    return expected


def validate_release(release):
    if not isinstance(release, dict) or release.get("tag_name") != TAG:
        raise ReleaseError("SOFTWARE_RELEASE_TAG_MISMATCH")
    if type(release.get("id")) is not int or release["id"] <= 0 or type(release.get("draft")) is not bool or not isinstance(release.get("assets"), list):
        raise ReleaseError("SOFTWARE_RELEASE_RESPONSE_INVALID")
    return release


def read_release(release_id):
    if type(release_id) is not int or not 0 < release_id < 2**63:
        raise ReleaseError("SOFTWARE_RELEASE_ID_INVALID")
    for attempt in range(3):
        release = gh_json([f"repos/{REPOSITORY}/releases/{release_id}", "--header", "Cache-Control: no-cache"], allow_missing=True)
        if release is not None:
            release = validate_release(release)
            if release["id"] != release_id:
                raise ReleaseError("SOFTWARE_RELEASE_ID_MISMATCH")
            return release
        if attempt < 2:
            time.sleep(2)
    # A known release must never be replaced by another draft after a 404.
    raise ReleaseError("SOFTWARE_RELEASE_KNOWN_ID_UNAVAILABLE")


def find_release():
    for attempt in range(3):
        release = gh_json([f"repos/{REPOSITORY}/releases/tags/{TAG}", "--header", "Cache-Control: no-cache"], allow_missing=True)
        if release is not None:
            return validate_release(release)
        # Authenticated release listing can expose drafts before tag lookup.
        candidates = gh_json([f"repos/{REPOSITORY}/releases?per_page=100", "--header", "Cache-Control: no-cache"])
        if not isinstance(candidates, list):
            raise ReleaseError("SOFTWARE_RELEASE_LIST_INVALID")
        matched = [item for item in candidates if item.get("tag_name") == TAG]
        if len(matched) > 1:
            raise ReleaseError("SOFTWARE_RELEASE_TAG_CONFLICT")
        if matched:
            return validate_release(matched[0])
        if attempt < 2:
            time.sleep(2)
    return None


def create_draft(output, commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ReleaseError("SOFTWARE_RELEASE_COMMIT_REQUIRED")
    # Use the structured POST response itself. A just-created draft may not
    # yet be visible through release-by-tag or release-list endpoints.
    with tempfile.TemporaryDirectory(prefix="release-request-", dir=output) as temporary:
        request = Path(temporary) / "release.json"
        write_json(request, {"tag_name": TAG, "target_commitish": commit, "draft": True, "prerelease": False,
            "name": "OpenFOAM Foundation 12 — complete official package 20250206",
            "body": (output / "RELEASE-NOTES.md").read_text(encoding="utf-8")})
        release = validate_release(gh_json(["--method", "POST", f"repos/{REPOSITORY}/releases", "--input", str(request)]))
    if release["draft"] is not True:
        raise ReleaseError("SOFTWARE_RELEASE_DRAFT_REQUIRED")
    return release


def verify_asset_bytes(asset: dict, local: Path, target: Path, *, authenticated=False):
    if asset.get("size") != local.stat().st_size or asset.get("state") != "uploaded":
        raise ReleaseError("SOFTWARE_RELEASE_ASSET_SIZE_MISMATCH")
    if authenticated:
        with target.open("wb") as stream:
            command(["gh", "api", f"repos/{REPOSITORY}/releases/assets/{int(asset['id'])}", "--header", "Accept: application/octet-stream"], stdout=stream, timeout=300)
        if file_sha256(target) != file_sha256(local):
            raise ReleaseError("SOFTWARE_DRAFT_ASSET_INTEGRITY_FAILED")
    else:
        url = f"https://github.com/{REPOSITORY}/releases/download/{TAG}/{local.name}"
        download(url, target, expected_sha=file_sha256(local), expected_bytes=local.stat().st_size)


def publish(output: Path, *, resume_release_id=None):
    expected = verify_prepared(output)
    if not os.environ.get("GH_TOKEN"):
        raise ReleaseError("SOFTWARE_EPHEMERAL_RELEASE_TOKEN_REQUIRED")
    release = read_release(resume_release_id) if resume_release_id is not None else find_release()
    if release is None:
        release = create_draft(output, os.environ.get("GITHUB_SHA", ""))
    remote_assets = {item["name"]: item for item in release.get("assets", [])}
    if len(remote_assets) != len(release.get("assets", [])) or set(remote_assets) - set(ASSETS):
        raise ReleaseError("SOFTWARE_IMMUTABLE_RELEASE_ASSET_CONFLICT")
    with tempfile.TemporaryDirectory(prefix="release-byte-verification-", dir=output) as temporary:
        directory = Path(temporary)
        for name in ASSETS:
            if name not in remote_assets:
                if release.get("draft") is not True:
                    raise ReleaseError("SOFTWARE_PUBLISHED_RELEASE_INCOMPLETE")
                command(["gh", "release", "upload", TAG, str(output / name), "--repo", REPOSITORY], timeout=300)
        release = read_release(release["id"])
        remote_assets = {item["name"]: item for item in release.get("assets", [])}
        if set(remote_assets) != set(ASSETS):
            raise ReleaseError("SOFTWARE_RELEASE_ASSET_MANIFEST_INCOMPLETE")
        for name in ASSETS:
            verify_asset_bytes(remote_assets[name], output / name, directory / name, authenticated=release.get("draft") is True)
        if release.get("draft") is True:
            release = validate_release(gh_json(["--method", "PATCH", f"repos/{REPOSITORY}/releases/{int(release['id'])}", "--field", "draft=false"]))
            if release["draft"] is not False:
                raise ReleaseError("SOFTWARE_RELEASE_PUBLICATION_REQUIRED")
        # Anonymous HTTPS downloads after publication prove public availability;
        # curl has no auth header or inherited GitHub token mechanism here.
        for name in ASSETS:
            verify_asset_bytes(remote_assets[name], output / name, directory / name, authenticated=False)
    result = {"schemaVersion": "untirta.openfoam-public-release-verification.v1", "status": "PublicSoftwareReleaseVerified", "tag": TAG,
              "url": f"https://github.com/{REPOSITORY}/releases/tag/{TAG}", "anonymousAssetDownloadsVerified": True,
              "assets": [{"name": name, "sha256": expected[name], "bytes": (output / name).stat().st_size,
                          "url": f"https://github.com/{REPOSITORY}/releases/download/{TAG}/{name}"} for name in ASSETS],
              "correspondingSourceIncluded": True, "solverExecuted": False, "engineeringValidated": False}
    write_json(output / "PUBLIC-RELEASE-VERIFICATION.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "publish"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume-release-id", type=int, help="Resume an existing fixed-tag release by its observed GitHub ID; never create a replacement.")
    args = parser.parse_args(argv)
    try:
        validate_public_context()
        if args.mode == "prepare" and args.resume_release_id is not None:
            raise ReleaseError("SOFTWARE_RESUME_PUBLISH_ONLY")
        result = prepare(args.output.resolve()) if args.mode == "prepare" else publish(args.output.resolve(), resume_release_id=args.resume_release_id)
    except (ReleaseError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "SoftwareReleaseStopped", "reasonCode": str(exc) if isinstance(exc, ReleaseError) else "SOFTWARE_RELEASE_INPUT_OR_RUNTIME_REQUIRED",
                          "action": "Inspect the pinned package identity, complete source inventory and fixed public release assets; no partial release is accepted.", "publicReleaseVerified": False}))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
