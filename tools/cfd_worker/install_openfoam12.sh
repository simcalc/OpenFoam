#!/usr/bin/env bash
# Hosted disposable Ubuntu runner only. Never called by a browser/Main Run.
set -euo pipefail
if [[ "${GITHUB_ACTIONS:-}" != "true" || "${RUNNER_OS:-}" != "Linux" ]]; then
  echo "This installer is restricted to disposable GitHub Linux development runners." >&2
  exit 2
fi
python -B tools/cfd_worker/public_runner_guard.py
artifact_directory="${RUNNER_TEMP}/untirta-openfoam-runtime"
mkdir -p "$artifact_directory"
package_path="$artifact_directory/openfoam12_20250206_amd64.deb"
# The Foundation download endpoint currently redirects through an HTTP mirror.
# Use its official SourceForge package path directly, and refuse any downgrade.
curl --fail --location --proto '=https' --proto-redir '=https' --tlsv1.2 --retry 2 --max-time 180 \
  'https://downloads.sourceforge.net/project/foam/foam/ubuntu/dists/noble/main/binary-amd64/openfoam12_20250206_amd64.deb' \
  --output "$package_path"
printf '%s  %s\n' 'b3dcd9a636a5a827bf43ffcf0156922ba6db492123f035eae1046ee759602dd1' "$package_path" | sha256sum --check --strict
sudo apt-get update -q
sudo apt-get install --no-install-recommends -y "$package_path"
test "$(dpkg-query --show --showformat='${Version}' openfoam12)" = '20250206'
