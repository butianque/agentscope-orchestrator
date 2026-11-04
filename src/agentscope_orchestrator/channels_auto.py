"""通道自动适配：**不枚举提供方**地从现场环境里发现可用模型 API。

═══════════════════════════════════════════════════════════════════════
为什么要有这个模块（真证据，2026-10-29）
═══════════════════════════════════════════════════════════════════════
交付前的状态：通道只能来自 `config/channels.yml`，而那是**写死的四个中转站**。
凭证里一换（今天只剩 `DDDDDD_API_KEY`，aaa/ggg/mf 全 401）就整趟崩在
「没有真实可用的模型通道」——用户手里明明有 `OPENAI_API_KEY`，
却因为档案里没写那条通道而**用不了自己的 API**。

⇒ 口径：**模型 API 必须能自动适配**，不能要求用户先手写 YAML 才能跑。

═══════════════════════════════════════════════════════════════════════
机制（**全称规则，不枚举**）
═══════════════════════════════════════════════════════════════════════
① 显式优先：`ORCH_BASE_URL` + `ORCH_API_KEY`（+ `ORCH_MODEL`）⇒ 一条 `env` 通道。
② 泛化发现：**任何** `<前缀>_API_KEY` 环境变量 ⇒ 一条名为 `<前缀小写>` 的通道，
   基址取 `<前缀>_BASE_URL`，模型取 `<前缀>_MODEL`（再回落 `ORCH_MODEL`）。
   ⇒ 新增任何提供方（含将来才出现的）**都不需要改这个文件**。
③ 少数常见前缀给**默认基址/默认模型**（只是"省一次配置"，不是白名单）：
   OPENAI / DEEPSEEK / DASHSCOPE / MOONSHOT / ZHIPU / 以及本仓原有的中转站。
   未知前缀只要给了 `<前缀>_BASE_URL` + `<前缀>_MODEL` 就照样能用。
④ 合并：档案文件的通道**优先**（同名以文件为准），环境通道补在后面。
⑤ **缺 key 的通道只是"跳过"，绝不炸整趟**：解析不到 key 的通道不建。

用法：
    from agentscope_orchestrator.channels_auto import 全部通道
    for ch in 全部通道():        # 文件通道 + 环境通道
        ...
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# ★ 只放"URL 与模型名稳定且 OpenAI 兼容"的少数几条，**不是白名单**：
#   任何未知前缀 + 自己的 `<前缀>_BASE_URL`/`<前缀>_MODEL` 一样能用。
_默认基址 = {
    "OPENAI": "https://api.openai.com/v1",
    "DEEPSEEK": "https://api.deepseek.com/v1",
    "DASHSCOPE": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "MOONSHOT": "https://api.moonshot.cn/v1",
    "ZHIPU": "https://open.bigmodel.cn/api/paas/v4",
    "ZHIPUAI": "https://open.bigmodel.cn/api/paas/v4",
    "SILICONFLOW": "https://api.siliconflow.cn/v1",
    "GROQ": "https://api.groq.com/openai/v1",
    "TOGETHER": "https://api.together.xyz/v1",
    "OPENROUTER": "https://openrouter.ai/api/v1",
    "AAA": "https://api.hlyrouter.top/v1/",
    "DDDDDD": "https://api.hlyrouter.top/v1/",
    "GGG": "https://api.hlyrouter.top/v1",
    "MF": "https://atmorouter.dev/v1",
}

_默认模型 = {
    "OPENAI": "gpt-4o-mini",
    "DEEPSEEK": "deepseek-chat",
    "DASHSCOPE": "qwen-plus",
    "MOONSHOT": "moonshot-v1-8k",
    "ZHIPU": "glm-4-flash",
    "ZHIPUAI": "glm-4-flash",
    "SILICONFLOW": "deepseek-ai/DeepSeek-V3",
    "GROQ": "llama-3.3-70b-versatile",
    "AAA": "deepseek-v4.1-flash",
    "DDDDDD": "deepseek-ai/deepseek-v4.1-flash",
    "GGG": "deepseek-v4.1-flash",
    "MF": "ali/deepseek-v4.1-flash",
}

# `<前缀>_API_KEY`；前缀必须以字母开头（过滤 `_R161_FAKE_KEY` 这类内部量）
_KEY_RE = re.compile(r"^([A-Z][A-Z0-9]*)__?API_?KEY$")


def _非空(v) -> str:
    return (v or "").strip()


def 环境通道() -> list[dict]:
    """从环境变量发现通道（**只发现真能拿到 key 的**）。返回普通 dict，便于自检。"""
    out: list[dict] = []
    已见: set[str] = set()

    # ① 显式单通道（最省事：三条环境变量就能接任意兼容 API）
    _k = _非空(os.environ.get("ORCH_API_KEY"))
    _u = _非空(os.environ.get("ORCH_BASE_URL"))
    _m = _非空(os.environ.get("ORCH_MODEL"))
    if _k and _u:
        out.append({
            "name": "env",
            "display_name": "env (ORCH_*)",
            "base_url": _u,
            "key_env": "ORCH_API_KEY",
            "models": [{"id": _m or "gpt-4o-mini",
                        "context_size": 128000, "max_tokens": 8192}],
        })
        已见.add("env")

    # ② 泛化发现：任何 `<前缀>_API_KEY`
    for 名, 值 in list(os.environ.items()):
        mt = _KEY_RE.match(名)
        if not mt:
            continue
        if not _非空(值):
            continue
        前 = mt.group(1)
        低 = 前.lower()
        if 低 in 已见:
            continue
        _基 = (_非空(os.environ.get(f"{前}_BASE_URL"))
               or _非空(os.environ.get(f"{前}_BASE"))
               or _默认基址.get(前, ""))
        _模 = (_非空(os.environ.get(f"{前}_MODEL"))
               or _非空(os.environ.get("ORCH_MODEL"))
               or _默认模型.get(前, ""))
        if not _基 or not _模:
            # ★ 有 key 但不知道自己该连哪 / 用哪个模型 ⇒ **不假装能连**，跳过并留给 doctor 报。
            continue
        out.append({
            "name": 低,
            "display_name": f"{低} (环境变量 {名})",
            "base_url": _基,
            "key_env": 名,
            "models": [{"id": _模, "context_size": 128000, "max_tokens": 8192}],
        })
        已见.add(低)
    return out


def 全部通道(文件通道: list | None = None) -> list:
    """文件通道（优先）+ 环境通道（补后）。返回 `Channel` 对象列表。

    ★ 同名以**文件**为准：档案是项目/部署的显式声明，环境只是兜底自动发现。
    """
    from .gateway import Channel, ModelSpec

    out: list = []
    名: set[str] = set()
    for ch in (文件通道 or []):
        out.append(ch)
        名.add(getattr(ch, "name", ""))
    for 据 in 环境通道():
        if 据["name"] in 名:
            continue
        out.append(Channel(
            name=据["name"],
            display_name=据["display_name"],
            base_url=据["base_url"],
            key_env=据["key_env"],
            models=[ModelSpec(id=m["id"], context_size=m["context_size"],
                              max_tokens=m["max_tokens"]) for m in 据["models"]],
        ))
        名.add(据["name"])
    return out


def _selftest() -> int:
    """结构守卫：不连网、不读项目文件。"""
    fails: list[str] = []
    n = 0

    def chk(name: str, cond: bool, why: str = "") -> None:
        nonlocal n
        n += 1
        if not cond:
            fails.append(f"{name}：{why}")

    源 = Path(__file__).read_text(encoding="utf-8")

    # ① 泛化规则必须在（这是"不枚举"的落形处）
    chk("generic_env_rule", "_KEY_RE" in 源 and "API_?KEY" in 源,
        "没有 `<前缀>_API_KEY` 泛化规则 ⇒ 又回到枚举提供方")
    # ② 显式三件套必须在
    chk("orch_explicit", "ORCH_BASE_URL" in 源 and "ORCH_API_KEY" in 源,
        "没有 ORCH_BASE_URL/ORCH_API_KEY ⇒ 用户接自建兼容 API 要改代码")
    # ③ 未知前缀 + 自建基址 ⇒ 必须能发现（行为齿）
    _备 = {k: os.environ.get(k) for k in
           ("ORCH_API_KEY", "ORCH_BASE_URL", "ORCH_MODEL",
            "ZZTEST_API_KEY", "ZZTEST_BASE_URL", "ZZTEST_MODEL")}
    try:
        for k in ("ORCH_API_KEY", "ORCH_BASE_URL", "ORCH_MODEL", "ZZTEST_API_KEY"):
            os.environ.pop(k, None)
        os.environ["ZZTEST_API_KEY"] = "sk-test"
        os.environ["ZZTEST_BASE_URL"] = "https://example.invalid/v1"
        os.environ["ZZTEST_MODEL"] = "some-model"
        _得 = {c["name"]: c for c in 环境通道()}
        chk("discover_unknown_prefix", "zztest" in _得,
            f"未知前缀没被发现（得 {list(_得)}）⇒ 仍然只认写死的几家")
        if "zztest" in _得:
            chk("unknown_prefix_url", _得["zztest"]["base_url"] == "https://example.invalid/v1",
                "基址没取 <前缀>_BASE_URL")
            chk("unknown_prefix_model", _得["zztest"]["models"][0]["id"] == "some-model",
                "模型没取 <前缀>_MODEL")
            chk("unknown_prefix_keyenv", _得["zztest"]["key_env"] == "ZZTEST_API_KEY",
                "key_env 没指回环境变量名")
        # ④ 有 key 但没基址且不认识 ⇒ 不许假装能连
        os.environ.pop("ZZTEST_BASE_URL")
        os.environ.pop("ZZTEST_MODEL")
        _得2 = {c["name"] for c in 环境通道()}
        chk("no_guess_without_url", "zztest" not in _得2,
            "不知道连哪却造了通道 ⇒ 探活必然失败，等于骗人")
        # ⑤ORCH_* 三件套 ⇒ env 通道
        os.environ["ORCH_API_KEY"] = "sk-x"
        os.environ["ORCH_BASE_URL"] = "https://self.hosted/v1"
        os.environ["ORCH_MODEL"] = "my-model"
        _得3 = {c["name"]: c for c in 环境通道()}
        chk("orch_env_channel", "env" in _得3 and
            _得3["env"]["models"][0]["id"] == "my-model",
            "ORCH_BASE_URL+ORCH_API_KEY+ORCH_MODEL 没能建成一条通道")
    finally:
        for k, v in _备.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.environ.pop("ZZTEST_API_KEY", None)

    print(f"channels_auto 自检：{'全绿' if not fails else '失败'} · 判据点 {n} · 失败 {len(fails)}")
    for f in fails:
        print(f"  ✗ {f}")
    return 0 if not fails else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_selftest() if "--selftest" in sys.argv else 0)
