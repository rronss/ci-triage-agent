output "agent_role_arn" {
  description = "Set as the repo variable AWS_AGENT_ROLE_ARN: the read-only role the triage agent assumes."
  value       = aws_iam_role.agent.arn
}

output "deploy_role_arn" {
  description = "Set as the repo variable AWS_DEPLOY_ROLE_ARN: the role the demo pipeline deploys with."
  value       = aws_iam_role.deploy.arn
}

output "sandbox_bucket" {
  description = "The bucket the demo pipeline deploys to."
  value       = aws_s3_bucket.sandbox.bucket
}

output "region" {
  value = var.region
}
