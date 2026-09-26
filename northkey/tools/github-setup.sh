#!/usr/bin/env bash
# One-time GitHub setup for the Northkey fork repository.
#
#   gh auth login                 # an account with admin rights on the fork
#   bash northkey/tools/github-setup.sh [owner/repo]
#
# Disables upstream's release/publish workflows (they would try to publish Nous
# artifacts from the fork), turns on private vulnerability reporting, and protects
# `main` against force-pushes and deletion. Safe to re-run.
set -euo pipefail

repo="${1:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
echo "Configuring $repo"

# Upstream workflows that publish, sign, or run on a schedule against Nous resources.
upstream_publishers=(
  canary-release.yml
  stable-release.yml
  stable-release-publication.yml
  desktop-bundled-release.yml
  bootstrap-installer-build.yml
  archive-inputs.yml
  pm-bundle.yml
  install-e2e.yml
  install-e2e-run.yml
  install-e2e-macos-run.yml
  install-e2e-windows-run.yml
  termux-verify.yml
  nix.yml
)
for wf in "${upstream_publishers[@]}"; do
  if gh workflow view "$wf" --repo "$repo" >/dev/null 2>&1; then
    gh workflow disable "$wf" --repo "$repo" && echo "  disabled $wf"
  fi
done

gh api -X PUT "repos/$repo/private-vulnerability-reporting" >/dev/null && echo "  private vulnerability reporting on"

# Protect main: installs fast-forward from it, so it must never be rewritten.
gh api -X PUT "repos/$repo/branches/main/protection" --input - >/dev/null <<'JSON'
{
  "required_status_checks": {"strict": false, "contexts": ["Fork invariant (upstream + owned paths + seams)"]},
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
echo "  main protected (no force-push, no deletion, invariant check required)"

# Merge commits only: squash/rebase would rewrite the sync merge and break fast-forwards.
gh api -X PATCH "repos/$repo" -f allow_merge_commit=true -F allow_squash_merge=false \
  -F allow_rebase_merge=false -F allow_auto_merge=true >/dev/null
echo "  merge commits only, auto-merge allowed"

echo "Done. Remaining manual step: add the NORTHKEY_SYNC_TOKEN secret (see .github/workflows/northkey-upstream-sync.yml)."
