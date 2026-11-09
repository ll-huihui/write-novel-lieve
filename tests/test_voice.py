"""语音系统单测（T7）：调度器互斥、队列上限、失败丢弃、音色扫描。OmniVoice 全 mock。"""

import asyncio
import json
import time

import pytest

from src.broadcaster import StageBroadcaster
from src.config import Config
from src.tts import scan_voices
from src.tts.omnivoice import TTSError
from src.voice_scheduler import QUEUE_MAX, VoiceScheduler


def _silent_mp3() -> bytes:
    """用 ffmpeg 生成 0.2s 静音 mp3（调度器要把 mp3 解码回 PCM 算时长）。"""
    import subprocess
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=24000:cl=mono", "-t", "0.2",
         "-codec:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "-"],
        capture_output=True, timeout=30, check=True)
    return r.stdout


class FakeTTS:
    """记录并发数的假合成器。"""
    def __init__(self, fail=False):
        self.concurrent = 0
        self.max_concurrent = 0
        self.calls: list[str] = []
        self.fail = fail
        self.wav = _silent_mp3()

    async def synth_mp3(self, text, preset):
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        self.calls.append(text)
        await asyncio.sleep(0.05)  # 模拟合成耗时
        self.concurrent -= 1
        if self.fail:
            raise TTSError("服务不可达")
        return self.wav


class FakeBC(StageBroadcaster):
    def __init__(self):
        self.msgs = []
        self._clients = set()

    async def broadcast(self, msg):
        self.msgs.append(msg)


@pytest.fixture
def voices_dir(tmp_path):
    for name in ("v01", "v02"):
        d = tmp_path / name
        d.mkdir()
        (d / "design.json").write_text(json.dumps(
            {"label": f"音色{name}", "lang": "Chinese", "ns": 32, "gs": 2.0,
             "dn": True, "sp": 1.0, "pp": True, "po": True}), encoding="utf-8")
    return str(tmp_path)


def make_scheduler(voices_dir, tts):
    return VoiceScheduler(tts, FakeBC(), voices_dir)


async def wait_done(s, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if s._worker is not None and s._worker.done():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("调度器未在超时内排空")


@pytest.mark.asyncio
async def test_mutual_exclusion_and_order(voices_dir):
    """连发 3 条（队列容量内）：并发恒 ≤1，合成顺序 = 入队顺序。"""
    tts = FakeTTS()
    s = make_scheduler(voices_dir, tts)
    for i in range(3):
        s.enqueue(f"第{i}条", "v01")
    await wait_done(s)
    assert tts.max_concurrent == 1
    assert tts.calls == [f"第{i}条" for i in range(3)]
    voice_msgs = [m for m in s.broadcaster.msgs if m["type"] == "voice"]
    assert len(voice_msgs) == 3
    assert voice_msgs[0]["duration_ms"] > 0


@pytest.mark.asyncio
async def test_queue_overflow_drops_newest(voices_dir):
    """队列上限 3：同时塞 6 条只处理 3 条，丢弃计数 3。"""
    tts = FakeTTS()
    s = make_scheduler(voices_dir, tts)
    for i in range(QUEUE_MAX):
        assert s.enqueue(f"第{i}条", "v01")
    for i in range(QUEUE_MAX, 6):
        assert not s.enqueue(f"第{i}条", "v01")
    assert s.dropped == 3
    await wait_done(s)
    assert len(tts.calls) == QUEUE_MAX


@pytest.mark.asyncio
async def test_tts_failure_does_not_block(voices_dir):
    """合成失败：条目丢弃、失败计数、后续条目继续。"""
    tts = FakeTTS(fail=True)
    s = make_scheduler(voices_dir, tts)
    s.enqueue("第一条", "v01")
    s.enqueue("第二条", "v01")
    await wait_done(s)
    assert s.failed == 2
    assert not [m for m in s.broadcaster.msgs if m["type"] == "voice"]


@pytest.mark.asyncio
async def test_empty_text_skipped(voices_dir):
    s = make_scheduler(voices_dir, FakeTTS())
    assert not s.enqueue("  ", "v01")
    assert s.pending == 0


# ---- scan_voices 纯函数 ----

def test_scan_voices_design(tmp_path):
    d = tmp_path / "va"
    d.mkdir()
    (d / "design.json").write_text(json.dumps(
        {"label": "测试音", "lang": "Chinese", "ns": 32, "gs": 2.0,
         "dn": True, "sp": 1.0, "pp": True, "po": True}), encoding="utf-8")
    out = scan_voices(str(tmp_path))
    assert out["va"]["label"] == "测试音" and out["va"]["kind"] == "design"


def test_scan_voices_clone(tmp_path):
    d = tmp_path / "vb"
    d.mkdir()
    (d / "ref_audio.wav").write_bytes(b"RIFF....")
    (d / "omnivoice.txt").write_text("", encoding="utf-8")
    (d / "label.txt").write_text("克隆音", encoding="utf-8")
    (d / "ref_text.txt").write_text("参考文本", encoding="utf-8")
    out = scan_voices(str(tmp_path))
    assert out["vb"]["kind"] == "clone" and out["vb"]["label"] == "克隆音"
    assert out["vb"]["ref_text"] == "参考文本"


def test_scan_voices_bad_and_missing(tmp_path):
    d = tmp_path / "bad"
    d.mkdir()
    (d / "design.json").write_text("not json{", encoding="utf-8")
    assert scan_voices(str(tmp_path)) == {}
    assert scan_voices(str(tmp_path / "nope")) == {}


def test_built_in_presets():
    """内置音色库 18 个，label 唯一。"""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "assets" / "voices"
    out = scan_voices(str(root))
    assert len(out) == 18
    labels = [v["label"] for v in out.values()]
    assert len(set(labels)) == 18


# ---- 节流时长（audio 纯函数） ----

def test_pcm_seconds():
    from src.tts import audio
    assert audio.pcm_seconds(b"\x00" * 48000) == pytest.approx(1.0)


def test_wav_roundtrip():
    from src.tts import audio
    pcm = b"\x01\x02" * 2400  # 0.1s
    wav = audio.wav_bytes(pcm)
    assert wav[:4] == b"RIFF"
