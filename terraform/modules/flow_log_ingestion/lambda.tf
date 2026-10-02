data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/../../../lambda/index.py"
  output_path = "${path.module}/.build/lambda.zip"
}

resource "aws_iam_role" "lambda_exec" {
  name = "${var.project_name}-flow-log-ingestion"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "lambda_dynamodb" {
  name = "${var.project_name}-dynamodb-access"
  role = aws_iam_role.lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
        "dynamodb:GetItem",
      ]
      Resource = aws_dynamodb_table.vpc_flow_log_edges.arn
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_exec.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "flow_log_ingestion" {
  function_name = "${var.project_name}-flow-log-ingestion"
  role          = aws_iam_role.lambda_exec.arn
  handler       = "index.handler"
  runtime       = "python3.12"
  timeout       = 30
  memory_size   = 256

  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      DYNAMODB_TABLE = aws_dynamodb_table.vpc_flow_log_edges.name
      TTL_SECONDS    = tostring(var.edge_ttl_days * 24 * 60 * 60)
    }
  }
}

resource "aws_cloudwatch_log_group" "flow_logs" {
  for_each          = toset(var.vpc_ids)
  name              = "/vpc/flow-logs/${each.value}"
  retention_in_days = 30
}

resource "aws_flow_log" "this" {
  for_each = toset(var.vpc_ids)

  vpc_id                   = each.value
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs[each.value].arn
  iam_role_arn              = aws_iam_role.flow_log_publish.arn
  max_aggregation_interval = var.flow_log_aggregation_interval
}

resource "aws_iam_role" "flow_log_publish" {
  name = "${var.project_name}-flow-log-publish"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "vpc-flow-logs.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "flow_log_publish" {
  name = "${var.project_name}-flow-log-publish"
  role = aws_iam_role.flow_log_publish.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_lambda_permission" "allow_cloudwatch" {
  for_each = toset(var.vpc_ids)

  statement_id  = "AllowCloudWatchLogs-${each.value}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.flow_log_ingestion.function_name
  principal     = "logs.${var.aws_region}.amazonaws.com"
  source_arn    = "${aws_cloudwatch_log_group.flow_logs[each.value].arn}:*"
}

resource "aws_cloudwatch_log_subscription_filter" "to_lambda" {
  for_each = toset(var.vpc_ids)

  name            = "${var.project_name}-to-lambda-${each.value}"
  log_group_name  = aws_cloudwatch_log_group.flow_logs[each.value].name
  filter_pattern  = ""
  destination_arn = aws_lambda_function.flow_log_ingestion.arn

  depends_on = [aws_lambda_permission.allow_cloudwatch]
}
