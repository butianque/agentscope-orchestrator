"""Phase 0 烟测：单频道直连中转站，返回 assistant 消息即 exit 0。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# ★ 2026-10-31（交付审计）：`agentscope` 是**测速脚本自用**的可选依赖
#   （产品运行期只依赖 mcp/httpx/pyyaml，不需要它）。
#   装成 wheel 后干净环境里没有 agentscope ⇒ 这里在**模块级**直接 ImportError
#   ⇒ 客户端看到的是"文件坏了"，而真实情况是"这台机器没装这个可选包"。
#   ⇒ 导入期就拦下并给专用返回码 3（未验），与 run_all 的口径一致。
try:
    from agentscope.message import UserMsg
except ModuleNotFoundError as _e:
    if "agentscope" in str(_e):
        print(f"[smoke] SKIP 环境不可用（缺可选依赖 agentscope）：{_e}")
        print("[smoke] 这一段验的是真实模型通道；本机未装该可选包，代码本身未参与判决。")
        raise SystemExit(3)
    raise

from agentscope_orchestrator.gateway import build_client, load_channels


def _extract_text(content) -> str:
    """从 ChatResponse.content（block 列表）提取文本。"""
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


async def main() -> int:
    channels = load_channels()
    if not channels:
        print("FAIL: channels.yml empty")
        return 1
    # ★ 2026-11-01：原来取 `channels[0]`。配置里第一个通道（`aaa`）本机无 key
    #   ⇒ SKIP 3；而同一时刻 `dddddd` 实测可达 ⇒ 机器有通道、验收却永远 exit 3。
    #   改为按**健康状况**挑（探活，读不到仍算候选），全死才记 3（未验）。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _通道 import 选可用通道
    名, _, 报告 = 选可用通道()
    if not 名:
        print("[smoke] SKIP 环境不可用（全部通道硬死，无可用通道）")
        print("[smoke] 这一段验的是真实模型通道；本机未配置，代码本身未参与判决。")
        return 3
    ch = next(c for c in channels if c.name == 名)
    spec = ch.models[0]
    print(f"[smoke] channel={ch.name} model={spec.id} base_url={ch.base_url}")
    # ★ 2026-10-31（交付审计）：本文件测的是"模型通道通不通"，
    #   本机没配 `AAA_API_KEY` 时 `build_client` 直接抛异常 ⇒ 整条 run_all 判红。
    #   那是**环境**在说话，不是代码坏了。按 run_all 的口径给 3（未验），
    #   绝不冒充通过，也绝不冒充缺陷。除缺 key 外的任何异常仍然照抛。
    try:
        model = build_client(ch, spec)
    except RuntimeError as e:
        if "key not found" in str(e):
            print(f"[smoke] SKIP 环境不可用（该通道无 key）：{str(e)[:120]}")
            print("[smoke] 这一段验的是真实模型通道；本机未配置，代码本身未参与判决。")
            return 3
        raise
    resp = await model([UserMsg(name="user", content="回复四个字：烟测通过")])
    text = _extract_text(resp.content)
    print(f"[smoke] reply=>>{text}<<")
    if not text.strip():
        print("FAIL: empty reply")
        return 1
    print("[smoke] PASS (assistant reply received)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))