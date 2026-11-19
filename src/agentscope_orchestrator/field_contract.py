"""R287：**跨模块字段契约表**——把"我猜的形状"换成"生产者声明的形状 + 判据验证"。

★ 为什么（真读数，2026-11-06，五轮里栽了 4 次）：
  这几轮的错**全是同一句**「不要猜数据的形状，去读它的生产者」：
    · R280e 读错层：`client.timeout`（真的在 `.client.timeout` 的上一层）
    · R281c 猜常量值：`硬死` 其实是 `'dead'`（我以为值就是中文）
    · R283b 读错层 + 猜键名：`复验.段` 不在顶层，键是 `tid` 不是 `id`
    · R286b 猜字段含义：证据里的 `file` 是 `"repo"`（仓目录名），不是文件路径
    · R286c 猜容器类型：`门` 是 `{"grep_required": false}`（dict），不是列表
  五次全是我**在别人的产出上瞎猜**，而且每次代码都能跑、不报错、结果看着合理，
  **只是判断全都反了**。这是本项目最贵的一类错。

★ 本模块的做法：把每个跨模块读取的字段，**在生产者那里实测一遍**，写成表；
  再由判据 `判二十九` 逐条验证"表里写的形状，和生产者真产出的一致"。
  ⇒ 形状一变，判据就红；而不是某个下游悄悄判反。

★ 诚实边界：
  本表**只覆盖这里列出的字段**，不是全部协议。它降低"我又猜错"的概率，
  **不消灭**它——新增跨模块读取时，必须同时往表里加一行。
"""
from __future__ import annotations

#: 字段契约：{生产者: {字段路径: 期望形状说明}}
#: 形状用**实测**写，不抄文档（文档也会过期）。
契约: dict[str, dict[str, str]] = {
    "gate_spec.evaluate -> (结果, 详情)": {
        "结果": "dict[str, bool]，键是门的 name",
        "详情": "list[dict]，**不是 dict**；每项含 gate/file/ok/cause",
        "详情[].gate": "str，门名",
        "详情[].file": ("str，⚠ **是被测对象名（通常是仓目录名 'repo'），"
                        "不是文件路径** —— R286b 我拿它当路径，"
                        "导致三种失败全被归成同一档"),
        "详情[].ok": "bool",
        "详情[].cause": "str，**归因的唯一凭据**（`门规格非法|…` / `必需缺失|…`）",
        "详情[].rc": "int|None，命令退出码；命令没跑起来时是 -1",
    },
    "goal_runner.run_plan -> dict": {
        "ok": "bool，父目标是否达成",
        "段": "list[dict]",
        "段[].tid": "str，段标识；⚠ **不是 `id`**（R283b 猜错过）",
        "段[].kind": "str（pre_emit|constraint_check|errand|subgoal|acceptance）",
        "段[].状态": "str（绿|红|跳过|拒跑|异常）",
        "段[].门": ("dict[str, bool]，⚠ **不是「证据列表」**；"
                    "值是布尔结论（R286c 我当列表遍历，拿到的是键名 ⇒ 报了假绿）"),
        "段[]._门规格": "list[dict]，门的原始规格（复跑 evaluate 要用它）",
        "段[].理由": "str",
        "达成": "bool",
        "未达成的原因": "list[str]",
        "耗时秒": "float",
        "诚实边界": "str",
    },
    "close_loop.close_loop -> dict": {
        "ok": "bool",
        "阶段": "str",
        "首轮": "dict，首轮裁决（run_plan 的结果）",
        "改动面": "list[str]",
        # ★★★★★ R287b（真读数，2026-11-06）：**契约表第一版这里写错了，
        #   是 `校验()` 自己把它抓出来的。** 我写"`复验` 是 dict"，可实测：
        #     adopted=None      ⇒ 复验=None、应用=None（`blocked_on_model=True`）
        #     adopted 给了      ⇒ 复验=dict、应用=dict
        #   ⇒ **`复验`/`应用` 是可空的**。下游若直接 `果["复验"]["段"]` 会 TypeError，
        #     而更坏的是用 `or {}` 静默吞掉 ⇒ 报"没有失败"（R286c 就报了假绿）。
        #   ⇒ 契约必须写明"什么时候是 None"。
        "复验": ("dict|None —— **它就是 run_plan 的结果**；⚠ 段在 `复验.段`，"
                "**顶层没有 `段`**（R283b 读错过层）。"
                "**没有改码输入时这里是 None**（不是空 dict）"),
        "应用": "dict|None —— 同上，没有改码输入时是 None",
        "应用.保留": "list[str]，真留在主树里的文件",
        "应用.回退": "list[str]",
        "应用.路径": "str，说明用的哪种应用策略",
        "blocked_on_model": "bool，True 表示没拿到改码 ⇒ 上面两个字段是 None",
    },
    "goal_iterate.iterate -> dict": {
        "ok": "bool",
        "停因": "str（达成|阻塞|打转|预算用尽）",
        "轮次": "list[dict]",
        "用了几轮": "int",
        "blocked_on_model": "bool",
        "诚实边界": "str",
    },
    "gateway": {
        "活": "str 常量，值是 'live'（**不是中文**）",
        "硬死": "str 常量，值是 'dead'（**不是中文**，R281c 猜错过）",
        "读不到": "str 常量，值是 'unreadable'",
    },
}


def 表() -> dict[str, dict[str, str]]:
    """返回契约表（供工具/判据读）。"""
    return {k: dict(v) for k, v in 契约.items()}


