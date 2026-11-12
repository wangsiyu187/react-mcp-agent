"""大模型 API 配置（仅会话内保留，不落盘明文 API Key）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_PROVIDER = "deepseek"
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_ROUNDS = 5


@dataclass
class LLMConfig:
    """OpenAI 兼容接口的大模型配置。

    provider 用于按服务商处理差异参数：DeepSeek 在调用时默认附加非思考模式
    参数（extra_body={"thinking": {"type": "disabled"}}），其他服务商不加。
    """

    api_key: str = ""                 # 仅当前运行会话保留，不写入任何文件
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    provider: str = DEFAULT_PROVIDER   # "deepseek" / "openai" / "other"
    timeout: float = DEFAULT_TIMEOUT  # 请求超时（秒）
    max_tool_rounds: int = DEFAULT_MAX_ROUNDS
    extra_body: Optional[dict] = None  # 用户自定义的额外请求体（高级用法）

    def masked_key(self) -> str:
        """返回脱敏后的 API Key（用于界面显示，避免泄露完整密钥）。"""
        if not self.api_key:
            return ""
        if len(self.api_key) <= 8:
            return "*" * len(self.api_key)
        return f"{self.api_key[:4]}****{self.api_key[-4:]}"

    @property
    def has_key(self) -> bool:
        """API Key 是否非空。"""
        return bool((self.api_key or "").strip())

    @property
    def is_configured(self) -> bool:
        """是否已具备可用的最小配置（API Key 与模型名均非空）。"""
        return self.has_key and bool((self.model or "").strip())

    def describe(self) -> str:
        """返回不含密钥的诊断描述（provider / base_url / model / key 是否非空）。"""
        return (
            f"provider={self.provider or ''}, base_url={self.base_url or ''}, "
            f"model={self.model or ''}, api_key={'已设置' if self.has_key else '未设置'}"
        )
