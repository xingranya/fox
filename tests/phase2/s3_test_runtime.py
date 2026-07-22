"""为 S3 兼容集成测试提供一次性 Moto HTTP 服务。"""

from __future__ import annotations

import socket
from uuid import uuid4

import boto3
from botocore.config import Config
from moto.server import ThreadedMotoServer


class TemporaryS3:
    """启动仅监听回环地址的临时 S3 兼容服务。"""

    def __init__(self) -> None:
        self.port = self._available_port()
        self.endpoint_url = f"http://127.0.0.1:{self.port}"
        self.access_key = "brand-os-test"
        self.secret_key = "brand-os-test-secret"
        self.region = "us-east-1"
        self._server = ThreadedMotoServer(
            ip_address="127.0.0.1",
            port=self.port,
            verbose=False,
        )
        self._started = False

    @staticmethod
    def _available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            return int(probe.getsockname()[1])

    def start(self) -> None:
        """启动服务并等待 Moto 完成监听。"""

        self._server.start()
        self._started = True

    def create_versioned_bucket(self) -> tuple[str, object]:
        """创建开启版本控制的独立测试桶并返回 boto3 客户端。"""

        bucket = f"brand-os-{uuid4().hex}"
        client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
            config=Config(s3={"addressing_style": "path"}),
        )
        client.create_bucket(Bucket=bucket)
        client.put_bucket_versioning(
            Bucket=bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
        return bucket, client

    def stop(self) -> None:
        """停止临时服务。"""

        if self._started:
            self._server.stop()
        self._started = False
