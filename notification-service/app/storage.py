"""Ticket PDFs in S3-compatible object storage, through the AWS SDK: MinIO in compose, S3 in production.

The bucket is private: no bucket policy grants anonymous access, so a ticket is only reachable through a short-lived
presigned URL that booking-service hands to the ticket's owner (ADR 0015).
"""

from functools import lru_cache

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import Settings, get_settings


@lru_cache
def s3_client() -> BaseClient:
    settings = get_settings()
    return client_for(settings)


def client_for(settings: Settings) -> BaseClient:
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},  # MinIO; virtual-hosted style needs wildcard DNS
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def ensure_bucket(client: BaseClient, bucket: str) -> None:
    """Create the bucket if it is missing. A new bucket has no policy, so it is private."""
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
            raise
        client.create_bucket(Bucket=bucket)


def put_ticket(client: BaseClient, bucket: str, key: str, pdf: bytes, sha256_hex: str) -> None:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=pdf,
        ContentType="application/pdf",
        ContentDisposition=f'inline; filename="{key.rsplit("/", 1)[-1]}"',
        Metadata={"sha256": sha256_hex},
    )


def get_object_bytes(client: BaseClient, bucket: str, key: str) -> bytes:
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()
