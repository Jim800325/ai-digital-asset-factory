terraform {
  required_version = ">= 1.6.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

variable "project_id" { type = string }
variable "location" { type = string, default = "global" }
variable "key_ring_id" { type = string, default = "shrimp-step10b27a" }
variable "pool_id" { type = string, default = "vercel-preview" }
variable "provider_id" { type = string, default = "vercel-preview" }
variable "service_account_id" { type = string, default = "shrimp-step10b27a" }
variable "vercel_issuer" { type = string, default = "https://oidc.vercel.com/jim-wus-projects-4bb66217" }
variable "vercel_audience" { type = string, default = "https://vercel.com/jim-wus-projects-4bb66217" }
variable "vercel_project_id" { type = string, default = "prj_orLCRCIm7aVfImH8ihB3gponFOEl" }
variable "vercel_owner_id" { type = string, default = "team_JO3GTfLCviMWb2pAvSClH0iK" }

resource "google_iam_workload_identity_pool" "vercel" {
  project                   = var.project_id
  workload_identity_pool_id = var.pool_id
  display_name              = "Vercel Preview"
  description               = "Step 10B.27A Preview-only federation"
}

resource "google_iam_workload_identity_pool_provider" "vercel" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.vercel.workload_identity_pool_id
  workload_identity_pool_provider_id = var.provider_id
  display_name                       = "Vercel Preview"

  oidc {
    issuer_uri        = var.vercel_issuer
    allowed_audiences = [var.vercel_audience]
  }

  attribute_mapping = {
    "google.subject"        = "assertion.sub"
    "attribute.project_id"  = "assertion.project_id"
    "attribute.owner_id"    = "assertion.owner_id"
    "attribute.environment" = "assertion.environment"
  }

  attribute_condition = "attribute.project_id == '${var.vercel_project_id}' && attribute.owner_id == '${var.vercel_owner_id}' && attribute.environment == 'preview'"
}

resource "google_service_account" "step10b27a" {
  project      = var.project_id
  account_id   = var.service_account_id
  display_name = "Shrimp Step 10B.27A"
}

resource "google_project_iam_custom_role" "kms_acceptance" {
  project     = var.project_id
  role_id     = "shrimpStep10b27aKms"
  title       = "Shrimp Step 10B.27A KMS"
  description = "Minimum permissions for sacrificial Cloud KMS acceptance"
  permissions = [
    "cloudkms.cryptoKeys.create",
    "cloudkms.cryptoKeys.get",
    "cloudkms.cryptoKeys.list",
    "cloudkms.cryptoKeyVersions.get",
    "cloudkms.cryptoKeyVersions.list",
    "cloudkms.cryptoKeyVersions.update",
    "cloudkms.cryptoKeyVersions.useToSign",
    "cloudkms.keyRings.get"
  ]
}

resource "google_kms_key_ring" "step10b27a" {
  project  = var.project_id
  name     = var.key_ring_id
  location = var.location
}

resource "google_project_iam_member" "kms_role" {
  project = var.project_id
  role    = google_project_iam_custom_role.kms_acceptance.name
  member  = "serviceAccount:${google_service_account.step10b27a.email}"
}

resource "google_service_account_iam_member" "wif_impersonation" {
  service_account_id = google_service_account.step10b27a.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.vercel.name}/attribute.environment/preview"
}

output "real_cloud_gcp_workload_identity_audience" {
  value = "//iam.googleapis.com/${google_iam_workload_identity_pool_provider.vercel.name}"
}
output "real_cloud_gcp_service_account" { value = google_service_account.step10b27a.email }
output "real_cloud_gcp_project_id" { value = var.project_id }
output "real_cloud_gcp_location" { value = var.location }
output "real_cloud_gcp_key_ring" { value = google_kms_key_ring.step10b27a.name }
