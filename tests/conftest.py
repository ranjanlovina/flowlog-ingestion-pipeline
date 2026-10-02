import os
import sys
from pathlib import Path

# Must be set before `index` (lambda/index.py) is imported anywhere, since
# it constructs a boto3 DynamoDB resource at module import time.
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("DYNAMODB_TABLE", "vpc_flow_log_edges")

# `lambda` is a Python keyword, so lambda/index.py can't be imported as
# `lambda.index` - instead, put the lambda/ directory itself on sys.path
# so `index` can be imported as a top-level module.
LAMBDA_DIR = Path(__file__).parent.parent / "lambda"
sys.path.insert(0, str(LAMBDA_DIR))
