"""判据：外置文档**按内容也查得到**（不只是按标题），且不越界读仓外文件。

钉的真事故（2026-11-05）：`search_docs` 只在 `title/kind/meta` 里找子串，
而归档文档的标题多是「任务列表(148条)」「xxx · w0」这种**不含内容**的名字
⇒ 按主题搜永远 0 命中。实测 `search_docs('通道')`、`('概念')`、`('回执')` 全空，
而索引里躺着 1256 条、1725 个文档、13.6 MB 正文。

口径：**「索引里有」不等于「查得到」。** 建了索引却只能按标题找，
等于把长期记忆锁在文件名上 —— 而人记的是内容。

本判据验四件事：
  ① 词只在**正文第 500 字**（不在摘录、不在标题）⇒ 仍能查到，且标 `命中位置=正文`；
  ② 词在**标题/摘录**里 ⇒ 走快路（`命中位置=元数据/摘录`），不扫正文；
  ③ 不存在的词 ⇒ 空（不许"凑几个最像的"）；
  ④ 索引里混进**仓外路径**的条目 ⇒ 扫正文时跳过（不许借索引读任意文件）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agentscope_orchestrator import doc_store as D  # noqa: E402

坏: list[str] = []


def main() -> int:
    根 = Path(tempfile.mkdtemp(prefix="docsearch_"))
    旧目录, 旧索引 = D.DOCS_DIR, D.INDEX
    D.DOCS_DIR = 根
    D.INDEX = 根 / "_index.jsonl"
    根.mkdir(parents=True, exist_ok=True)
    try:
        深词 = "漂流瓶里装着一张写有ZHENGTAN的纸条"          # 只在正文深处出现
        浅词 = "标题里就有的词"
        D.store_text("深", "chain", "任务列表(1条)", "前言。" * 200 + 深词 + "尾巴。")
        D.store_text("浅", "note", f"{浅词}的说明", "正文随便写点。")

        # ① 正文深处才有的词，仍要查得到
        r = D.search_docs(kw="ZHENGTAN", limit=5)
        中 = [x for x in r if x["doc_id"] == "深"]
        print(f"    ① 正文深处词：命中 {len(r)} 条 · 位置={[x.get('命中位置') for x in r]}")
        if not 中:
            坏.append("正文深处才出现的词查不到（兜底扫正文没生效）")
        elif 中[0].get("命中位置") != "正文":
            坏.append(f"命中位置标错：{中[0].get('命中位置')}（应为「正文」）")

        # ② 标题里的词走快路
        r2 = D.search_docs(kw=浅词, limit=5)
        浅 = [x for x in r2 if x["doc_id"] == "浅"]
        print(f"    ② 标题词：命中 {len(r2)} 条 · 位置={[x.get('命中位置') for x in r2]}")
        if not 浅:
            坏.append("标题里的词查不到（元数据阶段坏了）")
        elif 浅[0].get("命中位置") != "元数据/摘录":
            坏.append(f"标题命中位置标错：{浅[0].get('命中位置')}")

        # ③ 不存在的词必须空
        r3 = D.search_docs(kw="紫色大象在冰箱里烤面包", limit=5)
        print(f"    ③ 无意义词：命中 {len(r3)} 条")
        if r3:
            坏.append(f"无意义词也命中了 {len(r3)} 条 ⇒ 在凑数")

        # ④ 仓外路径条目：扫正文时必须跳过（否则索引=任意文件读取器）
        外 = Path(tempfile.mkdtemp(prefix="docsearch_out_")) / "机密.md"
        外.write_text("这是一份仓外文件，正文含OUTSIDE_TOKEN", encoding="utf-8")
        import json as _json
        D.INDEX.open("a", encoding="utf-8").write(_json.dumps({
            "doc_id": "越界", "kind": "chain", "title": "伪装条目",
            "path": str(外), "byte_len": 外.stat().st_size,
            "excerpt": "摘录里没有那个词", "ts": 1.0, "meta": {}}, ensure_ascii=False) + "\n")
        r4 = D.search_docs(kw="OUTSIDE_TOKEN", limit=5)
        print(f"    ④ 仓外条目：命中 {len(r4)} 条（应为 0）")
        if r4:
            坏.append(f"索引里的仓外路径被读取了：{[x['doc_id'] for x in r4]}")
    finally:
        D.DOCS_DIR, D.INDEX = 旧目录, 旧索引

    if 坏:
        print("文档检索：红 —— " + "；".join(坏))
        return 1
    print("文档检索：绿 —— **按内容查得到（正文兜底）· 标题走快路 · 无意义词回空 · 仓外路径不读**。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())