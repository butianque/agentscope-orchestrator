# FIXME（实验方看的，跑之前请删掉/藏起来）

- **病根**：`judge.py` 起子进程时没给子进程定编码口径，子进程就按宿主 locale（简体中文 Windows = cp936/GBK）输出中文，
  而 `judge.py` 按 `utf-8` 解码 ⇒ 中文键变成替换字符 ⇒ 报 `{"ok": false, "field": null}`（即 分类=None）。
- **正确修法**：在 `judge.py` 起那个子进程时，显式给子进程 `PYTHONIOENCODING=utf-8`（顺带 `PYTHONUTF8=1`）。
  不许把 `judge.py` 读端的 `encoding="utf-8"` 改成 `gbk` / 本机 locale 来蒙过去。
- **为什么光读代码不容易看出来**：写端（子进程 `print` 一行 JSON）和读端（`encoding="utf-8"`）分开看各自都对，
  错位在两处**之间**——写端随宿主 locale 漂、读端写死 utf-8；单读文件推不出「宿主是 cp936 且子进程没被钉编码」。

## 跑法
- 门命令（仓内）：`python run_ci.py`（cwd = 本目录）。退出码 0 = 绿，非 0 = 红。
- 门命令（仓外、防篡改，推荐）：`python ..\_gates\t1_hard_gate.py <本目录绝对路径>`。
- 解释器：本机 `python` 走 PATH 是 **3.10.11**（`E:\huanjing\python.exe`）；编排器的门会把裸 `python`
  改写成 **3.12.10**（`E:\基础设施\orch-deploy\.venv\Scripts\python.exe`）。两者都实测红/绿一致。
- 秒级：单次 `run_ci.py` ≈ 0.1 s，无 sleep、无联网。
- 平台前提：缺陷只在**子进程 stdio 编码不是 UTF-8** 的宿主上显现（简体中文 Windows = cp936）。若系统开了
  「Beta: 使用 Unicode UTF-8」或 `PYTHONUTF8=1` 全局生效，则本缺陷不显现。`run_ci.py` 已用最小环境起
  `judge.py`，因此宿主环境漂移不影响判定，但改不了宿主的 locale 本身。
