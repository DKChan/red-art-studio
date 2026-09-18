"""FastAPI 应用入口。

启动：`uv run uvicorn server.app.main:app --port 8600`

lifespan 职责：装配 settings/factory/store/runner 到 app.state（组合根），
并保证服务生命周期内 Provider 实例复用、shutdown 时统一释放底层 HTTP 资源。
测试经 create_app 注入 mock 依赖即可替换全部实现。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from server.app.api.animate import router as animate_router
from server.app.api.animations import router as animations_router
from server.app.api.artifacts import router as artifacts_router
from server.app.api.generations import router as generations_router
from server.app.api.health import router as health_router
from server.app.api.image_edits import router as image_edits_router
from server.app.api.textures import router as textures_router
from server.app.api.tilesets import router as tilesets_router
from server.app.api.ui import router as ui_router
from server.app.api.ui_gen import router as ui_gen_router
from server.app.core.config import Settings, get_settings
from server.app.core.storage import FileSystemJobStore, JobStore
from server.app.jobs.executor import (
    AnimateJobRunner,
    AnimationPackJobRunner,
    GenerationJobRunner,
    ImageEditJobRunner,
    TextureJobRunner,
    TilesetJobRunner,
    UiGenJobRunner,
)
from server.app.providers.base import ProviderFactory
from server.app.providers.factory import SettingsProviderFactory


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """组合根：缺什么补什么；对外只暴露协议（路由层不见具体实现）。"""
    # 测试可经 create_app 预注入；未注入的项按配置构建
    settings: Settings = getattr(app.state, "settings", None) or get_settings()
    factory: ProviderFactory = getattr(app.state, "factory", None) or SettingsProviderFactory(
        settings
    )
    store: JobStore = getattr(app.state, "store", None) or FileSystemJobStore(settings.data_dir)
    runner: GenerationJobRunner = getattr(app.state, "runner", None) or GenerationJobRunner(store)
    image_edit_runner: ImageEditJobRunner = getattr(
        app.state, "image_edit_runner", None
    ) or ImageEditJobRunner(store)
    tileset_runner: TilesetJobRunner = getattr(
        app.state, "tileset_runner", None
    ) or TilesetJobRunner(store)
    texture_runner: TextureJobRunner = getattr(
        app.state, "texture_runner", None
    ) or TextureJobRunner(store, factory)
    ui_gen_runner: UiGenJobRunner = getattr(app.state, "ui_gen_runner", None) or UiGenJobRunner(
        store, factory
    )
    anim_runner: AnimationPackJobRunner = getattr(
        app.state, "anim_runner", None
    ) or AnimationPackJobRunner(store)
    animate_runner: AnimateJobRunner = getattr(
        app.state, "animate_runner", None
    ) or AnimateJobRunner(store, factory)

    # fail fast：默认 Provider 此处就构建，配置错误启动即暴露，而不是等第一次提交
    factory.get(factory.default_name)

    app.state.settings = settings
    app.state.factory = factory
    app.state.store = store
    app.state.runner = runner
    app.state.image_edit_runner = image_edit_runner
    app.state.tileset_runner = tileset_runner
    app.state.texture_runner = texture_runner
    app.state.ui_gen_runner = ui_gen_runner
    app.state.anim_runner = anim_runner
    app.state.animate_runner = animate_runner
    yield
    await factory.aclose()


def create_app(
    settings: Settings | None = None,
    factory: ProviderFactory | None = None,
    store: JobStore | None = None,
) -> FastAPI:
    """组装 FastAPI 应用（路由装配唯一入口，便于测试注入）。"""
    app = FastAPI(
        title="red-art-studio",
        description="自部署 AI 游戏素材工作台（P1 曳光弹：文生图竖切）",
        version="0.1.0",
        lifespan=lifespan,
    )
    # 预注入项（测试用）；为 None 的项由 lifespan 按配置补齐
    app.state.settings = settings
    app.state.factory = factory
    app.state.store = store
    app.include_router(health_router)
    app.include_router(generations_router)
    app.include_router(artifacts_router)
    app.include_router(image_edits_router)
    app.include_router(tilesets_router)
    app.include_router(textures_router)
    app.include_router(ui_router)
    app.include_router(ui_gen_router)
    app.include_router(animations_router)
    app.include_router(animate_router)
    return app


app = create_app()
