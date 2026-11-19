"""R279：**模型驱动的改码器**——把迭代闭环的"产出改码"这一半接到真模型上。

★ 为什么（真读数，2026-11-06）：
  `goal_iterate.iterate(改码=...)` 的改码是**回调占位**，前两轮我只能用测试脚本里
  手写的函数喂它。那等于说：无人值守的"产出改动"这一半**仍然是人**。
  ⇒ 本模块提供 `模型改码器`，用现有通道把缺口 + 目标仓原文喂给真模型，
    收回**整文件新全文**，交给 `close_loop` 的机器判据门裁决（绿保/回退）。

★ 三条硬纪律（都是前几轮用真事故换来的）：
  1. **模型不给自己发合格证**：本模块只产 `{相对路径: 新全文}`，
     落不落盘、留不留，全由 `close_loop`/`gate_spec` 的机器门决定。
  2. **只许改改动面内的文件**：改动面由机器算出（失败栈 + 门声明），
     模型返回的任何越界文件一律拒收（`拒收` 里如实列出）。
  3. **拿不到可用产物就如实说**：通道全死 / 返回不是完整文件 / 越界 ⇒
     返回 `None` 并带上原因。**不许把"没拿到"说成"改完了"。**

★ 诚实边界：本模块不判断"改得对不对"——那是机器判据门的活。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

#: 单次派发给模型的原文上限（字节）。超了只发**改动面里前几个文件**，
#: 并在回执里说明"没全发"，不许静默截断成"看起来全发了"。
原文上限 = 60000


def _占位(提示: str, 改码器) -> str:          # pragma: no cover - 仅文档
    return 提示


def 建提示(计划: dict, 根: Path, 改动面: list[str], 上轮: dict | None = None,
            上限字节: int = 原文上限) -> tuple[str, list[str]]:
    """组提示。返回 (提示文本, 真正发出去的文件列表)。

    ★ 必须把**门原文**和**文件原文**一起给：模型看不到判据就只会瞎猜。
    """
    门文: list[str] = []
    for s in (计划.get("submissions") or []):
        for g in (s.get("gates") or []):
            门文.append(json.dumps(g, ensure_ascii=False))
    if not 门文:
        for t in (计划.get("tasks") or []):
            for g in (t.get("gates") or []):
                门文.append(json.dumps(g, ensure_ascii=False))

    片段: list[str] = []
    发出: list[str] = []
    用掉 = 0
    for rel in 改动面:
        p = 根 / rel
        try:
            原 = p.read_text(encoding="utf-8")
        except Exception:                       # noqa: BLE001
            continue
        if 用掉 + len(原.encode("utf-8")) > 上限字节 and 发出:
            break
        发出.append(rel)
        片段.append(f"### 文件 {rel}\n```\n{原}```")
        用掉 += len(原.encode("utf-8"))

    提示 = (
        "你在一个真实工程仓里完成一个**多文件/跨文件**工程目标。\n"
        "下面是机器从仓里读出来的**判据门**（必须全部通过才算完成）和**待改文件原文**。\n\n"
        "## 判据门（这些门会由机器在你返回后逐条执行，你不给自己判分）\n"
        + "\n".join(f"- `{x}`" for x in dict.fromkeys(门文)) +
        "\n\n## 待改文件原始内容\n" + "\n\n".join(片段) +
        "\n\n## 输出要求（严格照做）\n"
        "只输出一个 JSON 对象，键是**相对路径**，值是**该文件的完整新内容**：\n"
        '{"src/a.py": "…完整新内容…", "src/b.py": "…"}\n'
        "- 必须给出**完整文件**，不许只给 diff 片段；\n"
        "- 只许改上面列出的文件；\n"
        "- 不许输出解释、不许加代码围栏；直接输出 JSON。\n"
        "- 若确实无法完成，输出 {\"__无法完成__\": \"原因\"}。\n"
    )
    return 提示, 发出


def 解析改码(模型文: str, 允许: list[str]) -> tuple[dict, list[str]]:
    """从模型输出里取出 `{相对路径: 新全文}`。返回 (改码, 拒收说明)。

    ★ 容错但**不许放宽纪律**：
      · 剥代码围栏；取第一个 `{…}`；
      · 越界文件 **拒收并列出**（不许悄悄丢掉，否则"看起来改了三个其实只改了两个"）；
      · 值不是字符串的拒收。
    """
    文 = (模型文 or "").strip()
    if 文.startswith("```"):
        文 = re.sub(r"^```[A-Za-z0-9_+-]*\s*", "", 文)
        文 = re.sub(r"```\s*$", "", 文).strip()
    i, j = 文.find("{"), 文.rfind("}")
    if i < 0 or j <= i:
        return {}, ["模型输出里没有 JSON 对象"]
    try:
        对 = json.loads(文[i:j + 1])
    except ValueError as e:
        return {}, [f"模型输出不是合法 JSON：{e}"]
    if not isinstance(对, dict):
        return {}, ["模型输出的 JSON 不是对象"]
    if "__无法完成__" in 对:
        return {}, [f"模型自述无法完成：{对.get('__无法完成__')}"]

    允许集 = {x.replace("\\", "/").strip() for x in 允许}
    码: dict[str, str] = {}
    拒: list[str] = []
    for k, v in 对.items():
        rel = str(k).replace("\\", "/").strip()
        if rel not in 允许集:
            拒.append(f"{rel}：不在改动面内 ⇒ 拒收")
            continue
        if not isinstance(v, str) or not v.strip():
            拒.append(f"{rel}：值不是非空字符串 ⇒ 拒收")
            continue
        码[rel] = v
    return 码, 拒


def _探上游() -> str:
    """直连一次端点，取**上游自己的错误原文**（只为把原因带进回执，不改任何状态）。

    ★ 为什么单独探：SDK 抛的是 JSONDecodeError（它解析不了非标准响应体），
      而上游真实原因是 HTTP 200 + {"error":{"message":"…"}}。
      不探这一步，回执里只有"解析失败"，看不出是"上游死了"。
    """
    try:
        import json as _j
        import urllib.request as _u
        from agentscope_orchestrator import gateway as _gw
        # ★ R279b-2（真读数）：**别按"第一个通道"探。**
        #   实测 `select_channels("")[0]` 是 `aaa`，而 `aaa` 的 key 没配 ⇒
        #   探针自己抛 `RuntimeError: channel['aaa'] key not found` ——
        #   于是"上游原文"这一栏变成了"我自己取配置失败"，比不探还误导。
        #   ⇒ 逐个试，**取第一个真的能拿到 key 的通道**。
        #   ★ 还有一处形状错：`ModelSpec` 的模型名是 `.id`，不是 `.name`
        #     （我第一版取 `.name` ⇒ 永远取不到 ⇒ 用了个硬编码兜底）。
        #     「不要猜数据的形状，去读它的生产者」——这次我读了。
        基 = ""
        键 = ""
        模 = ""
        for ch in (_gw.select_channels("") or []):
            try:
                _基 = str(getattr(ch, "base_url", "") or getattr(ch, "base", "") or "").rstrip("/")
                _键 = str(getattr(ch, "api_key", "") or getattr(ch, "key", "") or "")
                _模 = ""
                for _m in (getattr(ch, "models", None) or []):
                    _模 = str(getattr(_m, "id", "") or getattr(_m, "name", "") or "")
                    if _模:
                        break
                if _基 and _键 and _模:
                    基, 键, 模 = _基, _键, _模
                    break
            except Exception:                       # noqa: BLE001
                continue
        if not (基 and 键):
            return "（所有通道都取不到 base_url/api_key）"
        体 = _j.dumps({"model": 模,
                       "messages": [{"role": "user", "content": "ping"}],
                       "max_tokens": 4}).encode()
        请 = _u.Request(基 + "/chat/completions", data=体,
                        headers={"Authorization": f"Bearer {键}",
                                 "Content-Type": "application/json",
                                 "User-Agent": "curl/8"})
        try:
            with _u.urlopen(请, timeout=25) as r:
                return r.read().decode("utf-8", "replace")[:240]
        except Exception as e2:                     # noqa: BLE001
            # HTTP 错误码也带响应体：那才是上游的解释（403 是"无权访问分组"，
            # 200+error 是"上游异常"——两者责任方完全不同，不许混成一档）
            _体 = ""
            try:
                _体 = e2.read().decode("utf-8", "replace")[:240]   # type: ignore[attr-defined]
            except Exception:                       # noqa: BLE001
                pass
            return f"HTTP {getattr(e2, 'code', '?')}：{_体 or e2}"
    except Exception as e:                          # noqa: BLE001
        return f"（探上游失败：{type(e).__name__}: {e}）"


def 模型改码器(计划: dict, *, 通道: str = "", 上限秒: float = 600.0,
                上限字节: int = 原文上限, 日志=None):
    """返回一个可喂给 `goal_iterate.iterate(改码=...)` 的回调。

    ★ 回调拿不到可用产物时返回 `None`，并把原因记进日志 —— 让 `iterate` 走
      "阻塞"这一档，而不是假装改了。
    """

    def _改(轮: int, plan: dict, 根: Path, 上轮: dict):
        面 = list((上轮 or {}).get("上轮改动面") or [])
        if not 面:
            # 上轮没给改动面（第 1 轮 / 已达成）⇒ 自己从首轮裁决里取
            from agentscope_orchestrator import close_loop as _cl
            试 = _cl.close_loop(plan, 根)          # 只跑到定位（没有 adopted）
            面 = list((试.get("改动面") or {}).get("文件") or [])
        if not 面:
            if 日志:
                日志("模型改码器：算不出改动面 ⇒ 不派模型（改无关文件只会更糟）")
            return None
        提示, 发出 = 建提示(plan, 根, 面, 上轮, 上限字节)
        if not 发出:
            if 日志:
                日志("模型改码器：改动面里的文件一个都没读到 ⇒ 不派模型")
            return None
        try:
            from agentscope_orchestrator.gateway import build_adaptive_router
            from agentscope_orchestrator.fixloop import _模型调用带上限
            路由, _chs, 报告 = build_adaptive_router(通道, probe=True)
            if 路由 is None:
                if 日志:
                    日志(f"模型改码器：没有可用通道 ⇒ 阻塞（{str(报告)[:200]}）")
                return None
            文 = _模型调用带上限(路由, 提示, 上限秒)
        except Exception as e:                     # noqa: BLE001
            # ★ R279b（真读数）：**报错要报最上游那条原因。**
            #   实测上游整段不可用时，这里收到的是
            #     JSONDecodeError: Expecting value: line 1 column 1 (char 0)
            #   —— 那是 SDK 对**空/非标准响应体**做解析时的报错，
            #   而**真正的上游原因**是端点回的
            #     HTTP 200 + {"error":{"message":"上游服务异常，请稍后重试 …"}}
            #   只报 JSONDecodeError 会让人以为"是我这边 JSON 写错了"，
            #   于是去改一个根本没坏的解析器。⇒ 必须把上游原文带上来。
            if 日志:
                日志(f"模型改码器：调用失败 {type(e).__name__}: {e}"
                     + (f" ⟵ 上游原文：{_上游原文}" if (_上游原文 := _探上游()) else ""))
            return None
        码, 拒 = 解析改码(str(文 or ""), 发出)
        if 日志:
            if 拒:
                日志("模型改码器：拒收 " + "；".join(拒))
            if 码:
                日志(f"模型改码器：第 {轮} 轮收到 {len(码)} 个文件 "
                     f"{sorted(码)}（发出 {len(发出)} 个）")
        return 码 or None

    return _改
