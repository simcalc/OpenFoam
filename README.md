# OpenFOAM student benchmarks

Two bounded educational OpenFOAM Foundation 12 examples: stationary transient cavity and cavitating bullet. This repository is an exported educational runner. The complete application, Lab APIs, Pump planner, databases, student inputs and snapshots remain in the private canonical system repository. There are no private-repository checkout credentials, no application API, no cloud database connection, no PyFluent SDK or license route here.

## Run scope

Only manual `workflow_dispatch` in `simcalc/OpenFoam`, while PUBLIC and `PUBLIC_EDUCATIONAL_RUNS_ENABLED=true`. Both workflow jobs use the standard `ubuntu-24.04` label, never larger/self-hosted runners. GitHub standard public Actions compute is free: https://docs.github.com/en/billing/concepts/product-billing/github-actions . Artifact storage is separate and bounded to ZIP + manifest <=25 MiB, retention seven days. No unlimited storage or billing-balance claim is made. No caches or custom runner images are created.

Each solve uses a dedicated delegated cgroup: one CPU, aggregate RAM <=2 GiB, no swap, <=32 tasks, overall solver stages <=1200 s, job <=30 min. Disk uses a 10-ms 256-MiB watchdog (not a filesystem hard quota), logs <=2 MiB. Timeout/cancellation kills and waits for descendants. Installation happens on a disposable runner; do not use the installer on the Main/student laptop.

## Commands

`python -B tools/cfd_worker/run.py --capabilities`

Inside the documented Linux delegated resource scope with Foundation 12 active:

`python -B tools/cfd_worker/run.py --benchmark transient-cavity-v1 --restart-smoke --workspace outputs/cfd-worker --report outputs/transient-benchmark.json`

`python -B tools/cfd_worker/run.py --benchmark cavitation-bullet-v1 --workspace outputs/cfd-worker --report outputs/cavitation-benchmark.json`

No `--input`, `--reference`, `--native-case`, arbitrary command, URL, mesh-upload or private snapshot arguments are accepted. Only generated constant tutorial bindings can enter this worker.

## What the results establish

Real OpenFOAM mesh/solver execution is claimed only after actual stage exit/log/native-field/physical-time evidence is collected. Contract tests never establish a real solver run. Cavity ends at 0.05 s with an exact copied 0.05 s checkpoint restart to 0.055 s; this verifies bounded continued execution and integrity, not equivalence to an uninterrupted solution. Bullet is a short 10-microsecond liquid-vapour numerical smoke. It may finish before vapour develops. Neither example models a real pump or establishes NPSHr/NPSH3, mesh independence, temporal convergence, conservation acceptance, or experimental validation.

Native export includes exact initial state, mesh, settings, paired finite time fields, solver logs and checksum-bound v2 checkpoint descriptors. The generated `reference/snapshot.json` contains only the fixed OpenFOAM tutorial profile/source commit, never Main/student data. Checksums prove integrity, not source authenticity. Outputs remain Imported Evidence and cannot make Main Current.

Python educational verification reports equations in Input â†’ Substitution â†’ Result stages from native operands. This repository does not perform application engineering calculations or alter Main.

## Source and licensing

The 31 official resources are unchanged and pinned in `tools/cfd_worker/fixtures/openfoam12/provenance.json`: OpenFOAM-12 source commit `0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd`. Their GPL-3.0-or-later notice and COPYING are retained. See https://github.com/OpenFOAM/OpenFOAM-12 and https://openfoam.org/licence/ . Orchestration source is a reviewed subset/projection of the private canonical system; release-manifest.json records relative source/export hashes without publishing private Git history or runtime data. The export is not a live mirror. Project-authored orchestration licensing is not silently reassigned by this export.

This candidate has no actual local OpenFOAM acceptance claim. Verify successful public workflow/native output evidence after publication; retain the exact release commit and run IDs with results.

## Measured disposable Ubuntu 24.04 installation footprint

A real preceding private development-run installer downloaded the exact checksum-bound OpenFOAM 12 package (version 20250206): 112,877,506 bytes (112.9 MB decimal / 107.65 MiB). Apt reported 68.5 MB dependency downloads, 181 MB total archives and 852 MB additional installed disk. These are observed installer values from that preconfigured runner, not universal requirements; preinstalled dependencies and devices can change them. No actual solver success is inferred from installation success. Runtime binaries are ephemeral on GitHub and are neither committed here nor installed on the student laptop.
