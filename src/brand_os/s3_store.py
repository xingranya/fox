"""S3 兼容对象存储端口的 boto3 适配器。"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import ConfigurationError
from .object_evidence import MultipartUploadInfo, ObjectInfo
from .server_config import ServerSettings


class S3ObjectStore:
    """只通过明确桶和对象版本执行原件读写。"""

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        server_side_encryption: str | None = "AES256",
        verify_tls: bool = True,
    ) -> None:
        for value, field_name in (
            (endpoint_url, "endpoint_url"),
            (bucket, "bucket"),
            (access_key, "access_key"),
            (secret_key, "secret_key"),
            (region, "region"),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} 不能为空")
        self.bucket = bucket
        self.server_side_encryption = server_side_encryption
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            verify=verify_tls,
            config=Config(
                retries={"mode": "standard", "max_attempts": 3},
                s3={"addressing_style": "path"},
            ),
        )

    @classmethod
    def from_settings(
        cls,
        settings: ServerSettings,
        *,
        region: str = "us-east-1",
        verify_tls: bool = True,
    ) -> S3ObjectStore:
        """从已经校验的服务器设置创建适配器。"""

        object_fields = {
            "object_store_endpoint",
            "object_store_bucket",
            "object_store_access_key",
            "object_store_secret_key",
        }
        issues = [
            issue
            for issue in settings.validation_issues()
            if issue.field in object_fields
        ]
        if issues:
            raise ConfigurationError(
                "；".join(issue.message for issue in issues)
            )
        assert settings.object_store_endpoint is not None
        assert settings.object_store_bucket is not None
        assert settings.object_store_access_key is not None
        assert settings.object_store_secret_key is not None
        return cls(
            endpoint_url=settings.object_store_endpoint,
            bucket=settings.object_store_bucket,
            access_key=settings.object_store_access_key.reveal(),
            secret_key=settings.object_store_secret_key.reveal(),
            region=region,
            verify_tls=verify_tls,
        )

    def versioning_enabled(self) -> bool:
        """确认桶已开启版本控制，未开启时准入服务拒绝运行。"""

        response = self._client.get_bucket_versioning(Bucket=self.bucket)
        return response.get("Status") == "Enabled"

    def put_stream(
        self,
        key: str,
        source: BinaryIO,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectInfo:
        """上传一个临时对象；boto3 自动处理并在失败时中止正常分片流程。"""

        extra_args: dict[str, object] = {
            "ContentType": content_type,
            "Metadata": dict(metadata),
        }
        if self.server_side_encryption:
            extra_args["ServerSideEncryption"] = self.server_side_encryption
        self._client.upload_fileobj(
            source,
            self.bucket,
            key,
            ExtraArgs=extra_args,
        )
        info = self.head(key)
        if info is None:
            raise RuntimeError("对象上传完成后无法读取元数据")
        return info

    def head(self, key: str, *, version_id: str | None = None) -> ObjectInfo | None:
        """读取一个明确对象版本；不存在时返回空。"""

        arguments: dict[str, object] = {"Bucket": self.bucket, "Key": key}
        if version_id:
            arguments["VersionId"] = version_id
        try:
            response = self._client.head_object(**arguments)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NoSuchVersion", "NotFound"}:
                return None
            raise
        resolved_version_value = response.get("VersionId") or version_id
        if not resolved_version_value or str(resolved_version_value) == "null":
            raise RuntimeError("对象存储未返回明确 VersionId")
        resolved_version = str(resolved_version_value)
        modified = response.get("LastModified") or datetime.now(UTC)
        return ObjectInfo(
            key=key,
            size_bytes=int(response["ContentLength"]),
            content_type=str(response.get("ContentType") or "application/octet-stream"),
            version_id=resolved_version,
            last_modified=modified.astimezone(UTC),
            metadata={str(k): str(v) for k, v in response.get("Metadata", {}).items()},
        )

    def iter_chunks(
        self,
        key: str,
        *,
        version_id: str | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        """流式读取明确对象版本，避免把大型原件一次载入内存。"""

        arguments: dict[str, object] = {"Bucket": self.bucket, "Key": key}
        if version_id:
            arguments["VersionId"] = version_id
        response = self._client.get_object(**arguments)
        body = response["Body"]
        try:
            for chunk in body.iter_chunks(chunk_size=chunk_size):
                if chunk:
                    yield bytes(chunk)
        finally:
            body.close()

    def copy(
        self,
        source_key: str,
        destination_key: str,
        *,
        source_version_id: str | None,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> ObjectInfo:
        """把隔离对象复制成内容寻址对象，并返回生成的 S3 版本。"""

        copy_source: dict[str, str] = {"Bucket": self.bucket, "Key": source_key}
        if source_version_id:
            copy_source["VersionId"] = source_version_id
        arguments: dict[str, object] = {
            "Bucket": self.bucket,
            "Key": destination_key,
            "CopySource": copy_source,
            "MetadataDirective": "REPLACE",
            "Metadata": dict(metadata),
            "ContentType": content_type,
        }
        if self.server_side_encryption:
            arguments["ServerSideEncryption"] = self.server_side_encryption
        response = self._client.copy_object(**arguments)
        version_id = str(response.get("VersionId") or "") or None
        info = self.head(destination_key, version_id=version_id)
        if info is None:
            raise RuntimeError("内容寻址对象复制完成后无法读取元数据")
        return info

    def delete(self, key: str, *, version_id: str | None = None) -> None:
        """删除明确版本；未给版本时仅创建 S3 删除标记。"""

        arguments: dict[str, object] = {"Bucket": self.bucket, "Key": key}
        if version_id:
            arguments["VersionId"] = version_id
        self._client.delete_object(**arguments)

    def list_objects(self, prefix: str) -> tuple[ObjectInfo, ...]:
        """列出前缀下所有仍存在的对象版本，用于可审计清理。"""

        paginator = self._client.get_paginator("list_object_versions")
        objects: list[ObjectInfo] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for version in page.get("Versions", ()):  # 删除标记不包含可读正文
                info = self.head(
                    str(version["Key"]),
                    version_id=str(version["VersionId"]),
                )
                if info is not None:
                    objects.append(info)
        return tuple(objects)

    def list_multipart_uploads(self, prefix: str) -> tuple[MultipartUploadInfo, ...]:
        """列出尚未完成的分片上传。"""

        uploads: list[MultipartUploadInfo] = []
        key_marker: str | None = None
        upload_marker: str | None = None
        while True:
            arguments: dict[str, object] = {"Bucket": self.bucket, "Prefix": prefix}
            if key_marker:
                arguments["KeyMarker"] = key_marker
            if upload_marker:
                arguments["UploadIdMarker"] = upload_marker
            response = self._client.list_multipart_uploads(**arguments)
            for upload in response.get("Uploads", ()):
                uploads.append(
                    MultipartUploadInfo(
                        key=str(upload["Key"]),
                        upload_id=str(upload["UploadId"]),
                        initiated_at=upload["Initiated"].astimezone(UTC),
                    )
                )
            if not response.get("IsTruncated"):
                break
            key_marker = str(response.get("NextKeyMarker") or "") or None
            upload_marker = str(response.get("NextUploadIdMarker") or "") or None
        return tuple(uploads)

    def abort_multipart_upload(self, key: str, upload_id: str) -> None:
        """中止一个过期分片上传并释放已上传分片。"""

        self._client.abort_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
        )


__all__ = ["S3ObjectStore"]