def 校验() -> dict:
    """**真打一遍生产者**，核对表里写的形状。

    ★ 这里刻意**不查文档**、不读注解 —— 只跑真代码看真产出。
      因为这几轮栽的正是"照着以为的样子写"。
    """
    错: list[str] = []
    过 = 0

    def 记(名: str, 条件: bool, 实得: object = None) -> None:
        nonlocal 过
        if 条件:
            过 += 1
        else:
            错.append(f"{名}：实得 {type(实得).__name__}={str(实得)[:120]!r}")

    # ---- gateway 常量（真去读模块属性，不抄值）----
    try:
        from . import gateway as _gw
        for _k in ("活", "硬死", "读不到"):
            记(f"gateway.{_k} 存在", hasattr(_gw, _k))
            if hasattr(_gw, _k):
                _v = getattr(_gw, _k)
                记(f"gateway.{_k} 是 str", isinstance(_v, str), _v)
                # 契约说"值是英文"，实测核对
                记(f"gateway.{_k} 值是英文（契约这么说）",
                   isinstance(_v, str) and _v.isascii(), _v)
    except Exception as e:                            # noqa: BLE001
        错.append(f"读 gateway 常量异常：{type(e).__name__}: {e}")

    # ---- gate_spec.evaluate：真跑一次 ----
    import tempfile as _tf
    from pathlib import Path as _P
    try:
        from . import gate_spec as _gs
        _d = _P(_tf.mkdtemp(prefix="r287_")) / "repo"
        _d.mkdir(parents=True)
        (_d / "src").mkdir()
        (_d / "src" / "a.py").write_text("V = 0" + chr(10), encoding="utf-8")
        _r, _det = _gs.evaluate(
            [{"name": "gf", "type": "grep_required", "in": "src/a.py",
              "patterns": ["V = 1"]}], _d, _d)
        记("evaluate 第1项是 dict", isinstance(_r, dict), _r)
        记("evaluate 第2项是 list（不是 dict）", isinstance(_det, list), _det)
        if _det:
            _e = _det[0]
            记("详情[0] 是 dict", isinstance(_e, dict), _e)
            记("详情[].cause 存在（归因唯一凭据）", "cause" in _e, list(_e.keys()))
            记("详情[].gate 是 str", isinstance(_e.get("gate"), str), _e.get("gate"))
            # ⚠ 契约说 file 是"被测对象名，不是文件路径"：真核对它长什么样
            记("详情[].file 是被测对象名（不是 .py 路径）",
               not str(_e.get("file") or "").endswith(".py"),
               _e.get("file"))
    except Exception as e:                            # noqa: BLE001
        错.append(f"核 evaluate 异常：{type(e).__name__}: {e}")

    # ---- run_plan / close_loop / iterate：真跑一遍核键 ----
    try:
        from . import auto_goal as _ag, goal_runner as _gr
        _d2 = _P(_tf.mkdtemp(prefix="r287b_")) / "repo"
        _d2.mkdir(parents=True)
        (_d2 / "src").mkdir()
        (_d2 / "src" / "a.py").write_text("V = 0" + chr(10), encoding="utf-8")
        _规 = {"name": "一段",
               "gates": [{"type": "grep_required", "in": "src/a.py",
                          "patterns": ["V = 1"]}],
               "acceptance": ["全绿"]}
        _判 = _gr.run_plan(_ag.plan_tasks(_规), _d2)
        for _k in ("ok", "段", "达成", "未达成的原因", "耗时秒", "诚实边界"):
            记(f"run_plan 有 {_k}", _k in _判, list(_判.keys()))
        _段s = _判.get("段") or []
        记("run_plan.段 是 list", isinstance(_段s, list), type(_段s).__name__)
        if _段s:
            _s = _段s[0]
            记("段[].tid 存在（不是 id）", "tid" in _s, list(_s.keys()))
            记("段[].状态 存在", "状态" in _s, list(_s.keys()))
            记("段[].门 是 dict（不是 list）", isinstance(_s.get("门"), dict),
               type(_s.get("门")).__name__)

        from . import close_loop as _cl
        # ① 没改码 ⇒ 复验/应用 必须是 None（契约是这么说的，真核一遍）
        _闭0 = _cl.close_loop(_ag.plan_tasks(_规), _d2, adopted=None)
        记("close_loop 顶层**没有** 段（段在 复验.段）", "段" not in _闭0,
           list(_闭0.keys()))
        if _闭0.get("blocked_on_model"):
            记("没改码时 复验 是 None（不是空 dict）",
               _闭0.get("复验") is None, _闭0.get("复验"))
            记("没改码时 应用 是 None（不是空 dict）",
               _闭0.get("应用") is None, _闭0.get("应用"))
        # ② 有改码 ⇒ 复验是 dict 且段在它里面
        _闭1 = _cl.close_loop(_ag.plan_tasks(_规), _d2,
                              adopted={"src/a.py": "V = 1" + chr(10)})
        _复 = _闭1.get("复验")
        记("有改码时 复验 是 dict", isinstance(_复, dict), type(_复).__name__)
        记("有改码时 复验.段 是 list", isinstance((_复 or {}).get("段"), list),
           type((_复 or {}).get("段")).__name__)
        记("有改码时 应用.保留 是 list",
           isinstance((_闭1.get("应用") or {}).get("保留"), list),
           type((_闭1.get("应用") or {}).get("保留")).__name__)
    except Exception as e:                            # noqa: BLE001
        错.append(f"核 run_plan/close_loop 异常：{type(e).__name__}: {e}")

    return {"过": 过, "错": 错, "ok": not 错}
