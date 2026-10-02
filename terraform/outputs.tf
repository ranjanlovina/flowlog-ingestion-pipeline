output "dynamodb_table_name" {
  description = "Name of the DynamoDB table storing flow log connection edges."
  value       = module.flow_log_ingestion.dynamodb_table_name
}

output "lambda_function_name" {
  description = "Name of the flow log ingestion Lambda function."
  value       = module.flow_log_ingestion.lambda_function_name
}

output "app_public_ip" {
  description = <<-EOT
    Public IP of the visualization app's Fargate task, captured at apply
    time. This goes STALE if the task is ever replaced (redeploy, crash,
    etc.) - there is no ALB providing a stable endpoint. To find the
    current IP later, see the "Finding the app's current IP" section in
    CLAUDE.md / README.md for the `aws ecs describe-tasks` lookup command.
  EOT
  value       = module.web_visualization.app_public_ip
}

output "app_url" {
  description = "Convenience URL built from the apply-time public IP. Subject to the same staleness caveat as app_public_ip."
  value       = "http://${module.web_visualization.app_public_ip}:${var.app_port}"
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster running the visualization app, for use with the IP-lookup CLI command."
  value       = module.web_visualization.ecs_cluster_name
}

output "ecs_service_name" {
  description = "Name of the ECS service running the visualization app, for use with the IP-lookup CLI command."
  value       = module.web_visualization.ecs_service_name
}

output "ecr_repository_url" {
  description = "ECR repository URL to push the app image to before the full `terraform apply` can succeed (see README.md deployment steps)."
  value       = module.web_visualization.ecr_repository_url
}
