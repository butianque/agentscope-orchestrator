# -*- coding: utf-8 -*-
"""状态 bundle：把「跨重启必须记住」的东西打成**可迁移的 zip**，并能核对着导入。

★ 为什么要有它（N1）：
  `migrate_state.py` 解决的是「装错落点 → 正确落点」的**同一台机器搬迁**；
  换机器/换人接手还要多一件：**把状态带过去，并且证明带过去的是同一份**。
  光有 copy 不够——copy 完不核对，就是「我搬过了」冒充「搬对了」。

★ 三条安全约定（与 `migrate_state` 同口径，不另立）：
  ① 导出带 `清单.json`：每个文件的大小与 **sha256**；导入时**先核对再落盘**，
     任何一条对不上 ⇒ 整包拒收（不许"部分导入"制造半个状态）。
  ② **绝不覆盖**目标已有文件（除非显式 `覆盖=True`）；跳过要在返回里列出来。
  ③ 落盘后写 `jobs/续跑来源.json`：写明这堆状态是**导入来的**、来自哪个 zip 的 sha256。
     没有它，日后无法区分「本机跑出来的」与「搬过来的」——那正是审计要问的第一句。

★ 只搬 `migrate_state.要搬 / 要搬目录` 声明的那几样（口径 R117：同一个清单不许两处各写一份）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import shutil
import sys
import tempfile
import zipfile

from . import migrate_state as _MS


def _状态根(根: str | pathlib.Path | None = None) -> pathlib.Path:
    if 根:
        return pathlib.Path(根).resolve()
    from . import profile as _pf
    return pathlib.Path(_pf.state_dir()).resolve()


def _要打(根: pathlib.Path) -> list[tuple[str, str, pathlib.Path]]:
    """(相对路径, 说明, 绝对路径) —— 只列**真实存在**的。"""
    出: list[tuple[str, str, pathlib.Path]] = []
    for 名, 说明 in _MS.要搬:
        p = 根 / 名
        if p.is_file():
            出.append((名.replace("\\", "/"), 说明, p))
    for 名, 说明 in _MS.要搬目录:
        d = 根 / 名
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if f.is_file():
                出.append((str(f.relative_to(根)).replace("\\", "/"), 说明, f))
    return 出


def 导出(zip路径: str | pathlib.Path, 根: str | pathlib.Path | None = None) -> dict:
    """把状态打成 zip（含 `清单.json` 与 `导出信息.json`）。"""
    R = _状态根(根)
    Z = pathlib.Path(zip路径).resolve()
    Z.parent.mkdir(parents=True, exist_ok=True)
    项 = _要打(R)
    清单 = []
    with zipfile.ZipFile(Z, "w", zipfile.ZIP_DEFLATED) as z:
        for 相对, 说明, p in 项:
            清单.append({"相对": 相对, "说明": 说明, "字节": p.stat().st_size,
                         "sha256": _MS._sha(p)})
            z.write(p, arcname=相对)
        信息 = {
            "导出时间": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "状态根": str(R),
            "主机": os.environ.get("COMPUTERNAME") or "",
            "文件数": len(清单),
            "总字节": sum(x["字节"] for x in 清单),
            "格式": 1,
        }
        z.writestr("清单.json", json.dumps({"文件": 清单, **信息}, ensure_ascii=False, indent=2))
        z.writestr("导出信息.json", json.dumps(信息, ensure_ascii=False, indent=2))
    return {"zip": str(Z), "文件数": len(清单), "总字节": 信息["总字节"],
            "sha256": _MS._sha(Z), "信息": 信息}


def 导入(zip路径: str | pathlib.Path, 根: str | pathlib.Path | None = None,
        覆盖: bool = False) -> dict:
    """核对后导入。任何清单核对失败 ⇒ **整包拒收**，一个字节都不落。"""
    R = _状态根(根)
    Z = pathlib.Path(zip路径).resolve()
    报告: dict = {"zip": str(Z), "zip_sha256": "", "状态根": str(R),
                  "写入": [], "跳过": [], "失败": [], "拒绝": None, "续跑来源": ""}
    if not Z.is_file():
        报告["拒绝"] = f"zip 不存在：{Z}"
        return 报告
    报告["zip_sha256"] = _MS._sha(Z)

    暂 = pathlib.Path(tempfile.mkdtemp(prefix="state-bundle-"))
    try:
        try:
            with zipfile.ZipFile(Z) as z:
                z.extractall(暂)
        except (zipfile.BadZipFile, OSError) as e:
            报告["拒绝"] = f"zip 打不开：{type(e).__name__}: {e}"
            return 报告
        清 = 暂 / "清单.json"
        if not 清.is_file():
            报告["拒绝"] = "包里没有 清单.json ⇒ 无法核对，拒收（不认没清单的包）"
            return 报告
        清单 = json.loads(清.read_text(encoding="utf-8"))

        # ① **先核对再落盘**：任何一条对不上就整包拒收
        坏: list[str] = []
        for x in 清单.get("文件", []):
            源 = 暂 / x["相对"]
            if not 源.is_file():
                坏.append(f"{x['相对']}：包里缺文件")
                continue
            got_b = 源.stat().st_size
            got_h = _MS._sha(源)
            if got_b != x.get("字节") or got_h != x.get("sha256"):
                坏.append(f"{x['相对']}：字节 {got_b}!={x.get('字节')} 或 sha256 不符")
        if 坏:
            报告["拒绝"] = "清单核对失败（" + "；".join(坏[:5]) + "），**整包拒收**"
            return 报告

        # ② 落盘（默认不覆盖）
        for x in 清单.get("文件", []):
            源, 目标 = 暂 / x["相对"], R / x["相对"]
            if 目标.exists() and not 覆盖:
                报告["跳过"].append(x["相对"] + "（目标已存在，不覆盖）")
                continue
            try:
                目标.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(源, 目标)
                if _MS._sha(目标) != x["sha256"]:
                    报告["失败"].append(x["相对"] + "：落盘后 sha256 不符")
                    continue
                报告["写入"].append({"相对": x["相对"], "sha256": x["sha256"]})
            except OSError as e:
                报告["失败"].append(f"{x['相对']}：{type(e).__name__}: {e}")

        # ③ 续跑来源标记（日后能区分"本机跑的"与"搬过来的"）
        来 = R / "jobs" / "续跑来源.json"
        来.parent.mkdir(parents=True, exist_ok=True)
        来.write_text(json.dumps({
            "来源": "导入",
            "zip": str(Z),
            "zip_sha256": 报告["zip_sha256"],
            "导出信息": 清单.get("导出时间"),
            "导出主机": 清单.get("主机"),
            "导入时间": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "导入字节": len(报告["写入"]),
            "跳过条数": len(报告["跳过"]),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        报告["续跑来源"] = str(来)
        return 报告
    finally:
        shutil.rmtree(暂, ignore_errors=True)


def _cli(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="状态 bundle：导出/导入（跨机器续跑）")
    ap.add_argument("--导出", metavar="ZIP")
    ap.add_argument("--导入", metavar="ZIP")
    ap.add_argument("--根")
    ap.add_argument("--覆盖", action="store_true")
    a = ap.parse_args(argv)
    if a.导出:
        r = 导出(a.导出, a.根)
        print(json.dumps({k: v for k, v in r.items() if k != "信息"}, ensure_ascii=False, indent=2))
        return 0
    if a.导入:
        r = 导入(a.导入, a.根, 覆盖=a.覆盖)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        if r["拒绝"] or r["失败"]:
            return 1
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
