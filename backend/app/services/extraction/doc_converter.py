"""遗留 .doc（Word 97-2003 二进制）→ .docx 离线转换（LibreOffice headless）。

上游解析链（``parse_word_to_tiptap`` / python-docx）只认 .docx；用户在 AST 模板页
上传的示例文档 / 默认源文件可能是遗留 .doc。本模块用 ``soffice --headless
--convert-to docx`` 把 .doc 就地重存为 .docx（保留 section 结构与排版，非渲染）。

离线安全（Principle VI）：转换纯本地进程，不触网。soffice 缺失 / 超时 / 非零退出 /
无产出一律抛 :class:`DocConversionError`，由端点转成 422 明确告知用户，绝不静默吞掉。

并发：每次调用独立 ``-env:UserInstallation``（一次性 profile 目录），规避 soffice
单实例 profile 锁——多用户并发上传时的经典竞态（否则第二个调用会「source file could
not be loaded」）。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

# soffice 与 libreoffice 是同一二进制的两个入口名，取其一。
_SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")

# 转换超时（秒）：含 soffice 冷启动（容器内约 2-4s），单文档重存远不及此。
_CONVERT_TIMEOUT = 120

# 并发闸：每个 soffice 实例吃内存（数十 MB 起），并发上传时限流避免内存尖峰。
# 用 threading.Semaphore（而非 asyncio）：转换经 asyncio.to_thread 落到线程池执行，
# 线程级信号量跨这些工作线程限流，且不绑定事件循环、对同步调用方同样有效。
_CONVERT_LIMIT = threading.Semaphore(2)


class DocConversionError(Exception):
    """.doc → .docx 转换失败（soffice 缺失 / 超时 / 非零退出 / 无产出）。"""


def soffice_available() -> bool:
    """当前环境是否装有 LibreOffice（soffice 二进制可用）。"""
    return _SOFFICE is not None


def ensure_docx(src_path: str) -> str:
    """确保返回一个 .docx 路径。

    - ``.docx``：原样返回 ``src_path``（不复制、不转换）。
    - ``.doc``：用 LibreOffice headless 转为 ``.docx``，产物落在 **源文件同目录**下
      （``<stem>.docx``），返回其路径；调用方负责后续清理（临时目录场景由
      ``TemporaryDirectory`` 兜底，持久目录场景端点自行删除源 .doc）。
    - 其他后缀：抛 :class:`DocConversionError`（调用方应在此之前完成 .doc/.docx 白名单校验）。
    """
    p = Path(src_path)
    suffix = p.suffix.lower()
    if suffix == ".docx":
        return src_path
    if suffix != ".doc":
        raise DocConversionError(f"不支持的格式：{suffix or '(无后缀)'}（仅 .doc/.docx）")
    if _SOFFICE is None:
        raise DocConversionError("LibreOffice(soffice) 不可用，无法转换 .doc 文件")

    out_dir = p.parent
    # 一次性 profile 目录：避免与其它 soffice 调用/桌面实例共享默认 profile 而触发单实例锁。
    profile_dir = Path(tempfile.mkdtemp(prefix="lo_profile_"))
    cmd = [
        _SOFFICE,
        "--headless",
        "--norestore",
        f"-env:UserInstallation=file://{profile_dir}",
        "--convert-to",
        "docx",
        "--outdir",
        str(out_dir),
        str(p),
    ]
    # 限流 + 独立 session：start_new_session 让 soffice 自成进程组，run() 超时时
    # Python 会 kill 直接子进程；自成组便于（现在或将来）连同 soffice.bin 子进程一并收敛。
    with _CONVERT_LIMIT:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=_CONVERT_TIMEOUT, start_new_session=True
            )
        except subprocess.TimeoutExpired as exc:
            raise DocConversionError("LibreOffice 转换超时") from exc
        finally:
            shutil.rmtree(profile_dir, ignore_errors=True)

    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()[:300]
        raise DocConversionError(f"LibreOffice 转换失败（退出码 {proc.returncode}）：{detail}")

    out_path = out_dir / f"{p.stem}.docx"
    # soffice 偶发 returncode=0 却产出缺失/损坏文件；三重校验（存在+非空+真 zip），
    # 绝不把不可解析的路径交给下游 python-docx（否则会在解析处炸成 500）。
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise DocConversionError("LibreOffice 未产出 .docx 文件（源文档可能损坏）")
    if not zipfile.is_zipfile(out_path):
        raise DocConversionError("LibreOffice 产物不是有效的 .docx（zip 校验失败）")
    return str(out_path)


async def ensure_docx_async(src_path: str) -> str:
    """``ensure_docx`` 的异步包装：把阻塞的 soffice 子进程调用挪到线程池，

    避免在 ``async def`` 端点里阻塞事件循环（转换约 2-4s，期间会卡住所有并发请求）。
    并发限流由 ``ensure_docx`` 内的 ``_CONVERT_LIMIT`` 统一负责。
    """
    return await asyncio.to_thread(ensure_docx, src_path)
