"""使用服务器密钥加密 OIDC verifier 和令牌材料。"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from .identity import IdentityError, SensitiveValue
from .server_config import SecretValue, ServerSettings


class SecretCipherError(IdentityError):
    """敏感材料无法加密或解密。"""


class FernetSecretCipher:
    """为 PostgreSQL 中的短期身份秘密提供认证加密。"""

    def __init__(self, key: str | SecretValue) -> None:
        raw_key = key.reveal() if isinstance(key, SecretValue) else key
        try:
            self._fernet = Fernet(raw_key.encode("ascii"))
        except (ValueError, TypeError) as error:
            raise SecretCipherError("会话加密密钥必须是有效的 Fernet key") from error

    @classmethod
    def from_settings(cls, settings: ServerSettings) -> FernetSecretCipher:
        """从服务器秘密配置创建加密器。"""

        if settings.session_encryption_key is None:
            raise SecretCipherError("缺少会话加密密钥")
        return cls(settings.session_encryption_key)

    def encrypt(self, value: SensitiveValue) -> str:
        """加密敏感字符串并返回可持久化文本。"""

        return self._fernet.encrypt(value.reveal().encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> SensitiveValue:
        """验证密文完整性并恢复脱敏值对象。"""

        try:
            plaintext = self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, ValueError) as error:
            raise SecretCipherError("OIDC 敏感材料解密失败") from error
        return SensitiveValue(plaintext)


__all__ = ["FernetSecretCipher", "SecretCipherError"]
