variable "region" {
  description = "AWS region for the sandbox resources."
  type        = string
  default     = "us-east-1"
}

variable "github_repository" {
  description = "The GitHub repository allowed to assume the roles, as owner/name."
  type        = string
  default     = "rronss/ci-triage-agent"
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
