"""本机 OmniVoice TTS（WSL2 常驻服务）直连适配（T7）。

- 引擎地址：127.0.0.1:9877 优先（WSL2 localhost 转发，不可用时瞬时被拒），
  其次 config tts.url（WSL2 内网 IP 兜底）；拨通后记进 _active_base
- 音色：design（参数设计）/ clone（参考音频克隆）两种预设，见 scan_voices
- 调用：gradio_client（直接 HTTP 缺 session_hash 会 500，必须走官方客户端库）
- 输出：OmniVoice 原生 24kHz 单声道 WAV → audio.transcode 统一转 PCM/MP3
- 线程安全：gradio_client 非线程安全 → 全局锁串行；async 侧再套 executor 不卡事件循环
- 服务不可达/合成失败抛 TTSError，由 voice_scheduler 决定丢弃（绝不阻塞写作与闲聊）
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import threading

from . import audio

AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}

# OmniVoice 公共采样参数（与引擎 demo 同源）
DEFAULT_PARAMS = {"lang": "Chinese", "ns": 32, "gs": 2.0, "dn": True,
                  "sp": 1.0, "du": None, "pp": True, "po": True}


class TTSError(RuntimeError):
    pass


# ───────────────────────── 音色预设库 ─────────────────────────

def scan_voices(voices_dir: str) -> dict[str, dict]:
    """扫描音色预设目录 → {name: preset}。preset.kind: design|clone。

    目录约定（与 xiaoya 语音桥同源）：
      子目录含 design.json                    → design（参数设计音色）
      子目录含 ref_audio.* + omnivoice.txt    → clone（克隆参考音频，可选 label.txt/ref_text.txt）
    """
    out: dict[str, dict] = {}
    base = pathlib.Path(voices_dir)
    if not base.is_dir():
        return out
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        design_file = entry / "design.json"
        omni_marker = entry / "omnivoice.txt"
        audio_file = next((f for f in entry.iterdir()
                           if f.is_file() and f.suffix.lower() in AUDIO_EXTS), None)
        if design_file.is_file():
            try:
                raw = json.loads(design_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            label = str(raw.pop("label", name))
            out[name] = {"name": name, "label": label, "kind": "design", "params": raw}
        elif audio_file is not None and omni_marker.is_file():
            ref_text_file = next((f for f in entry.iterdir()
                                  if f.is_file() and f.name.lower().startswith("ref_text")), None)
            label_file = entry / "label.txt"
            label = label_file.read_text(encoding="utf-8", errors="ignore").strip() \
                if label_file.is_file() else name
            out[name] = {"name": name, "label": label or name, "kind": "clone",
                         "ref_audio": str(audio_file),
                         "ref_text": ref_text_file.read_text(encoding="utf-8",
                                                             errors="ignore").strip()
                         if ref_text_file else ""}
    return out


# ───────────────────────── OmniVoice 引擎 ─────────────────────────

class OmniVoiceTTS:
    def __init__(self, base: str = "http://127.0.0.1:9877", timeout_sec: float = 120):
        self.cfg_base = (base or "").rstrip("/")
        self.timeout_sec = float(timeout_sec)
        self._client = None
        self._active_base = ""
        self._lock = threading.Lock()
        self._loop_lock = asyncio.Lock()

    def _candidate_bases(self) -> list[str]:
        # 127.0.0.1 优先：不可用是瞬时被拒，配置的 WSL 内网 IP 失效时会挂几十秒
        cands = ["http://127.0.0.1:9877"]
        if self.cfg_base and self.cfg_base not in cands:
            cands.append(self.cfg_base)
        return cands

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        errs = []
        for base in self._candidate_bases():
            try:
                from gradio_client import Client
                self._client = Client(base, verbose=False)
                self._active_base = base
                return self._client
            except Exception as e:  # noqa: BLE001
                errs.append(f"{base}: {type(e).__name__}")
        raise TTSError("OmniVoice 服务不可达: " + " | ".join(errs))

    def _predict_sync(self, text: str, preset: dict) -> str:
        """阻塞调用 gradio → 返回 wav 本地路径。只在线程池里跑。"""
        params = dict(DEFAULT_PARAMS)
        params.update(preset.get("params") or {})
        with self._lock:
            client = self._ensure_client()
            try:
                if preset.get("kind") == "design":
                    result = client.predict(
                        text, params.get("lang", "Chinese"),
                        int(params.get("ns", 32)), float(params.get("gs", 2.0)),
                        bool(params.get("dn", True)), float(params.get("sp", 1.0)),
                        params.get("du"), bool(params.get("pp", True)),
                        bool(params.get("po", True)),
                        params.get("gender", "Auto"), params.get("age", "Auto"),
                        params.get("pitch", "Auto"), params.get("style", "Auto"),
                        params.get("accent", "Auto"), params.get("dialect", "Auto"),
                        api_name="/_design_fn",
                    )
                else:  # clone
                    from gradio_client import handle_file
                    result = client.predict(
                        text, params.get("lang", "Chinese"),
                        handle_file(preset["ref_audio"]), preset.get("ref_text", ""),
                        "",  # instruct
                        int(params.get("ns", 32)), float(params.get("gs", 2.0)),
                        bool(params.get("dn", True)), float(params.get("sp", 1.0)),
                        params.get("du"), bool(params.get("pp", True)),
                        bool(params.get("po", True)),
                        api_name="/_clone_fn",
                    )
            except Exception as exc:  # noqa: BLE001
                self._client = None  # 客户端可能因服务重启失效：重置后重新拨号
                raise TTSError(f"OmniVoice 合成失败: {type(exc).__name__}: {exc}") from exc
        return str(result[0])

    async def synth_pcm(self, text: str, preset: dict) -> bytes:
        """文本 → PCM s16le/24kHz/mono。失败抛 TTSError。"""
        if not text.strip():
            return b""
        async with self._loop_lock:
            loop = asyncio.get_running_loop()
            wav_path = await loop.run_in_executor(None, self._predict_sync, text, preset)
        try:
            return await loop.run_in_executor(None, audio.transcode, wav_path, None, "pcm")
        finally:
            _drop_wav(wav_path)

    async def synth_mp3(self, text: str, preset: dict) -> bytes:
        """文本 → MP3（WS 推送用）。wav 先落包再转码（ffmpeg 不吃无扩展名管道）。"""
        if not text.strip():
            return b""
        async with self._loop_lock:
            loop = asyncio.get_running_loop()
            wav_path = await loop.run_in_executor(None, self._predict_sync, text, preset)
        try:
            return await loop.run_in_executor(None, audio.transcode, wav_path, None, "mp3")
        finally:
            _drop_wav(wav_path)

    def active_base(self) -> str:
        return self._active_base or self.cfg_base


def _drop_wav(wav_path: str) -> None:
    """合成产物用完即删：gradio 客户端会把服务端 wav 下载进 %TEMP% 且从不清理
    （xiaoya 长跑实测 5989 个文件 / 1.0GB，坐实的无界累积物）。"""
    try:
        p = pathlib.Path(wav_path)
        p.unlink(missing_ok=True)
        try:
            p.parent.rmdir()  # 空哈希目录顺手带走（非空抛 OSError，忽略）
        except OSError:
            pass
    except Exception:  # noqa: BLE001 清理失败绝不影响合成主链路
        pass
