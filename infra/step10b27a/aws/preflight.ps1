param(
  [Parameter(Mandatory=$true)][string]$Region,
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
Require-Command "aws"

Write-Host "=== Step 10B.27A AWS Preflight ==="
$identity = aws sts get-caller-identity --output json | ConvertFrom-Json
if (-not $identity.Account) { throw "無法確認 AWS 身份" }
Write-Host ("AWS Account: " + $identity.Account)
Write-Host ("AWS ARN: " + $identity.Arn)
Write-Host ("Region: " + $Region)
Write-Host "注意：請確認這是專用犧牲/測試帳戶，而不是 Production 帳戶。"

terraform init
terraform fmt -check
terraform validate
terraform plan -out step10b27a.tfplan -var="aws_region=$Region"

if (-not $Apply) {
  Write-Host "Preflight 完成。未執行 terraform apply。"
  Write-Host "確認 plan 後，使用 -Apply 明確執行。"
  exit 0
}

Write-Host "開始套用 AWS 犧牲身份與 KMS 權限..."
terraform apply step10b27a.tfplan
Write-Host "=== Vercel Preview 回填值（非秘密）==="
terraform output real_cloud_aws_role_arn
terraform output real_cloud_aws_region
