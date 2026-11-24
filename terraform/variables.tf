variable "region" {
  description = "AWS region for the sandbox resources."
  type        = string
  default     = "us-east-1"
}

variable "github_subject_prefix" {
  description = <<-EOT
    The GitHub OIDC subject prefix of the repository allowed to assume the roles. GitHub now puts the immutable
    numeric ids of the owner and the repository in the subject, as repo:OWNER@OWNER_ID/REPO@REPO_ID, so a renamed or
    re-created repository cannot reuse the trust. The ids are public: GET https://api.github.com/repos/OWNER/REPO
    returns the repository id (id) and the owner id (owner.id).
  EOT
  type        = string
  default     = "repo:rronss@43352774/ci-triage-agent@1393849076"
}

variable "name_prefix" {
  description = "Prefix for every resource this module creates, so it never collides with other things in the account."
  type        = string
  default     = "ci-triage"
}

variable "deploy_can_write" {
  description = <<-EOT
    Whether the sandbox deploy role may write to the bucket. Set to false to plant a permission change: the managed
    policy is edited (a CreatePolicyVersion event in CloudTrail) and the next deploy fails with AccessDenied.
    Set back to true to restore it.
  EOT
  type        = bool
  default     = true
}
