"""User configuration: which model, which endpoint, how much compute.

~/.r2g/config.yaml holds everything except the API key. The key comes from the
environment variable the config names (api_key_env), or -- when a user types it
into the UI -- from ~/.r2g/credentials.yaml written with mode 0600. It is never
returned by the API, never logged, never put in a job record.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from .paths import r2g_home

# Mainstream providers. Every one but Anthropic speaks the OpenAI chat
# completions protocol (Gemini through its OpenAI-compatible endpoint). Model
# names are suggestions -- providers rename models often; the user's own
# `model:` always wins.
PROVIDERS = {
    "deepseek":  {"protocol": "openai", "base_url": "https://api.deepseek.com/v1",
                  "model": "deepseek-chat", "key_env": "DEEPSEEK_API_KEY", "label": "DeepSeek"},
    "qwen":      {"protocol": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "model": "qwen-plus", "key_env": "DASHSCOPE_API_KEY", "label": "通义千问"},
    "kimi":      {"protocol": "openai", "base_url": "https://api.moonshot.cn/v1",
                  "model": "kimi-k2-turbo-preview", "key_env": "MOONSHOT_API_KEY", "label": "Kimi"},
    "glm":       {"protocol": "openai", "base_url": "https://open.bigmodel.cn/api/paas/v4",
                  "model": "glm-4.5", "key_env": "ZHIPUAI_API_KEY", "label": "智谱 GLM"},
    "doubao":    {"protocol": "openai", "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                  "model": "", "key_env": "ARK_API_KEY", "label": "豆包（填写推理接入点 ID）"},
    "openai":    {"protocol": "openai", "base_url": "https://api.openai.com/v1",
                  "model": "gpt-4.1", "key_env": "OPENAI_API_KEY", "label": "OpenAI"},
    "gemini":    {"protocol": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                  "model": "gemini-2.5-flash", "key_env": "GEMINI_API_KEY", "label": "Google Gemini"},
    "anthropic": {"protocol": "anthropic", "base_url": "https://api.anthropic.com",
                  "model": "claude-sonnet-5-5", "key_env": "ANTHROPIC_API_KEY", "label": "Anthropic Claude"},
    "ollama":    {"protocol": "openai", "base_url": "http://localhost:11434/v1",
                  "model": "qwen2.5:14b", "key_env": "", "label": "本地 Ollama"},
    "custom":    {"protocol": "openai", "base_url": "", "model": "", "key_env": "R2G_API_KEY",
                  "label": "自定义（OpenAI 兼容）"},
}


@dataclass
class ModelConfig:
    provider: str = "deepseek"
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    protocol: str = ""
    temperature: float = 0.2
    max_tokens: int = 4096

    def resolved(self) -> "ModelConfig":
        pre = PROVIDERS.get(self.provider, PROVIDERS["custom"])
        return ModelConfig(provider=self.provider,
                           model=self.model or pre["model"],
                           base_url=(self.base_url or pre["base_url"]).rstrip("/"),
                           api_key_env=self.api_key_env or pre["key_env"],
                           protocol=self.protocol or pre["protocol"],
                           temperature=self.temperature, max_tokens=self.max_tokens)


@dataclass
class Config:
    model: ModelConfig = field(default_factory=ModelConfig)
    platform: str = "nangate45"
    workers: int = 2
    cores_per_design: int = 8
    ingest: bool = True
    host: str = "127.0.0.1"
    port: int = 8770

    def to_dict(self) -> dict:
        return asdict(self)


def config_path() -> Path:
    return r2g_home() / "config.yaml"


def load() -> Config:
    p = config_path()
    raw = yaml.safe_load(p.read_text()) if p.is_file() else {}
    raw = raw or {}
    m = raw.pop("model", {}) or {}
    cfg = Config(**{k: v for k, v in raw.items() if k in Config.__dataclass_fields__})
    cfg.model = ModelConfig(**{k: v for k, v in m.items() if k in ModelConfig.__dataclass_fields__})
    return cfg


def save(cfg: Config) -> None:
    config_path().write_text(yaml.safe_dump(cfg.to_dict(), allow_unicode=True, sort_keys=False))


def _cred_path() -> Path:
    return r2g_home() / "credentials.yaml"


def api_key(mc: ModelConfig) -> str | None:
    mc = mc.resolved()
    if mc.api_key_env and os.environ.get(mc.api_key_env):
        return os.environ[mc.api_key_env]
    p = _cred_path()
    if p.is_file():
        creds = yaml.safe_load(p.read_text()) or {}
        return creds.get(mc.provider)
    return None


def store_api_key(provider: str, key: str) -> None:
    p = _cred_path()
    creds = (yaml.safe_load(p.read_text()) or {}) if p.is_file() else {}
    creds[provider] = key
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        yaml.safe_dump(creds, f)
    os.chmod(p, 0o600)


def public_view(cfg: Config) -> dict:
    """Config as the UI may see it: no key, only whether one is available."""
    d = cfg.to_dict()
    r = cfg.model.resolved()
    d["model_resolved"] = {"provider": r.provider, "model": r.model, "base_url": r.base_url,
                           "protocol": r.protocol, "api_key_env": r.api_key_env,
                           "has_key": bool(api_key(cfg.model)) or r.provider == "ollama"}
    d["providers"] = {k: {kk: vv for kk, vv in v.items()} for k, v in PROVIDERS.items()}
    return d
