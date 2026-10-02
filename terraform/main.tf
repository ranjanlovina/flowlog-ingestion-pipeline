terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Local state (terraform.tfstate) - no remote backend configured.
  # To switch to remote state later, add an S3 backend block here after
  # bootstrapping the bucket/DynamoDB lock table out-of-band.
}

provider "aws" {
  region = var.aws_region
}

module "flow_log_ingestion" {
  source = "./modules/flow_log_ingestion"

  project_name                  = var.project_name
  vpc_ids                       = var.vpc_ids
  dynamodb_table_name           = var.dynamodb_table_name
  edge_ttl_days                 = var.edge_ttl_days
  flow_log_aggregation_interval = var.flow_log_aggregation_interval
  aws_region                    = var.aws_region
}

module "web_visualization" {
  source = "./modules/web_visualization"

  project_name        = var.project_name
  app_vpc_cidr         = var.app_vpc_cidr
  allowed_cidr_blocks  = var.allowed_cidr_blocks
  app_port             = var.app_port
  dynamodb_table_name  = module.flow_log_ingestion.dynamodb_table_name
  dynamodb_table_arn   = module.flow_log_ingestion.dynamodb_table_arn
  aws_region           = var.aws_region
}
