"""领域值对象的边界测试。"""

from __future__ import annotations

import hashlib
import unittest

from brand_os.domain import SourceRecord


class DomainValueTest(unittest.TestCase):
    """验证来源元数据不能携带逃逸路径。"""

    def test_source_relative_path_rejects_parent_escape(self) -> None:
        with self.assertRaises(ValueError):
            SourceRecord(
                "source-1",
                hashlib.sha256(b"source").hexdigest(),
                6,
                "../outside.md",
                "current_work",
                "P2",
            )


if __name__ == "__main__":
    unittest.main()
