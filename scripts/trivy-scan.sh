#!/usr/bin/env bash
# Scan a container image for known vulnerabilities with Trivy.
#
#   scripts/trivy-scan.sh <image-ref | image.tar> [out-dir]
#
# Writes <out-dir>/trivy.json and <out-dir>/trivy.sarif (for GitHub code
# scanning) and prints a Markdown summary (for $GITHUB_STEP_SUMMARY). Only
# vulnerabilities with a fix are reported; accepted ones go in .trivyignore.
#
# Trivy runs from its official image pinned by digest (the trivy-action tags
# were hijacked in 2026, so the action is not used).
#
# Env:
#   TRIVY_SEVERITY   severities to report (default MEDIUM,HIGH,CRITICAL)
#   TRIVY_FAIL_ON    exit 1 if any finding has one of these severities
#                    (e.g. CRITICAL); empty = report only (default)
#   TRIVY_PLATFORM   platform of a registry image, e.g. linux/arm64
#   TRIVY_USERNAME / TRIVY_PASSWORD   registry credentials (e.g. ghcr.io)
#   CONTAINER_CLI    docker (default) or podman
set -euo pipefail

TRIVY_IMAGE="docker.io/aquasec/trivy:0.76.2@sha256:8f6bb947dbc528902e11f2f5be52f7b519f6a39d5d148f41166f2040499768fb"

target="${1:?usage: $0 <image-ref | image.tar> [out-dir]}"
out="${2:-trivy-out}"
severity="${TRIVY_SEVERITY:-MEDIUM,HIGH,CRITICAL}"
cli="${CONTAINER_CLI:-docker}"
cache="${TRIVY_CACHE_DIR:-${HOME}/.cache/trivy}"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

mkdir -p "$out" "$cache"
out="$(cd "$out" && pwd)"

mounts=(-v "$cache:/root/.cache/trivy" -v "$out:/out" -v "$repo/.trivyignore:/trivyignore:ro")
if [[ "$target" == *.tar ]]; then
  mounts+=(-v "$(cd "$(dirname "$target")" && pwd)/$(basename "$target"):/image.tar:ro")
  source_args=(--input /image.tar)
else
  # A locally built image is read from the Docker daemon, otherwise pulled.
  [[ -S /var/run/docker.sock ]] && mounts+=(-v /var/run/docker.sock:/var/run/docker.sock)
  source_args=("$target")
fi

trivy() {
  "$cli" run --rm "${mounts[@]}" -e TRIVY_USERNAME -e TRIVY_PASSWORD -e TRIVY_PLATFORM "$TRIVY_IMAGE" "$@"
}

trivy image --quiet --scanners vuln --ignore-unfixed --severity "$severity" \
  --ignorefile /trivyignore --format json --output /out/trivy.json "${source_args[@]}"
trivy convert --quiet --format sarif --output /out/trivy.sarif /out/trivy.json

jq -r --arg image "$target" '
  [.Results[]?.Vulnerabilities[]?] as $v
  | def n($s): [$v[] | select(.Severity == $s)] | length;
  "### Trivy: `\($image)`", "",
  "| Critical | High | Medium |", "| ---: | ---: | ---: |",
  "| \(n("CRITICAL")) | \(n("HIGH")) | \(n("MEDIUM")) |", "",
  if ($v | length) == 0 then "No fixable vulnerabilities."
  else
    "<details><summary>Critical and high findings</summary>", "",
    "| Severity | Package | Installed | Fixed in | Vulnerability |", "| --- | --- | --- | --- | --- |",
    ($v | map(select(.Severity == "CRITICAL" or .Severity == "HIGH"))
        | unique_by(.VulnerabilityID + .PkgName) | sort_by(.Severity, .PkgName) | .[]
        | "| \(.Severity) | \(.PkgName) | \(.InstalledVersion) | \(.FixedVersion) | [\(.VulnerabilityID)](\(.PrimaryURL // "")) |"),
    "", "</details>"
  end, ""' "$out/trivy.json"

if [[ -n "${TRIVY_FAIL_ON:-}" ]]; then
  found="$(jq --arg sev "$TRIVY_FAIL_ON" '[.Results[]?.Vulnerabilities[]? | select(.Severity as $s | $sev | split(",") | index($s))] | length' "$out/trivy.json")"
  if (( found > 0 )); then
    echo "::error::$found vulnerabilities with severity in $TRIVY_FAIL_ON in $target" >&2
    exit 1
  fi
fi
