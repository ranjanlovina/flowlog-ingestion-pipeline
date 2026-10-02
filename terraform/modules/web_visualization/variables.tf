variable "project_name" {
  type = string
}

variable "app_vpc_cidr" {
  type = string
}

variable "allowed_cidr_blocks" {
  type = list(string)
}

variable "app_port" {
  type = number
}

variable "dynamodb_table_name" {
  type = string
}

variable "dynamodb_table_arn" {
  type = string
}

variable "aws_region" {
  type = string
}
