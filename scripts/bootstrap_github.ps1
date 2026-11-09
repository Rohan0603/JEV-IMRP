<#
Bootstrap GitHub governance on THIS repository or on a repo generated from the template.
Requires GitHub CLI authentication with repo and project OAuth scopes.
Default is READ-ONLY preview. Use -Execute for writes.

This script does not create a branch protection bypass or grant new access.
Projects v2 status options and views require manual or separately authorized setup.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Repo,
  [switch]$Execute
)
$ErrorActionPreference = 'Stop'
if ($Repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
  throw 'Repo must be OWNER/REPO'
}
$owner, $name = $Repo.Split('/')
& gh auth status 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'Authenticate with gh auth login first' }
$who = & gh api user --jq '.login'
if ($LASTEXITCODE -ne 0) { throw 'GitHub user lookup failed' }
Write-Host "Authenticated GitHub user: $who"
$info = & gh api "repos/$Repo" | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Cannot read $Repo" }
if (-not $info.permissions.admin) { throw 'Repository administration permission required' }
Write-Host "Repository: $Repo | private: $($info.private) | template: $($info.is_template)"

# GitHub repository templates do not copy labels or Projects settings.
$labels = @(
  @{name='chief'; color='5319e7'; description='Superior model: research, architecture, decisions and review'},
  @{name='worker'; color='0e8a16'; description='Scoped implementation and technical evidence'},
  @{name='human'; color='d93f0b'; description='Human-only gate; permitted together with chief only'},
  @{name='metadata-bootstrap'; color='c5def5'; description='Metadata-only worker setup before Project Ready; must return to chief'}
)
$currentLabels = @(& gh api --paginate "repos/$Repo/labels?per_page=100" --jq '.[].name')
if ($LASTEXITCODE -ne 0) { throw 'Cannot read repository labels' }
foreach ($label in $labels) {
  if ($currentLabels -contains $label.name) {
    Write-Host "[EXISTS] Label $($label.name)"
  } elseif ($Execute) {
    & gh api -X POST "repos/$Repo/labels" -f "name=$($label.name)" -f "color=$($label.color)" -f "description=$($label.description)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Failed to create label $($label.name)" }
    Write-Host "[CREATED] Label $($label.name)"
  } else {
    Write-Host "[PREVIEW] Create label $($label.name)"
  }
}
$settings = @{
  has_issues=$true; has_projects=$true; has_discussions=$true
  allow_squash_merge=$true; allow_merge_commit=$false; allow_rebase_merge=$false
  delete_branch_on_merge=$true; allow_auto_merge=$false
}
if ($Execute) {
  $json = $settings | ConvertTo-Json -Compress
  $json | & gh api -X PATCH "repos/$Repo" --input - | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'Repository settings update failed' }
  Write-Host '[UPDATED] Repository PR/Discussions/merge settings'
} else {
  Write-Host "[PREVIEW] Repo settings: $($settings | ConvertTo-Json -Compress)"
}
# Required fields/views, automatic add, permissions and protected-branch settings
# are deliberately not claimed completed here.
Write-Host 'GitHub Projects: inspect/create separately with gh project list/create/link'
Write-Host 'Branch protection/rulesets: inspect availability; private repos may need paid plan'
if ($Execute) {
  $after = & gh api "repos/$Repo" | ConvertFrom-Json
  if ($LASTEXITCODE -ne 0) { throw 'Post-write readback failed' }
  foreach ($k in $settings.Keys) {
    if ([bool]$after.$k -ne [bool]$settings[$k]) {
      throw "Readback mismatch: $k"
    }
  }
  $afterLabels = @(& gh api --paginate "repos/$Repo/labels?per_page=100" --jq '.[].name')
  if ($LASTEXITCODE -ne 0) { throw 'Label readback failed' }
  foreach ($label in $labels) { if ($afterLabels -notcontains $label.name) { throw "Missing label after write: $($label.name)" } }
  Write-Host 'PASS: labels + repository settings verified'
}
