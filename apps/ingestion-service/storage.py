# storage.py
import logging
import os
import re
import time
from functools import lru_cache
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from config import get_settings

log = logging.getLogger("storage")

# AWS S3 endpoint: s3[.dualstack].{region}.amazonaws.com
_AWS_ENDPOINT_RE = re.compile(r"\.([a-z]{2}-[a-z]+-\d+)\.amazonaws\.com")


def _resolve_region(endpoint: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    m = _AWS_ENDPOINT_RE.search(endpoint or "")
    return m.group(1) if m else "us-east-1"


@lru_cache
def _s3():
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=_resolve_region(s.s3_endpoint, s.s3_region),
        config=Config(signature_version="s3v4"),
    )


def _bucket() -> str:
    return get_settings().s3_bucket


def ensure_bucket():
    """Ensure our bucket exists. Uses head_bucket (bucket-scoped) rather than
    list_buckets (needs s3:ListAllMyBuckets) so it works with tight IAM policies."""
    bucket = _bucket()
    try:
        _s3().head_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchBucket"):
            _s3().create_bucket(Bucket=bucket)
        else:
            raise


def put_image(key: str, data: bytes, content_type="image/png"):
    _s3().put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=content_type)


def get_image(key: str) -> bytes:
    return _s3().get_object(Bucket=_bucket(), Key=key)["Body"].read()


def get_object(key: str) -> tuple[bytes, str | None]:
    """Fetch a single object; return (bytes, content_type). Content-type may be None."""
    obj = _s3().get_object(Bucket=_bucket(), Key=key)
    return obj["Body"].read(), obj.get("ContentType")


def head_object_etag(key: str) -> str:
    """Return the object's ETag without transferring any bytes.

    Used as a cheap cache key for downstream work (e.g. parse-preview) that
    should invalidate the instant the underlying S3 object changes. S3
    returns the ETag wrapped in double quotes; strip them so equality checks
    match the value shipped in `list_files`."""
    resp = _s3().head_object(Bucket=_bucket(), Key=key)
    return (resp.get("ETag") or "").strip('"')


def presigned_url(key: str, expires=3600) -> str:
    return _s3().generate_presigned_url(
        "get_object", Params={"Bucket": _bucket(), "Key": key}, ExpiresIn=expires
    )


def list_files(prefix: str = "") -> list[dict]:
    """List real objects in the bucket. Returns [{key, etag, size, last_modified}]. Skips folder-markers (keys ending with '/').
    ETag comes back from S3 wrapped in quotes; we strip them for cleaner comparison downstream."""
    paginator = _s3().get_paginator("list_objects_v2")
    out: list[dict] = []
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            if key.endswith("/"):
                continue
            out.append({
                "key": key,
                "etag": (obj.get("ETag") or "").strip('"'),
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            })
    return out


def list_folder_markers(prefix: str = "") -> list[str]:
    """Return folder-marker keys (0-byte objects whose key ends in '/'), trailing slash stripped.
    S3 has no real folders — the console shows a folder for any prefix, but empty folders only
    exist as explicit markers."""
    paginator = _s3().get_paginator("list_objects_v2")
    out: list[str] = []
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            if key.endswith("/"):
                out.append(key.rstrip("/"))
    return out


def download_file(key: str, dest_path: str) -> int:
    """Download a single object to dest_path. Creates parent dirs. Returns bytes written."""
    t0 = time.perf_counter()
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    _s3().download_file(_bucket(), key, dest_path)
    size = os.path.getsize(dest_path)
    log.info("s3 download key=%s bytes=%d took=%.2fs",
             key, size, time.perf_counter() - t0)
    return size


def upload_object(key: str, data: bytes, content_type: str | None = None) -> int:
    """Put arbitrary bytes at key. Returns bytes uploaded."""
    kwargs = {"Bucket": _bucket(), "Key": key, "Body": data}
    if content_type:
        kwargs["ContentType"] = content_type
    _s3().put_object(**kwargs)
    return len(data)


def create_folder(path: str) -> str:
    """S3 has no real folders; create a zero-byte marker at path/. Returns the normalized key."""
    key = path if path.endswith("/") else path + "/"
    _s3().put_object(Bucket=_bucket(), Key=key, Body=b"")
    return key


def delete_object(key: str) -> str:
    """Delete a single object at key. Returns the key."""
    _s3().delete_object(Bucket=_bucket(), Key=key)
    return key


def delete_prefix(prefix: str) -> list[str]:
    """Delete every object under the given prefix. Returns the keys removed."""
    t0 = time.perf_counter()
    if not prefix.endswith("/"):
        prefix += "/"
    paginator = _s3().get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        for obj in page.get("Contents", []) or []:
            keys.append(obj["Key"])
    if not keys:
        log.info("s3 delete_prefix prefix=%s deleted=0 took=%.2fs",
                 prefix, time.perf_counter() - t0)
        return []
    # DeleteObjects caps at 1000 keys per call
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        _s3().delete_objects(
            Bucket=_bucket(),
            Delete={"Objects": [{"Key": k} for k in batch]},
        )
    log.info("s3 delete_prefix prefix=%s deleted=%d took=%.2fs",
             prefix, len(keys), time.perf_counter() - t0)
    return keys
