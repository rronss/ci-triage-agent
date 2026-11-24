terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "ci-triage-agent"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  repo_sub   = "repo:${var.github_repository}:*"
  bucket     = "${var.name_prefix}-sandbox-${local.account_id}"
}

# ---------------------------------------------------------------- GitHub OIDC: short-lived credentials, no stored keys
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # AWS validates GitHub's certificate chain itself; the thumbprint is required by the API but not used.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.repo_sub]
    }
  }
}

# ---------------------------------------------------------------- the agent's role: read-only, CloudTrail lookup only
resource "aws_iam_role" "agent" {
  name                 = "${var.name_prefix}-agent-readonly"
  description          = "Used by the triage agent to read CloudTrail events. It cannot change anything."
  assume_role_policy   = data.aws_iam_policy_document.github_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "agent" {
  statement {
    sid       = "ReadCloudTrailEventHistory"
    actions   = ["cloudtrail:LookupEvents"]
    resources = ["*"] # LookupEvents does not support resource-level permissions
  }
}

resource "aws_iam_role_policy" "agent" {
  name   = "cloudtrail-lookup-only"
  role   = aws_iam_role.agent.id
  policy = data.aws_iam_policy_document.agent.json
}

# ---------------------------------------------------------------- the sandbox deploy target the demo pipeline deploys to
resource "aws_s3_bucket" "sandbox" {
  bucket        = local.bucket
  force_destroy = true # sandbox only: lets `terraform destroy` remove it even when it holds files
}

resource "aws_s3_bucket_public_access_block" "sandbox" {
  bucket                  = aws_s3_bucket.sandbox.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_role" "deploy" {
  name                 = "${var.name_prefix}-sandbox-deploy"
  description          = "Used by the demo pipeline to deploy to the sandbox bucket."
  assume_role_policy   = data.aws_iam_policy_document.github_trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "ListSandboxBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.sandbox.arn]
  }

  statement {
    sid       = "WriteSandboxObjects"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["${aws_s3_bucket.sandbox.arn}/*"]
  }
}

# The policy is attached as a managed policy, not inline, so that a later change to it is its own CloudTrail event.
resource "aws_iam_policy" "deploy" {
  name        = "${var.name_prefix}-sandbox-deploy"
  description = "Write access to the sandbox bucket only."
  policy      = data.aws_iam_policy_document.deploy.json
}

resource "aws_iam_role_policy_attachment" "deploy" {
  role       = aws_iam_role.deploy.name
  policy_arn = aws_iam_policy.deploy.arn
}
