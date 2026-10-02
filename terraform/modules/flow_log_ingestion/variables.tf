variable "project_name" {
  type = string
}

variable "vpc_ids" {
  type = list(string)
}

variable "dynamodb_table_name" {
  type = string
}

variable "edge_ttl_days" {
  type = number
}

variable "flow_log_aggregation_interval" {
  type = number
}

variable "aws_region" {
  type = string
}
