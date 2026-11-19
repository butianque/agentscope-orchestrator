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

import json
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
                 stream: bool = True, max_retries: int = 0,
                 retry_delay: float = 1.0, context_size: int = 128_000,
                 client_kwargs: dict | None = None, **_欠: Any):
        self.credential = credential or 凭据()
        self.model = model
        # ★★★★★ R298（真事故，2026-11-06，用户当面指出）：
        #   这里原来是 `stream: bool = False`，注释写着
        #     「★ 恒走非流式：一次拿全量最好判」
        #   —— **这就是把模型调用写死成失败的那一行。**
        #
        #   真对照读数（同通道 `api.hlyrouter.top/v1`、同 key、同 prompt）：
        #     stream=False → HTTP 200 + {"error":{"message":"上游服务异常，请稍后重试…"}}
        #     stream=True  → HTTP 200 + choices，内容正常（实测回了「通道通了」）
        #   ⇒ **这条通道只认流式。** 写死非流式 = 模型永远调不通，
        #     而报出来的错长得像"上游挂了"——
        #     **能力坏了，却报得像是环境坏了**，这是本产品最坏的一类 bug。
        #
        #   为什么 40 多轮没发现：我自己就跑在 DSH 的这条通道上。
        #   我把这个矛盾当成了"环境不可用"的借口，而没有去解决它。
        #   用户一句「如果模型渠道本身有问题你还能在这里跟我说 40 几？」
        #   直接把它逼出来了。
        #
        #   ★ 口径：「**一次拿全量最好判**」这个便利**不值得拿能力去换**。
        #     流式也能拿全量 —— 拼起来就是（见下面 __call__）。
        self.stream = stream
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
            # ★ R298：跟随 self.stream（默认 True —— 这条通道只认流式）
            "stream": bool(self.stream),
        }
        头 = {"Content-Type": "application/json"}
        if self.credential.api_key:
            头["Authorization"] = f"Bearer {self.credential.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as c:
            if not self.stream:
                r = await c.post(url, json=体, headers=头)
                if r.status_code >= 400:
                    # 把**上游原文**带出来：诊断读数必须指回真因（R185）
                    raise RuntimeError(f"HTTP {r.status_code} @ {url} :: {r.text[:800]}")
                数 = r.json()
                选 = ((数.get("choices") or [{}])[0] or {}).get("message") or {}
                文 = 选.get("content") or ""
                return 应答(content=[文本块(text=文)], raw=数,
                            usage=数.get("usage") or {})

            # ★ R298：流式路径 —— 拼成一次完整响应（对外形状不变）
            #   为什么错误必须读**响应体**：这条通道对非流式请求
            #   **返回 HTTP 200 但体里是 error**（R280 已记过这个坑）。
            #   流式也一样：200 + error 要判成失败，不能当成功。
            片: list[str] = []
            末数: dict = {}
            原始行: list[str] = []
            async with c.stream("POST", url, json=体, headers=头) as r:
                if r.status_code >= 400:
                    文 = (await r.aread()).decode("utf-8", "replace")[:800]
                    raise RuntimeError(f"HTTP {r.status_code} @ {url} :: {文}")
                行们 = []
                async for 行 in r.aiter_lines():
                    if 行:
                        行们.append(行)
                全文 = "\n".join(行们)
                原始行 = 行们
            if '"error"' in 全文 and '"choices"' not in 全文:
                raise RuntimeError(f"上游在流式响应里报错 @ {url} :: {全文[:800]}")
            for 行 in 原始行:
                if not 行.startswith("data:"):
                    continue
                载荷 = 行[5:].strip()
                if 载荷 == "[DONE]":
                    break
                try:
                    片数 = json.loads(载荷)
                except Exception:                     # noqa: BLE001
                    continue
                if 片数.get("usage"):
                    末数["usage"] = 片数["usage"]
                for ch in (片数.get("choices") or []):
                    d = ch.get("delta") or {}
                    if d.get("content"):
                        片.append(d["content"])
            文 = "".join(片)
            return 应答(content=[文本块(text=文)], raw=末数,
                        usage=末数.get("usage") or {})


def _转消息(m: Any) -> dict:
    if isinstance(m, dict):
        return {"role": m.get("role", "user"), "content": m.get("content", "")}
    return {"role": getattr(m, "role", "user") or "user",
            "content": _扁平文本(getattr(m, "content", ""))}
