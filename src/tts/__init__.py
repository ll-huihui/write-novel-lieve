"""TTS 包：OmniVoice 适配 + 音频转码工具。"""
from .omnivoice import OmniVoiceTTS, TTSError, scan_voices

__all__ = ["OmniVoiceTTS", "TTSError", "scan_voices"]
