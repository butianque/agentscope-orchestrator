"""R294：**从宿主 DSH 自动发现模型渠道**——挂到哪台 DSH 上，就用那台的渠道。

★ 为什么（用户当面指出的设计缺陷，2026-11-06）：
  原来的形态是 `config/channels.yml` **硬编码**一份渠道表（从 DSH 迁过来的快照）。
  ⇒ 插件挂到任何一台 DSH 上，用的都是**本仓自带的那份旧快照**：
    · DSH 里换了 provider / 加了模型 / 改了 baseURL ⇒ 本仓一无所知；
    · DSH 里删了某个 provider ⇒ 本仓还在拿死通道去试；
    · 换一台机器 ⇒ 得手工再抄一遍 channels.yml。
  **「随便装一个接上就算」不是适配，是另一次硬编码。**

★ 正确的形态（DSH 自己已经把答案放在环境里了）：
      DSH_HOME        = <DSH 数据根>
      DSH_PROFILE     = <当前 profile 名，如 desktop / web / headless>
      DSH_PROFILE_DIR = <该 profile 的目录>
  进程被挂在哪台 DSH 上，这几个变量就在哪台：
    · 它们由**宿主**注入，不依赖我们在哪个目录、不依赖机器名、不依赖用户路径；
    · ⇒ 读它们 = **自动获取当前宿主**；读不到 = 不在 DSH 里（源码树直跑），
      那就退回原来的静态配置路径，行为不变。

★ 渠道声明在 DSH 里的位置（真读过，不是猜）：
      <DSH_PROFILE_DIR>/cordis.patch.yml
        → 某个节点  {id: "llm-pi-ai", config: {providers: {...}}}
        → 每个 provider: {apiKeyEnv, baseURL, models: [{id, ...}]}
      key 本身**不在**这里，在 `$DSH_HOME/.credentials.yaml` 的 `refs.<KEY名>`，
      或环境变量 `<KEY名>`（gateway 原有的凭据解析已经能处理这两种）。

★ 诚实边界：
  · 只读**宿主当前的**声明；找不到就**如实返回「没找到」**，绝不编一个默认渠道；
  · DSH 的配置格式是它的内部结构，**可能变**。变了这里会返回「没找到」而不是
    瞎猜一个 —— 那种失败是**可察觉的**（doctor_channels 会报"未发现"）；
  · 不写宿主的任何文件，纯只读。
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

#: DSH 里声明模型 provider 的插件节点 id（真读过 `profiles/desktop/cordis.patch.yml`）
_LLM_NODE_ID = "llm-pi-ai"

#: 依次尝试的 profile 配置文件名（真布局：cordis.patch.yml 覆盖 cordis.yml）
_PROFILE_FILES = ("cordis.patch.yml", "cordis.yml")


def 宿主线索引() -> dict:
    """从环境变量读出"我现在被挂在哪台 DSH 上"。读不到就是不在 DSH 里。"""
    根 = os.environ.get("DSH_HOME") or ""
    档 = os.environ.get("DSH_PROFILE") or ""
    目 = os.environ.get("DSH_PROFILE_DIR") or ""
    if not 目 and 根 and 档:
        目 = str(Path(根) / "profiles" / 档)
    return {
        "在dsh里": bool(目),
        "DSH_HOME": 根,
        "profile": 档,
        "profile_dir": 目,
        # 凭据文件：DSH 的 key 落在这里（gateway 原有逻辑会读它）
        "凭据": str(Path(根) / ".credentials.yaml") if 根 else "",
    }


def _深找节点(node, 节点id: str):
    """在嵌套 dict/list 里找 `{"id": 节点id, ...}` —— 不假设层级。"""
    if isinstance(node, dict):
        if node.get("id") == 节点id:
            return node
        for v in node.values():
            r = _深找节点(v, 节点id)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _深找节点(v, 节点id)
            if r is not None:
                return r
    return None


def _读档(路径: Path):
    """★ R294b（我自己抓的，第 6 次「猜形状」）：**顶层是 list，不是 dict。**

    第一版写 `return d if isinstance(d, dict) else {}` ⇒ 对 `cordis.patch.yml`
    （顶层是 list，`[4].id == 'llm-pi-ai'`）**直接返回空 dict** ⇒ 报「没找到」。
    而这个"没找到"看起来完全合理（"DSH 配置结构可能变了"）——
    **差的不是配置，是我假设了容器类型。**
    ⇒ 原样返回，类型判断交给 `_深找节点`（它本来就支持 dict/list 两种）。
    """
    try:
        return yaml.safe_load(路径.read_text(encoding="utf-8"))
    except Exception:                              # noqa: BLE001
        return None


def _模型(cfg: dict) -> list[dict]:
    """DSH 的 models 可能是 str，也可能是 dict（带 contextWindow / maxTokens）。"""
    out = []
    for m in (cfg.get("models") or []):
        if isinstance(m, str):
            out.append({"id": m})
        elif isinstance(m, dict) and m.get("id"):
            out.append(m)
    return out


def 发现渠道() -> dict:
    """读宿主 DSH 的 provider 声明，转成本仓的 Channel 形状。

    返回:
      {"ok": bool, "来源": str, "渠道": [...], "没找到的原因": str}
    渠道项 = {"name", "display_name", "base_url", "key_env", "models":[{id, context_size, max_tokens}]}
    """
    宿 = 宿主线索引()
    if not 宿["在dsh里"]:
        return {"ok": False, "来源": "", "渠道": [],
                "没找到的原因": "不在 DSH 里（没有 DSH_PROFILE_DIR / DSH_HOME+DSH_PROFILE）"
                             "—— 源码树直跑，行为退回静态配置"}

    目录 = Path(宿["profile_dir"])
    if not 目录.is_dir():
        return {"ok": False, "来源": str(目录), "渠道": [],
                "没找到的原因": f"DSH profile 目录不存在：{目录}"}

    用过的 = ""
    渠道: list[dict] = []
    for 名 in _PROFILE_FILES:
        f = 目录 / 名
        if not f.is_file():
            continue
        档 = _读档(f)
        节点 = _深找节点(档, _LLM_NODE_ID)
        if not 节点:
            continue
        prov = ((节点.get("config") or {}).get("providers") or {})
        if not isinstance(prov, dict) or not prov:
            continue
        用过的 = str(f)
        for pname, pcfg in prov.items():
            if not isinstance(pcfg, dict):
                continue
            基址 = str(pcfg.get("baseURL") or pcfg.get("base_url") or "").strip()
            键env = str(pcfg.get("apiKeyEnv") or pcfg.get("key_env") or "").strip()
            if not 基址:
                continue          # 没有基址的 provider 在本仓这里用不了，如实跳过
            渠道.append({
                "name": pname,
                "display_name": pcfg.get("displayName") or pname,
                "base_url": 基址,
                "key_env": 键env,
                "models": [
                    {"id": m["id"],
                     "context_size": int(m.get("contextWindow")
                                          or m.get("context_size") or 128_000),
                     "max_tokens": int(m.get("maxTokens")
                                       or m.get("max_tokens") or 8192)}
                    for m in _模型(pcfg)
                ],
            })
        if 渠道:
            break

    if not 渠道:
        return {"ok": False, "来源": str(目录), "渠道": [],
                "没找到的原因": f"{目录} 下没找到 `{_LLM_NODE_ID}` 的 providers 声明"
                             "（DSH 配置结构可能变了；这不是'没有渠道'）"}

    return {"ok": True, "来源": 用过的, "渠道": 渠道, "没找到的原因": ""}


def 并入(已有: list) -> list:
    """把宿主发现的渠道**并进**已有通道列表。

    ★ 排序口径（这条是重点）：**同名以宿主为准。**
      DSH 是活的、当前生效的；本仓 `channels.yml` 是可能过期的快照。
      快照比活配置权威，正是这次要修的病 —— 所以必须反过来。
      已有的、DSH 没有的通道**保留**（本仓可能显式配了 DSH 里没声明的），
      但同名时 DSH 覆盖。
    """
    try:
        from . import gateway as _gw
    except Exception:                              # noqa: BLE001
        return 已有

    发现 = 发现渠道()
    if not 发现.get("ok"):
        return 已有

    宿主: list = []
    for c in 发现["渠道"]:
        try:
            宿主.append(_gw.Channel(
                name=c["name"],
                display_name=c["display_name"],
                base_url=c["base_url"],
                key_env=c["key_env"],
                models=[_gw.ModelSpec(**m) for m in c["models"]],
            ))
        except Exception:                          # noqa: BLE001
            continue

    宿主名 = {c.name for c in 宿主}
    # 同名以宿主为准 ⇒ 先把非同名老通道留下，再拼宿主
    return [c for c in 已有 if c.name not in 宿主名] + 宿主
