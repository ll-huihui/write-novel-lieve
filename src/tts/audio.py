"""音频工具（T7）：ffmpeg 统一转码。纯函数，无业务状态。

统一出口 = 裸 PCM s16le/24kHz/mono（时长计算、互斥节流用）
或 MP3（WS 推送用，体积约为 WAV 的 1/5）。
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
import wave

SAMPLE_RATE = 24000
BYTES_PER_MS = SAMPLE_RATE * 2 // 1000  # 16bit mono: 48 字节/毫秒

_MP3_ARGS = ["-codec:a", "libmp3lame", "-b:a", "64k"]


def find_ffmpeg() -> str:
    p = shutil.which("ffmpeg")
    if p:
        return p
    for cand in (r"C:\ffmpeg7.1.1\bin\ffmpeg.exe", r"C:\ffmpeg\bin\ffmpeg.exe"):
        if os.path.isfile(cand):
            return cand
    return "ffmpeg"


def _run_ffmpeg(args: list[str], src_label: str) -> bytes:
    r = subprocess.run(args, capture_output=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败({src_label}): "
                           f"{r.stderr.decode('utf-8', 'ignore')[:300]}")
    return r.stdout


def transcode(src_path: str | None = None, src_bytes: bytes | None = None,
              to: str = "pcm") -> bytes:
    """wav/mp3/... → to='pcm'（s16le/24k/mono 裸流）或 to='mp3'（64kbps）。"""
    if not src_path and not src_bytes:
        return b""
    ff = find_ffmpeg()
    args = [ff, "-hide_banner", "-loglevel", "error", "-nostdin"]
    tmp = None
    if src_path:
        args += ["-i", src_path]
    else:
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(src_bytes or b"")
        tmp.close()
        args += ["-i", tmp.name]
    if to == "mp3":
        args += _MP3_ARGS + ["-f", "mp3"]  # 管道输出必须显式指定 muxer
    else:
        args += ["-f", "s16le", "-acodec", "pcm_s16le"]
    args += ["-ac", "1", "-ar", str(SAMPLE_RATE), "-"]
    try:
        return _run_ffmpeg(args, src_path or "bytes")
    finally:
        if tmp:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def pcm_seconds(pcm: bytes) -> float:
    return len(pcm) / (BYTES_PER_MS * 1000.0)


def wav_bytes(pcm: bytes) -> bytes:
    """裸 PCM 包成 24kHz 单声道 WAV（转 MP3 的中间态/试听用）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()
