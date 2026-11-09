"""配置加载单测（T1）。"""

import json
import os

import pytest

from src.config import Config, ensure_config_file, load_config


@pytest.fixture()
def tmp_project(tmp_path, monkeypatch):
    """把配置路径指到临时目录，避免污染真实项目。"""
    import src.config as cfg

    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cfg, "CONFIG_PATH", tmp_path / "config.json")
    example = tmp_path / "config.example.json"
    example.write_text(
        json.dumps({
            "llm": {
                "writing": {"base_url": "https://api.example.com", "model": "test-model", "api_key": "file-key"},
                "chat": {"base_url": "https://api.example.com", "model": "chat-model"},
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "CONFIG_EXAMPLE_PATH", example)
    return cfg


def test_missing_config_copies_example(tmp_project, tmp_path):
    path = ensure_config_file()
    assert path.exists()
    assert path == tmp_path / "config.json"
    # 未提供的字段用默认值补齐
    config = load_config()
    assert isinstance(config, Config)
    assert config.server.port == 8800
    assert config.llm.writing.model == "test-model"
    assert config.writing.target_words == 800000


def test_existing_config_not_overwritten(tmp_project):
    ensure_config_file()
    config_path = tmp_project.CONFIG_PATH
    config_path.write_text(
        json.dumps({"server": {"port": 9999}}, ensure_ascii=False), encoding="utf-8"
    )
    ensure_config_file()
    assert load_config().server.port == 9999


def test_env_overrides_api_key(tmp_project, monkeypatch):
    monkeypatch.setenv("LLM_WRITING_API_KEY", "env-key-123")
    config = load_config()
    assert config.llm.writing.api_key == "env-key-123"
    # 未设置的环境变量不影响文件值
    assert config.llm.chat.model == "chat-model"


def test_invalid_field_rejected(tmp_project):
    config_path = tmp_project.CONFIG_PATH
    config_path.write_text(
        json.dumps({"server": {"port": "not-a-number"}}), encoding="utf-8"
    )
    with pytest.raises(Exception):
        load_config()
