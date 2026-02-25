import base64
import uuid
from datetime import datetime, timezone
import boto3
from botocore.client import Config

def decode_b64_image(image_b64: str) -> bytes:
    return base64.b64decode(image_b64)

def s3_client(endpoint: str, access_key: str, secret_key: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )

def make_object_key(prefix: str = "raw") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y/%m/%d/%H")
    return f"{prefix}/{ts}/{uuid.uuid4().hex}.jpg"