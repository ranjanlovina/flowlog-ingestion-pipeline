output "dynamodb_table_name" {
  value = aws_dynamodb_table.vpc_flow_log_edges.name
}

output "dynamodb_table_arn" {
  value = aws_dynamodb_table.vpc_flow_log_edges.arn
}

output "lambda_function_name" {
  value = aws_lambda_function.flow_log_ingestion.function_name
}
