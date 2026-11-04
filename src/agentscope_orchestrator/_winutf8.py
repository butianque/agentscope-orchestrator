"""Windows 控制台 UTF-8 兜底。

★ 为什么需要这个文件（2026-10-30，Win10 22H2 + PowerShell 5.1 实测）：

  这台机器的活动代码页是 **936（GBK）**。此时：
    · 直接打到控制台 —— Python 走 `_WindowsConsoleIO` 写 UTF-16，中文正常；
    · **重定向到文件/管道**（`> log.txt`、`2>&1 | tee`、被别的工具捕获）——
      默认按 GBK 编码，中文变乱码或直接 `UnicodeEncodeError`。

  今天我是靠外部手工设 `PYTHONIOENCODING=utf-8 / PYTHONUTF8=1` 绕过去的。
  但**用户的机器不会替我们设这些变量**：一个产品不该要求用户先配环境才能看到
  自己能读懂的报错。⇒ 由程序自己保证输出可读。

  ★ 另一个真实后果：日志乱码会让人**误判**——把"编码问题"看成"程序坏了"，
    或者反过来把真故障当乱码忽略掉。这与本项目主缺陷家族同源：
    **读数不可信，判据就不可信。**
"""

from __future__ import annotations

import sys


def 确保UTF8输出() -> None:
    """把 stdout/stderr 切到 UTF-8（幂等、失败不致命）。

    只动编码与错误策略，不动缓冲、不动颜色、不改写内容。
    `errors="replace"` 是刻意的：宁可显示一个 `?`，也不让一条诊断输出
    因为某个字符编不出来而整个抛异常——那会把"报错"变成"不报错"。
    """
    for 流名 in ("stdout", "stderr"):
        流 = getattr(sys, 流名, None)
        if 流 is None:
            continue
        重配 = getattr(流, "reconfigure", None)
        if 重配 is None:                      # 被替换成不支持 reconfigure 的对象
            continue
        try:
            重配(encoding="utf-8", errors="replace")
        except Exception:                     # noqa: BLE001
            # 有些宿主把 stdout 换成不可重配的包装器；此时保持原状即可，
            # 不因为"想把输出变好看"而让程序起不来。
            pass


def _selftest() -> int:
    """自检：切完之后 stdout 的编码必须是 utf-8。"""
    失败: list[str] = []
    try:
        重配前 = getattr(sys.stdout, "encoding", None)
        确保UTF8输出()
        重配后 = getattr(sys.stdout, "encoding", None)
        if 重配后 and str(重配后).lower().replace("-", "") != "utf8":
            失败.append(f"stdout 编码应为 utf-8，实得 {重配后}")
        # 幂等性：再调一次不许抛
        确保UTF8输出()
        print(f"  自检：UTF-8 输出兜底 · 重配前 {重配前} → 重配后 {重配后}")
    except Exception as e:                    # noqa: BLE001
        失败.append(f"异常：{type(e).__name__}: {e}")
    for f in 失败:
        print(f"  ✗ {f}")
    return 0 if not 失败 else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
