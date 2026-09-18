"""确定性图像处理基础工具（铁律 #3：确定性处理归服务层，不塞给 Provider）。

当前仅实现零依赖的头部尺寸嗅探；P2 引入去背/像素化等重处理时再评估 Pillow。
"""

import logging

logger = logging.getLogger(__name__)

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
# SOF0-SOF15 中携带图像尺寸的标记（剔除 DHT=0xC4 / JPG=0xC8 / DAC=0xCC）
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def image_dimensions(data: bytes, fmt: str) -> tuple[int, int]:
    """从图像字节头部解析 (宽, 高)；无法解析返回 (0, 0)，绝不抛错。"""
    parser = {
        "png": _png_dimensions,
        "jpeg": _jpeg_dimensions,
        "webp": _webp_dimensions,
        "gif": _gif_dimensions,
    }.get(fmt)
    if parser is None:
        return (0, 0)
    try:
        return parser(data)
    except Exception:
        logger.debug("图像尺寸解析失败 fmt=%s len=%d", fmt, len(data), exc_info=True)
        return (0, 0)


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """PNG：IHDR 前固定 16 字节，随后宽高各 4 字节大端。"""
    if len(data) < 24 or not data.startswith(_PNG_SIG) or data[12:16] != b"IHDR":
        raise ValueError("不是预期的 PNG 结构")
    return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))


def _gif_dimensions(data: bytes) -> tuple[int, int]:
    """GIF：逻辑屏幕描述符在第 6-9 字节，小端。"""
    if len(data) < 10 or data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("不是预期的 GIF 结构")
    return (int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little"))


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    """WebP：按 chunk 类型分 VP8X / VP8（有损）/ VP8L（无损）三种头部。"""
    if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("不是预期的 WebP 结构")
    chunk = data[12:16]
    if chunk == b"VP8X":  # 扩展格式：画布宽高-1 各 3 字节小端，位于 24 起
        if len(data) < 30:
            raise ValueError("VP8X 头截断")
        return (
            int.from_bytes(data[24:27], "little") + 1,
            int.from_bytes(data[27:30], "little") + 1,
        )
    if chunk == b"VP8 ":  # 有损：关键帧头 3 字节 tag + 同步字 + 宽高各 2 字节（低 14 位）
        if len(data) < 30:
            raise ValueError("VP8 头截断")
        if data[23:26] != b"\x9d\x01\x2a":
            raise ValueError("VP8 关键帧同步字缺失")
        return (
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    if chunk == b"VP8L":  # 无损：签名 1 字节 + 4 字节打包位，宽高-1 各占 14 位
        if len(data) < 25:
            raise ValueError("VP8L 头截断")
        bits = int.from_bytes(data[21:25], "little")
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    raise ValueError(f"不支持的 WebP chunk：{chunk!r}")


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """JPEG：走标记流找到 SOF0-SOF15 段，段内偏移 5/7 处为高/宽（大端）。"""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise ValueError("不是预期的 JPEG 结构")
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            raise ValueError("JPEG 标记流错位")
        marker = data[pos + 1]
        if marker == 0xFF:  # 填充字节
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # 无长度字段的独立标记
            pos += 2
            continue
        seg_len = int.from_bytes(data[pos + 2 : pos + 4], "big")
        if seg_len < 2:
            raise ValueError("JPEG 段长度非法")
        if marker in _JPEG_SOF_MARKERS:
            if pos + 9 > len(data):
                raise ValueError("SOF 段截断")
            height = int.from_bytes(data[pos + 5 : pos + 7], "big")
            width = int.from_bytes(data[pos + 7 : pos + 9], "big")
            return (width, height)
        pos += 2 + seg_len
    raise ValueError("未找到 SOF 段")
