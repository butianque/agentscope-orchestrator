# 三分钟上手

## 一、装（**不联网也能装**：`wheels/` 里带全部依赖轮子）

Windows：`powershell -ExecutionPolicy Bypass -File .\install.ps1`
Linux/macOS：`bash install.sh`

装完脚本会跑 `orch-doctor`。看到 `✓ 可以开工` 就继续。

两个脚本都用 `pip install --no-index --find-links wheels/` 装 —— **只认本地轮子、全程不联网**；
万一离线装失败（比如平台轮子不匹配），错误信息里会直接给"联网补装依赖"的修法。

## 二、让它认识你的项目

把 `orchestrator.profile.example.yml` 复制到项目根，改名 `orchestrator.profile.yml`，改四处：

| 字段 | 填什么 |
|---|---|
| `repo_root` | 仓库根（相对档案文件或绝对路径） |
| `work_dir` | 编排器工作目录（放大文件，别放仓库里） |
| `gates` | 判据门：怎么算"改对了"，如 `[cargo, test, --lib, --no-run]` / `[pytest, -q]` |
| `anchors.main` | 源码根的点分键（模型改哪些地方） |

判据门写得好不好的**唯一标准**：门绿了，是不是真的等于"目标达成了"。

## 三、接你自己的模型 API（无需改代码）

```powershell
# ① 任意 OpenAI 兼容服务
$env:ORCH_BASE_URL = "https://your-endpoint/v1"
$env:ORCH_API_KEY  = "sk-..."
$env:ORCH_MODEL    = "your-model"

# ② 常见提供方：只要 key
$env:OPENAI_API_KEY = "sk-..."        # 或 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / …

# ③ 任何别的提供方：前缀三件套
$env:MYSERVICE_API_KEY="sk-..."; $env:MYSERVICE_BASE_URL="https://api.x.com/v1"; $env:MYSERVICE_MODEL="m"
```

★ 一条通道都拿不到 key 时，编排器报**基础设施不可用**（退出码 3），
不会假装是"任务没做到"。

## 四、跑

```powershell
orch-goal --goal "让 cargo test --lib --no-run 退出码 0" --max-work 4
orch-plan --goal "…"      # 只看它打算怎么干
orch-mcp                  # 当插件给别的智能体用
```

## 五、跑完怎么核

```powershell
orch-doctor     # 环境还健康吗
orch-log        # 历次运行的账
```

**永远自己再跑一遍判据命令**：日志说成了不算，`cargo` / `pytest` 说成了才算。
