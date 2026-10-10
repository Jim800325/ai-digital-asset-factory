param(
  [Parameter(Mandatory=$true)][string]$ProjectId,
  [string]$Location = "global",
  [switch]$Apply
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

function Require-Command([string]$Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "缺少必要命令: $Name"
  }
}

Require-Command "terraform"
Require-Command "gcloud"

Write-Host "=== Step 10B.27A GCP Preflight ==="
$active = gcloud auth list --filter=status:ACTIVE --format="value(account)"
if (-not $active) { throw "沒有有效的 gcloud 登入身份" }
$currentProject = gcloud config get-value project 2>$null
Write-Host ("GCP Identity: " + $active)
Write-Host ("Target Project: " + $ProjectId)
Write-Host ("Configured Project: " + $currentProject)
Write-Host ("Location: " + $Location)
Write-Host "注意：請確認 Target Project 是專用犧牲/測試 Project，而不是 Production Project。"

terraform init
terraform fmt -check
terraform validate
terraform plan -out step10b27a.tfplan -var="project_id=$ProjectId" -var="location=$Location"

if (-not $Apply) {
  Write-Host "Preflight 完成。未執行 terraform apply。"
  Write-Host "確認 plan 後，使用 -Apply 明確執行。"
  exit 0
}

Write-Host "開始套用 GCP WIF / Service Account / KMS Key Ring..."
terraform apply step10b27a.tfplan
Write-Host "=== Vercel Preview 回填值（非秘密）==="
terraform output real_cloud_gcp_workload_identity_audience
terraform output real_cloud_gcp_service_account
terraform output real_cloud_gcp_project_id
terraform output real_cloud_gcp_location
terraform output real_cloud_gcp_key_ring
