variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "vpc_ids" {
  description = "VPC IDs to enable VPC Flow Logs for (traffic_type = ALL)."
  type        = list(string)
}

variable "allowed_cidr_blocks" {
  description = "CIDR blocks allowed to reach the visualization web app on port 80. Required - no default, so the app is never accidentally exposed to 0.0.0.0/0."
  type        = list(string)
}

variable "flow_log_aggregation_interval" {
  description = "VPC Flow Log max_aggregation_interval in seconds (60 or 600)."
  type        = number
  default     = 60

  validation {
    condition     = contains([60, 600], var.flow_log_aggregation_interval)
    error_message = "flow_log_aggregation_interval must be 60 or 600."
  }
}

variable "dynamodb_table_name" {
  description = "Name of the DynamoDB table storing flow log connection edges."
  type        = string
  default     = "vpc_flow_log_edges"
}

variable "edge_ttl_days" {
  description = "Number of days of inactivity before a connection edge expires from DynamoDB via TTL."
  type        = number
  default     = 30
}

variable "app_vpc_cidr" {
  description = "CIDR block for the dedicated VPC hosting the ECS visualization app."
  type        = string
  default     = "10.90.0.0/16"
}

variable "app_port" {
  description = "Port the visualization web app listens on (exposed directly on the Fargate task's ENI)."
  type        = number
  default     = 80
}

variable "project_name" {
  description = "Name prefix applied to created resources."
  type        = string
  default     = "flowlog-viz"
}
