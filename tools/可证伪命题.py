# -*- coding: utf-8 -*-
"""可证伪命题登记与审判（P6）：命题必须先**预注册**，判据必须**第三方**，**阴性结果必须入账**。

## 为什么需要它（P6 原状的问题）

仓里已经有两臂基准（`tools\\bench_ab.py` / `tools\\kb_ab.py` / `tools\\bench_kb.py`），
它们能跑出"两臂差多少"。但**跑出读数不等于做成了科学**，原来缺三样东西：

1. **预注册**：命题、样本数、判据门、以及**「什么结果算把它否掉」**，必须在跑之前写死。
   否则跑完再挑一个好看的说法解释结果 —— 那不是验证，是事后编故事。
2. **第三方判据**：判成败的门**不许由任何一臂产生**。两臂各自给自己发合格证时，
   比较的是"谁的尺子松"，不是"谁做得好"。
3. **阴性结果必入账**：命题被否的结果**也必须写进账**。只留阳性 = 幸存者偏差，
   而且下一次还会拿同一个说法去骗人。

## 判据（本工具只判这四件事，全部可回放）

- **A 可证伪性**：预注册必须同时给出 `命题` 与 `若被否`（判据说：出现什么读数就算否）。
  缺 `若被否` ⇒ **拒注册**，理由是"这句话不可能被否，因此不是命题"。
- **B 第三方性**：`判据门` 的每一条命令，**不得**出现在任何一臂的 `cmd` 里
  （也不得是臂脚本的子串反向包含）。违反 ⇒ 拒注册。
- **C 不许倒签**：结果文件的 `产生时间` 早于预注册的 `注册时间` ⇒ 拒判（那是事后编的）。
  预注册文件内容与其中记录的 `预注册哈希` 不符 ⇒ 拒判（**注册后被改过**）。
- **D 阴性必入账**：判完**无论方向**都要写账；自检会故意喂一个阴性结果，
  并断言它**真的出现在账里**（不是被过滤掉的）。

## 用法

    python tools\\可证伪命题.py              # 自检（默认）：把 A–D 四条判据各跑一遍
    python tools\\可证伪命题.py --判 预注册.json 结果.json

## 诚实边界

· 本工具**不判定命题真假**，它判定的是"这次比较**算不算一次可证伪的检验**"。
  命题本身的真值仍要由第三方判据门的读数说话。
· 不自带模型调用：两臂怎么跑由 `bench_ab.py` / `kb_ab.py` 负责，本工具只做纪律与账。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_包根 = pathlib.Path(__file__).resolve().parents[1]
_账 = _包根 / "work" / "可证伪命题账.jsonl"
_必填 = ("编号", "命题", "若被否", "样本数", "臂们", "判据门", "注册时间")


def _哈希(对: dict) -> str:
    """预注册内容的稳定哈希（不含 `预注册哈希` 自身）。"""
    d = {k: v for k, v in 对.items() if k != "预注册哈希"}
    return hashlib.sha256(
        json.dumps(d, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _规范化(cmd) -> str:
    """命令规范化成可比较的字符串（空格归一、小写、去引号差异）。"""
    if isinstance(cmd, (list, tuple)):
        s = " ".join(str(x) for x in cmd)
    else:
        s = str(cmd or "")
    return " ".join(s.replace("\\", "/").split()).lower()


def 检预注册(预) -> list[str]:
    """返回**拒注册**的理由列表（空 = 可以注册）。"""
    失: list[str] = []
    for k in _必填:
        if k not in 预 or 预.get(k) in (None, "", [], {}):
            失.append(f"缺字段「{k}」")
    if 失:
        return 失
    # A 可证伪性
    if len(str(预.get("若被否", "")).strip()) < 6:
        失.append("「若被否」太短/空 ⇒ 这句话不可能被否，因此不是命题")
    臂们 = 预.get("臂们") or []
    if len(臂们) < 2:
        失.append(f"臂数 {len(臂们)} < 2 ⇒ 没有对照，无从否证")
    for b in 臂们:
        if not (isinstance(b, dict) and b.get("名") and b.get("cmd")):
            失.append(f"臂定义不完整：{b!r}")
    # B 第三方性
    臂命令 = [_规范化(b.get("cmd")) for b in 臂们 if isinstance(b, dict)]
    for g in 预.get("判据门") or []:
        if not isinstance(g, dict) or not g.get("cmd"):
            失.append(f"判据门定义不完整：{g!r}")
            continue
        门命令 = _规范化(g.get("cmd"))
        for 名, 臂命令文 in zip((b.get("名") for b in 臂们 if isinstance(b, dict)), 臂命令):
            if not 门命令 or not 臂命令文:
                continue
            if 门命令 == 臂命令文 or 门命令 in 臂命令文 or 臂命令文 in 门命令:
                失.append(
                    f"判据门「{g.get('名') or g.get('type')}」与臂「{名}」的命令重合/互相包含 "
                    f"⇒ 不是第三方判据（自己给自己发合格证）"
                )
    if not (预.get("判据门") or []):
        失.append("没有判据门 ⇒ 结果无法机器判")
    if int(预.get("样本数") or 0) < 1:
        失.append("样本数 < 1")
    return 失


def 检账本(预, 结果) -> list[str]:
    """返回**拒判**的理由列表（空 = 可以判）。"""
    失: list[str] = []
    记 = str(预.get("预注册哈希") or "")
    if not 记:
        失.append("预注册里没有「预注册哈希」⇒ 无法确认注册后没被改")
    elif 记 != _哈希(预):
        失.append(f"预注册哈希不符（记 {记} ≠ 现 {_哈希(预)}）⇒ **注册后被改过**")
    t注 = str(预.get("注册时间") or "")
    t果 = str(结果.get("产生时间") or "")
    if not t果:
        失.append("结果没有「产生时间」⇒ 无法确认没倒签")
    elif t注 and t果 < t注:
        失.append(f"结果时间 {t果} 早于注册时间 {t注} ⇒ **倒签**，事后编的")
    for k in ("臂读数",):
        if k not in 结果:
            失.append(f"结果缺「{k}」")
    return 失


def 判向(预, 结果) -> dict:
    """按预注册的判据门把结果**判成一个方向**：成立 / 被否 / 未验。

    ★ 先看结果自己有没有声明**作废**：仪器（如 `tools\\kb_ab.py`）若判定
      「两臂不是只差一个变量」，那次比较**不构成一次可证伪的检验** ——
      既不能判成立（拿被污染的比较当证据），也不能判被否（那是在说"外挂知识没用"，
      而这次根本没测到干净的两臂）。按口径 545 记**未验**，把仪器的原话带进账。
    """
    if 结果.get("作废"):
        return {"方向": "未验", "理由": f"仪器自判作废：{结果.get('作废理由') or '未给出理由'}"}
    门们 = 预.get("判据门") or []
    读 = 结果.get("臂读数") or {}
    缺 = [g.get("名") or g.get("type") for g in 门们
          if any(a not in (读.get(b, {}) or {}) for a in ("A", "B") for b in [g.get("臂键") or "臂读数"])]
    # 简化且诚实的判法：只认预注册里写死的「若被否」读数条件
    条 = 预.get("若被否条") or {}
    阈值 = 条.get("阈值")
    方向 = 条.get("方向", "B_不小于_A")
    甲 = 读.get("A")
    乙 = 读.get("B")
    if 阈值 is None or 甲 is None or 乙 is None:
        return {"方向": "未验", "理由": "缺阈值或臂读数 ⇒ 按口径 545 记未验，不记成立"}
    if 方向 == "B_不小于_A":
        成立 = 乙 >= 甲 + float(阈值)
    else:
        成立 = 乙 <= 甲 - float(阈值)
    return {
        "方向": "成立" if 成立 else "被否",
        "A": 甲, "B": 乙, "阈值": 阈值, "规则": 方向,
        "理由": ("B 达到预注册的正向阈值" if 成立 else "B 未达预注册的正向阈值 ⇒ 命题被否"),
    }


def 入账(条: dict) -> pathlib.Path:
    路径 = 条.get("账本") or str(_账)
    p = pathlib.Path(路径)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(条, ensure_ascii=False) + "\n")
    return p


def _造合成(阴性: bool, 账本: str) -> tuple[dict, dict]:
    """造一对**合成**预注册/结果（只用于自检判据逻辑，不进真账）。"""
    t = "2026-11-18T00:00:00"
    cmd = [sys.executable, "-c", "print(1)"]
    预 = {
        "编号": "自检-001",
        "命题": "有机制的一臂比无机制的一臂更容易过第三方门",
        "若被否": "B 臂过门数不高于 A 臂（差值 ≤ 0）⇒ 命题被否",
        "若被否条": {"方向": "B_不小于_A", "阈值": 1},
        "样本数": 3,
        "臂们": [
            {"名": "A_无机制", "cmd": [sys.executable, "-c", "print('a')"]},
            {"名": "B_有机制", "cmd": [sys.executable, "-c", "print('b')"]},
        ],
        # ★ 第三方门：命令与两臂都不同（自检里就是要它不重合）
        "判据门": [{"名": "第三方门", "type": "shell_ok", "cmd": [sys.executable, "-c", "print('gate')"]}],
        "注册时间": t,
    }
    预["预注册哈希"] = _哈希(预)
    结果 = {
        "编号": "自检-001",
        "产生时间": t,
        "臂读数": {"A": 0, "B": 0 if 阴性 else 3},
        "账本": 账本,
    }
    return 预, 结果


def 自检() -> int:
    失: list[str] = []
    临时 = _包根 / "work" / "可证伪命题自检账.jsonl"
    if 临时.exists():
        临时.unlink()
    账 = str(临时)

    # ── A 可证伪性：缺「若被否」必须拒 ──
    预0, _r = _造合成(False, 账)
    预0.pop("若被否")
    理由 = 检预注册(预0)
    拒A = any("若被否" in x for x in 理由)
    print(f"A 不可证伪的命题被拒 = {拒A} · 理由 = {理由[:1]}")

    # ── B 第三方性：判据门与一臂同命令必须拒 ──
    预1, _r = _造合成(False, 账)
    预1["判据门"] = [{"名": "伪第三方", "type": "shell_ok",
                     "cmd": [sys.executable, "-c", "print('a')"]}]
    预1["预注册哈希"] = _哈希(预1)
    理由1 = 检预注册(预1)
    拒B = any("第三方" in x for x in 理由1)
    print(f"B 自己给自己发合格证被拒 = {拒B} · 理由 = {理由1[:1]}")

    # ── 正常预注册必须能过 ──
    预2, 结果阳 = _造合成(False, 账)
    理由2 = 检预注册(预2)
    print(f"  合法预注册通过 = {not 理由2} {理由2[:1]}")

    # ── C 不许倒签 / 不许改 ──
    改后 = dict(预2)
    改后["样本数"] = 99
    拒C1 = any("哈希" in x for x in 检账本(改后, 结果阳))
    结果早 = dict(结果阳)
    结果早["产生时间"] = "2026-01-01T00:00:00"
    拒C2 = any("倒签" in x for x in 检账本(预2, 结果早))
    print(f"C 注册后改过被拒 = {拒C1} · 结果早于注册被拒 = {拒C2}")

    # ── D 阴性必入账：喂一个阴性结果，判「被否」，并且**真的写进账** ──
    _预3, 结果阴 = _造合成(True, 账)
    判阳 = 判向(预2, 结果阳)
    判阴 = 判向(预2, 结果阴)
    print(f"D 阳性判向 = {判阳['方向']}（A={判阳.get('A')} B={判阳.get('B')} 阈值={判阳.get('阈值')}）")
    print(f"  阴性判向 = {判阴['方向']}（A={判阴.get('A')} B={判阴.get('B')}）")
    for 条预, 条果, 向 in ((预2, 结果阳, 判阳), (预2, 结果阴, 判阴)):
        入账({"编号": 条预["编号"], "命题": 条预["命题"], "方向": 向["方向"],
              "证据": 向, "注册时间": 条预["注册时间"],
              "结果时间": 条果["产生时间"], "预注册哈希": 条预["预注册哈希"],
              "账本": 账})
    行们 = [json.loads(x) for x in 临时.read_text(encoding="utf-8").splitlines() if x.strip()]
    方向们 = [x.get("方向") for x in 行们]
    阴入账 = "被否" in 方向们
    阳入账 = "成立" in 方向们
    print(f"  账本行数 = {len(行们)} · 阳性入账 = {阳入账} · **阴性入账 = {阴入账}**")

    # ── 断言 ──
    if not 拒A:
        失.append("A：缺「若被否」的预注册没有被拒（不可证伪的命题混进了登记）")
    if not 拒B:
        失.append("B：判据门与一臂同命令却没有被拒（自证合格）")
    if 理由2:
        失.append(f"合法预注册被误拒：{理由2[:2]}")
    if not 拒C1:
        失.append("C：预注册被改过却没抓到（哈希没起作用）")
    if not 拒C2:
        失.append("C：结果早于注册时间却没抓到（倒签）")
    if 判阳["方向"] != "成立":
        失.append(f"D：阳性结果未判为成立（实得 {判阳['方向']}）")
    # ── 作废 ⇒ 未验（仪器自己说"两臂不独立"时，不许判成立/被否）──
    判废 = 判向(预2, {"产生时间": 结果阳["产生时间"], "臂读数": {"A": 0, "B": 3},
                     "作废": True, "作废理由": "两臂经验环不一致"})
    废对 = 判废["方向"] == "未验" and "作废" in 判废.get("理由", "")
    print(f"  仪器自判作废 ⇒ {判废['方向']}（必须=未验）· {判废.get('理由', '')[:60]}")
    if not 废对:
        失.append(f"D2：作废结果被误判成 {判废['方向']}（应记未验）")

    if 判阴["方向"] != "被否":
        失.append(f"D：阴性结果未判为被否（实得 {判阴['方向']}）—— 说明判据只会点头")
    if not 阴入账:
        失.append("D：**阴性结果没有入账**（幸存者偏差）")
    if 临时.exists():
        临时.unlink()

    # ── E 真注册也必须过纪律：仓里**每一个**已注册命题都要能通过 检预注册 ──
    册目录 = _包根 / "work" / "可证伪命题"
    # ★ 只认"注册册"：结果文件（文件名含「结果」）是**读数**不是任务书，
    #   不许它被要求含「命题」「若被否」（2026-11-19 实测：两条 P6-001_结果_*.json
    #   被 E 检查判成"不合纪律"，把一条好判据打成红 —— 判据自己搞错了对象）。
    册们 = sorted(p for p in 册目录.glob("*.json") if "结果" not in p.name) if 册目录.is_dir() else []
    合法 = 0
    for p in 册们:
        try:
            预 = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:                      # noqa: BLE001
            失.append(f"预注册文件读不了：{p.name}：{e}")
            continue
        r = 检预注册(预)
        if r:
            失.append(f"已注册命题 {p.name} 不合纪律：{r[:2]}")
        else:
            合法 += 1
    print(f"E 已注册预注册 {len(册们)} 条 · 合法 {合法}/{len(册们)}")

    if 失:
        for x in 失:
            print(f"✗ {x}")
        print("判定=不达标")
        return 1
    print("判定=OK")
    print("（本工具只判「这次比较算不算一次可证伪的检验」；命题真假由第三方判据门的读数说话）")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[1] == "--判":
        预 = json.loads(pathlib.Path(argv[2]).read_text(encoding="utf-8"))
        果 = json.loads(pathlib.Path(argv[3]).read_text(encoding="utf-8"))
        理由 = 检预注册(预) + 检账本(预, 果)
        if 理由:
            for x in 理由:
                print(f"✗ {x}")
            print("判定=不达标（拒判）")
            return 1
        向 = 判向(预, 果)
        p = 入账({"编号": 预.get("编号"), "命题": 预.get("命题"), "方向": 向["方向"],
                  "证据": 向, "注册时间": 预.get("注册时间"),
                  "结果时间": 果.get("产生时间"), "预注册哈希": 预.get("预注册哈希")})
        print(f"判向 = {向['方向']} · 账本 = {p}")
        print("判定=OK")
        return 0
    return 自检()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
