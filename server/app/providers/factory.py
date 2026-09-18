"""Provider 组合根：从配置构建当前启用的推理后端。

仅由 main.py 组装时使用（路由层/执行器只见 providers/base.py 的协议——编码规范）。
实例按名称惰性构建并缓存：服务生命周期内复用，shutdown 时由 lifespan 统一关闭。
"""

from server.app.core.config import Settings
from server.app.providers.base import Provider
from server.app.providers.comfyui import ComfyUIProvider
from server.app.providers.openai_compat import OpenAICompatProvider
from server.app.providers.pollinations import PollinationsProvider


class SettingsProviderFactory:
    """按名称（请求覆盖或配置默认）构建并缓存 Provider（实现 base.ProviderFactory 协议）。"""

    def __init__(self, settings: Settings, instances: dict[str, Provider] | None = None) -> None:
        self._settings = settings
        # 预置实例（测试注入 MockTransport 后端 / 未来真实多后端复用）；未预置的按需构建
        self._instances: dict[str, Provider] = dict(instances or {})

    @property
    def default_name(self) -> str:
        """配置默认的 Provider 名（请求未指定时启用）。"""
        return self._settings.provider

    def get(self, name: str | None = None) -> Provider:
        """取指定名称的共享实例；未知名称抛 ValueError（路由层映射 422）。"""
        key = name or self._settings.provider
        if key not in self._instances:
            self._instances[key] = self._build(key)
        return self._instances[key]

    async def aclose(self) -> None:
        """关闭全部已构建实例的底层资源（lifespan shutdown 调用）。"""
        for instance in self._instances.values():
            await instance.aclose()
        self._instances.clear()

    def _build(self, name: str) -> Provider:
        if name == "openai_compat":
            return OpenAICompatProvider.from_settings(self._settings)
        if name == "comfyui":
            return ComfyUIProvider.from_settings(self._settings)
        if name == "pollinations":
            return PollinationsProvider.from_settings(self._settings)
        # 配置了未实现的后端要在使用时立刻暴露
        raise ValueError(
            f"provider={name!r} 尚未实现，请在 .env 切换为 openai_compat / comfyui / pollinations"
        )
