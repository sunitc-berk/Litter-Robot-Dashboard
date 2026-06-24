# ============================================================================
#  setup_github.ps1
#  Initializes this folder as a git repo and pushes it to a new PUBLIC GitHub
#  repository named "Litter_Robot_Dashboard".
#
#  PREREQUISITES (one-time, see chat for details):
#    1. Git for Windows  -> winget install --id Git.Git -e
#    2. GitHub CLI (gh)  -> winget install --id GitHub.cli -e
#    3. Sign in to GitHub -> gh auth login
#  Close and reopen PowerShell after installing, then run this script:
#       powershell -ExecutionPolicy Bypass -File .\setup_github.ps1
# ============================================================================

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> Cleaning up leftover files..." -ForegroundColor Cyan
Remove-Item -Recurse -Force ".git" -ErrorAction SilentlyContinue
Remove-Item -Force "_probe.txt", "_b.txt" -ErrorAction SilentlyContinue

Write-Host "==> Initializing git repository..." -ForegroundColor Cyan
git init
git branch -M main
git config user.name  "Your Name"
git config user.email "you@example.com"

Write-Host "==> Staging and committing files..." -ForegroundColor Cyan
git add -A
git commit -m "Initial commit: Litter Robot monitor, dashboards, and logs"

Write-Host "==> Creating GitHub repo and pushing..." -ForegroundColor Cyan
gh repo create Litter_Robot_Dashboard --public --source=. --remote=origin --push

Write-Host "==> Done! Your repo is live." -ForegroundColor Green
gh repo view --web
