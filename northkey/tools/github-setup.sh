#!/usr/bin/env bash
# One-time GitHub setup for the Northkey fork repository. Safe to re-run.
#
#   gh auth login                                   # an account with admin rights on the fork
#   bash northkey/tools/github-setup.sh [owner/repo]   # default: repo.owner/repo.name in brand.yaml
#
# - disables upstream's release/publish workflows (they would publish Nous artifacts)
# - creates the `upstream-sync` environment that holds the sync token (protected branches only)
# - makes the default workflow token read-only
# - protects `main`: no force-push, no deletion, both Northkey CI jobs required
# - allows merge commits only (squash/rebase would break installs' fast-forwards), auto-merge on
# - enables private vulnerability reporting and creates the `needs-review` label
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
brand_value() {  # brand_value <section> <key>: a two-level lookup in northkey/brand.yaml
  awk -v s="$1:" -v k="$2:" '$1 == s {f = 1; next} f && /^[^ #]/ {f = 0} f && $1 == k {print $2; exit}' \
    "$here/northkey/brand.yaml"
}
repo="${1:-$(brand_value repo owner)/$(brand_value repo name)}"
[[ "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { echo "usage: $0 owner/repo" >&2; exit 2; }
gh repo view "$repo" --json nameWithOwner >/dev/null || { echo "cannot access $repo with gh" >&2; exit 1; }
echo "Configuring $repo"

# GitHub forks start with Actions off: turn them on, then disable upstream's publishers below.
if [ "$(gh api "repos/$repo/actions/permissions" --jq .enabled)" != "true" ]; then
  gh api -X PUT "repos/$repo/actions/permissions" -F enabled=true -f allowed_actions=all >/dev/null
  echo "  GitHub Actions enabled"
fi

for wf in canary-release.yml stable-release.yml stable-release-publication.yml desktop-bundled-release.yml \
          bootstrap-installer-build.yml archive-inputs.yml pm-bundle.yml install-e2e.yml install-e2e-run.yml \
          install-e2e-macos-run.yml install-e2e-windows-run.yml termux-verify.yml nix.yml js-autofix.yml; do
  if gh workflow view "$wf" --repo "$repo" >/dev/null 2>&1; then
    gh workflow disable "$wf" --repo "$repo" >/dev/null 2>&1 || true
    echo "  disabled $wf"
  fi
done
for wf in northkey-ci.yml northkey-upstream-sync.yml; do
  gh workflow enable "$wf" --repo "$repo" >/dev/null 2>&1 && echo "  enabled $wf"
done

gh api -X PUT "repos/$repo/environments/upstream-sync" --input - >/dev/null <<'JSON'
{"deployment_branch_policy": {"protected_branches": true, "custom_branch_policies": false}}
JSON
echo "  environment upstream-sync (protected branches only)"

gh api -X PUT "repos/$repo/actions/permissions/workflow" \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false >/dev/null
echo "  default workflow token: read-only"

# 15368 is the GitHub Actions app: only checks reported by Actions can satisfy these.
gh api -X PUT "repos/$repo/branches/main/protection" --input - >/dev/null <<'JSON'
{
  "required_status_checks": {"strict": false, "checks": [
    {"context": "Fork invariant (upstream + owned paths + seams)", "app_id": 15368},
    {"context": "Northkey tests + upstream brand-sensitive suites", "app_id": 15368}
  ]},
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
echo "  main protected (no force-push, no deletion, both CI jobs required)"

gh api -X PATCH "repos/$repo" -F allow_merge_commit=true -F allow_squash_merge=false \
  -F allow_rebase_merge=false -F allow_auto_merge=true -F delete_branch_on_merge=true >/dev/null
echo "  merge commits only, auto-merge on"

gh api -X PUT "repos/$repo/private-vulnerability-reporting" >/dev/null
echo "  private vulnerability reporting on"
gh label create needs-review --color B60205 --description "Upstream sync that needs a human" \
  --force --repo "$repo" >/dev/null
echo "  label needs-review"

cat <<EOF
Done. Last step, the sync token (fine-grained: Contents, Pull requests, Workflows, Actions = read/write):
  gh secret set NORTHKEY_SYNC_TOKEN --env upstream-sync --repo $repo
EOF
