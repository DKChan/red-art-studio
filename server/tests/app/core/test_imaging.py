"""image_dimensions 测试：各格式头部解析 + 容错（坏头返回 (0,0) 不抛错）。"""

import struct

from server.app.core.imaging import image_dimensions


def _png(width: int, height: int) -> bytes:
    """手工构造 PNG 头（签名 + IHDR 长度/类型 + 宽高）。"""
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\x0d"
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x06\x00\x00\x00"
        + b"trailing-body"
    )


def test_png_dimensions() -> None:
    data = _png(64, 32)
    assert image_dimensions(data, "png") == (64, 32)


def test_gif_dimensions() -> None:
    data = b"GIF89a" + struct.pack("<HH", 128, 96) + b"trailing"
    assert image_dimensions(data, "gif") == (128, 96)


def test_webp_extended_dimensions() -> None:
    # VP8X：画布宽高-1 各 3 字节小端
    payload = (
        b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8X" + b"\x00" * 8
        + struct.pack("<I", 99)[:3]
        + struct.pack("<I", 47)[:3]
    )
    assert image_dimensions(payload, "webp") == (100, 48)


def test_webp_lossy_dimensions() -> None:
    # VP8 有损：同步字 9d 01 2a 后接宽高各 2 字节（低 14 位）
    payload = (
        b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x00" * 4
        + b"\x30\x01\x00"  # 帧头 tag（关键帧）
        + b"\x9d\x01\x2a"
        + struct.pack("<HH", 200, 150)
    )
    assert image_dimensions(payload, "webp") == (200, 150)


def test_webp_lossless_dimensions() -> None:
    # VP8L：签名 0x2f 后 4 字节打包位，宽高-1 各占 14 位
    bits = (199) | ((149) << 14)
    payload = (
        b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"VP8L"
        + b"\x00\x00\x00\x00" + b"\x2f" + struct.pack("<I", bits)
    )
    assert image_dimensions(payload, "webp") == (200, 150)


def test_jpeg_dimensions() -> None:
    # 极简 SOF0 段：标记 + 段长 + 精度 + 高 + 宽
    payload = (
        b"\xff\xd8"
        + b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">HH", 32, 64)  # 高在前
        + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    )
    assert image_dimensions(payload, "jpeg") == (64, 32)


def test_unknown_format_returns_zero() -> None:
    assert image_dimensions(b"whatever", "unknown") == (0, 0)


def test_truncated_or_garbage_returns_zero() -> None:
    assert image_dimensions(b"\x89PNG\r\n\x1a\nshort", "png") == (0, 0)
    assert image_dimensions(b"not an image at all", "jpeg") == (0, 0)
    assert image_dimensions(b"", "gif") == (0, 0)
    assert image_dimensions(b"RIFFxxxxBADS" + b"\x00" * 20, "webp") == (0, 0)
