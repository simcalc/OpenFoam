# OpenFOAM student benchmarks

Two bounded educational OpenFOAM Foundation 12 examples: stationary transient cavity and cavitating bullet. This repository is an exported educational runner. The complete application, Lab APIs, Pump planner, databases, student inputs and snapshots remain in the private canonical system repository. There are no private-repository checkout credentials, no application API, no cloud database connection, no PyFluent SDK or license route here.

## Run scope

The two CFD workflows run only by manual `workflow_dispatch` in `simcalc/OpenFoam`, while PUBLIC and `PUBLIC_EDUCATIONAL_RUNS_ENABLED=true`. The separate software-release workflow requires `PUBLIC_SOFTWARE_RELEASE_ENABLED=true` and never executes a CFD solve. All three workflows use the standard `ubuntu-24.04` label, never larger/self-hosted runners. GitHub standard public Actions compute is free: https://docs.github.com/en/billing/concepts/product-billing/github-actions . Simulation artifact storage is separate and bounded to ZIP + manifest <=25 MiB, retention seven days. No unlimited storage or billing-balance claim is made. No caches or custom runner images are created.

Each solve uses a dedicated delegated cgroup: one CPU, aggregate RAM <=2 GiB, no swap, <=32 tasks, overall solver stages <=1200 s, job <=30 min. Disk uses a 10-ms 256-MiB watchdog (not a filesystem hard quota), logs <=2 MiB. Timeout/cancellation kills and waits for descendants. Installation happens on a disposable runner; do not use the installer on the Main/student laptop.

## Commands

`python -B tools/cfd_worker/run.py --capabilities`

Inside the documented Linux delegated resource scope with Foundation 12 active:

`python -B tools/cfd_worker/run.py --benchmark transient-cavity-v1 --restart-smoke --workspace outputs/cfd-worker --report outputs/transient-benchmark.json`

`python -B tools/cfd_worker/run.py --benchmark cavitation-bullet-v1 --workspace outputs/cfd-worker --report outputs/cavitation-benchmark.json`

No `--input`, `--reference`, `--native-case`, arbitrary command, URL, mesh-upload or private snapshot arguments are accepted. Only generated constant tutorial bindings can enter this worker.

## What the results establish

Real OpenFOAM mesh/solver execution is claimed only after actual stage exit/log/native-field/physical-time evidence is collected. Contract tests never establish a real solver run. Cavity ends at 0.05 s with an exact copied 0.05 s checkpoint restart to 0.055 s; this verifies bounded continued execution and integrity, not equivalence to an uninterrupted solution. Bullet is a short 10-microsecond liquid-vapour numerical smoke. It may finish before vapour develops. Neither example models a real pump or establishes NPSHr/NPSH3, mesh independence, temporal convergence, conservation acceptance, or experimental validation.

The bullet input revision is `fixture-v3`, with mesh profile `castellated-staircase-uniform-v3`: surface refinement level `0`, region refinement level `0`, `snap=false`, `addLayers=false`, and `maxGlobalCells=maxLocalCells=100000`. The unchanged background mesh is 15 x 15 x 50; its coarse resolution gives only five cells across the 0.01 m bullet diameter. It preserves the original pinned upstream STL and records a deliberately coarse uniform castellated staircase approximation of its boundary. This does not establish fidelity to a snapped/smooth bullet surface. All strict `checkMesh -allGeometry -allTopology` checks remain required before potential flow or physical solve; earlier rejected meshes remain rejection evidence, including the snapped revision's 917 concave cells. Revision v3 has no mesh-quality or actual-solve acceptance claim merely from preparation. Developed cavitation, mesh independence, and NPSHr/NPSH3 are not claimed. Cavity remains `fixture-v1`.

