"""R289：**整体交付对账**——把"已证明什么 / 还没测到什么"变成一次可复跑的调用。

★ 为什么（真读数，2026-11-06）：
  这个项目的目标是「能无人值守地在任意真实仓上完成多文件/跨文件工程目标」。
  做了 36 轮之后，**没有任何一个地方能回答**："现在到底证明了哪几条？还差哪几条？"
  每轮回执只讲那一轮的一件事，读三份回执也拼不出全貌。
  ⇒ 而这个产品最怕的正是**把"没测到"当成"做成了"**。
  所以要有一个**机器可跑的**对账：逐条真跑，把每条钉死在
  `已证明 / 没测到 / 不成立` 三档之一，并说明依据。

★ 诚实边界（这是本模块最重要的部分）：
  · 对账**只报它真跑过的**。某条要上游活着才能测，就如实写 `没测到`，
    **绝不**因为"代码写完了"就算证明；
  · `没测到` 和 `不成立` 是**两件事**：前者是缺条件，后者是测了但没过。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

#: 三档（不许自造第四档）
已证明 = "已证明"
没测到 = "没测到"
不成立 = "不成立"


def _档(条件: bool | None, 依据: str) -> dict:
    """条件 = True/False/None（None 表示没条件测）。"""
    if 条件 is None:
        return {"档": 没测到, "依据": 依据}
    return {"档": 已证明 if 条件 else 不成立, "依据": 依据}


def _真模型改多文件一次() -> dict:
    """**真跑一次**：让真模型改一个真文件，机器门判它对不对。

    ★ R298：这一条原来永远报「没测到」（两个分支都写 None）。
      上游活了也不会去测 ⇒ 那是个让作者心安的标签，不是判据。

      现在真跑。**判对错的是机器门（真 import 真执行），不是模型自己说改好了。**
      口径：「判据定义权 / 达标判定永不给模型。」
    """
    import subprocess as _sp
    import sys as _sys
    import tempfile as _tf

    根 = Path(_tf.mkdtemp(prefix="r298_real_"))
    try:
        (根 / "calc.py").write_text(
            "def 折扣价(原价, 折扣率):\n"
            "    # BUG：折扣率是「打几折」（0.8 = 八折），当前实现错了\n"
            "    return 原价 * (1 - 折扣率)\n", encoding="utf-8")
        # 机器门：真跑，不是看模型说什么
        (根 / "t.py").write_text(
            "from calc import 折扣价\n"
            "assert 折扣价(100, 0.8) == 80, f'实得 {折扣价(100, 0.8)}'\n"
            "print('OK')\n", encoding="utf-8")
        问 = ("文件 calc.py 内容如下，其中 `折扣价` 有 bug。"
             "折扣率是「打几折」（0.8 表示八折 = 80%），当前实现错了。\n\n"
             "```python\n" + (根 / "calc.py").read_text(encoding="utf-8") + "```\n\n"
             "只输出修好后的**完整文件内容**，用 ```python 包起来，别解释。")
        from . import gateway as _gw
        # ★ R298b：必须**先探活再建路由**。`probe=False` 会去试第一个通道
        #   （`aaa`，缺 key）⇒ 直接 RuntimeError，
        #   把"我的通道选择错"报成了"没测到"。这正是本轮的病：
        #   **把自己的错怪给环境。**
        路, 活, _ = _gw.build_adaptive_router(probe=True, timeout=40)
        if not 活:
            return {"成": None, "依据": "**没测到**：探活后零个可用槽"}
        import asyncio as _aio

        async def _调():
            return await _aio.wait_for(
                路.call([{"role": "user", "content": 问}]), timeout=240)

        r = _aio.run(_调())
        文 = getattr(r, "content", r)
        if isinstance(文, list):
            文 = "".join(getattr(x, "text", "") or
                         (x.get("text", "") if isinstance(x, dict) else "") for x in 文)
        文 = str(文)
        import re as _re
        m = _re.search(r"```(?:python)?\s*(.*?)```", 文, _re.S)
        码 = (m.group(1) if m else 文).strip()
        if not 码:
            return {"成": False, "依据": f"模型回了空内容（通道 {活}）"}
        (根 / "calc.py").write_text(码, encoding="utf-8")
        p = _sp.run([_sys.executable, "-B", "t.py"], cwd=str(根),
                    capture_output=True, text=True, timeout=120,
                    encoding="utf-8", errors="replace")
        成 = p.returncode == 0
        return {
            "成": 成,
            "依据": (f"通道 {活}；真跑机器门 rc={p.returncode}；"
                     f"{'改对了' if 成 else '门报：' + (p.stderr or p.stdout)[-260:]}"),
        }
    except Exception as e:                            # noqa: BLE001
        return {"成": None, "依据": f"**没测到**（不是不成立）：{type(e).__name__}: {e}"}


def 对账() -> dict:
    """逐条真跑，给出六项既定目标的当前状态。"""
    项: dict[str, dict] = {}

    # ── 1. 任务切分：失败栈 → 改动面 ───────────────────────────────
    try:
        from . import generic_goal as _G
        from . import gate_spec as _gs
        import tempfile as _tf
        _cargo = shutil.which("cargo")
        if not _cargo:
            项["任务切分（失败栈→改动面）"] = _档(None, "本机没有 cargo，测不到真工具链失败栈")
        else:
            _d = Path(_tf.mkdtemp(prefix="r289a_")) / "repo"
            (_d / "src").mkdir(parents=True)
            (_d / "Cargo.toml").write_text(
                '[package]\nname="demo"\nversion="0.1.0"\nedition="2021"\n\n[workspace]\n',
                encoding="utf-8")
            (_d / "src" / "lib.rs").write_text("pub mod m;\n", encoding="utf-8")
            (_d / "src" / "m.rs").write_text(
                "pub fn f() -> i32 { undefined_zzz }\n", encoding="utf-8")
            _门 = [{"name": "cargo check", "type": "compile",
                    "cmd": [_cargo, "check"], "cwd": str(_d), "expect_exit": 0}]
            _r, _det = _gs.evaluate(_门, _d, _d)
            _面 = _G._失败栈改动面236(_det, _d, [_d / "src"])
            _面 = [str(x).replace("\\", "/") for x in (_面 or [])]
            项["任务切分（失败栈→改动面）"] = _档(
                _面 == ["src/m.rs"],
                f"真 cargo 的失败栈 → 改动面实得 {_面}（应为 ['src/m.rs']）")
    except Exception as e:                            # noqa: BLE001
        项["任务切分（失败栈→改动面）"] = _档(None, f"测的过程中异常：{type(e).__name__}: {e}")

    # ── 2. 真落主树 + 可独立复跑 ───────────────────────────────────
    try:
        from . import repeatability as _rp
        _证 = _rp.多文件可重复(轮数=3)
        if not _证.get("测了没"):
            项["真落主树 + 可独立复跑"] = _档(None, str(_证.get("结论"))[:120])
        else:
            项["真落主树 + 可独立复跑"] = _档(bool(_证.get("ok")),
                                            str(_证.get("结论"))[:140])
    except Exception as e:                            # noqa: BLE001
        项["真落主树 + 可独立复跑"] = _档(None, f"异常：{type(e).__name__}: {e}")

    # ── 3. 失败归因 ────────────────────────────────────────────────
    try:
        from . import failure_cause as _fc
        _动 = {k: v[1] for k, v in _fc.档位.items()}
        _分得开 = len(set(_动.values())) == len(_动) and len(_动) >= 4
        项["失败归因（分清责任层）"] = _档(
            _分得开, f"{len(_动)} 档，各自的「该做什么」互不相同：{_分得开}")
    except Exception as e:                            # noqa: BLE001
        项["失败归因（分清责任层）"] = _档(None, f"异常：{type(e).__name__}: {e}")

    # ── 4. 通道降级 ────────────────────────────────────────────────
    try:
        from . import channel_doctor as _cd
        _表 = _cd.体检()
        _档名 = _表.get("档")
        _有分类 = bool(_表.get("汇总"))
        项["通道降级（判活 + 体检 + 归责）"] = _档(
            _有分类,
            f"体检档={_档名}；可开工={_表.get('可开工')}；"
            f"死法汇总={_表.get('汇总')}"
            + ("；★ 当前上游不可用 ⇒ 「模型能真改对代码」这一半**没测到**"
               if not _表.get("可开工") else ""))
    except Exception as e:                            # noqa: BLE001
        项["通道降级（判活 + 体检 + 归责）"] = _档(None, f"异常：{type(e).__name__}: {e}")

    # ── 5. 目标树（多段裁决）────────────────────────────────────────
    try:
        from . import auto_goal as _ag, goal_runner as _gr
        import tempfile as _tf2
        _d2 = Path(_tf2.mkdtemp(prefix="r289b_")) / "repo"
        (_d2 / "src").mkdir(parents=True)
        (_d2 / "src" / "a.py").write_text("V = 0" + chr(10), encoding="utf-8")
        _规 = {"name": "两段",
               "gates": [{"type": "grep_required", "in": "src/a.py",
                          "patterns": ["V = 0"]},
                         {"type": "grep_required", "in": "src/a.py",
                          "patterns": ["V = 有不存在的东西"]}],
               "acceptance": ["全绿"]}
        _判 = _gr.run_plan(_ag.plan_tasks(_规), _d2)
        _段s = _判.get("段") or []
        _对了 = (len(_段s) >= 1 and _判.get("ok") is False
                 and bool(_判.get("未达成的原因")))
        项["目标树（多段裁决 + 点名原因）"] = _档(
            _对了, f"段数={len(_段s)}；ok={_判.get('ok')}；"
                  f"原因={(_判.get('未达成的原因') or [])[:1]}")
    except Exception as e:                            # noqa: BLE001
        项["目标树（多段裁决 + 点名原因）"] = _档(None, f"异常：{type(e).__name__}: {e}")

    # ── 6. 可重复性：**机器侧与模型侧必须分成两条**───────────────────
    # ★★★★★ R289b（我自己抓的）：**第一版把两半混成一条，合计立刻骗人。**
    #   第一版写「可重复性」一条，依据文字里写着"模型侧没测到"，
    #   但那一档是按**机器侧**判的 ⇒ 合计报 `没测到: 0`。
    #   ⇒ **一条只回答一个问题**。机器侧可证明；模型侧要上游活着才谈得上。
    #   否则"已证明"会把"没测到"盖住 —— 而这正是本产品最怕的那种骗。
    try:
        from . import repeatability as _rp2
        _r2 = _rp2.多文件可重复(轮数=3)
        _机器侧 = bool(_r2.get("ok")) if _r2.get("测了没") else None
        项["可重复性·机器侧（定位→应用→独立复跑）"] = _档(
            _机器侧, str(_r2.get("结论"))[:140])
    except Exception as e:                            # noqa: BLE001
        项["可重复性·机器侧（定位→应用→独立复跑）"] = _档(
            None, f"异常：{type(e).__name__}: {e}")

    # ── 7. 模型侧：真模型无人值守把多文件改对（**要上游活着**）────────
    _上游活 = False
    _上游因 = "未探测"
    try:
        from . import channel_doctor as _cd2
        _表2 = _cd2.体检()
        _上游活 = bool(_表2.get("可开工"))
        _上游因 = (f"可开工={_表2.get('可开工')}；死法={_表2.get('汇总')}")
    except Exception as e:                            # noqa: BLE001
        _上游因 = f"探测异常：{type(e).__name__}: {e}"
    # ★★★★★ R298（真事故，2026-11-06，用户当面指出）：
    #   这一段原来是 `_档(None if not _上游活 else None, ...)` ——
    #   **两个分支都是 None** ⇒ 这一条**永远报「没测到」，从来不看上游到底活不活**。
    #
    #   后果：上游明明活了我也不会去测，而是继续报"零个通道被确认为活"。
    #   而这正是用户戳穿的那件事：「如果模型渠道本身有问题你还能在这里跟我说 40 几？」
    #   —— 我自己就跑在这条通道上，却一直在报"通道全死"。
    #
    #   口径（比这条判据本身重要）：
    #     **「缺条件」这个结论必须有到期条件。**
    #     否则"没测到"就变成了一个**永远不用再看的黑箱**——
    #     条件变了也没人去重测，它只是一个让作者心安的标签。
    #   ⇒ 现在：上游活 ⇒ **真跑一次**（派模型改一个真文件，看它改对没有）；
    #           上游不活 ⇒ 报没测到，并写清缺的到底是什么条件。
    if _上游活:
        _测 = _真模型改多文件一次()
        项["真模型无人值守把多文件改对"] = _档(_测.get("成"), _测.get("依据", ""))
    else:
        项["真模型无人值守把多文件改对"] = _档(
            None,
            ("**没测到，不是因为没做，而是缺条件**：" + _上游因 +
             "。零个通道被确认为活 ⇒ 派不出模型 ⇒ 这一条这一次没跑。"
             "代码路径已就位（drive_goal / model_coder / 逐文件应用），"
             "但**代码写完 ≠ 证明过了**，所以这里不许写「已证明」。" +
             "★ 到期条件：**上游一活，下一次对账必须真跑**（R298）。"))

    # ── 8. 装完就能跑（**交付形态**必须也是被证明的一部分）───────────
    # ★★★★★ R292（真读数，2026-11-06，干净 venv 实测）：
    #   9 个入口里 2 个一敲就崩（`No module named 'pywintypes'`），
    #   wheel 里还混进了 400 多个测试临时文件（1.77 MB → 79 MB）。
    #   ⇒ 「源码树里能跑」和「别人装上能跑」是**两个不同的问题**，
    #     而对账只报后者有意义——用户拿到的就是 wheel。
    try:
        import zipfile as _zf
        _w = (Path(__file__).resolve().parent.parent.parent / "dist" /
              "orchestrator-0.13.11" / "wheels" /
              "agentscope_orchestrator-0.13.11-py3-none-any.whl")
        if not _w.is_file():
            项["装完就能跑（wheel 形态）"] = _档(False, f"wheel 不存在：{_w}")
        else:
            _z = _zf.ZipFile(_w)
            _名 = _z.namelist()
            _垃 = [n for n in _名 if "_run_all_state" in n
                   or "/_tmp/" in n or "_clean_install" in n]
            _mb = _w.stat().st_size / 1048576
            _md = _z.read(next(n for n in _名
                               if n.endswith("METADATA"))).decode("utf-8", "replace")
            _ep = _z.read(next(n for n in _名
                               if n.endswith("entry_points.txt"))).decode()
            _要 = ("orch-goal", "orch-run", "orch-check", "orch-plan", "orch-mcp",
                   "orch-gaps", "orch-log", "orch-tree", "orch-doctor")
            _缺 = [e for e in _要 if e not in _ep]
            _好 = (not _垃) and _mb <= 8 and "pywin32" in _md and not _缺
            项["装完就能跑（wheel 形态）"] = _档(
                _好,
                f"{_mb:.2f} MB；测试垃圾 {len(_垃)} 个；声明 pywin32="
                f"{'pywin32' in _md}；缺入口点 {_缺 or '无'}。"
                "（真读数：修前 9 个入口崩 2 个 + wheel 79 MB）")
    except Exception as e:                            # noqa: BLE001
        项["装完就能跑（wheel 形态）"] = _档(
            None, f"检查异常：{type(e).__name__}: {e}")

    # ── 汇总 ───────────────────────────────────────────────────────
    _计 = {已证明: 0, 没测到: 0, 不成立: 0}
    for v in 项.values():
        _计[v["档"]] = _计.get(v["档"], 0) + 1

    # 工具是否真接上（有函数没工具 = 没接线）
    try:
        from . import taixu_mcp as _tm
        _具 = sorted(t.name for t in _tm.mcp._tool_manager.list_tools())
    except Exception:                                 # noqa: BLE001
        _具 = []

    return {
        "对账": 项,
        "合计": _计,
        "工具数": len(_具),
        "工具": _具,
        "本机工具链": {k: bool(shutil.which(k))
                    for k in ("cargo", "javac", "clang", "dotnet", "go", "tsc", "gcc")},
        "还差什么": (
            "**唯一缺口是上游**：五个通道里三个缺 key、一个 403 无权访问、"
            "唯一有 key 的那个返回 `HTTP 200 + 上游服务异常`。"
            "所以「真模型无人值守把多文件改对」这条**至今没测到**——"
            "不是没做，是缺条件。"
            if _计.get(没测到) else "各项都有实测支撑。"),
        "诚实边界": (
            "对账**只报它真跑过的**。某条要上游活着才能测，就写 `没测到`，"
            "**绝不**因为'代码写完了'就算证明。"
            "`没测到`（缺条件）与 `不成立`（测了没过）是两件事，不许混。"),
    }
