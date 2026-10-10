# OpenFOAM Foundation 12 — complete Ubuntu package

This release redistributes the unchanged official OpenFOAM Foundation package
`openfoam12_20250206_amd64.deb` for **Ubuntu 24.04, amd64**. It contains the
installed OpenFOAM solvers, utilities, libraries, source, build scripts,
configuration and tutorials listed in `INVENTORY.json`. The separate
`openfoam12_20250206-corresponding-source.tar.gz` preserves the corresponding
OpenFOAM source/build/tutorial tree from that same checksum-verified package.
`COPYING` retains the GNU GPL version 3 text supplied by the package.

## Verify the download

Download all release assets into one directory, then run:

```bash
sha256sum --check SHA256SUMS
dpkg-deb --field openfoam12_20250206_amd64.deb Package Version Architecture
```

Expected package identity: `openfoam12`, version `20250206`, architecture
`amd64`. The official package SHA-256 is:

```text
b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1
```

## Install on Ubuntu 24.04 amd64

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends ./openfoam12_20250206_amd64.deb
set +eu
source /opt/openfoam12/etc/bashrc
foam_setup_status=$?
set -euo pipefail
test "$foam_setup_status" -eq 0
test "${WM_PROJECT_VERSION:-}" = 12
command -v foamRun blockMesh checkMesh snappyHexMesh potentialFoam
```

The official package is 112,877,506 bytes; its `Installed-Size` is 555,188 KiB.
APT also installs required Ubuntu dependencies. Their additional download and
disk space depend on the computer; they are not bundled in this package.
ParaView is an optional recommended package, excluded by the example command.
This `.deb` is not a native Windows installer. This release workflow does not
install software or change WSL on the student's laptop.

## Corresponding source and build

The official `.deb` already includes source. The separate source archive is
repackaged without generated `platforms/` binaries; all other OpenFOAM tree
members, including `src/`, `applications/`, `wmake/`, `Allwmake`, `etc/`,
`bin/`, `thirdparty/`, tutorials, documentation and license files, remain included.
Its complete member list and checksum are recorded in the inventory.

```bash
tar -xzf openfoam12_20250206-corresponding-source.tar.gz
```

Building requires the compiler and system development dependencies listed by
the package metadata and OpenFOAM's official source instructions. Follow the
retained `Allwmake`/`wmake` scripts and official instructions; this release does
not claim a separate successful source rebuild.

## Scope

Software installation and source availability are separate from CFD numerical
acceptance. This complete software release does not establish exact-pump
geometry, mesh independence, temporal convergence, cavitation-model validation
or NPSHr/NPSH3 results. Educational benchmark outputs are separate artifacts.

Official sources:

- https://dl.openfoam.org/ubuntu/dists/noble/main/binary-amd64/Packages
- https://openfoam.org/download/12-ubuntu/
- https://openfoam.org/download/12-source/
- https://openfoam.org/licence/