Native export includes exact initial state, mesh, settings, paired finite time fields, solver logs and checksum-bound v2 checkpoint descriptors. The generated `reference/snapshot.json` contains only the fixed OpenFOAM tutorial profile/source commit, never Main/student data. Checksums prove integrity, not source authenticity. Outputs remain Imported Evidence and cannot make Main Current.

Python educational verification reports equations in Input -> Substitution -> Result stages from native operands. This repository does not perform application engineering calculations or alter Main.

## Source and licensing

The 29 official tutorial files are unchanged and pinned in `tools/cfd_worker/fixtures/openfoam12/provenance.json` at OpenFOAM-12 source commit `0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd`. Retained COPYING and project-authored provenance.json make 31 trace resources. Their GPL-3.0-or-later notice and COPYING are retained. See https://github.com/OpenFOAM/OpenFOAM-12 and https://openfoam.org/licence/ . Orchestration source is a reviewed subset/projection of the private canonical system; release-manifest.json records relative source/export hashes without publishing private Git history or runtime data. The export is not a live mirror. Project-authored orchestration licensing is not silently reassigned by this export.

This export records preparation state, not live publication or solver acceptance. Actual local OpenFOAM acceptance is not claimed. Verify successful public workflow/native output evidence separately, retaining the exact source commit and run IDs with results.

## Measured disposable Ubuntu 24.04 installation footprint

A real preceding private development-run installer downloaded the exact checksum-bound OpenFOAM 12 package (version 20250206): 112,877,506 bytes (112.9 MB decimal / 107.65 MiB). Apt reported 68.5 MB dependency downloads, 181 MB total archives and 852 MB additional installed disk. These are observed installer values from that preconfigured runner, not universal requirements; preinstalled dependencies and devices can change them. No actual solver success is inferred from installation success. Runtime binaries are ephemeral on GitHub and are neither committed here nor installed on the student laptop.

## Software package and source ZIP are different downloads

GitHub **Code -> Download ZIP** contains the educational Python runner, original tutorial inputs, tests and documentation: about 1 MB extracted, not the installed OpenFOAM software. The full official Ubuntu 24.04 x86-64 software package is `openfoam12_20250206_amd64.deb`, exactly **112,877,506 bytes**, with SHA-256 `b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1`.

The planned Release tag is [`openfoam12-20250206`](https://github.com/simcalc/OpenFoam/releases/tag/openfoam12-20250206), with the full `.deb`, checksum, package inventory, upstream source/license references and Ubuntu installation instructions as separate assets. The fixed manual software-release workflow first verifies the downloaded bytes and package identity. Release publication and subsequent remote-asset verification are separate evidence; this source-preparation document does not claim that the tag or assets are already available. Inspect the [Releases page](https://github.com/simcalc/OpenFoam/releases) and successful release-workflow evidence before treating the planned link as a verified download.

The fixed asset set is the `.deb`, `openfoam12_20250206-corresponding-source.tar.gz`, `COPYING`, `INSTALL.md`, `INVENTORY.json`, `INVENTORY.md`, `RELEASE.json`, `SHA256SUMS` and `RELEASE-NOTES.md`. The inventory records every member of the OpenFOAM tree extracted from the checksum-verified `.deb`, with package identity/control metadata recorded separately; the corresponding-source archive retains the source/build/documentation areas and excludes generated platform binaries. Missing required source or license areas prevent publication. Read the [prepared Ubuntu instructions](docs/software-release-INSTALL.md) for the package/dependency distinction. The workflow does not install this package or start a solver.

The `.deb` contains OpenFOAM software; it is not a Windows installer and does not include every Ubuntu dependency. This pilot installs it only on the disposable GitHub Ubuntu VM, where measured total archives were 181 MB including 68.5 MB dependencies, and additional installed software occupied 852 MB. No laptop installation is performed. The software Release asset is separate from bounded 25 MiB simulation-result artifacts. The private `npsh` repository remains the canonical source of the full application and export logic; this public repository exposes only the reviewed educational package and official software distribution asset.
