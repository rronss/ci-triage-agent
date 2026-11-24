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
