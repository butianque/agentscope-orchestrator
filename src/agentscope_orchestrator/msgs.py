"""消息与模型客户端的**自带实现**——运行期只依赖 httpx，不依赖 agent 框架。

═══════════════════════════════════════════════════════════════════════
为什么要有这个文件（2026-10-29 交付形态驱动）
═══════════════════════════════════════════════════════════════════════
交付前 `pyproject` 的硬依赖里有 `agentscope==2.0.9`，而它拉 `numpy`。
真证据（全新 venv 实测）::

    ERROR: Could not find a version that satisfies the requirement numpy (from agentscope)
    ERROR: No matching distribution found for numpy

⇒ 「一键部署」名不副实：用户得先有一个完整 CPython，还得先拉一整套 agent
  框架的依赖树，才能让编排器跑起来——而他只想接自己的模型 API。

本编排器对框架的真实需求只有两样：
  · 一个**消息**结构（role/name/content）
  · 一个**Chat 客户端**（POST /chat/completions，带超时）
两者加起来不到 200 行，用 `httpx` 直接写，**不需要把整个框架搬进来**。

口径：`agentscope` 降为**可选后端**（`pip install .[agentscope]`）；
运行期核心 = `httpx` + `pyyaml` + `mcp`。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx


# ════════════════════════════════════════════════════════════════
# 消息
# ════════════════════════════════════════════════════════════════
@dataclass
class Msg:
    """最小消息结构：与 agentscope 的 `Msg`/`UserMsg` 在**被用到的那一面**等价。

    ★ 只实现被真正用到的字段（`role` / `name` / `content`）。
      多实现的部分是负债：会用假完整度掩盖"其实没人用"。
    """

    content: Any = ""
    name: str = ""
    role: str = "assistant"

    def to_dict(self) -> dict:
        return {"role": self.role, "content": _扁平文本(self.content)}

    def __str__(self) -> str:                      # 便于日志与 f-string
        return _扁平文本(self.content)


@dataclass
class UserMsg(Msg):
    role: str = "user"
    name: str = "user"


@dataclass
class AssistantMsg(Msg):
    role: str = "assistant"
    name: str = "assistant"


def _扁平文本(content: Any) -> str:
    """把 content（str / block 列表 / 对象）压成纯文本。

    ★ 与 `generic_goal._extract_text` / `chief._取文本` 同一口径：
      取不到文本就返回空串，**绝不返回 repr 垃圾**
      （R99b 的教训：`str(obj)` 会把"读不了应答"伪装成"它说了废话"）。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        段 = []
        for b in content:
            if isinstance(b, str):
                段.append(b)
            else:
                t = getattr(b, "text", None)
                if isinstance(t, str) and t:
                    段.append(t)
        return "\n".join(段)
    t = getattr(content, "text", None)
    return t if isinstance(t, str) else ""


# ════════════════════════════════════════════════════════════════
# 应答块（下游用 `resp.content` + `.text` 取文本）
# ════════════════════════════════════════════════════════════════
@dataclass
class 文本块:
    text: str = ""
    type: str = "text"


@dataclass
class 应答:
    """最小应答结构。`content` 是块列表，与下游 `_extract_text` 对齐。"""

    content: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return _扁平文本(self.content)


@dataclass
class 凭据:
    api_key: str = ""
    base_url: str = ""


class ChatModel:
    """OpenAI 兼容 Chat 客户端（异步、无状态、带双层超时）。

    ★ 接口刻意与原来用的框架类**同名同参**（`credential/model/stream/
      max_retries/retry_delay/context_size/client_kwargs`），
      这样换掉它不牵动调用方——**替换点只有一处**。
    ★ 重试归 Router（换槽），本层 `max_retries` 恒不生效：
      两层各自重试会叠乘（真事故：4×3=12 次真 HTTP，单槽烧 22 分钟）。
    """

    def __init__(self, credential: 凭据 | None = None, model: str = "",
                 stream: bool = False, max_retries: int = 0,
                 retry_delay: float = 1.0, context_size: int = 128_000,
                 client_kwargs: dict | None = None, **_欠: Any):
        self.credential = credential or 凭据()
        self.model = model
        self.stream = stream                  # ★ 恒走非流式：一次拿全量最好判
        self.context_size = context_size
        _kw = dict(client_kwargs or {})
        self.timeout = _kw.get("timeout") or httpx.Timeout(
            float(os.environ.get("ORCH_HTTP_TIMEOUT", "180")),
            connect=float(os.environ.get("ORCH_HTTP_CONNECT_TIMEOUT", "5")),
        )

    # ── 唯一出口 ─────────────────────────────────────────────
    async def __call__(self, msgs: list, **欠: Any) -> 应答:
        url = self.credential.base_url.rstrip("/") + "/chat/completions"
        体 = {
            "model": self.model,
            "messages": [_转消息(m) for m in (msgs or [])],
            "stream": False,
        }
        头 = {"Content-Type": "application/json"}
        if self.credential.api_key:
            头["Authorization"] = f"Bearer {self.credential.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as c:
            r = await c.post(url, json=体, headers=头)
            if r.status_code >= 400:
                # 把**上游原文**带出来：诊断读数必须指回真因（R185）
                raise RuntimeError(f"HTTP {r.status_code} @ {url} :: {r.text[:800]}")
            数 = r.json()
        选 = ((数.get("choices") or [{}])[0] or {}).get("message") or {}
        文 = 选.get("content") or ""
        return 应答(content=[文本块(text=文)], raw=数, usage=数.get("usage") or {})


def _转消息(m: Any) -> dict:
    if isinstance(m, dict):
        return {"role": m.get("role", "user"), "content": m.get("content", "")}
    return {"role": getattr(m, "role", "user") or "user",
            "content": _扁平文本(getattr(m, "content", ""))}
