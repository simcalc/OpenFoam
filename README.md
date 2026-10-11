# OpenFOAM student benchmarks

Four fixed educational OpenFOAM Foundation 12 profiles cover stationary transient cavity, cavitating bullet, sliding-mesh impeller and a declared MRF impeller derivative. This repository is an exported educational runner. The complete application, Lab APIs, Pump planner, databases, student inputs and snapshots remain in the private canonical system repository. There are no private-repository checkout credentials, no application API, no cloud database connection, no PyFluent SDK or license route here.

The full official Ubuntu software package and its corresponding source are now available in [Release `openfoam12-20250206`](https://github.com/simcalc/OpenFoam/releases/tag/openfoam12-20250206). Use the Release downloads for the software; **Code -> Download ZIP** contains the educational runner source.

## Run scope

The three CFD workflows run only by manual `workflow_dispatch` in `simcalc/OpenFoam`, while PUBLIC and `PUBLIC_EDUCATIONAL_RUNS_ENABLED=true`. The separate software-release workflow requires `PUBLIC_SOFTWARE_RELEASE_ENABLED=true` and never executes a CFD solve. All four workflows use the standard `ubuntu-24.04` label, never larger/self-hosted runners. GitHub standard public Actions compute is free: https://docs.github.com/en/billing/concepts/product-billing/github-actions . Simulation artifact storage is separate and bounded to ZIP + manifest <=25 MiB, retention seven days. No unlimited storage or billing-balance claim is made. No caches or custom runner images are created.

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

## Latest verified execution status

| Route | Actual workflow | Result and limit |
| --- | --- | --- |
| Complete software Release | [38043948163](https://github.com/simcalc/OpenFoam/actions/runs/38043948163), source commit `61f9067` | SUCCESS; all nine custom assets published, with anonymous download bytes and checksums verified. No installation or solver execution occurs in this workflow. |
| Transient cavity | [38045996897](https://github.com/simcalc/OpenFoam/actions/runs/38045996897), source commit `e316ad7837b2a4794aa5faceca24e9ae072b014c` | SUCCESS for the bounded educational workflow. This does not establish real-pump or NPSHr acceptance. |
| Cavitation bullet | [38045993669](https://github.com/simcalc/OpenFoam/actions/runs/38045993669), same source commit | SUCCESS; strict mesh checks passed for 11,089 cells, and 35 physical steps reached 1e-5 s. Native-artifact verification passed with no diagnostics. |
| Impeller sliding mesh | [38049164206](https://github.com/simcalc/OpenFoam/actions/runs/38049164206), source commit `8af9a8c9282997c19f79a2b229ccd971f174b565` | SUCCESS; 9,371 cells, 10 physical steps of 0.001 s to 0.01 s. Native topology and paired saved points verify 3.6 degrees of rotor motion. |
| Impeller MRF | [38049166737](https://github.com/simcalc/OpenFoam/actions/runs/38049166737), same source commit | SUCCESS; the same initial geometry, 50 steady iterations. Physical time, time step and rotation history do not apply. |

The Cavitation native fields `U`, `p_rgh` and `alpha.water` are finite with explicit SI dimensions; paired checkpoints at 5e-6 s and 1e-5 s bind the exported state to the input, mesh and settings. The auxiliary `Phi` mapping preserves original source bytes without a filename collision. Canonical native verification returned `valid=true` and `diagnostics=[]`.

All four fixed educational profiles have successful execution evidence. Acceptance is limited to the documented execution and native-integrity checks; the benchmarks do not establish quantitative Pump results, NPSHr/NPSH3, developed cavitation, mesh or temporal convergence, conservation qualification, or experimental validation.

The impeller uses the unchanged official package-derived 30-degree radial teaching passage. Python verifies 7,344 rotating and 4,114 stationary native points against the actual rotor cell topology; the maximum rigid-rotation coordinate residual is 6.70e-14 m. Pressure `p` is kinematic pressure in m²/s², not Pa. Density, vapour pressure and an absolute-pressure datum are not supplied by this single-phase tutorial. This does not provide a full volute or a manufacturer Pump model. Native impeller restart is not qualified.

Authenticated GitHub run/job/artifact reads and full outer-artifact digest verification establish the actual executions separately from the offline native verifier. The offline verifier checks file integrity and run association; its `actualCfdRunVerified`, `provenanceAuthenticityVerified` and `engineeringValidated` flags remain false because it does not reauthenticate GitHub or validate an engineering study. Changing this README or verifier does not rerun the recorded solver jobs; their immutable source commit remains the one above.

## Source and licensing

The 29 official tutorial files are unchanged and pinned in `tools/cfd_worker/fixtures/openfoam12/provenance.json` at OpenFOAM-12 source commit `0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd`. Retained COPYING and project-authored provenance.json make 31 trace resources. Their GPL-3.0-or-later notice and COPYING are retained. See https://github.com/OpenFOAM/OpenFOAM-12 and https://openfoam.org/licence/ . Orchestration source is a reviewed subset/projection of the private canonical system; release-manifest.json records relative source/export hashes without publishing private Git history or runtime data. The export is not a live mirror. Project-authored orchestration licensing is not silently reassigned by this export.

The export manifest records source preparation and integrity; live Release availability is established by the separate successful software-release workflow above. Actual local OpenFOAM acceptance is not claimed. Keep the exact source commit, run IDs and native output evidence with each result.

## Measured disposable Ubuntu 24.04 installation footprint

A real preceding private development-run installer downloaded the exact checksum-bound OpenFOAM 12 package (version 20250206): 112,877,506 bytes (112.9 MB decimal / 107.65 MiB). Apt reported 68.5 MB dependency downloads, 181 MB total archives and 852 MB additional installed disk. These are observed installer values from that preconfigured runner, not universal requirements; preinstalled dependencies and devices can change them. No actual solver success is inferred from installation success. Runtime binaries are ephemeral on GitHub and are neither committed here nor installed on the student laptop.

## Software package and source ZIP are different downloads

GitHub **Code -> Download ZIP** contains the educational Python runner, original tutorial inputs, tests and documentation: about 1 MB extracted, not the installed OpenFOAM software. The verified [Release `openfoam12-20250206`](https://github.com/simcalc/OpenFoam/releases/tag/openfoam12-20250206) provides these direct downloads:

| Download | Verified bytes | Contents |
| --- | ---: | --- |
| [Complete official `.deb`](https://github.com/simcalc/OpenFoam/releases/download/openfoam12-20250206/openfoam12_20250206_amd64.deb) | 112,877,506 | OpenFOAM Foundation 12, version 20250206, for Ubuntu 24.04 amd64; unchanged official package. |
| [Corresponding source archive](https://github.com/simcalc/OpenFoam/releases/download/openfoam12-20250206/openfoam12_20250206-corresponding-source.tar.gz) | 71,228,570 | Exact source/build/documentation tree from that verified package, with generated platform binaries excluded. |

The official `.deb` SHA-256 is:

```text
b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1
```

The fixed manual workflow verified the original package bytes and identity, then verified the published assets through anonymous downloads. See [SHA256SUMS](https://github.com/simcalc/OpenFoam/releases/download/openfoam12-20250206/SHA256SUMS) for the complete asset checksums.

The nine custom assets are the `.deb`, `openfoam12_20250206-corresponding-source.tar.gz`, `COPYING`, `INSTALL.md`, `INVENTORY.json`, `INVENTORY.md`, `RELEASE.json`, `SHA256SUMS` and `RELEASE-NOTES.md`. The inventory records every member of the OpenFOAM tree extracted from the checksum-verified `.deb`, with package identity/control metadata recorded separately. Read the [Ubuntu installation instructions](docs/software-release-INSTALL.md) for package verification and dependencies. This source-preparation document does not claim quantitative Pump/NPSHr/NPSH3 results or convergence qualification merely because the software assets and educational benchmark outputs are available.

The `.deb` contains OpenFOAM software; it is not a Windows installer and does not include every Ubuntu dependency. This pilot installs it only on the disposable GitHub Ubuntu VM, where measured total archives were 181 MB including 68.5 MB dependencies, and additional installed software occupied 852 MB. No laptop installation is performed. The software Release asset is separate from bounded 25 MiB simulation-result artifacts. The private `npsh` repository remains the canonical source of the full application and export logic; this public repository exposes only the reviewed educational package and official software distribution asset.

## Fixed educational impeller profiles

The exact OpenFOAM Foundation 12 package-derived radial impeller tutorial is retained with its original geometry and source checksums. `impeller-transient-v1` uses actual sliding mesh/NCC at 60 rpm for 10 physical steps of 0.001 s (0.01 s). `impeller-mrf-v1` is a declared derived single-phase MRF model with fixed geometry/NCC and 50 steady iterations; iterations are not seconds. MRF numerical dictionaries come from the separately pinned official mixerVessel2DMRF reference. Every derivative is recorded by the authoritative adapter.

The successful run IDs above establish bounded solver execution and reviewed native integrity. This 30-degree radial passage example is not an exact manufactured Pump, cavitating Pump, full volute model, measured Q-H curve, or NPSHr/NPSH3 prediction. Mesh independence, temporal convergence, conservation qualification and native impeller restart remain unqualified. The manual impeller workflow accepts only these two profiles, no user input/geometry files and no restart request. It retains the existing 2 GiB/one-CPU/32-task scope and bounded native output.

## Controlled cavity refinement

The manual cavity workflow preserves `ras-early` (original five solves to0.05s) and `laminar-re100` (six solves to0.5s). Laminar Re100 uses the immutable official square, nu0.001m2/s, lid1m/s, meshes20/40/80, Euler steps0.001/0.0005/0.00025s, baseline linear tolerance1e-9 and fine repeat1e-11. `laminar-re100-refined` is separately named cavity-laminar-re100-discretisation-v2: exactly TWO NEW160x160 solves at0.5s/dt0.00025s, baseline1e-9 and tight1e-11. It authenticates and checksum-verifies fixed public run38109150013, recomputes the original40/80native mesh/U evidence, and forms linked40/80/160spatial sensitivity. A pinned public evidence Release preserves the exact prior archive after seven-day Actions retention. Prior mesh80temporal evidence is labeled separately and NEVER qualifies mesh160temporal sensitivity. A fine-grid linear repeat does not qualify nonlinear/coupling or every-grid iterative error; quality and GCI remain null. The proposed full six-run40/80/160archive exceeded unchanged budgets, so v2 publishes only new160native output and compact sealed prior provenance. Native fields, logs and Python equation stages preserve raw sensitivity. No physical, real Pump or NPSHr claim. All2GiB/oneCPU/32task/1200s/256MiBworkspace/2MiBlog/25MiBcompressed/64MiBrawevidence caps remain fixed.
