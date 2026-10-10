terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "aws_region" { type = string }
variable "vercel_oidc_url" {
  type    = string
  default = "https://oidc.vercel.com/jim-wus-projects-4bb66217"
}
variable "vercel_audience" {
  type    = string
  default = "https://vercel.com/jim-wus-projects-4bb66217"
}
variable "vercel_project_id" {
  type    = string
  default = "prj_orLCRCIm7aVfImH8ihB3gponFOEl"
}
variable "vercel_owner_id" {
  type    = string
  default = "team_JO3GTfLCviMWb2pAvSClH0iK"
}

resource "aws_iam_openid_connect_provider" "vercel" {
  url            = var.vercel_oidc_url
  client_id_list = [var.vercel_audience]
}

data "aws_iam_policy_document" "assume_role" {
  statement {
    sid     = "VercelPreviewOnly"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.vercel.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "oidc.vercel.com/jim-wus-projects-4bb66217:aud"
      values   = [var.vercel_audience]
    }
    condition {
      test     = "StringEquals"
      variable = "oidc.vercel.com/jim-wus-projects-4bb66217:project_id"
      values   = [var.vercel_project_id]
    }
    condition {
      test     = "StringEquals"
      variable = "oidc.vercel.com/jim-wus-projects-4bb66217:owner_id"
      values   = [var.vercel_owner_id]
    }
    condition {
      test     = "StringEquals"
      variable = "oidc.vercel.com/jim-wus-projects-4bb66217:environment"
      values   = ["preview"]
    }
  }
}

resource "aws_iam_role" "step10b27a" {
  name               = "shrimp-step10b27a-preview"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags = {
    "shrimp-live-acceptance" = "true"
    "environment"            = "preview"
  }
}

data "aws_iam_policy_document" "kms_acceptance" {
  statement {
    sid       = "CreateTaggedSacrificialKey"
    effect    = "Allow"
    actions   = ["kms:CreateKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/shrimp-live-acceptance"
      values   = ["true"]
    }
  }

  statement {
    sid    = "UseOnlyTaggedSacrificialKeys"
    effect = "Allow"
    actions = [
      "kms:Sign",
      "kms:GetPublicKey",
      "kms:DescribeKey",
      "kms:DisableKey",
      "kms:ScheduleKeyDeletion",
      "kms:TagResource"
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/shrimp-live-acceptance"
      values   = ["true"]
    }
  }
}

resource "aws_iam_role_policy" "kms_acceptance" {
  name   = "shrimp-step10b27a-kms-acceptance"
  role   = aws_iam_role.step10b27a.id
  policy = data.aws_iam_policy_document.kms_acceptance.json
}

output "real_cloud_aws_role_arn" { value = aws_iam_role.step10b27a.arn }
output "real_cloud_aws_region" { value = var.aws_region }
