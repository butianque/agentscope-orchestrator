"""跨任务经验环 —— 只收**过门验证过的修法**，按**非退化情境指纹**检索。

★ 为什么这两条是全部难点（不是设计偏好，是 示例工程 自己的审计结论）
  · 回执067：记忆没起作用，根因是「情境深度 = 恒等」—— 情境码退化成常量，
    任何两条经验都"同样相似"⇒ 检索恒命中或恒不命中。所以本模块的指纹必须**可分**：
    失败门 + 语言 + 错误词集合（Jaccard）+ 改动文件后缀，四项都不许是常量。
  · G-044：兜底的危害在记忆 —— **猜的被记成知道的**。所以写入只有一条通道：
    `记()` 只在**机器判据门绿**时被调用（由 fixloop 保证），没有任何"推测写入"。

★ 边界
  管：把"某类失败 → 某类修法"的**已证事实**存下来，下次同类失败时作为提示注入。
  不管：不保证照抄能过（门会再判一次）、不做遗忘曲线、不跨机同步。

★ 存储
  JSONL（追加写、可人读、坏行跳过），默认 `<ORCH_PROFILE 所在目录>/.orchestrator/experience.jsonl`。
  这样可以换成 示例工程 `结构记忆网络`（src/模块甲/结构记忆网络.rs）的存储后端，
  只要保持 `记/取/统计` 三个函数签名不变。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

# 错误词里剔掉的高频噪声（否则 Jaccard 被"python/error/test"这类词拉满 ⇒ 指纹退化）
_噪声 = {
    "python", "python3", "error", "errors", "test", "tests", "testing", "assert",
    "assertion", "traceback", "most", "recent", "call", "last", "file", "line",
    "self", "unittest", "pytest", "failed", "failure", "ok", "ran", "module",
    "import", "the", "and", "for", "not", "with", "from", "this", "that", "none",
    "true", "false", "expected", "actual", "exit", "code", "stdout", "stderr",
    "cmd", "gate", "cause", "green", "red", "ok.", "skipped", "passed",
}


def 经验文件() -> Path:
    """经验库路径：跟着 profile 走；没 profile 就落到 cwd/.orchestrator。"""
    p = os.environ.get("ORCH_PROFILE") or ""
    if p:
        return Path(p).expanduser().resolve().parent / ".orchestrator" / "experience.jsonl"
    return Path.cwd() / ".orchestrator" / "experience.jsonl"


def _语言(命令: list) -> str:
    文 = " ".join(str(x).lower() for x in (命令 or [])[:4])
    if any(k in 文 for k in ("cargo", "rustc")):
        return "rust"
    if any(k in 文 for k in ("go test", "go build", "go run")):
        return "go"
    if any(k in 文 for k in ("node", "npm", "pnpm", "yarn", "jest", "vitest", "deno", "bun")):
        return "js"
    if "lean" in 文:
        return "lean"
    return "python"


def _错误词(证据: str) -> list[str]:
    """从红门证据抽"能区分这类失败"的词：标识符 + 异常名，去掉路径/数字/噪声。

    ★ 只保留 length>=4 的标识符，避免 `a/b/c/x` 这种单字母把指纹拉成恒等。
    """
    词 = re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", 证据 or "")
    计: dict[str, int] = {}
    for w in 词:
        低 = w.lower()
        if 低 in _噪声 or 低.startswith("test") or 低.isdigit():
            continue
        计[低] = 计.get(低, 0) + 1
    return sorted(计, key=lambda k: (-计[k], k))[:10]


# ★★★★★【回执217 · 门B 第三处根因 —— 黑名单把**答案**当垃圾滤掉】
#
# 【前两处已修，失败门仍恒「未知」】
#   第一处：`\w` 含中文 ⇒ 门名剥离正则把「通过不足」自己吃掉。
#   第二处（本处，**致命**）：`_门修饰` 黑名单里明摆着写着
#       `("门", "gate", "✗", "×", "通过不足", "退出码不符")`
#     ⇒ 剥完得到的**正确答案**「通过不足」又被 `any(段 == x ...)` 判为修饰符
#     ⇒ `if 段 and not any(...)` 为假 ⇒ 丢弃 ⇒ 返回「未知」。
#   ⇒ 这不是「解析不到」，是**解析到了又把结果扔掉**。
#
# 【为什么之前没人发现】它不报错、不崩溃、恒返回「未知」；
#   而 `_相似()` 里 `门 == "未知" ⇒ 0.0` ⇒ `取()` 恒空 ⇒ 12 条经验**从未被检索**。
#   空转报绿，最难发现的那种坏。
#
# 【修法】黑名单只保留**纯符号**（它们确实只是装饰）。
#   「通过不足」「退出码不符」是门**种类**，必须作为返回值保留。
_门修饰 = ("✗", "×", "✓", "·", "-", "—")

# ★★★★★【回执217 · 门B 挖出的根因 —— `_失败门()` 恒返回「未知」，指纹退化】
#
# 【实测】真实证据首行 `[test_counts] 通过不足|passed=0 failed=2 skipped=0`
#   ⇒ 解析结果 = **「未知」**，而库里 12 条经验**全部**是「未知」。
#
# 【为什么】原正则把「门名修饰」和「缺括号」写进**同一个**可选组：
#     `^[\[【(（]?[\w.\-\u4e00-\u9fff]{1,24}[\]】)）]?\s*`
#   而 `\w` 在 Python3 里**包含中文**（Unicode 词字符）⇒ `通过不足` 自己就是 `\w`。
#   ⇒ `[test_counts] 通过不足` 被解释成「`[` + 门名`test_counts` + 半角`]`」后，
#      再让后面那半去吃 `通过不足`（因为整组是可选的、贪心）⇒ 剥完是**空串**。
#   剥空前还有 `or 段` 兜底，但那是「剥空了就还原」，这里剥出了 `' '`→`.strip()`→空，
#   于是 `if 段` 为假 ⇒ **跳过唯一那段，返回「未知」**。
#
# 【后果，比 bug 更严重】`_相似()` 里 `门A == "未知" ⇒ return 0.0`
#   ⇒ 库里**任何两条经验相似度恒为 0** ⇒ `取()` **永远返回空**
#   ⇒ 经验环没接线：12 条已过门经验**从未被检索过**。
#   而它报的是「无命中」——**绿色的空转**，最难发现的那种坏。
#
# 【修法】门名与门名以外的中文**必须分开**：门名段只许 ASCII（`\w` 收紧成
#   `[A-Za-z0-9_.\-]`），修饰符单独剥。这样 `[test_counts] 通过不足` ⇒ `通过不足`，
#   而 `[gate:test_counts] 退出码不符` ⇒ `退出码不符`。
_门名段 = re.compile(r"^[\[【(（]?[A-Za-z0-9_.\-]{1,24}[\]】)）]?\s*")


def _失败门(证据: str) -> str:
    """从红门证据里取"失败种类"（`通过不足` / `退出码不符` / `无法判定` / `有失败`）。

    ★ 退化就是致命（示例工程 回执067：情境深度=恒等 ⇒ 记忆永不命中或恒命中）。
      第一版只认 `cause=` 前缀 ⇒ 实际证据是 `[test_counts] 通过不足|passed=0…`
      ⇒ 全部解析成「未知」，权重 0.5 的那一维**变常量**，指纹退化。
      现改为：任意位置找 `xxx|` 片段，剥掉门名/符号修饰后再归一。

    ★ 第二版（本处）修的是同一个病的**更深一层**：门名剥离正则把中文也当成门名
      的一部分（`\\w` 含中文），把「通过不足」自己吃掉了 ⇒ 仍然恒「未知」。
      ⇒ **两版都是「看起来在解析、结果恒为常量」**。这类 bug 的特征是：
        它的输出**永远不报错**，只是把判据悄悄变成空转。
    """
    文 = (证据 or "")
    for m in re.finditer(r"([^|·\n]{1,24})\|", 文):
        段 = m.group(1).strip()
        # 只剥门名段（纯 ASCII 标识符 + 可选括号），不碰中文修饰
        段 = _门名段.sub("", 段) or 段
        段 = 段.lstrip("✗×✓·-— ").strip()
        段 = _门名段.sub("", 段) or 段
        段 = 段.lstrip("✗×✓·-— ").strip()
        if 段 and not any(段 == x for x in _门修饰):
            return 段[:24]
    return "未知"


_异常式 = re.compile(r"\b([A-Z][A-Za-z]{2,24}(?:Error|Exception|Failure))\b")


def _异常类型(证据: str) -> str:
    """异常/失败的种类：`AssertionError` / `NameError` / `KeyError` / `TestFailure` …

    ★ 为什么必须单列：`不是过（rc=1）` 这类"失败门"在**所有**红门上都一样，
      拿它当主判据等于情境恒等（回执067）。异常类型才是真正能区分"错误性质"的那一维。
    """
    m = _异常式.search(证据 or "")
    if m:
        return m.group(1)
    文 = (证据 or "").lower()
    for k, 名 in (("assert", "AssertionError"), ("nameerror", "NameError"),
                  ("importerror", "ImportError"), ("modulenotfound", "ImportError"),
                  ("typeerror", "TypeError"), ("keyerror", "KeyError"),
                  ("indexerror", "IndexError"), ("attributeerror", "AttributeError"),
                  ("syntaxerror", "SyntaxError"), ("timeout", "Timeout"),
                  ("error[", "CompileError"), ("error:", "CompileError")):
        if k in 文:
            return 名
    return "未知"


def 指纹(命令: list, 证据: str, 改动路径们=()) -> dict:
    后缀 = sorted({Path(str(p)).suffix.lower() for p in (改动路径们 or []) if str(p)})
    return {"语言": _语言(命令), "失败门": _失败门(证据),
            "异常": _异常类型(证据), "错误词": _错误词(证据), "后缀": 后缀}


def _相似(a: dict, b: dict) -> float:
    """分档而不是加权求平均：**错误性质不同的经验不许注入**。

    ★ 实测反例（2026-10-31）：加权版把"断言差一"的经验注入到"漏 import"任务上
      （同语言 + 通用测试词 ⇒ 0.55 刚过线）——那是**错误经验的污染**，比没有更坏。
      现改为：失败门不同 ⇒ 直接 0；然后同语言 + 错误词重合度 + 后缀。
    """
    门A, 门B = a.get("失败门") or "未知", b.get("失败门") or "未知"
    异A, 异B = a.get("异常") or "未知", b.get("异常") or "未知"
    if 异A != 异B:
        return 0.0                      # 错误性质不同（断言 vs 缺失名）⇒ 经验不通用
    if 门A != 门B or 门A == "未知":
        return 0.0
    分 = 0.45
    if a.get("语言") == b.get("语言"):
        分 += 0.15
    A, B = set(a.get("错误词") or []), set(b.get("错误词") or [])
    重 = (len(A & B) / len(A | B)) if (A and B) else 0.0
    分 += 0.30 * 重
    if a.get("后缀") and a.get("后缀") == b.get("后缀"):
        分 += 0.10
    return 分 if 重 >= 0.15 else 0.0


def _读() -> list[dict]:
    文 = 经验文件()
    if not 文.is_file():
        return []
    出 = []
    for 行 in 文.read_text(encoding="utf-8", errors="replace").splitlines():
        行 = 行.strip()
        if not 行:
            continue
        try:
            条 = json.loads(行)
        except Exception:                       # noqa: BLE001 坏行跳过，不因一行坏掉整库
            continue
        if isinstance(条, dict) and 条.get("指纹"):
            出.append(条)
    return 出


def 已验(条: dict) -> bool:
    """**条目的可检索性判据** —— 必须真的过了机器门，否则不算「已验」。

    # 门 B（未验条目查询不可见）的落点
    写入通道有两条：`记()`（门绿）与「未提供门」（推测）。`验证` 字段是唯一痕迹：
      · `"门:test_counts"` / `"门:a,b"` —— 有门名 ⇒ **已验**
      · `"门:（未提供）"` / `""` / 缺字段 —— ⇒ **未验**
    ⇒ 未验条目 `取()` **不返回**。不是降权、不是排后面、不是标「仅供参考」——
      **不可见**。理由：`G-044` 说「猜的被记成知道的」；
      一个「标了仅供参考」的猜测仍然会被读成线索，而线索会决定下一步改哪里。
    """
    v = str(条.get("验证") or "").strip()
    if not v.startswith("门:"):
        return False
    内容 = v[len("门:"):].strip()
    return bool(内容) and "未提供" not in 内容 and 内容 not in ("门", "-", "?")


def 取(命令: list, 证据: str, 上限: int = 2, 阈值: float = 0.55) -> str:
    """检索同类已证经验，返回**可直接塞进提示**的短文本（≤700B）；无命中返回 ''。

    # 门 C（同一指纹二现 ⇒ 命中首现产物 + 门重跑）
    ① **首现优先**：同一指纹（逐字段相等）只保留**时间最早**的那一条。
       为什么是首现，不是「最新」也不是「都列出来」：
         · 首现是**第一次把这条路走通**的记录 —— 解最干净；
         · 后现是复述，会夹带当时环境的偶然细节（同一错误换种写法、换台机器）；
         · 「都列出来」让 `上限` 失效：同类失败在长期运行里会重复上百次，
           全列等于把提示挤爆，还把复述当成第二条印证 —— 那是**虚假的多票**。
       指纹相等必须**逐字段**比，不能用 `_相似()` 分数（0.90 的两条可能指纹不同，
       那是「相似」不是「同一」；只有「同一」才存在首现/后现关系）。
    ② **门重跑**：输出头部固定声明「必须自己验证」，且全篇不得出现
       「已通过 / 可直接用 / 照抄即可 / 无需验证」。
       机判：删掉该声明 ⇒ `tests/phase_learning_gates.py` 门C② 翻红。
    """
    我 = 指纹(命令, 证据)
    命中 = []
    已见指纹: dict[str, dict] = {}
    for 条 in _读():
        if not 已验(条):
            continue                       # 门B：未验条目不可见
        条指纹 = json.dumps(条.get("指纹") or {}, ensure_ascii=False, sort_keys=True)
        前现 = 已见指纹.get(条指纹)
        if 前现 is not None:
            # 门C①：同指纹二现 ⇒ 只留**时间最早**那条
            if str(条.get("时间") or "") < str(前现.get("时间") or ""):
                已见指纹[条指纹] = 条
            continue
        已见指纹[条指纹] = 条
    for 条 in 已见指纹.values():
        分 = _相似(我, 条.get("指纹") or {})
        if 分 >= 阈值:
            命中.append((分, 条))
    if not 命中:
        return ""
    命中.sort(key=lambda x: (-x[0], x[1].get("时间") or ""))
    行们 = ["【同类历史（**已过机器门验证**；可参考思路，但必须自己验证，不许照抄未验证结论）】"]
    for 分, 条 in 命中[:上限]:
        做法 = "；".join(f"{x.get('op')} {x.get('path')}" for x in (条.get("做法") or []))
        行们.append(f"· 相似度{分:.2f} 症状:{_截(条.get('症状'), 90)}"
                    f" ⇒ 根因:{_截(条.get('根因'), 90)} ⇒ 做法:{_截(做法, 120)}"
                    f"（验证:{_截(条.get('验证'), 60)}）")
    文 = "\n".join(行们)
    return 文 if len(文) <= 700 else 文[:697] + "…"


def _截(x, n: int) -> str:
    s = str(x or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def 记(命令: list, 证据: str, 模型说明: str, 已应用: list,
       门表: list | None = None) -> dict | None:
    """写入一条**已过门**的经验。调用方必须保证"门绿"（fixloop 的完成分支）。

    只存做法摘要（op + path + 模型说明），**不存文件内容**：内容会失败、会过期，
    存了也会被下次照抄成错误；存"改哪儿、为什么"才是可迁移的部分。
    """
    做法 = []
    for 项 in (已应用 or []):
        if isinstance(项, dict):
            做法.append({"op": 项.get("op"), "path": 项.get("path")})
        elif isinstance(项, str):
            做法.append({"op": "?", "path": 项})
    过门 = [x.get("门") for x in (门表 or []) if x.get("通过")]
    验证 = ("门:" + ",".join(str(x) for x in 过门)) if 过门 else "门:（未提供）"
    条 = {"时间": time.strftime("%Y-%m-%dT%H:%M:%S"),
          "指纹": 指纹(命令, 证据, [x.get("path") for x in 做法]),
          "症状": _截(证据, 240), "根因": _截(模型说明, 240),
          "做法": 做法[:20], "验证": 验证,
          "键": hashlib.sha1((json.dumps([命令, 做法], ensure_ascii=False)).encode()
                            ).hexdigest()[:12]}
    文 = 经验文件()
    文.parent.mkdir(parents=True, exist_ok=True)
    with 文.open("a", encoding="utf-8") as f:
        f.write(json.dumps(条, ensure_ascii=False) + "\n")
    return 条


def 统计() -> dict:
    """库的样子：**必须分开报已验 / 未验**。

    # 为什么不能只报「经验条数」
    只报总数会把推测算进「学到的东西」里 —— 那正是 `G-044`「猜的被记成知道的」。
    实测：12 条全是 `门:test_counts`（已验）；但只要有一条未验混进去而无区分，
    看数的人就会以为「12 条经验」，实际可检索的可能只有 11 条。
    ⇒ `已验条数` 才是**可被 `取()` 检索到**的条数；`未验条数` 是**不可见**的条数。
    """
    条们 = _读()
    已 = [x for x in 条们 if 已验(x)]
    未 = [x for x in 条们 if not 已验(x)]
    return {"经验条数": len(条们), "文件": str(经验文件()),
            "已验条数": len(已), "未验条数": len(未),
            "失败门分布": _计(已, "失败门"), "语言分布": _计(已, "语言")}


def _计(条们: list[dict], 键: str) -> dict:
    出: dict[str, int] = {}
    for 条 in 条们:
        值 = str((条.get("指纹") or {}).get(键) or "?")
        出[值] = 出.get(值, 0) + 1
    return 出
