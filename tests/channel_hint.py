"""判据：查不到通道时，必须把「**凭据里有、配置里没引用**」的键交出来。

钉的真事故（2026-11-05，我自己踩的）：
`qww` 的 key 一直在 `~/.dsh/.credentials.yaml` 里，而 `channels.yml` 里**没有**这个通道。
产品如实报「无可用通道」⇒ 我据此写下「四条通道全 dead」这种错结论。
真相是：**配置看不见它**，不是世界没给。

口径：**「配置里没有」≠「环境里没有」。**
查不到通道是"最需要指路"的时刻，此时只回一句"无可用通道"等于让人去猜。

本判据验五件事（全部离线，不碰网络）：
  ① 凭据**扁平布局**里未被引用的键要报出来；
  ② 凭据 **refs 布局**（DSH v1）里未被引用的键也要报出来；
  ③ 已被通道 `key_env` 引用的键**不许**报（否则是噪音）；
  ④ 环境变量里已有的键**不许**报（它已经在环境里，不是"配置漏了"）；
  ⑤ 没有未用键时**不许编提示**（宁可不说，不许假指路）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentscope_orchestrator import gateway as G  # noqa: E402

坏: list[str] = []


def _环境(凭据: dict) -> tuple[Path, object, object]:
    根 = Path(tempfile.mkdtemp(prefix="chanhint_"))
    凭据文件 = 根 / ".credentials.yaml"
    凭据文件.write_text(yaml.safe_dump(凭据, allow_unicode=True), encoding="utf-8")
    旧凭据 = G._DSH_CREDENTIALS
    旧装载 = G.load_channels
    G._DSH_CREDENTIALS = 凭据文件
    G.load_channels = lambda *a, **k: [
        G.Channel(name="在用通道", display_name="在用通道",
                  base_url="https://example.invalid/v1", key_env="USED_API_KEY")]
    return 凭据文件, 旧凭据, 旧装载


def _还原(旧: tuple) -> None:
    G._DSH_CREDENTIALS, G.load_channels = 旧[1], 旧[2]


def main() -> int:
    for k in list(os.environ):
        if k.endswith("_API_KEY"):
            os.environ.pop(k, None)                      # 环境变量会污染④，先清

    # ① 扁平布局
    旧 = _环境({"USED_API_KEY": "sk-1", "QQW_API_KEY": "sk-2", "FD_API_KEY": "sk-3"})
    try:
        键 = [x["键"] for x in G.凭据里的没用上的键()]
        print(f"    ① 扁平布局未引用键：{键}")
        if "QQW_API_KEY" not in 键 or "FD_API_KEY" not in 键:
            坏.append(f"扁平布局漏报：{键}")
        if "USED_API_KEY" in 键:
            坏.append(f"在用的键被当成未引用（噪音）：{键}")
    finally:
        _还原(旧)

    # ② refs 布局（DSH v1）
    旧 = _环境({"version": 1, "refs": {"REFS_API_KEY": "sk-9"}, "records": {}})
    try:
        键 = [x["键"] for x in G.凭据里的没用上的键()]
        print(f"    ② refs 布局未引用键：{键}")
        if "REFS_API_KEY" not in 键:
            坏.append(f"refs 布局漏报：{键}")
    finally:
        _还原(旧)

    # ④ 环境里已有的键不算"配置漏了"
    旧 = _环境({"ENVONLY_API_KEY": "sk-4"})
    os.environ["ENVONLY_API_KEY"] = "sk-4"
    try:
        键 = [x["键"] for x in G.凭据里的没用上的键()]
        print(f"    ④ 环境已有键：{键}（应不含 ENVONLY_API_KEY）")
        if "ENVONLY_API_KEY" in 键:
            坏.append("环境里已存在的键被报成未引用 ⇒ 假线索")
    finally:
        os.environ.pop("ENVONLY_API_KEY", None)
        _还原(旧)

    # ⑤ 没有未用键 ⇒ 不许编提示
    旧 = _环境({"USED_API_KEY": "sk-1"})
    try:
        报: dict = {}
        G._补线索(报)
        print(f"    ⑤ 无未用键时的提示：{报.get('提示')!r}")
        if 报.get("提示") or 报.get("未用凭据"):
            坏.append("没有未用键却编了提示 ⇒ 假指路")
    finally:
        _还原(旧)

    # ⑤b 有未用键 ⇒ 提示必须点名那个键、并说清口径
    旧 = _环境({"LOST_API_KEY": "sk-7"})
    try:
        报 = {}
        G._补线索(报)
        提示 = 报.get("提示") or ""
        print(f"    ⑤b 提示片段：{提示.splitlines()[1][:80] if len(提示.splitlines()) > 1 else 提示[:80]}")
        if "LOST_API_KEY" not in 提示:
            坏.append("提示里没点名具体键 ⇒ 无法照着修")
        if "配置里没有" not in 提示:
            坏.append("提示里没写「配置里没有 ≠ 环境里没有」这条口径")
        if "401" not in 提示 and "过期" not in 提示:
            坏.append("提示没提醒「可能是过期 key」⇒ 会误导人去加一条死通道")
    finally:
        _还原(旧)

    # ⑥ 端到端：`ORCH_CHANNELS_FILE` 指向空配置 ⇒ 真装载成 0 条，且提示要点名键
    #    （这条同时钉住 load_channels 的默认值必须**调用时**解析 ——
    #     导入时定死的话，这里仍会读到仓库那份 5 条 ⇒ 红）
    空配置 = Path(tempfile.mkdtemp(prefix="chanhint_empty_")) / "channels.yml"
    空配置.write_text("channels: {}\n", encoding="utf-8")
    旧env = os.environ.get("ORCH_CHANNELS_FILE")
    os.environ["ORCH_CHANNELS_FILE"] = str(空配置)
    try:
        装载 = G.select_channels()
        通道, 报 = G.select_live_channels("", probe=False)
        提示 = 报.get("提示") or ""
        print(f"    ⑥ 空配置：装载 {len(装载)} 条 · 选中 {len(通道)} 条 · 提示首行="
              f"{提示.splitlines()[0] if 提示 else '（无）'}")
        if 装载 or 通道:
            坏.append(f"ORCH_CHANNELS_FILE 没生效：空配置仍装载 {len(装载)} 条")
        if not 提示:
            坏.append("空配置时没有给提示 ⇒ 又回到「无可用通道」四个字")
        elif "环境里没有" not in 提示:
            坏.append("空配置提示没写清「配置里没有 ≠ 环境里没有」")
    finally:
        if 旧env is None:
            os.environ.pop("ORCH_CHANNELS_FILE", None)
        else:
            os.environ["ORCH_CHANNELS_FILE"] = 旧env

    if 坏:
        print("通道线索：红 —— " + "；".join(坏))
        return 1
    print("通道线索：绿 —— **凭据里有但没配的键会被点名（两种布局）、在用的不噪音、"
          "环境已有的不算、没有就不编。**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())