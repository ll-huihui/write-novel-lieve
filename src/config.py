"""配置加载：config.json（缺失时自动从 example 复制）+ 环境变量覆盖 + pydantic 校验。"""

import json
import os
import shutil
from pathlib import Path

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
CONFIG_EXAMPLE_PATH = PROJECT_ROOT / "config.example.json"
DATA_DIR = PROJECT_ROOT / "data"


class LLMPresetConfig(BaseModel):
    """自定义预设（llm.presets 段）：内置预设表之外的 OpenAI 兼容平台。"""

    base_url: str = ""
    auth: str = "bearer"  # bearer | none
    api_key: str = ""


class LLMProfileConfig(BaseModel):
    """角色档位（writing/chat）：引用预设名，可覆盖模型与 key。"""

    preset: str = "lmstudio"
    model: str = ""
    api_key: str = ""
    temperature: float = 0.8
    max_tokens: int = 2000
    thinking: str = ""  # enabled | disabled | 空


class LLMConfig(BaseModel):
    presets: dict[str, LLMPresetConfig] = Field(default_factory=dict)
    writing: LLMProfileConfig = Field(default_factory=LLMProfileConfig)
    chat: LLMProfileConfig = Field(default_factory=LLMProfileConfig)
    review: LLMProfileConfig = Field(default_factory=LLMProfileConfig)  # preset 空 = 回退写作模型


class TTSConfig(BaseModel):
    enabled: bool = False
    url: str = "http://127.0.0.1:9877"  # 127.0.0.1 优先探测，失败回落此地址
    voice: str = "v10"  # AI 主播音色（音色库目录名）
    voices_dir: str = ""  # 缺省 = 项目 assets/voices；可指向自定义音色目录


class WritingConfig(BaseModel):
    para_min: int = 200
    para_max: int = 400
    speed_ms: int = 100
    target_words: int = 800000
    target_chapters: int = 150
    gap_seconds: int = 90
    context_recent_paras: int = 5
    chapter_max_words: int = 3000
    review_recent_chapters: int = 2
    review_every_paras: int = 0  # 段级 LLM 抽查间隔，0=关闭（用户拍板：规则每段 + LLM 每章）
    roadmap_update_every: int = 10  # 每 N 章更新一次剧情路线图


class DanmakuConfig(BaseModel):
    platform: str = "douyin"
    room_id: str = ""
    douyin_ws_url: str = "ws://127.0.0.1:1088"


class InteractionConfig(BaseModel):
    chat_enabled: bool = True
    chat_interval: int = 8  # 闲聊节流窗口（秒）：窗口内只回最新一条弹幕
    vote_enabled: bool = True
    auto_vote: bool = True  # 章末自动 AI 出题并发起投票
    vote_duration: int = 90  # 投票倒计时（秒）
    gift_events_enabled: bool = True
    gift_rules: dict[str, str] = Field(default_factory=dict)  # 礼物名 → 剧情事件文案


class ServerConfig(BaseModel):
    port: int = 8800


class Config(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    writing: WritingConfig = Field(default_factory=WritingConfig)
    danmaku: DanmakuConfig = Field(default_factory=DanmakuConfig)
    interaction: InteractionConfig = Field(default_factory=InteractionConfig)


# 环境变量 -> 配置路径 的覆盖映射（用于不放 key 进 config.json 的场景）
_ENV_OVERRIDES = {
    "LLM_WRITING_API_KEY": ("llm", "writing", "api_key"),
    "LLM_CHAT_API_KEY": ("llm", "chat", "api_key"),
}


def _apply_env_overrides(raw: dict) -> dict:
    for env_key, path in _ENV_OVERRIDES.items():
        value = os.environ.get(env_key)
        if value:
            node = raw
            for part in path[:-1]:
                node = node.setdefault(part, {})
            node[path[-1]] = value
    return raw


def ensure_config_file() -> Path:
    """config.json 不存在时从 example 复制，返回实际配置文件路径。"""
    if not CONFIG_PATH.exists():
        shutil.copyfile(CONFIG_EXAMPLE_PATH, CONFIG_PATH)
    return CONFIG_PATH


def load_config() -> Config:
    """加载完整配置：文件 -> 环境变量覆盖 -> pydantic 校验。"""
    path = ensure_config_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw = _apply_env_overrides(raw)
    return Config.model_validate(raw)


def save_config(config: Config) -> None:
    """配置写回 config.json（控制台保存用；api_key 明文落盘，文件已 gitignore）。"""
    CONFIG_PATH.write_text(config.model_dump_json(indent=2), encoding="utf-8")


def masked_config(config: Config) -> dict:
    """对外展示用：api_key 脱敏成 has_key 布尔（前端回传 bool = 未修改该 key）。"""
    d = config.model_dump()
    for preset in d["llm"]["presets"].values():
        if "api_key" in preset:
            preset["api_key"] = bool(preset["api_key"])
    for name in ("writing", "review", "chat"):
        d["llm"][name]["api_key"] = bool(d["llm"][name]["api_key"])
    return d
