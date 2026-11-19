"""组装每频道 OpenAICredential + OpenAIChatModel。

key 解析优先级：环境变量 key_env -> DSH 凭据文件（优先 `$DSH_HOME`）-> `~/.dsh`。
key 不落本仓库代码/配置。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from .msgs import ChatModel, 凭据 as OpenAICredential, ChatModel as OpenAIChatModel


def 凭据候选() -> list[Path]:
    """**凭据文件按优先级找，不写死一个路径**（R536）。

    ★★★★★ 真缺陷（2026-11-16 实测抓到的，不是推测）：
      原实现是模块级常量 `_DSH_CREDENTIALS = Path.home() / ".dsh" / ".credentials.yaml"`。
      在这台机器上 `C:\\Users\\00\\.dsh\\` **根本不存在** —— 因为 DSH 的数据根
      已经搬到 `E:\\dsh-data\\.dsh`，由宿主以 **`DSH_HOME`** 注入。
      ⇒ 后果：**5 个通道全部「凭据不可读」**，而这个失败**看起来完全合理**
        （「key 没配吧」）—— 实际是**我读错了地方**。

    ★★★ 而 `dsh_channels.py`（R294，用户 2026-11-06 当面指出该缺陷时写的）
      **早就算出了正确路径**：`宿主线索引()["凭据"] = $DSH_HOME/.credentials.yaml`，
      **但没人接上来** —— `gateway` 仍在读写死的那一个。
      ⇒ 口径 359：**认权威标记（`DSH_HOME`），不认名字像不像**。
      ⇒ 口径 309：系统里已经做好的东西，先接上，别再造一份。

    顺序（先到先得）：
      ① `ORCH_CREDENTIALS_FILE` —— 用户显式指定；
      ② `DSH_HOME/.credentials.yaml` —— **宿主注入的权威位置**；
      ③ `DSH_PROFILE_DIR` 上溯找 `.credentials.yaml` —— 换个布局也能兜住；
      ④ `~/.dsh/.credentials.yaml` —— 老布局兜底（行为不变）。
    """
    候: list[Path] = []
    显式 = os.environ.get("ORCH_CREDENTIALS_FILE")
    if 显式:
        候.append(Path(显式))
    根 = os.environ.get("DSH_HOME")
    if 根:
        候.append(Path(根) / ".credentials.yaml")
    目 = os.environ.get("DSH_PROFILE_DIR")
    if 目:
        目p = Path(目)
        for 上 in (目p, 目p.parent, 目p.parent.parent):
            候.append(上 / ".credentials.yaml")
    候.append(Path.home() / ".dsh" / ".credentials.yaml")
    出: list[Path] = []
    for c in 候:
        if c not in 出:
            出.append(c)
    return 出


#: 老名字保留（有的地方 import 它）：**只作兜底展示**，真正的解析走 `凭据候选()`。
_DSH_CREDENTIALS = Path.home() / ".dsh" / ".credentials.yaml"


def _找配置目录() -> Path | None:
    """配置目录按**实际安装位置**找，不按源码树位置找。

    ★★★★★ 真事故（2026-10-30 真跑 install.ps1 之后、用**装出来的包**自检）：
      原实现是 `Path(__file__).resolve().parents[2] / "config"`。
      在源码树里 parents[2] = 仓库根 ⇒ 命中 `config/channels.yml`；
      装进 site-packages 之后 parents[2] = `.venv/Lib` ⇒ 去找
      `.venv/Lib/config/channels.yml`，**永远不存在** ⇒
      selftest / 首跑直接 FileNotFoundError 崩掉。
      ⇒ 「源码里对」不等于「装完对」；这条只在**真装一遍**时才暴露。

    查找顺序（先到先得，全不存在 ⇒ None）：
      ① `ORCH_CONFIG_DIR` —— 用户显式指定；
      ② 源码树布局 `<包>/../../config`（开发态）；
      ③ 包内 `config/`（wheel 里随包分发，装完也在）；
      ④ 当前目录 `config/`（用户自己的工作目录）。
    """
    候选 = []
    显式 = os.environ.get("ORCH_CONFIG_DIR")
    if 显式:
        候选.append(Path(显式))
    此处 = Path(__file__).resolve().parent
    候选 += [此处.parents[1] / "config" if len(此处.parents) > 1 else None,
             此处 / "config",
             Path.cwd() / "config"]
    for c in 候选:
        if c is not None and Path(c).is_dir():
            return Path(c)
    return None


_CONFIG_DIR = _找配置目录()
_CHANNELS_FILE = (_CONFIG_DIR / "channels.yml") if _CONFIG_DIR else Path("channels.yml")


@dataclass(frozen=True)
class ModelSpec:
    id: str
    context_size: int = 128_000
    max_tokens: int = 8192


@dataclass(frozen=True)
class Channel:
    name: str
    display_name: str
    base_url: str
    key_env: str
    models: list = field(default_factory=list)

    @property
    def api_key(self) -> str:
        """解析真实 key：优先环境变量，兜底按优先级读 DSH 凭据文件。

        ★ R536：兜底**不再只读一个写死的路径**，而是遍历 `凭据候选()`；
          并且失败信息里**列出试过哪些路径**（口径 92：明说没查到什么，
          别让「我读错地方」看起来像「key 没配」）。
        """
        v = os.environ.get(self.key_env)
        if v:
            return v
        试过: list[str] = []
        for 路 in 凭据候选():
            if not 路.is_file():
                试过.append(f"{路}（不存在）")
                continue
            try:
                data = yaml.safe_load(路.read_text(encoding="utf-8")) or {}
            except Exception as e:                     # noqa: BLE001
                试过.append(f"{路}（读不动：{type(e).__name__}）")
                continue
            # 兼容两种 DSH 凭据布局：
            #   v1（现用）: version/refs/{KEY}/records/...  → key 在 refs 下
            #   旧扁平布局: {KEY}: sk-...                    → key 在顶层
            # 只读 refs 与顶层，不碰 records（那是会话授权，不是模型 key）。
            for scope in (data.get("refs"), data):
                if isinstance(scope, dict):
                    val = scope.get(self.key_env)
                    if val:
                        return str(val)
            试过.append(f"{路}（在，但没有 {self.key_env}）")
        raise RuntimeError(
            f"channel[{self.name!r}] key not found: env {self.key_env} unset；"
            f"凭据文件试过 {len(试过)} 处 —— " + "；".join(试过)
        )


def load_channels(path: Path | None = None) -> list[Channel]:
    """装载通道配置。`path=None` ⇒ 用 `渠道文件()`（认 `ORCH_CHANNELS_FILE`）。

    ★ 2026-11-05 修：默认参数原来是 `_CHANNELS_FILE`（**导入时**定死的常量）
      ⇒ `ORCH_CHANNELS_FILE` 对 `select_channels` / `smoke` / `phase_gateway_channels`
      这些调用**全部无效**，只有 `渠道源` 认它。
      实测：把 `ORCH_CHANNELS_FILE` 指向 `channels: {}` 的空配置，
      `select_live_channels("", probe=False)` 仍返回 5 条通道 —— 它读的是仓库那份。
      ⇒ 同一族病：**「配置文件写在哪」与实际装载不一致。**
      默认值必须**调用时**解析，不能在导入时定死（「机制不许有隐式全局状态」）。
    """
    # ★★★★★ R294b（真读数，2026-11-06，加完宿主发现后 selftest 红 3 条）：
    #   `✗ R72：渠道源首次刷新应得 ['甲:m1']，实得 ['甲:m1', 'qww:...']`
    #   `✗ ORCH_CHANNELS_FILE 指向的文件**没有被真的装载**（得 ['甲','乙','qww']）`
    #   ⇒ **自动发现污染了"显式指定"的场景**：自检把渠道文件指向临时件（只放 甲乙），
    #     结果宿主 DSH 的 qww 也混进来了，于是"文件有没有被真的装载"根本验不出来。
    #   这是真回归，不是自检太严 —— 口径是：
    #     **显式 > 自动发现。** 调用方点名了渠道文件（传 `path` 或设
    #     `ORCH_CHANNELS_FILE` / `ORCH_PROFILE`），就不许自动往里掺东西，
    #     否则"我指定了却多出来"会让任何隔离测试失效。
    #   ★ 我第一次只判了环境变量，**漏了 `path` 参数本身** ——
    #     而自检走的正是 `load_channels(_f)` 这条，所以红了 2 条还在。
    #     「判据红了先对账」：红的位置才是真的调用点，不是我以为的那个。
    _显式 = (path is not None
             or os.environ.get("ORCH_CHANNELS_FILE") is not None
             or os.environ.get("ORCH_PROFILE") is not None)
    if path is None:
        path = 渠道文件()
    # ★ 真事故（2026-10-30，用**装出来的包**自检）：channels.yml 不存在时
    #   这里直接 read_text ⇒ FileNotFoundError ⇒ 整个 selftest 崩掉。
    #   配置文件是**可选的**（没有它也能靠环境变量自动适配通道），
    #   ⇒ 读不到就当空配置，绝不崩。
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    except Exception:                            # noqa: BLE001
        data = {}
    out: list[Channel] = []
    for name, cfg in (data.get("channels") or {}).items():
        specs = [
            ModelSpec(
                id=m["id"],
                context_size=int(m.get("context_size", 128_000)),
                max_tokens=int(m.get("max_tokens", 8192)),
            )
            for m in cfg.get("models", [])
        ]
        out.append(
            Channel(
                name=name,
                display_name=cfg.get("display_name", name),
                base_url=cfg["base_url"],
                key_env=cfg["key_env"],
                models=specs,
            )
        )
    # ★★★★★ R294（用户当面指出的设计缺陷）：
    #   `channels.yml` 是**从 DSH 迁过来的快照**，迁完就不跟随了 ⇒
    #   插件挂到任何一台 DSH 上，用的都是本仓那份旧快照。
    #   ⇒ **挂到哪台 DSH，就用那台的渠道**：宿主通过 `DSH_PROFILE_DIR` 告诉我们
    #     "你在哪"，读它的 `cordis.patch.yml` 就拿到了当前真实的 providers。
    #   ★ 同名**以宿主为准**（DSH 是活的，本仓文件可能过期）。
    #     这条正是要修的病：快照比活配置权威 ⇒ 必须反过来。
    #   不在 DSH 里（源码树直跑）⇒ 原样返回，行为不变。
    #   ★ R294b：**显式指定渠道文件时让路** —— 见上面 `_显式` 的真读数。
    try:
        from . import dsh_channels as _dsh294
        if not _显式:
            out = _dsh294.并入(out)
    except Exception:            # noqa: BLE001  宿主发现坏了不许拖垮老路径
        pass
    # ★★★★★ R225（交付形态）：**模型 API 自动适配** ——
    #   文件只声明"本部署想用哪些"；**环境里有的提供方自动补上**（channels_auto）。
    #   真证据：凭证只剩 DDDDDD_API_KEY 时，用户手里有 OPENAI_API_KEY 也用不了，
    #   因为通道只能来自写死的 YAML ⇒ 整趟崩在「没有真实可用的模型通道」。
    # ★ R294b：与宿主发现同一个口径 —— **显式指定 ⇒ 自动适配让路**。
    #   不然"我指定了渠道文件，结果多出别人的通道"，任何隔离测试/自检都失效。
    if _显式:
        return out
    try:
        from .channels_auto import 全部通道 as _全部通道225
        return _全部通道225(out)
    except Exception:            # noqa: BLE001  自动适配坏了不许拖垮老路径
        return out


def _按名选(channels: list[Channel], channel_name: str) -> list[Channel]:
    """从**已装载**的通道里按名字选。空串 ⇒ 全部。

    ★ 抽出来是为了让"从哪个文件装载"和"选哪些名字"分开
      —— 原来两者焊死在 `select_channels` 里（它自己调 `load_channels()`
      的**默认路径**），于是 `渠道源(路径=…)` / `ORCH_CHANNELS_FILE`
      **只在 mtime 检查上生效、实际装载仍走默认文件**（R72 自检抓出）。
    """
    if not channel_name or not channel_name.strip():
        return channels
    avail = {c.name for c in channels}
    names = [n.strip() for n in channel_name.split(",") if n.strip()]
    missing = [n for n in names if n not in avail]
    if missing:
        raise ValueError(
            f"未知频道 {missing!r}；可用：{sorted(avail)}")
    picked = [c for c in channels if c.name in names]
    if not picked:
        raise ValueError(
            f"channel_name={channel_name!r} 未选中任何频道；可用：{sorted(avail)}")
    return picked


def select_channels(channel_name: str = "") -> list[Channel]:
    """按名字选出要用的频道。空串 ⇒ 全部频道。

    channel_name 支持逗号分隔多频道（如 "aaa,dddddd"），逐个 trim 后精确匹配
    频道名。指定了名字但某个不存在、或因 trim 后为空导致什么都没选中时，直接抛
    ValueError（给出可用名单），绝不让调用方静默得到空列表 —— 否则 Router([])
    会误报 `no healthy channel`，把配置错误伪装成模型通道故障（此前千条假 blocked
    的元凶之一）。"""
    return _按名选(load_channels(), channel_name)


def _root_base(url: str) -> str:
    """规范化 base_url：只去末尾斜杠。openai 1.x 按字面用 base_url（不自动拼 /v1）。"""
    return url.strip().rstrip("/")


class 流式模型适配件:
    """把 `stream=True` 的 OpenAIChatModel 包成"一次 await 拿完整响应"。

    ★★★★★ R298（真事故，2026-11-06，用户当面指出）：
      「如果模型渠道本身有问题你还能在这里跟我说 40 几？」

      三件事连在一起才看清：
      ① 这条通道（`api.hlyrouter.top`）**只认流式**：
           stream=False → HTTP 200 + error「上游服务异常，请稍后重试」
           stream=True  → HTTP 200 + choices，内容正常（实测回了「通道通了」）
      ② 所以 `build_client` 里原来的 `stream=False` **写死了模型调用必然失败**，
         而失败信息长得像"上游挂了" ⇒ **能力坏了，报出来像环境坏了**。
      ③ 但把 `stream=True` 直接塞进去也不行：AgentScope 的
         `OpenAIChatModel.__call__` 在 stream 下 **返回异步生成器而不是协程**
         ⇒ Router 里的 `resp = await model(msgs)` 会 TypeError；
         而且 `_parse_stream_response` 走 httpx 的 `r.json()`，
         空内容时抛 `JSONDecodeError: Expecting value: line 1 column 1`。

      ⇒ 正确形状：**通道要流式，调用方要一次完整响应** —— 这一层就干这件事：
         内部 `async for` 消费流，外部只 `await` 一次。
      口径：**不要为了迁就调用方的形状而改通道的形状**（那会让通道死掉），
            也不要为了迁就通道而改所有调用方 —— **加一层适配**。
    """

    def __init__(self, 内: object, 名字: str = "") -> None:
        self._内 = 内
        self.name = 名字 or getattr(内, "model", "?")

    def __getattr__(self, 名: str):
        """**透传底层 client 的属性**。

        ★ R298b（真读数，第一次跑全量回归就中）：
          包了适配件之后，`gateway._selftest()` 的 4 条齿全部假红：
            ✗【R161】槽建出来的 client 模型名不对
            ✗【R159】底层 client **没有 timeout**
          因为它们读的是 `client.model` / `client.client_kwargs` / `client.timeout`，
          而这些东西在**被包住的那一层**上。
          ⇒ 适配器必须**透明**：包装不该让调用方看见少了的属性。
            （这正是「包一层」最容易犯的错：**包住了能力，也包掉了证据**。）
        """
        return getattr(self._内, 名)

    async def __call__(self, msgs, **kw):          # noqa: ANN001, ANN003
        果 = self._内(msgs, **kw)
        # 协程 → 直接等；异步生成器 → 消费成一次完整响应
        if hasattr(果, "__await__"):
            return await 果
        末 = None
        片: list[str] = []
        async for c in 果:
            末 = c
            文 = getattr(c, "content", None)
            if isinstance(文, str):
                片.append(文)
            elif isinstance(文, list):
                for x in 文:
                    s = getattr(x, "text", None) or (x.get("text") if isinstance(x, dict) else None)
                    if s:
                        片.append(s)
        if 末 is None:
            return ""
        return "".join(片) if 片 else 末


def build_client(channel: Channel, model_spec: ModelSpec):
    """构造单频道的 OpenAI 兼容 client，**外面包一层流式适配**。

    ★ R298：原来 docstring 写「stream 关掉便于同步拿全量结果」——
      而这正是把模型调用写死的元凶：**这条通道只认流式**。
      现在：`stream=True` + `流式模型适配件`（对外仍是一次 await）。
    """
    credential = OpenAICredential(
        api_key=channel.api_key,
        base_url=_root_base(channel.base_url),
    )
    # ★★★★★ R159（2026-10-26）：**模型客户端必须有超时。**
    #
    # 【真事故 —— 第 42 次真跑挂死，14:09→14:29 整整 20 分钟零进展】
    #   现象：进程**活着**（PID 24032、3 个线程），但 **CPU 60 秒内一动不动**，
    #         日志停在 `Retrying in 1.0s...`，err 里最后一段是
    #         **一段 nginx 的错误页 HTML**（`<hr><center>nginx/1.31.5</center>`）。
    #   ⇒ 它在**读**一个已经坏掉的响应，而**没有任何东西会打断它**。
    #
    # 【为什么没有超时】
    #   本函数原来**一个超时参数都没传**。而 `OpenAIChatModel` 的
    #   `client_kwargs` 是**直接透传给 `openai.AsyncClient(...)`** 的
    #   （源码已核：`self.client = openai.AsyncClient(api_key=…, **self.client_kwargs)`）。
    #   ⇒ 不传 = 用 openai 的**默认超时**（600 秒，且**只**是总超时，
    #     **连接阶段没有独立超时**）。
    #   ★ 600 秒 = 10 分钟；而本编排器一次真跑要 1.5–3 小时、几十次模型调用
    #     ⇒ 一次坏响应就能让整趟静默卡 10 分钟，且**没有任何日志说它在等**。
    #
    # 【这是第 74 层，形态又是那个】
    #   **手里那个「超时」不是判据需要的那个超时**（R143 的兄弟）：
    #   我手里有的是「外层 gate 的 timeout」（分钟级、按结构算），
    #   而**真正会卡死的是「这一次 HTTP 读」**（秒级）。
    #   ⇒ 外层的超时**保护不到**内层的读 —— 因为外层是在
    #     `await model(...)` **之后**才计时的，而卡就卡在**这个 await 里面**。
    #
    # 【口径】
    #   · `timeout`：**单次**请求的总预算。给 180 秒 —— 足够长上下文生成，
    #     又远小于外层 gate 的分钟级预算（不抢外层的位置）。
    #   · `connect`：**连接**预算，5 秒。连接不上就是连接不上，
    #     不该让它占掉 180 秒（这是 R144「瞬时/终态」在连接层的落地）。
    #   · ★ 两者都从**环境变量**读，默认值只是缺省：
    #     `ORCH_HTTP_TIMEOUT` / `ORCH_HTTP_CONNECT_TIMEOUT`
    #     ⇒ 换网络环境不用改代码。
    import os as _os159
    try:
        _总超时159 = float(_os159.environ.get("ORCH_HTTP_TIMEOUT", "180"))
    except ValueError:
        _总超时159 = 180.0
    try:
        _连超时159 = float(_os159.environ.get("ORCH_HTTP_CONNECT_TIMEOUT", "5"))
    except ValueError:
        _连超时159 = 5.0
    import httpx as _httpx159
    内 = OpenAIChatModel(
        credential=credential,
        model=model_spec.id,
        # ★★★★★ R298（真事故，2026-11-06）：这里原来是 `stream=False`，
        #   而真对照读数（同通道/同 key/同 prompt）是：
        #     stream=False → "上游服务异常，请稍后重试"
        #     stream=True  → 正常出 content
        #   ⇒ **这条通道只认流式**。写死在 False 上等于**模型调用永远失败**，
        #     而失败原因看起来像"上游挂了"——
        #     这是本产品最坏的一类 bug：**能力是坏的，报出来却像环境坏了。**
        #   由用户当面指出：「如果模型渠道本身有问题你还能在这里跟我说 40 几？」
        #   我自己就跑在这条通道上 ⇒「通道全死」这个结论从一开始就不成立。
        stream=True,
        max_retries=0,          # ★ R166：见下，**慢错不许在同槽重试**
        retry_delay=1.0,
        context_size=model_spec.context_size,
        # ★ 透传给 `openai.AsyncClient`：它收 `timeout`。
        #   用 httpx.Timeout 才能**分别**给「连接」和「读」两个预算。
        # ★★★★★ R162（2026-10-27）：**重试预算要花在「换槽」，不是「磨同一个槽」。**
        #
        # 【真数据 —— 第 46 次真跑，上游 504 风暴期间的 err 实测间隔】
        #   12:17:20 → 12:22:54 → 12:28:28   ⇒ **每次 Attempt 330 秒**
        #   而「Attempt 1..4 全失败」⇒ 一个槽要烧 **22 分钟**；
        #   第 46 次实测有 6 个槽，全磨一遍 ≈ **2.2 小时/一次模型调用**。
        #
        # 【为什么是 330 秒 —— **两层重试叠乘**，没人算过总账】
        #   · agentscope `_base.__call__` 自己按 `max_retries=3` 重试 ⇒ 4 次；
        #   · 下面 `client_kwargs` 透传给 `openai.AsyncClient`，而它**默认又重试 2 次** ⇒ 3 次；
        #   ⇒ 4 × 3 = **12 次真 HTTP**，每次 110 秒（nginx 自己的 504 超时）
        #     = 1320 秒 = **22 分钟**。
        #   ★ 这是「**同一个事实两处实现**」（R117）在重试上的翻版：
        #     两层各自以为自己是唯一的重试者，合起来变成 12 倍。
        #
        # 【口径】
        #   · `client_kwargs["max_retries"] = 0` —— **底层不再自己重试**；
        #   · ~~`max_retries=1`~~ → ★ R166 改成 **0**，理由见下。
        #   ⇒ 单槽 **1 次**真 HTTP ≈ 110 秒。
        #
        # ★★★★★ R166（2026-10-27）：**「重试一次」值不值，取决于错得多快。**（第 80 层）
        #
        # 【真数据 —— 第 49 次真跑 err 里的 Attempt 时间戳】
        #   16:46:33 Attempt 1 failed（deepseek）→ 16:48:24 Attempt 2 failed
        #   ⇒ 单次 **110 秒**，两次 **220 秒**，然后才轮到下一个槽。
        #   而同一轮里 503 类的槽 **2–3 秒**就失败换走。
        #
        # 【为什么慢错不该重试】
        #   · 504 不是「抖了一下」，是**这个模型在这个提示上 110 秒还没生成完**
        #     （nginx 的 timeout）。第二次它**必然还是** 110 秒 ——
        #     **重试拿不到任何新信息**，只是把 110 秒再烧一遍。
        #   · 换槽只要 2–3 秒（快错）或最多 110 秒（另一个慢槽，但那**是新信息**）。
        #   ⇒ 判据：**同样 110 秒，花在「换一个槽」上收益严格更大。**
        #   ★ 这又是「手里那个量不是判据需要的那个量」：
        #     「重试次数」是均匀分配给所有错误的，而错误的**贵贱差了 50 倍**。
        #
        # ★ R18「瞬时重试一次」在**这里**依然成立 —— 只是执行者是 Router
        #   （换槽就是换一次机会）和 R163 的轮级重试（带 20s 起步的退避），
        #   不是 agentscope 在同槽上原地重来。
        #   ★ 真正的重试手段是 **Router 的换槽**（R161 之后有 6 个槽），
        #     以及 R144 的**轮级**瞬时重试 —— 两者都带退避，不会叠加成 12 倍。
        #   ★ 一个槽磨 12 次而别的 5 个槽在排队空等，这是**把冗余当摆设**。
        client_kwargs={
            "timeout": _httpx159.Timeout(_总超时159, connect=_连超时159),
            "max_retries": 0,
        },
    )
    # ★ R298：对外仍是一次 await（内部消费流式，见 流式模型适配件）
    return 流式模型适配件(内, f"{channel.name}:{model_spec.id}")

# ════════════════════════════════════════════════════════════════
# ★ 通道真健康探测（2026-10-14 新增 —— 实测缺口驱动，不是设计偏好）
# ════════════════════════════════════════════════════════════════
# 【为什么需要它 —— 实测证据】
#   2026-10-14 09:11 按 `--channel ""`（全通道）真跑，任务在第 1 轮**全窗失败**：
#       round_error: InternalServerError: 503 model_not_found
#       「No available channel for model deepseek-v4.1-flash under group default」
#   逐通道真发请求（models[0]，文件顺序）实测：
#       aaa      deepseek-v4.1-flash            ✗ 503（组：国模 2-1线路）
#       dddddd   deepseek-ai/deepseek-v4.1-flash ✓ 200   ← 唯一可用
#       ggg      deepseek-v4.1-flash            ✗ 503（组：default）
#       mf       ali/deepseek-v4.1-flash        ✗ 401（invalid API key）
#
#   ★ 且 `ggg` 在 **23:01 是 200 OK、09:11 变成 503** ⇒ **通道可用性随时间变**。
#     ⇒ 任何**硬编码默认通道**都注定会在某个时刻失效 —— 这不是"配错了"，
#       是**设计上不该假设「配置了 = 可用」**。
#
#   Router 原有 failover 也救不了：`MAX_ATTEMPTS = 4` **恰好等于通道数** ⇒
#     轮询 4 次正好每通道各一次 ⇒ 唯一活通道一次瞬时失败即耗尽配额。
#
# 【修法】发**最小真实请求**（max_tokens=1）逐通道探活，只把活通道交给 Router。
#   这符合本库的统一诊断：**判据必须读它真正要判的那个东西**
#   —— 判"通道能不能用"要读**真实响应**，不能读**配置文件**。

# 探测用的浏览器 UA。★ 必需：不带 UA 时 Cloudflare 会对 urllib 一律返回
#   `HTTP 403 error code 1010`，把"通道可用"误判成"通道死了"（实测踩过）。
_PROBE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

def _探测用流式(url: str, key: str, 模型: str, timeout: float) -> str | None:
    """**用流式再探一次**，成功返回拼好的内容，失败返回 None。

    ★ R298（真事故）：这条通道（`api.hlyrouter.top` 的某些分组）**只认流式**：
        stream=False → HTTP 200 + error「上游服务异常」
        stream=True  → HTTP 200 + choices，内容正常
      我一直用非流式探测 ⇒ 把"我的探针方式错"报成了"通道全死"，连错 40 多轮。
      ⇒ 探针必须两种方式都试；**「我探不到」≠「它不能用」。**
    """
    import json as _j
    import urllib.request
    body = _j.dumps({
        "model": 模型,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 8,
        "stream": True,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": _PROBE_UA,
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            块 = r.read(65536) or b""
        文 = 块.decode("utf-8", "replace")
        if '"error"' in 文 and '"choices"' not in 文:
            return None
        拼 = []
        for 行 in 文.splitlines():
            行 = 行.strip()
            if not 行.startswith("data:"):
                continue
            载荷 = 行[5:].strip()
            if 载荷 == "[DONE]":
                break
            try:
                片 = _j.loads(载荷)
            except Exception:                     # noqa: BLE001
                continue
            for ch in (片.get("choices") or []):
                c = (ch.get("delta") or {}).get("content")
                if c:
                    拼.append(c)
        return "".join(拼) if 拼 else ("" if '"choices"' in 文 else None)
    except Exception:                             # noqa: BLE001
        return None



# ★★★★★ R18：探活的三类判定（**不是两类**）。
#   「读不到」≠「违反」—— 处置完全不同（同 R14 数术层格裁决的口径）。
活 = "live"            # HTTP 2xx
硬死 = "dead"          # 凭据错 / 模型不存在 / 404 —— 重试无意义，**真违反**
读不到 = "unreadable"   # 超时 / 5xx / 网络异常 —— **不是死**，仍应交给 Router 试
# ★ 硬死判据：只有这些码/字样才算「重试无意义」。
_HARD_DEAD_CODES = {400, 401, 403, 404, 422}
_HARD_DEAD_WORDS = ("model_not_found", "invalid api key", "invalid_api_key",
                    "no available channel", "insufficient", "unauthorized")

# ★★★★★ R126（真跑抓出的第 40 层 · 2026-10-21 · 第二十三次端到端）：
#   **`400` 被写死在「硬死」码表里，而这个上游把瞬时故障包在 400 里。**
#
#   真证据（12:42→14:12 那一跑，最后一行的原文）：
#     `★★★ 重规划**崩了**（_base_client.py:1594）：
#        BadRequestError: Error code: 400 -
#        {'error': {'message': '上游服务异常，请稍后重试
#                   (request id: 202610210556136359635658268d9d6CzNWKyer)',
#                   'type': 'invalid_request_error', ...}}`
#   ⇒ 它自己的消息**明写「请稍后重试」**，却被 `_HARD_DEAD_CODES` 判成
#     「重试无意义」⇒ 通道被摘 ⇒ 整趟跑死（exit 1）。
#   ★ 而同一个 err 日志里还有
#     `Attempt 1 failed … Error code: 520`
#     `[router] 无健康槽 ⇒ 等 2 秒（已等 0/600 秒）后重试（上游抖动是暂时的…）`
#     ⇒ Router 的 failover **确实在工作**；是这条分类把它最后的活路掐了。
#
#   ★ 这是 R18 自己那条原则的**执行漏洞**：
#     R18 写着「判据要**宽进严出**：认不出的一律归读不到」，
#     但 `_HARD_DEAD_CODES` 用一个**状态码白名单**把它覆盖掉了 ——
#     **状态码不是因由**，同一个 400 既可能是「请求本身错」（真硬死），
#     也可能是「上游过载」（瞬时）。判据必须读**消息**，不能只读码。
#   ★ 与 R117「同一个事实两处实现」同族：判据读了**码**，而因由在**文字**里。
_TRANSIENT_WORDS = ("稍后重试", "请重试", "服务异常", "服务繁忙", "上游服务",
                    "retry_after", "retry later", "try again", "overloaded",
                    "rate limit", "too many requests", "timeout", "timed out",
                    "temporarily", "service unavailable", "bad gateway",
                    "gateway timeout", "internal server error")


def 是上游瞬态(exc_or_text) -> bool:
    """这次失败是**上游瞬时故障**（等一等/换条通道就能过）吗？—— 唯一读口。

    ★ R126：`generic_goal` 的 `_replan` 曾把这种失败当成「**产品缺陷**」上报，
      而它其实是「上游暂时不可用」。两者处置完全相反：
        · 产品缺陷 ⇒ 显眼报出来，人去修代码；
        · 上游瞬态 ⇒ **重试 / 换通道**，不该惊动人。
      ★ 把两件事压成同一个值，就是 R116b 那一族（「没发生」与「发生了」
        被压成同一个值）。⇒ 判定收口到**一个函数**，两处都问它。
    """
    low = str(exc_or_text or "").lower()
    if any(w.lower() in low for w in _TRANSIENT_WORDS):
        return True
    # ★ 5xx / 429 / 520-530：HTTP 层的瞬时码（4xx 里只有 429 是瞬时的）
    import re as _re126
    m = _re126.search(r"\b(?:error code[:\s]*)?(\d{3})\b", low)
    if m:
        try:
            c = int(m.group(1))
        except ValueError:
            c = 0
        if c >= 500 or c == 429:
            return True
    return False


def _classify_failure(code: int | None, detail: str) -> str:
    """把一次失败分成**硬死**还是**读不到**（R18）。

    ★ 判据要**宽进严出**：认不出的一律归「读不到」——
      因为归错方向的代价不对称：把「读不到」误判成硬死 ⇒ **整台机器拒跑**；
      把「硬死」误判成读不到 ⇒ 多试一次，Router 的 failover 兜住。

    ★★★★★ R126：**顺序改了 —— 先读消息，再读码。**
      旧顺序是「先查硬死词 → 再查硬死码」，而 `400` 在硬死码里 ⇒
      一个消息明写「请稍后重试」的 400 仍然被判硬死。
      ⇒ 现在：硬死词 → **瞬时词（明确否决硬死）** → 硬死码 → 读不到。
      ★ 这不是放松：真硬死的 401/403/404 没有瞬时词，照样判硬死。
    """
    low = (detail or "").lower()
    if any(w in low for w in _HARD_DEAD_WORDS):
        return 硬死
    if 是上游瞬态(detail):
        return 读不到          # ★ R126：消息说了「稍后重试」⇒ 码不作数
    if code is not None and code in _HARD_DEAD_CODES:
        return 硬死
    return 读不到


# ★★★★★ R161（2026-10-27）：**「模型不存在」不是「通道死了」。**
#
# 【真证据 —— 第 45 次真跑又卡在上游 504 风暴里，我去查配置，查出这个】
#   实测（本机、真实请求）：
#     aaa   : /v1/models 返回 **8 个**模型，含 deepseek-ai/deepseek-v4.1-flash（**实测 1.5s 通**）
#     ggg   : /v1/models 返回 **1 个**：chatgpt/gpt-6-astra（通道活着）
#     mf    : /v1/models → **401 invalid api key**（这个是真死）
#   ★ 而配置里 aaa / ggg 声明的模型是 `deepseek-v4.1-flash`（**少了 `deepseek-ai/` 前缀**）
#     ⇒ 上游回 503 `model_not_found: No available channel for model ...`
#     ⇒ `_classify_failure` 判「硬死」⇒ `probe_channels` 判**整个通道死**
#     ⇒ `live_model_pairs` 把 aaa / ggg 全部排除
#     ⇒ 全机器只剩 **1 个槽**（dddddd:deepseek-ai/…）
#     ⇒ 那个槽一遇 504 风暴（第 44 次实测连挂 4 小时）就**整趟白等**。
#
# 【缺陷形态】又是**手里那个量不是判据需要的那个量**（第 76 层）：
#   我手里有「**这个模型在这个通道上不能用**」，
#   我把它当成了「**这个通道不能用**」。
#   ⇒ 一个通道有 8 个模型，其中 7 个能用，我却因为第 8 个不存在把通道整个扔掉。
#
# 【处置：不要猜模型名，去**问通道自己**】
#   上游提供 `/v1/models`。当配置里的模型报「模型不存在」时，
#   不是判死，而是**列出它真有的模型，逐个试**，能用的就加进候选槽。
#   ★ 这正是「自适应扫配置、能用的就用」在产品里的落地 ——
#     不是我手工去改 channels.yml（那只是把这一次的答案写死）。
#
# 【边界：**凭据错才判通道死**】
#   `invalid api key` / `unauthorized` ⇒ 列模型也一定失败 ⇒ 真死，不去试。
_模型级死词 = ("model_not_found", "model not found", "no available channel",
               "no available channel for model", "does not exist",
               "model_not_exist", "unknown model")
_通道级死词 = ("invalid api key", "invalid_api_key", "unauthorized",
               "missing or invalid", "authentication", "permission denied")


def 是模型级死(why: str) -> bool:
    """这个「硬死」是**模型级**（换模型就能活）还是**通道级**（凭据错）？

    ★ 唯一读口。两处都要问它（R116b：同一个事实不许两处实现）。
    """
    low = str(why or "").lower()
    if any(w in low for w in _通道级死词):
        return False          # 凭据错 ⇒ 换模型也没用
    return any(w in low for w in _模型级死词)


def 列出模型(channel: Channel, timeout: float = 20.0) -> list[str]:
    """问通道它**到底有哪些模型**（`GET {base}/models`）。失败返回空表。

    ★ 只读。不缓存 —— 通道的模型会变，缓存等于把「上次对」当「这次对」（R49）。
    """
    import json as _json
    import urllib.request
    try:
        key = channel.api_key
    except Exception:      # noqa: BLE001 - 凭据不可读 ⇒ 没有清单
        return []
    url = _root_base(channel.base_url) + "/models"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "User-Agent": _PROBE_UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = _json.loads(r.read().decode("utf-8", "replace"))
    except Exception:      # noqa: BLE001 - 列不出来不算通道死，只是没有清单
        return []
    out: list[str] = []
    for m in (d.get("data") or []):
        mid = m.get("id") if isinstance(m, dict) else None
        if mid:
            out.append(str(mid))
    return out


def probe_model(channel: Channel, model: ModelSpec,
                timeout: float = 25.0, retries: int = 1,
                backoff: float = 1.5) -> tuple[str, str]:
    """对单个 (通道, 模型) 发最小真实请求。返回 (**三类判定**, 说明)。

    ★ 读的是**真实响应**，不是配置。这是本函数存在的全部理由。

    ★★★★★ R18（真跑抓出的缺陷）：
      · **返回三类而非布尔** —— `活`/`硬死`/`读不到`。
        旧实现只有「可用/不可用」，把**慢**（超时）和**死**（401）压成一个桶
        ⇒ 一个活的慢通道被判死 ⇒ **整台编排器拒跑**（实测）。
      · **瞬时失败重试一次**：一次坏运气不该判死。
      · ★ `timeout` 默认仍是 25s（快），但**超时只算「读不到」**，
        由调用方决定是否仍要试 —— 而不是在这里就把它判死。
    """
    import time as _time

    why = ""
    for 第 in range(max(1, retries + 1)):
        if 第:
            _time.sleep(backoff * 第)
        类, why = _probe_once(channel, model, timeout)
        if 类 != 读不到:
            return 类, why
    return 读不到, why


def _probe_once(channel: Channel, model: ModelSpec,
                timeout: float) -> tuple[str, str]:
    """发一次最小真实请求，返回 (三类判定, 说明)。"""
    import json as _json
    import urllib.error
    import urllib.request

    try:
        key = channel.api_key
    except Exception as exc:      # noqa: BLE001 - 凭据缺失是**硬死**（重试无意义）
        return 硬死, f"凭据不可读：{exc}"
    url = _root_base(channel.base_url) + "/chat/completions"
    body = _json.dumps({
        "model": model.id,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": _PROBE_UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            # ★★★★★ R280（真读数，2026-11-06）：**HTTP 200 不等于"能用"。**
            #   真证据：`qww` 通道返回
            #     HTTP 200 + {"error":{"message":"上游服务异常，请稍后重试 …",
            #                           "type":"new_api_error"}}
            #   而这里原来的代码是 `return 活, f"HTTP {r.status}"`
            #   —— **只看状态码、从不读响应体** ⇒ 被判成「活」。
            #   后果（这才是关键的）：`live_model_pairs` 把 qww 当唯一活槽交给 Router，
            #   于是**通道降级形同虚设** —— 明明有 4 个通道死了、剩下这个也死了，
            #   编排器却认为"有活通道"，一路跑到超时才报一个看不懂的 JSONDecodeError。
            #   ⇒ 判活必须看**响应体里有没有 error**。200 + error 是「硬死」：
            #     它是通道/分组级别的拒绝（分组无权限、额度耗尽、上游挂），重试无意义。
            #   ★ 同时保留"有内容就算活"这一档：真模型回一个正常 JSON 就放行，
            #     不做更严的字段校验（不同上游的形状不完全一致，别猜）。
            体 = b""
            try:
                体 = r.read(8192) or b""
            except Exception:                     # noqa: BLE001  读不到体不算错
                体 = b""
            _文 = 体.decode("utf-8", "replace").strip()
            # ★★★★★ R298（真事故，用户当面指出，2026-11-06）：
            #   用户问：「说明现在编排器根本不是用的 DSH 的模型渠道，
            #            之前说的已经可以自适应适配全是假的，
            #            如果模型渠道本身有问题你还能在这里跟我说 40 几？」
            #
            #   我连 40 多轮报「通道全死、无法验证」，而**我自己就跑在这条通道上**。
            #   这是自相矛盾的，我没去解决它，反而把它当成了借口。
            #
            #   真对照读数（同一条通道 `api.hlyrouter.top/v1`，同一个 key，同一句话）：
            #     stream=False → HTTP 200 + {"error":{"message":"上游服务异常…"}}
            #     stream=True  → HTTP 200 + {"choices":[{"delta":{…}}]}   ← **正常**
            #   ⇒ **这条通道只认流式**。我一直用 stream:False 探测，
            #     于是把自己的**探测方式错**，报成了"环境不可用"。
            #
            #   口径（这条比什么都重要）：
            #     **「我探测失败」≠「环境不可用」。**
            #     探针自己也要先被证明有效（这条口径早写过，我这次没照做）。
            #   修法：非流式结论是错误时，**自动用流式再试一次**；
            #     两次都不行才算真不能走。并且**回报里必须写清用的是哪种方式**。
            if '"error"' in _文 and '"content"' not in _文:
                # ★ R280b（我自己抓的）：**不许另起一套判据。**
                #   我第一版在这里直接 `return 硬死` —— 而这与同文件的
                #   `_classify_failure` 冲突：它的规则是
                #     「硬死词 → **瞬时词（明确否决硬死）** → 硬死码 → 读不到」
                #   而 `qww` 的原文恰恰含「请稍后重试」⇒ 按既有判据应判**读不到**。
                #   ⇒ 两套尺子会让同一句话在不同入口得出不同结论，
                #     而这正是"通道降级"最容易出幻觉的地方。
                #   ⇒ 统一交给 `_classify_failure(None, _文)`：状态码是 200 不是死码，
                #     所以结论完全由消息决定 —— 有瞬时词就是读不到，有硬死词就是硬死。
                #
                # ★ R298：但**先用流式再试一次**（这条通道只认流式，见上）。
                流试 = _探测用流式(url, key, model.id, timeout)
                if 流试 is not None:
                    return 活, (f"HTTP {r.status} 非流式报错、**流式可用**"
                                f"（内容 {len(流试)} 字符）⇒ 判活；"
                                f"非流式原文：{_文[:120]}")
                return _classify_failure(None, _文), \
                    f"HTTP {r.status} 但响应体是错误（流式也试过）：{_文[:220]}"
            return 活, f"HTTP {r.status}"
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:      # noqa: BLE001
            detail = ""
        类 = _classify_failure(exc.code, detail)
        return 类, f"HTTP {exc.code} {detail}".strip()
    except Exception as exc:      # noqa: BLE001 - 网络/超时 ⇒ **读不到**，不是死
        return 读不到, f"{type(exc).__name__}: {exc}"


def 凭据里的没用上的键() -> list[dict]:
    """凭据文件里**有**、但**没有任何通道引用**的 key ⇒ 可能有一条能用却没配的通道。

    ★★★★★ 2026-11-05 真事故（我自己踩的）：
      `qww` 的 key 一直在 `~/.dsh/.credentials.yaml` 里，`channels.yml` 里却没这个通道。
      产品如实报「无可用通道」，而环境里明明有一条**能干活**的模型。
      我因此写过「四条通道全 dead」这种错结论 —— 真相是**配置看不见它**。

    ⇒ 口径：**「配置里没有」≠「环境里没有」。**
      查不到通道时必须把这条线索交出来（键名 + 建议通道名），
      让调用方一眼看出"是配置漏了，不是世界没给"。

    只读 `refs` 与顶层（DSH 凭据两种布局），不碰 `records`（那是会话授权，不是模型 key）。
    在用的键 = `load_channels()` 声明的 `key_env` ∪ 环境里出现的 `*_API_KEY`。
    """
    在用 = {c.key_env for c in load_channels()}
    在用 |= {k for k in os.environ if k.endswith("_API_KEY")}
    键们: list[str] = []
    try:
        if _DSH_CREDENTIALS.is_file():
            data = yaml.safe_load(_DSH_CREDENTIALS.read_text(encoding="utf-8")) or {}
            for scope in (data.get("refs"), data):
                if isinstance(scope, dict):
                    键们 += [str(k) for k in scope if str(k).endswith("_API_KEY")]
    except Exception:                                  # noqa: BLE001 读凭据失败绝不拖垮探活
        return []
    out: list[dict] = []
    for k in dict.fromkeys(键们):
        if k in 在用:
            continue
        out.append({"键": k, "建议通道名": k[: -len("_API_KEY")].lower()})
    return out


def 无通道提示(报: dict) -> str:
    """没有可用通道时给**可执行**的下一步（不是"请检查配置"这种空话）。"""
    未用 = 报.get("未用凭据") or []
    行 = ["没有可用通道。"]
    死 = 报.get("dead") or {}
    if 死:
        行.append("逐条原因：" + "；".join(f"{k}: {v}" for k, v in 死.items()))
    if 未用:
        键表 = "、".join(f"{x['键']}（→ 通道名 {x['建议通道名']}）" for x in 未用)
        行.append(f"★ **凭据里有、配置里没引用**的键：{键表}。")
        行.append("  ⇒ 「配置里没有」≠「环境里没有」：它**可能**是一条能用却没配的通道"
                  "（也可能已过期 —— 本仓实测 `DEEPSEEK_API_KEY` 在 api.deepseek.com"
                  " 与 hlyrouter 都是 401/403，所以**先试一次 /models** 再下结论）。")
        行.append("  能用就补进 channels.yml（2 空格缩进、key_env 写这个键名），"
                  "或直接导出同名环境变量，不用等世界给。")
    return "\n".join(行)


def _补线索(报: dict) -> dict:
    """给探活报告补上「凭据里有但没用上」的线索（只在真没通道时才补）。"""
    try:
        未用 = 凭据里的没用上的键()
    except Exception:                                  # noqa: BLE001
        未用 = []
    if 未用:
        报["未用凭据"] = 未用
        报["提示"] = 无通道提示(报)
    return 报


def probe_channels(channels: list[Channel], timeout: float = 25.0) -> dict:
    """逐通道**逐模型**探活（★ R49：不再是每通道只探 `models[0]`）。

    报告含：
      · `live`（**活**：该通道至少有一个模型 HTTP 2xx）；
      · `dead`（**硬死**：该通道**所有**模型都凭据错/模型不存在 ⇒ 重试无意义）；
      · `unreadable`（**读不到**：超时/5xx/网络 ⇒ **不是死**，仍应交给 Router 试）；
      · `probed`（探测的通道数）；
      · `pairs`（★ R49：逐 **(通道,模型)** 的判定，键 `"通道|模型"`）。
    ★★★★★ R18：**三个桶，不是两个**。旧实现把「慢」和「死」压成一个 `dead`
      ⇒ 一个活的慢通道被判死 ⇒ **整台编排器拒跑**（真跑实测）。

    ★★★★★ R49（真跑抓出的缺陷 · 2026-10-15）：
      旧实现每通道**只探 `models[0]`**。而配置里一个通道**可以声明多个模型**
      （本仓 `dddddd` 就有 `deepseek-ai/…` 与 `kimi-k3`）。
      ⇒ 第一个模型不可用时，整条通道被判死 —— **即使它的第二个模型是活的**。
      ★ 这直接导致"上游全挂"的**假结论**：实测 `dddddd` 的第一个模型 503，
        但我因此从没试过 `kimi-k3`，也从没把 `mf`（**另一个域名**）当候选。
      ⇒ 可用性必须**按 (通道,模型) 对**判定，不是按通道判定。
    **不缓存** —— 可用性随时间变，缓存等于把"上次对"当成"这次对"。
    """
    live: list[str] = []
    dead: dict[str, str] = {}
    unreadable: dict[str, str] = {}
    pairs: dict[str, dict] = {}
    # ★ R161：**发现出来的**模型（配置里没写、但通道真有且实测能用的）
    discovered: dict[str, list] = {}
    n_pairs = 0
    for c in channels:
        if not c.models:
            dead[c.name] = "配置里没有模型"
            continue
        类表: list[tuple[str, str, str]] = []
        for m in c.models:
            mid = str(getattr(m, "id", m))
            类, why = probe_model(c, m, timeout=timeout)
            pairs[f"{c.name}|{mid}"] = {"类": 类, "why": why, "model": mid}
            类表.append((mid, 类, why))
            n_pairs += 1
        # ★ 通道级判定**由模型级聚合而来**（不是只看第一个）
        if any(类 == 活 for _, 类, _ in 类表):
            live.append(c.name)
        elif all(类 == 硬死 for _, 类, _ in 类表):
            原因 = "；".join(f"{mid}: {why}" for mid, _, why in 类表)
            # ★★★★★ R161：**模型不存在 ⇒ 先问通道真有哪些模型，别判通道死。**
            #   只在「至少一条是模型级死」时才去发现（凭据错就别浪费时间）。
            发现: list = []
            if any(是模型级死(why) for _, _, why in 类表):
                已有 = {mid for mid, _, _ in 类表}
                for 模型id in 列出模型(c, timeout=timeout):
                    # ★★★★★ R165（2026-10-27）：发现上限 **4 → 12**。
                    #   真实通道 `aaa` 的 /models 有 8 个模型，而**实测最能干活的那个
                    #   （`mm/minimax-m3.1-flash`，真实工程提示 40.4 秒出 1626 字）
                    #   排在第 6 位附近 ⇒ 上限 4 让它**永远进不了槽表**。
                    #   ★ 这是 R161 留下的「发现得出来、但只给一半」——
                    #     上限 4 是当初为了「别把探活拖太慢」拍的，没算过
                    #     「后面那些模型里有没有比我手里更好的」。
                    #   ★ 代价可控：发现出来的模型只试 **1 次**，死的 2–3 秒就摘掉。
                    if 模型id in 已有 or len(发现) >= 12:
                        continue
                    已有.add(模型id)
                    _m = ModelSpec(id=模型id,
                                   context_size=max(
                                       (getattr(m, "context_size", 128_000)
                                        for m in c.models), default=128_000))
                    # ★ 发现出来的模型**只试一次**：清单已证明它存在，
                    #   这一步是在验「我这个 key 能不能用它」。
                    _类, _why = probe_model(c, _m, timeout=timeout, retries=0)
                    pairs[f"{c.name}|{模型id}"] = {
                        "类": _类, "why": _why, "model": 模型id,
                        "discovered": True}
                    n_pairs += 1
                    if _类 in (活, 读不到):
                        发现.append(_m)
            if 发现:
                live.append(c.name)
                discovered[c.name] = 发现
            else:
                dead[c.name] = 原因
        else:
            unreadable[c.name] = "；".join(f"{mid}: {why}" for mid, _, why in 类表)
    报 = {"probed": len(channels), "probed_pairs": n_pairs,
          "live": live, "dead": dead, "unreadable": unreadable,
          "pairs": pairs, "discovered": discovered}
    if not live:
        _补线索(报)                    # 全探死 ⇒ 必须把"配置漏了"这条线索交出来
    return 报


def live_model_pairs(channels: list[Channel],
                     report: dict | None) -> list[tuple[str, Channel, ModelSpec]]:
    """从探活报告选出**可用的 (通道,模型) 对**，供 Router 做 failover 目标。

    ★★★★★ R50（真跑抓出的缺陷 · 2026-10-15）：
      调用方（`generic_goal`）此前只取 `c.models[0]` 建 client
      ⇒ 一个通道声明的**其余模型永远不会被用到**，Router 的 failover
        也就**只能在通道之间跳，不能在模型之间跳**。
      实测后果：`dddddd` 第一个模型 503 时，配置里明明还有 `kimi-k3`
      和另一个域名的 `mf`，却**没有任何一条路径会去试它们**。

    【判定口径（与 R18 一致，不猜）】
      · 探到「活」⇒ 用；
      · 探到「硬死」（凭据错/模型不存在）⇒ **不用**（重试确实无意义）；
      · 探到「读不到」（超时/5xx/网络）⇒ **仍用**（R18：读不到不是死，
        真调用才是判据，Router 有冷却与重试）；
      · 没有报告（`probe=False`）⇒ 配置里声明的**全部**模型都算候选。
    【槽名】`"通道:模型"` —— Router 按**名字**记失败/冷却
      ⇒ 每个模型独立计数，一个模型坏掉不会把同通道的好模型一起拖下水。
    """
    pairs = (report or {}).get("pairs") or {}
    # ★★★★★ R161：**配置里没写的模型也要用上。**
    #   探活若发现某通道有别的可用模型，它会记在 `discovered` 里。
    #   不读这一项 ⇒ 发现了也白发现（「算了，但没给出去」那一族）。
    发现表 = (report or {}).get("discovered") or {}
    out: list[tuple[str, Channel, ModelSpec]] = []
    for c in channels:
        for m in list(c.models or []) + list(发现表.get(c.name) or []):
            mid = str(getattr(m, "id", m))
            类 = (pairs.get(f"{c.name}|{mid}") or {}).get("类")
            if 类 == 硬死:
                continue
            out.append((f"{c.name}:{mid}", c, m))
    return out


def select_live_channels(channel_name: str = "", probe: bool = True,
                         timeout: float = 25.0) -> tuple[list[Channel], dict]:
    """选出**真实可用**的通道。返回 (通道列表, 报告)。

    `probe=False` 时退回 `select_channels`（配置口径），报告标 `probed: 0`。
    ★ 全部通道都探死时**如实返回空列表 + 报告**，不静默退回配置口径
      —— 退回就等于把"都死了"伪装成"有通道"，调用方会收到难懂的 503 而不是
      "你的通道全不可用（逐条原因如下）"。
    """
    picked = select_channels(channel_name)
    if not probe:
        报 = {"probed": 0, "live": [c.name for c in picked], "dead": {},
              "unreadable": {}}
        if not picked:
            _补线索(报)            # ★ 配置里一个通道都没有时，这一步才是救命的那句
        return picked, 报
    rep = probe_channels(picked, timeout=timeout)
    # ★★★★★ R18：**「读不到」的通道仍然交给 Router 试**。
    #   理由：探活只是**启发式**（读一次响应推断"以后能不能用"），
    #   而 Router 有 failover + 冷却，**真调用**才是判据。
    #   把「读不到」排除掉 ⇒ 一次瞬时抖动就让整台机器拒跑（实测踩过）。
    #   ⇒ 只排除**硬死**（凭据错/模型不存在：重试确实无意义）。
    试 = set(rep["live"]) | set(rep.get("unreadable") or {})
    # ★★★★★ R280c（真读数，2026-11-06）：**报告的 `live` 与返回的通道池不一致。**
    #   实测：`select_live_channels("")` 返回 `['qww']`，而 `rep["live"] == []`，
    #   qww 在 `rep["unreadable"]` 里。这是因为上面的规则（读不到也交给 Router 试）
    #   **是对的**，但报告没把"最终交给 Router 的有哪些、各自是什么身份"说出来。
    #   ⇒ 调用方只看 `rep["live"]` 就会得出"一个通道都没有"——与事实相反，
    #     于是它可能去报"没有可用通道"，而实际上 Router 还有一次机会。
    #   口径：**报告的字段必须与返回值自洽**；"确定活"和"还能一试"要分开写清。
    rep["交给router试"] = sorted(试)
    rep["确定活"] = sorted(set(rep.get("live") or []))
    rep["只是还能一试"] = sorted(set(rep.get("unreadable") or {}) - set(rep.get("live") or []))
    return [c for c in picked if c.name in 试], rep


# ════════════════════════════════════════════════════════════════
# ★★★★★ R72（2026-10-17）：**渠道源 —— 持续加载 + 自适应重探**
# ════════════════════════════════════════════════════════════════
# 【为什么需要它 —— 本文件自己的注释就是证据】
#   上面 R18 那段记着：`ggg` 在 **23:01 是 200、09:11 变 503**。
#   ⇒ **可用性是时间的函数，不是启动时的常数。**
#
#   而原实现是：启动时探一次 ⇒ 把结论**冻结**成 Router 的槽表 ⇒
#   整趟无人值守（几小时）里：
#     · 一个**后来恢复**的渠道永远不会被重新用上；
#     · 一个**后来才配好**的渠道（凭据补上、或用户改了 yaml）永远发现不了；
#     · 用户改了渠道文件，**必须重启**才生效。
#   ★ 这与"无人值守"的定义直接冲突：无人值守的价值就是**在没人看着的时候
#     自己适应环境变化**。冻结的渠道表 = 把"启动那一刻的世界"当成"整晚的世界"。
#
# 【口径】
#   ① 配置**按 mtime 持续加载**：文件变了就重读（改 yaml 无需重启）；
#   ② 重探**有节流**：探活是网络动作，不能每次失败都探一遍；
#   ③ 返回**当前可用**的 (槽名, Channel, ModelSpec) 对 —— **哪个能用就用哪个**。
#   ★ 不缓存**可用性**：`probe_channels` 的注释已写明「不缓存 —— 可用性随时间变，
#     缓存等于把"上次对"当成"这次对"」。本类只缓存**配置文本**（按 mtime 失效），
#     不缓存探活结论。
#
# 【渠道文件位置可自适应】
#   环境变量 `ORCH_CHANNELS_FILE` 可指向任意渠道文件（换部署/多份配置时用）；
#   不设 ⇒ 用仓库自带的 `config/channels.yml`。
_CHANNELS_ENV = "ORCH_CHANNELS_FILE"


def 渠道文件() -> Path:
    """当前渠道配置文件（`ORCH_CHANNELS_FILE` 可覆盖）。"""
    v = (os.environ.get(_CHANNELS_ENV) or "").strip()
    return Path(v) if v else _CHANNELS_FILE


class 渠道源:
    """**持续加载**的渠道配置 + **自适应重探**（R72）。

    用法：启动时 `刷新(强制=True)` 拿一次；之后把它交给 Router 当 `刷新器`
    —— Router 在"没有健康槽"或"预算耗尽"时会问它一次，
    它按节流决定要不要真的重探 ⇒ **恢复的渠道自动回到池子里**。
    """

    def __init__(self, 频道名: str = "", 最小重探间隔: float = 60.0,
                 探活=probe_channels, 装载=None,
                 路径: Path | None = None) -> None:
        self.频道名 = 频道名
        self.最小重探间隔 = float(最小重探间隔)
        self._探活 = 探活
        self._路径 = Path(路径) if 路径 else None
        # ★★ `装载` 的默认值**必须绑到 `self.配置路径()`**，不能是
        #   `select_channels` —— 后者内部读的是**默认文件**，
        #   于是 `路径=` / `ORCH_CHANNELS_FILE` 会变成**只在 mtime 检查上生效、
        #   实际装载仍读默认文件**的摆设（R72 自检第一版就抓出了这个）。
        self._装载 = 装载 or (lambda 名: _按名选(
            load_channels(self.配置路径()), 名))
        self._mtime: float | None = None
        self._channels: list[Channel] = []
        self._对: list[tuple[str, Channel, ModelSpec]] = []
        self._报告: dict = {}
        self._上次重探 = 0.0
        self._首刷过 = False    # ★ 首次装载是**初始化**，不是"接纳"
        self.装载次数 = 0      # 真的重读了配置几次
        self.重探次数 = 0      # 真的发了探活请求几次
        self.新接纳 = 0        # 累计"从不可用变回可用"的槽数
        # ★★★★★ R181：**硬死台账**（槽名 → 原因）。
        #   真证据（第 86 次真跑 · `_真跑R186_err.log`）：启动时已确诊
        #   `dddddd|kimi-k3`（503 model_not_found）、`ggg|*`、`mf|*`（401）硬死，
        #   槽表只有 2 个；但后续刷新它们探活**超时**（读不到）⇒ 按 R18 口径
        #   「读不到仍用」⇒ 又回池子（日志：`池子现有 4 槽`）⇒ 每轮白烧 4 次
        #   必然失败的调用 + 30 秒等待 ⇒ 6 轮预算在轮1 烧光（整趟 6 分钟归零）。
        #   ★ 判据需要的是「这个槽**有没有被确诊过**」，手里拿到的却是
        #     「这次探活读到了什么」—— 同一族缺陷在**渠道层**的样子。
        self._硬死台账: dict[str, str] = {}
        self.拦下硬死槽 = 0        # 累计「已确诊硬死、本次只是读不到」被拦下的次数
        self.最近拦下: list[str] = []

    # ── ① 配置：按 mtime 持续加载 ──────────────────────────────
    def 配置路径(self) -> Path:
        return self._路径 or 渠道文件()

    def 装载配置(self) -> bool:
        """配置文件变了（或第一次）⇒ 重读。返回**是否真的重读了**。

        ★ 用 mtime 而不是每次都重读：重读本身不贵，但**重探**很贵
          （网络），而"要不要重探"由"配置变没变 + 节流"共同决定。
        """
        p = self.配置路径()
        try:
            mt = p.stat().st_mtime
        except OSError:
            mt = None
        if mt is not None and mt == self._mtime and self._channels:
            return False
        self._channels = self._装载(self.频道名)
        self._mtime = mt
        self.装载次数 += 1
        return True

    # ── ②③ 重探（有节流）⇒ 当前可用的对 ────────────────────────
    def 刷新(self, 强制: bool = False) -> list[tuple[str, Channel, ModelSpec]]:
        """返回**当前可用**的 (槽名, Channel, ModelSpec) 对。

        `强制=True` ⇒ 忽略节流与 mtime（启动时用；也用于"我就是要现在探"）。
        ★ 节流只挡**重复探**，不挡**配置变更**：配置一变就立刻重探
          —— 用户改了 yaml 就是在说"现在按这个来"。
        """
        import time as _time

        配置变了 = self.装载配置()
        现在 = _time.time()
        if not 强制 and not 配置变了:
            if 现在 - self._上次重探 < self.最小重探间隔:
                return list(self._对)
        self._上次重探 = 现在
        self.重探次数 += 1
        报告 = self._探活(self._channels)
        self._报告 = 报告
        # ★★★★★ R181：先按本次探活结论维护硬死台账。
        for _k181, _v181 in (报告.get("pairs") or {}).items():
            _类181 = _v181.get("类")
            if _类181 == 硬死:
                self._硬死台账[_k181] = str(_v181.get("why"))[:120]
            elif _类181 == 活:
                # 真探到活 ⇒ 上次的硬死结论**被推翻** ⇒ 删台账（这才是「恢复」）
                self._硬死台账.pop(_k181, None)
        新对 = live_model_pairs(self._channels, 报告)
        # ★ R181：**读不到**（超时/5xx）但**台账里有它** ⇒ 不进池子 ——
         #   不是「可能活」，是「上次的硬死结论还没被推翻」。
         #   ★ 从没确诊过的槽不受影响：R18「超时 ≠ 死」原样保留。
        _p181 = 报告.get("pairs") or {}
        _拦181 = [
            n for n, _, _ in 新对
            if n.replace(":", "|", 1) in self._硬死台账
            and (_p181.get(n.replace(":", "|", 1)) or {}).get("类") != 活
        ]
        if _拦181:
            新对 = [x for x in 新对 if x[0] not in _拦181]
            # ★ 落地成**可观测量**（gateway 里没有 logger，也不该为一行日志引入依赖）
            self.拦下硬死槽 += len(_拦181)
            self.最近拦下 = list(_拦181)
        新名 = {n for n, _, _ in 新对}
        旧名 = {n for n, _, _ in self._对}
        # ★★★ 首次装载是**初始化**，不是"接纳"（自检抓出：原写法把配置里
        #   本来就有的槽全算成"新接纳"，报出 2 而真相是 1）。
        #   ★ 不能改用"`self._对` 非空"来判断：首次全死（`_对` 为空）、
        #     第二次才活 ⇒ 那种**确实该算接纳**，用空判断会漏掉它。
        if self._首刷过:
            self.新接纳 += len(新名 - 旧名)
        self._首刷过 = True
        self._对 = 新对
        return list(self._对)

    def 全部对(self) -> list[tuple[str, Channel, ModelSpec]]:
        """**配置里声明的全部** (槽名, Channel, ModelSpec)（含当前不可用的）。

        ★ 为什么还要这个：`刷新` 只回"现在能用的"，而 Router 的"新接纳"
          判断需要知道**池子里本来有什么** —— 否则"一个之前被排除、现在
          恢复了"的槽会被当成"从来没出现过"，日志里看不出它回来过。
        """
        out: list[tuple[str, Channel, ModelSpec]] = []
        for c in self._channels:
            for m in (c.models or []):
                out.append((f"{c.name}:{str(getattr(m, 'id', m))}", c, m))
        return out

    def 状态(self) -> dict:
        """给回执/审计用的事实（**不是**判断，只是事实）。"""
        return {
            "渠道文件": str(self.配置路径()),
            "配置装载次数": self.装载次数,
            "重探次数": self.重探次数,
            "新接纳槽数": self.新接纳,
            "当前可用槽": [n for n, _, _ in self._对],
            "最小重探间隔": self.最小重探间隔,
        }


# ════════════════════════════════════════════════════════════════
# ★★★★★ R157（2026-10-25）：**统一的自适应通道入口 —— 一处定义，处处使用**
# ════════════════════════════════════════════════════════════════
# 【为什么要有这个函数 —— 用户的一句话点破了它】
#   `自适应读取持续加载渠道配置，那个能用就用哪个，自动连接自动重连`
#   —— 这个机制**早就存在**（`select_live_channels` 探活 + `渠道源` 持续重探
#   + Router 的 `刷新器=`）。但它只接在 `generic_goal` 一条路径上。
#
# 【实测的真缺口（不是猜测）】
#   我在本仓跑了 `select_live_channels("", probe=True)`：
#     `探了 4 个通道 / 能用: ['dddddd'] / 硬死: ['aaa','ggg','mf']`
#   ⇒ 自适应**是对的**，它把三个死通道丢掉了。
#   ★ 但同一时刻：
#     · `develop.py:836`   `channels = select_channels(channel_name)`  ← 静态
#     · `real_task.py:392` `channels = select_channels(channel_name)`  ← 静态
#   ⇒ **这两条入口拿到的是配置口径（4 个通道，3 个是死的）。**
#     而且它们写的是 `build_client(c, c.models[0])` ——
#     **每个通道只取第一个模型**，正是 R50 在 `generic_goal` 修过的那个缺陷，
#     在这两条路径上**没修**。
#
#   ★★★ 这是「修了一侧，另一侧没修」的第 8 次。
#     根因不是"忘了改"，是**没有单一入口**：同一件事被抄了三遍，
#     改一处就必须记得另外两处。⇒ 收成一个函数，谁要通道都问它。
#
# 【口径（与 R18 / R50 / R72 完全一致，不新造判据）】
#   ① 探活：只排除**硬死**（凭据错/模型不存在），「读不到」仍交给 Router 试；
#   ② 候选 = **所有可用的 (通道,模型) 对**，不是一个通道一个模型；
#   ③ Router 接 `渠道源` ⇒ 没有健康槽时**先问一次外面**（重读配置 + 重探）
#      ⇒ 后来恢复的通道会被重新用上，改 yaml 不用重启；
#   ④ 全死时**如实抛错**并逐条给原因，不静默退回配置口径。
def build_adaptive_router(channel_name: str = "", probe: bool = True,
                          timeout: float = 25.0):
    """造一个**自适应** Router。返回 (router, 槽名列表, 报告)。

    ★ 这是全仓**唯一**该被调用的通道入口。新增入口请调它，不要抄。
    """
    from agentscope_orchestrator.router import Router

    channels, rep = select_live_channels(channel_name, probe=probe,
                                         timeout=timeout)
    if not channels:
        死 = "；".join(f"{k}: {v}" for k, v in (rep.get("dead") or {}).items())
        raise RuntimeError(
            f"没有真实可用的模型通道（已探测 {rep.get('probed')} 个）。"
            f"逐条原因：{死 or '（未探测）'}")
    pairs = [(slot, build_client(c, m))
             for slot, c, m in live_model_pairs(channels, rep)]
    if not pairs:
        raise RuntimeError(
            f"没有真实可用的 (通道,模型) 对（已探 {rep.get('probed')} 个通道）。"
            f"逐条原因：{rep.get('dead') or '（见报告）'}")
    # ★ ④ 渠道源：持续加载 + 自适应重探（增强，不是前提）
    源 = None
    try:
        源 = 渠道源(channel_name)
        _源 = 源

        def _刷新槽():
            return [(n, build_client(c, m)) for n, c, m in _源.刷新()]

        源.刷新(强制=True)
        router = Router(pairs, 刷新器=_刷新槽)
    except Exception as exc:                       # noqa: BLE001
        # ★ 装不上就退回「启动时探一次」，但**如实说出来**
        #   —— 不静默降级成"看起来一样"。
        print(f"[gateway] ★ 渠道源未接上（退回启动时一次性探活）："
              f"{type(exc).__name__}: {exc}", flush=True)
        router = Router(pairs)
    # ★ 把 (槽名, client) 对挂在 router 上：只需要**一个**模型的调用方
    #   （如 `orchestrate` 的最小 demo）从这里取，**不要再自己拼通道**。
    #   ★ 不用改 Router 的签名 —— 它不持有这个信息，而这里天然就有。
    router.槽对 = list(pairs)
    return router, [n for n, _ in pairs], rep


# ════════════════════════════════════════════════════════════════
# 纯机器自检（**不碰网络**：用假 _probe_once 注入三类结果）
# ════════════════════════════════════════════════════════════════
# ★★★★★ 为什么本件必须有自检（R18 的教训）：
#   本模块此前**没有自检** —— 而它正是那个让**整台编排器拒跑**的缺陷所在
#   （探活把「慢」判成「死」）。没自检的模块，缺陷**没有地方暴露**。
def _有发送循环162(码: str) -> bool:
    """R162 的判据：有没有**包着发请求**的语句级循环。

    ★ R298c（真读数，包了流式之后立刻中）：
      这条齿原来是「`__call__` 里出现行首 for/while ⇒ 可疑」。
      而 R298 加的**流式解析**必须有循环：
        `async for 行 in r.aiter_lines():`  /  `for 行 in 原始行:`
      ⇒ 报「可能是同槽原地重试」= **假红**。
      它要防的是「**同一个槽上反复发 HTTP**」，而读一个响应体的循环
      **一次 HTTP 都没多发**。

      ⇒ 只认「循环体里含 `c.post(` / `c.request(` / `c.stream(`」的那种循环。
        这是"测能力不测形状"：事实是"一次调用发几次 HTTP"。
    """
    行们 = [l for l in 码.splitlines() if l.strip()]
    进循环深 = 0
    for l in 行们:
        缩 = len(l) - len(l.lstrip())
        if l.lstrip().startswith(("for ", "while ", "async for ")):
            进循环深 = 缩
            continue
        if 进循环深 and 缩 <= 进循环深 and l.strip():
            进循环深 = 0
        if 进循环深 and any(k in l for k in ("c.post(", "c.request(", "c.stream(")):
            return True
    return False


def _selftest() -> int:
    from pathlib import Path as _P

    fails: list[str] = []
    # ★★★★★ R72（2026-10-17）：**分母必须机器数出来，不能声明。**
    #   本文件原来写的是 `N = 13` —— 手写常数。这一轮加了 8 条 R72 齿、
    #   若不改它，报表会照样打「13/13 全绿」：**齿加了、分母没动**。
    #   ★ 同一天 `router.py` / `generic_goal.py` 各犯过一次（R68、R70c）。
    #   ⇒ 分母运行时从源码数；另配下限防成批删除。
    _源码 = _P(__file__).read_text(encoding="utf-8", errors="replace")
    N = _源码.count("fails.append")
    _判据下限 = 18
    if N < _判据下限:
        fails.append(f"R72：判据点只有 {N} 个（下限 {_判据下限}）"
                     "—— 齿被成批删掉而报表照样绿")

    # ══════════════════════════════════════════════════════════════
    # 【R161】「模型不存在」不是「通道死了」（第 76 层）
    # ══════════════════════════════════════════════════════════════
    # 真证据（本机实测）：
    #   aaa 的 /v1/models 有 **8 个**模型，其中 deepseek-ai/deepseek-v4.1-flash
    #   **1.5 秒就通**；而 channels.yml 里 aaa 声明的模型名少了 `deepseek-ai/`
    #   前缀 ⇒ 上游 503 model_not_found ⇒ 判「通道硬死」⇒ 整个通道被扔掉。
    # ★ 后果（这是它为什么必须修）：全机器只剩 1 个槽，
    #   那个槽一遇 504 风暴（第 44 次连挂 4 小时）就整趟白等。
    # ⇒ 修后实测可用槽 **1 → 6**。
    #
    # ★★ 齿要测**结果**，而且要用**假上游**测 —— 不依赖外网、可复现：
    #   假上游的配置模型叫 bad-model（回 503 model_not_found），
    #   但 /v1/models 报的是 good-model（回 200）。
    #   期望：通道**判活**，good-model 被**发现**，并且**真的出现在槽列表里**。
    try:
        import http.server as _hs161
        import json as _json161
        import os as _os161
        import threading as _th161

        class _假上游161(_hs161.BaseHTTPRequestHandler):
            def log_message(self, *a):      # 安静
                pass

            def _发(self, 码, 体: dict):
                _b = _json161.dumps(体).encode("utf-8")
                self.send_response(码)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(_b)))
                self.end_headers()
                self.wfile.write(_b)

            def do_GET(self):
                if self.path.rstrip("/").endswith("/models"):
                    self._发(200, {"data": [{"id": "good-model"}]})
                else:
                    self._发(404, {"error": {"message": "not found"}})

            def do_POST(self):
                try:
                    _n = int(self.headers.get("Content-Length") or 0)
                    _d = _json161.loads(self.rfile.read(_n) or b"{}")
                except Exception:      # noqa: BLE001
                    _d = {}
                _模型 = str(_d.get("model") or "")
                if _模型 == "good-model":
                    self._发(200, {"choices": [{"message": {"content": "ok"}}]})
                else:
                    # ★ 上游真实原话的形状（model_not_found + no available channel）
                    self._发(503, {"error": {
                        "code": "model_not_found",
                        "message": f"No available channel for model {_模型}"}})

        _srv161 = _hs161.HTTPServer(("127.0.0.1", 0), _假上游161)
        _th161.Thread(target=_srv161.serve_forever, daemon=True).start()
        try:
            _os161.environ["_R161_FAKE_KEY"] = "sk-fake"
            _假161 = Channel(
                name="fake161", display_name="fake161",
                base_url=f"http://127.0.0.1:{_srv161.server_port}/v1",
                key_env="_R161_FAKE_KEY",
                models=[ModelSpec(id="bad-model")])
            _报161 = probe_channels([_假161], timeout=5.0)
        finally:
            _srv161.shutdown()
            _srv161.server_close()
            # ★ 假 key 的环境变量**留到最后再清** —— 下面还要用它建 client。
            #   第一版就在这里清了，结果齿自己红了（夹具缺陷，不是产品缺陷）。

        if "fake161" not in (_报161.get("live") or []):
            fails.append(
                "【R161】★★ 配置模型不存在、但通道另有可用模型 ⇒ **不该判通道死**；"
                f"实测判死了：{_报161.get('dead')}")
        else:
            _发161 = [getattr(m, "id", m) for m in
                      ((_报161.get("discovered") or {}).get("fake161") or [])]
            if "good-model" not in _发161:
                fails.append(f"【R161】★ 没发现 good-model（实得 {_发161}）")
            # ★★ 「发现了」不等于「给出去了」（「算了但没给出去」那一族）
            _槽161 = [s for s, _, _ in live_model_pairs([_假161], _报161)]
            if not any("good-model" in s for s in _槽161):
                fails.append(
                    f"【R161】★★★ 发现了可用模型却**没进槽列表**"
                    f"（实得 {_槽161}）—— 「算了，但没给出去」")
            # ★ 端到端：真用这个槽建 client（证明它不是纸面槽）
            try:
                _c161 = build_client(_假161,
                                     next(m for m in (_报161["discovered"]["fake161"])
                                          if getattr(m, "id", m) == "good-model"))
                if str(getattr(_c161, "model", "")) != "good-model":
                    fails.append("【R161】★ 槽建出来的 client 模型名不对")
            except Exception as _e:
                fails.append(f"【R161】★ 用发现的模型建 client 失败：{_e}")

        # ★ 边界：凭据错必须仍是**通道级死**（别把真死洗成活）
        # ★★ 这一条要用**同时含两类词**的串才咬得住：
        #   纯 "invalid api key" 里没有任何模型级死词，
        #   即使删掉通道级判断它也照样返回 False ⇒ **测不出差别**。
        #   （第一版就是这样「无齿」的 —— 齿要让它要测的那件事成为唯一差异。）
        if 是模型级死("HTTP 401 model_not_found: invalid api key"):
            fails.append("【R161】★★ 凭据错被判成了**模型级**死"
                         " ⇒ 会去白试 4 个模型，且掩盖真正该修的东西")
        if 是模型级死("HTTP 401 {\"error\":{\"message\":\"invalid api key\"}}"):
            fails.append("【R161】★★ 凭据错被判成了**模型级**死（纯凭据错）")
        if not 是模型级死("HTTP 503 model_not_found: No available channel for model x"):
            fails.append("【R161】★ 模型不存在没被识别成模型级死")
    except Exception as _e161:
        fails.append(f"【R161】齿自身异常：{type(_e161).__name__}: {_e161}")
    try:
        __import__("os").environ.pop("_R161_FAKE_KEY", None)
    except Exception:      # noqa: BLE001
        pass
    for _f161 in [x for x in fails if "【R161】" in str(x)]:
        print(f"  ✗ gateway {_f161}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R159】模型客户端**必须带超时**（第 74 层）
    # ══════════════════════════════════════════════════════════════
    # 真事故（第 42 次真跑）：进程活着、CPU 60 秒一动不动、日志停在
    #   `Retrying in 1.0s...`，err 末尾是一段 **nginx 错误页 HTML**
    #   ⇒ 它在**读**一个坏掉的响应，而**没有任何东西会打断它**。
    # ★ 根因：`build_client` 一个超时都没传，而 `client_kwargs` 是
    #   **直接透传给 `openai.AsyncClient`** 的 ⇒ 用 openai 默认 600 秒。
    # ★★ 齿要测的是**结果**，不是「有没有 client_kwargs 这个词」：
    #    真的构造一个 client，读它底下的 `timeout`，看 connect/read 是不是
    #    有限的、而且 **connect 比 read 小**（连接不该占掉读的预算）。
    try:
        _ch159 = load_channels()
        if not _ch159:
            fails.append("【R159】读不到任何通道，无法验超时")
        else:
            # ★★★★★ R280d（真读数，2026-11-06）：**齿自己取错了样本。**
            #   原实现写死 `_ch159[0]` —— 而配置里第一个通道是 `aaa`，
            #   它的 key 没配 ⇒ `build_client` 抛 RuntimeError: channel['aaa'] key not found
            #   ⇒ 判据报「齿自身异常」，看上去像"超时没设"，其实**是齿自己坏了**。
            #   这正是台账里「判据自己也会坏，坏了要能看出来」的第 N 次现场：
            #   坏的方式还是"看起来像被测代码错"。
            #   ⇒ 齿挑**真能构造出 client** 的通道；一个都构造不出就如实说"没测到"，
            #     不许把"没有可用样本"混进"被测代码不合格"。
            _c159 = None
            for _c0 in _ch159:
                try:
                    _c159 = build_client(_c0, _c0.models[0])
                    break
                except Exception:                  # noqa: BLE001  缺 key，换下一个
                    _c159 = None
            if _c159 is None:
                fails.append("【R159】所有通道都构造不出 client（缺 key）⇒ 本条**没测到**，"
                             "不算被测代码不合格")
            # ★★★★★ R280e（真读数，2026-11-06）：**判据从错的层读超时。**
            #   原实现读 `_c159.client.timeout` —— 而 `build_client` 返回的对象
            #   把 `client` 做成**惰性**（此刻是 None）⇒ 读到 None ⇒
            #   判据报「底层 client 没有 timeout ⇒ 会卡死 20 分钟」。
            #   而实测外层对象上的超时是
            #     Timeout(connect=5.0, read=180.0, write=180.0, pool=180.0)
            #   —— **配置完全正确**。也就是说：这是**判据自己读错了地方**，
            #   报出来的却是"被测代码有严重缺陷"。
            #   ⇒ 「判据自己也会坏，坏了要能看出来」——这次坏法最难发现：
            #     它红得很有道理（理由写得很吓人），只是**理由不属于这件事**。
            #   修法：先读外层（真值所在），读不到再退回内层 client（兼容两种布局）。
            _t159 = getattr(_c159, "timeout", None)
            if _t159 is None:
                _t159 = getattr(getattr(_c159, "client", None), "timeout", None)
            if _t159 is None:
                fails.append("【R159】★★ 底层 client **没有 timeout** ⇒ "
                             "坏响应会一直卡住（真跑实测卡死 20 分钟）")
            else:
                _连159 = getattr(_t159, "connect", None)
                _读159 = getattr(_t159, "read", None)
                if not _连159 or not _读159:
                    fails.append(f"【R159】★★ 超时不全：connect={_连159} read={_读159}")
                else:
                    # ★★ 上界不能写成「> 600」—— 那正是 **openai 的默认值**，
                    #   于是「完全不传超时」和「传 600」都恰好**不触发**。
                    #   这是齿自己的边界错：**判据的界正好等于事故值**，
                    #   事故就永远咬不住。⇒ 界要**严格低于**事故值。
                    if _读159 > 300:
                        fails.append(f"【R159】★ read 超时 {_读159}s 太长"
                                     "（>300s；openai 默认 600s 正是事故值，"
                                     "一次坏响应能静默卡 10 分钟）")
                    if not (_连159 < _读159):
                        fails.append(f"【R159】★★ connect({_连159}s) 应**小于** "
                                     f"read({_读159}s) —— 连不上就是连不上，"
                                     "不该占掉读的预算")
    except Exception as _e159:
        fails.append(f"【R159】齿自身异常：{type(_e159).__name__}: {_e159}")
    for _f159 in [x for x in fails if "【R159】" in str(x)]:
        print(f"  ✗ gateway {_f159}", flush=True)
    # 【R162/R166】**单次模型调用只许发「一条」真 HTTP。**（第 77/80 层）
    # ══════════════════════════════════════════════════════════════
    # 真数据（第 46 次真跑，504 风暴期 err 里的 Attempt 间隔）：
    #   12:17:20 → 12:22:54 → 12:28:28 = **330 秒/Attempt**；
    #   「Attempt 1..4 全失败」⇒ 22 分钟/槽；6 个槽 ≈ 2.2 小时/次模型调用。
    # ★ 当时的根因：agentscope 的 `max_retries=3` 与 openai client 默认 2 次
    #   **叠乘** = 12 次真 HTTP——两层各自以为自己是唯一的重试者。
    #
    # ★★★★★ R280g（真读数，2026-11-06）：**这条判据测的对象已经被换掉了。**
    #   实测 `build_client` 返回的是 `agentscope_orchestrator.msgs.ChatModel`
    #   ——**自研实现**，不是 openai SDK：
    #       async with httpx.AsyncClient(timeout=..., follow_redirects=True) as c:
    #           r = await c.post(url, json=体, headers=头)      # ← 单次，无重试
    #   它只暴露 context_size/credential/model/stream/timeout；
    #   `max_retries`/`client_kwargs` **构造完就丢了**（文档明写「本层恒不生效」）。
    #   ⇒ 旧判据 `getattr(_c, "max_retries", 3)` 永远读到默认值 3，
    #     与 `getattr(_c.client, "max_retries", 2)` 永远读到 2 ⇒ **恒算 12**
    #     ⇒ 这是**假红**：报得极有道理（12 次、22 分钟），但理由不属于现在的代码。
    #
    #   ⇒ 现在钉的是**能力**而不是**旧字段**：直接读 `ChatModel.__call__` 的源码，
    #     数里面有几处 `c.post(` / `c.request(`。**1 处 = 单次无重试**；
    #     出现循环重试或多处 post 就是叠乘回来了。
    #   ★ 这样即使以后又换实现，判据测的仍是"一次调用发几次真 HTTP"这个**事实**。
    try:
        import inspect as _insp162
        from agentscope_orchestrator import msgs as _m162
        _源162 = _insp162.getsource(_m162.ChatModel.__call__)
        _码162 = chr(10).join(l for l in _源162.splitlines()
                              if l.strip() and not l.strip().startswith("#"))
        _发162 = _码162.count("c.post(") + _码162.count("c.request(")
        if _发162 == 0:
            fails.append("【R162】读不到 ChatModel.__call__ 里的真实发请求点 ⇒ "
                         "本条**没测到**（不算被测代码不合格）")
        elif _发162 > 1:
            fails.append(f"【R162】★★ 单次调用里出现 {_发162} 个发请求点 ⇒ "
                         "同槽重试又回来了（实测一次 504 要 110 秒，"
                         "重试拿不到新信息，而换槽只要 2–3 秒）")
        # ★ 循环重试也是叠乘。但要分清「**语句**级循环」和「推导式里的 for」：
        #   第一版写成 `"for " in _码162` ⇒ 被
        #     `[_转消息(m) for m in (msgs or [])]`
        #   这个**列表推导**咬中 ⇒ 报"可能是同槽原地重试" ⇒ **假红**。
        #   （又一条"判据自己也会坏"，而且这次坏得很难看：它会把正确代码判成重试。）
        #   ⇒ 只认**行首**是 for/while 的**语句**。
        elif _有发送循环162(_码162):
            fails.append("【R162】★ ChatModel 里有**语句级循环** ⇒ 可能是同槽原地重试；"
                         "重试应归 Router 的换槽")
        # 时延护栏：真事故里单次调用能烧 22 分钟，超时必须是有限的
        _c162 = None
        for _c0 in (load_channels() or []):
            try:
                _c162 = build_client(_c0, _c0.models[0])
                break
            except Exception:                  # noqa: BLE001  缺 key，换下一个
                _c162 = None
        if _c162 is not None:
            _t162 = getattr(_c162, "timeout", None)
            _读162 = getattr(_t162, "read", None)
            if _读162 is None:
                fails.append("【R162】ChatModel 没有 timeout.read ⇒ 坏响应会一直挂着")
            elif float(_读162) > 300:
                fails.append(f"【R162】read 超时 {_读162}s 太长（>300s）")
    except Exception as _e162:
        fails.append(f"【R162】齿自身异常：{type(_e162).__name__}: {_e162}")
        print(f"  ✗ gateway {_f162}", flush=True)

    # ══════════════════════════════════════════════════════════════
    # 【R157】自适应通道入口必须**只有一处**
    # ══════════════════════════════════════════════════════════════
    # 真缺口（2026-10-25 实测）：
    #   `select_live_channels("")` 探出 `能用: ['dddddd'] / 硬死: ['aaa','ggg','mf']`
    #   —— 自适应**是对的**，但它只接在 `generic_goal` 一条路径上。
    #   `develop.py` / `real_task.py` 仍是静态 `select_channels` +
    #   每通道只取 `models[0]`（R50 修过的那条缺陷，在这两条路径上没修）。
    # ★ 这是「修了一侧，另一侧没修」的第 8 次；根因是**没有单一入口**。
    # ★ 齿要测的是「入口是不是真的只有一处」，不是「有没有这个函数」。
    try:
        _包 = _P(__file__).resolve().parent
        # ① 函数存在且可调用
        if not callable(globals().get("build_adaptive_router")):
            fails.append("【R157】没有 `build_adaptive_router` 统一入口")
        # ② **兄弟模块不得再自己拼通道**（静态口径 + 每通道一个模型）
        _违规157 = []
        for _f157 in sorted(_包.glob("*.py")):
            if _f157.name in ("gateway.py", "__init__.py"):
                continue
            _t157 = _f157.read_text(encoding="utf-8", errors="replace")
            # 去掉注释与字符串，只看**代码**
            try:
                _树157 = __import__("ast").parse(_t157)
            except SyntaxError:
                continue
            for _n157 in __import__("ast").walk(_树157):
                if (isinstance(_n157, __import__("ast").Call)
                        and isinstance(_n157.func, __import__("ast").Name)
                        and _n157.func.id == "select_channels"):
                    _违规157.append(f"{_f157.name}:{_n157.lineno}")
                # 每通道只取第一个模型 —— R50 的老形态
                if (isinstance(_n157, __import__("ast").Attribute)
                        and _n157.attr == "models"):
                    pass
            # 每通道 models[0] 的文本形态（AST 里是 Subscript）
            for _n157b in __import__("ast").walk(_树157):
                if (isinstance(_n157b, __import__("ast").Subscript)
                        and isinstance(_n157b.value, __import__("ast").Attribute)
                        and _n157b.value.attr == "models"):
                    _违规157.append(f"{_f157.name}:{_n157b.lineno}（每通道只取 models[0]）")
        if _违规157:
            fails.append(
                "【R157】★★ 仍有模块**自己拼通道**（应统一走 "
                f"`build_adaptive_router`）：{_违规157[:6]}")
        # ③ 入口本身必须同时做三件事：探活 / 全部(通道,模型)对 / 接渠道源
        #   ★★ 必须用**语法树**查真实调用，不能查文本 ——
        #     第一版查文本时，我在函数注释里写着「接 `渠道源` …」，
        #     注入「不接渠道源」之后**注释还在** ⇒ **无齿**。
        #     ★ 这是「文本会骗人，语法树不会」的又一次。
        _ast157 = __import__("ast")
        _fn157 = None
        for _n157c in _ast157.walk(_ast157.parse(_源码)):
            if (isinstance(_n157c, _ast157.FunctionDef)
                    and _n157c.name == "build_adaptive_router"):
                _fn157 = _n157c
        if _fn157 is None:
            fails.append("【R157】找不到 `build_adaptive_router` 定义")
        else:
            _被调157 = set()
            for _n157d in _ast157.walk(_fn157):
                if isinstance(_n157d, _ast157.Call):
                    _g = _n157d.func
                    if isinstance(_g, _ast157.Name):
                        _被调157.add(_g.id)
                    elif isinstance(_g, _ast157.Attribute):
                        _被调157.add(_g.attr)
            for _必157, _名157 in ((("select_live_channels", "探活"),
                                   ("live_model_pairs", "全部(通道,模型)对"),
                                   ("渠道源", "持续重探"))):
                if _必157 not in _被调157:
                    fails.append(f"【R157】★★★ 统一入口**没有真的调用**「{_名157}」"
                                 f"（`{_必157}` 不在调用里）")
    except Exception as _e157:
        fails.append(f"【R157】齿自身异常：{type(_e157).__name__}: {_e157}")
    for _f157b in [x for x in fails if "【R157】" in str(x)]:
        print(f"  ✗ gateway {_f157b}", flush=True)

    # ① 分类：硬死判据（凭据错 / 模型不存在 ⇒ 重试无意义）
    if _classify_failure(401, "invalid API key") != 硬死:
        fails.append("401 应判硬死")
    if _classify_failure(404, "model_not_found") != 硬死:
        fails.append("404 model_not_found 应判硬死")
    # ★★★★★ R126：**状态码不是因由** —— 消息说「请稍后重试」的 400 必须判「读不到」
    #   真证据（12:42→14:12 那一跑的最后一行）：
    #     `BadRequestError: Error code: 400 -
    #      {'message': '上游服务异常，请稍后重试(request id: …)'}`
    #   ⇒ 被 `_HARD_DEAD_CODES` 里的 400 判死 ⇒ 整趟跑死。
    _400瞬时 = ("Error code: 400 - {'error': {'message': '上游服务异常，请稍后重试"
                "(request id: 202610210556136359635658268d9d6CzNWKyer)', "
                "'type': 'invalid_request_error'}}")
    if _classify_failure(400, _400瞬时) != 读不到:
        fails.append("R126 ★ 消息明写「请稍后重试」的 400 被判**硬死** ⇒ "
                     "通道被摘 ⇒ 整趟跑死（真跑实测）")
    if not 是上游瞬态(_400瞬时):
        fails.append("R126 ★ `是上游瞬态` 认不出「上游服务异常，请稍后重试」")
    # ★ 反向：真硬死不许被「瞬时」吞掉（不放松）
    if _classify_failure(401, "invalid api key") != 硬死:
        fails.append("R126 ★ 401 + invalid api key 不再判硬死 ⇒ 口径被放松")
    if 是上游瞬态("invalid api key"):
        fails.append("R126 ★ `是上游瞬态` 把「凭据错」当瞬时 ⇒ 会无限重试")
    # ★ 5xx / 429 是瞬时（`Error code: NNN` 形式也要认）
    for _码126 in ("Error code: 520", "Error code: 503", "Error code: 429"):
        if not 是上游瞬态(_码126):
            fails.append(f"R126 ★ `是上游瞬态` 认不出 {_码126}")

    # ② 分类：**读不到**（超时 / 5xx ⇒ **不是死**）
    if _classify_failure(None, "TimeoutError: read operation timed out") != 读不到:
        fails.append("超时应判**读不到**（不是死）—— 这是 R18 的核心")
    if _classify_failure(503, "Service Unavailable") != 读不到:
        fails.append("503 应判读不到")
    if _classify_failure(504, "Gateway time-out") != 读不到:
        fails.append("504 应判读不到")
    # ③ ★ 认不出的码一律归「读不到」（代价不对称：归错方向的代价见下）
    if _classify_failure(599, "weird") != 读不到:
        fails.append("认不出的码应归读不到（宽进严出）")

    # ④ ★★★★★ 真缺陷回归：**慢通道不得被判死**。
    #   用假 _probe_once 注入：dddddd 超时（读不到），aaa 401（硬死）。
    原 = globals()["_probe_once"]

    class 假通道:
        def __init__(self, name, key="k", 模型表=("m",)):
            self.name = name
            self._key = key
            self.models = [type("M", (), {"id": x})() for x in 模型表]
            self.base_url = "http://x/v1"

        @property
        def api_key(self):
            if self._key is None:
                raise RuntimeError("no key")
            return self._key

    def 假探(ch, model, timeout):
        if ch.name == "慢":
            return 读不到, "TimeoutError: read operation timed out"
        if ch.name == "死":
            return 硬死, "HTTP 401 invalid API key"
        return 活, "HTTP 200"

    try:
        globals()["_probe_once"] = 假探
        rep = probe_channels([假通道("慢"), 假通道("死"), 假通道("好")])
        if rep["live"] != ["好"]:
            fails.append(f"活通道应只含好，实得 {rep['live']}")
        if "慢" not in rep["unreadable"]:
            fails.append("慢通道应进 unreadable 桶（**不是 dead**）")
        if "慢" in rep["dead"]:
            fails.append("★★★ 慢通道**不得**进 dead 桶 —— 这就是 R18 的缺陷")
        if "死" not in rep["dead"]:
            fails.append("401 通道应进 dead 桶")

        # ★★★★★ 核心不变量：**只有「慢」时不得拒跑**（R18 前这里会拒跑）
        class 假Prof:
            pass

        原选 = globals()["select_channels"]
        try:
            globals()["select_channels"] = lambda name="": [假通道("慢")]
            picked, rep2 = select_live_channels("", probe=True)
            if [c.name for c in picked] != ["慢"]:
                fails.append("★★★ 全通道只是「慢」时必须**仍交给 Router 试**"
                             "（拒跑＝用一次坏运气否掉整台机器）")
        finally:
            globals()["select_channels"] = 原选

        # ★ 但**全硬死**仍须如实返回空（那时重试确实无意义）
        try:
            globals()["select_channels"] = lambda name="": [假通道("死")]
            picked3, rep3 = select_live_channels("", probe=True)
            if picked3:
                fails.append("全硬死应返回空列表（如实报，不静默退回配置口径）")
        finally:
            globals()["select_channels"] = 原选

        # ⑤ 探活重试：瞬时失败重试一次后成功 ⇒ 判活（一次坏运气不判死）
        计 = {"n": 0}

        def 抖动(ch, model, timeout):
            计["n"] += 1
            return (读不到, "timeout") if 计["n"] == 1 else (活, "HTTP 200")

        globals()["_probe_once"] = 抖动
        globals()["time"] = __import__("time")
        类, why = probe_model(假通道("抖"), 假通道("抖").models[0],
                              timeout=1, retries=1, backoff=0.0)
        if 类 != 活:
            fails.append(f"瞬时失败重试一次后应判活，实得 {类}（{why}）")
        if 计["n"] < 2:
            fails.append(f"应重试（调用 {计['n']} 次）")

        # ════════════════════════════════════════════════════════════
        # ★★★★★ R49/R50：可用性必须按 **(通道,模型) 对** 判定（3 条）
        # ════════════════════════════════════════════════════════════
        # 【真跑抓出】旧实现每通道**只探 `models[0]`**，调用方也只取
        #   `c.models[0]` ⇒ 一个通道声明的其余模型**永远用不到**。
        #   实测后果：`dddddd` 第一个模型 503 时，配置里明明还有 `kimi-k3`
        #   和**另一个域名**的 `mf`，却没有任何路径会去试它们
        #   ⇒ 我据此得出"上游全挂"的**假结论**。
        def 按模型(ch, model, timeout):
            # 该通道的 `首` 模型硬死、`次` 模型活 —— 这正是被旧实现压掉的情形
            if str(model.id) == "首":
                return 硬死, "HTTP 503 model_not_found"
            return 活, "HTTP 200"

        globals()["_probe_once"] = 按模型
        _双 = 假通道("双", 模型表=("首", "次"))
        _rep49 = probe_channels([_双])
        # ① 通道级：**至少一个模型活** ⇒ 该通道算活（旧实现会判死）
        if "双" not in _rep49["live"]:
            fails.append("★ R49：通道有一个模型活时**不得**判该通道死"
                         "（旧实现只看 models[0]）")
        # ② 逐对判定必须落盘（否则调用方无从知道哪个模型可用）
        if (_rep49.get("pairs") or {}).get("双|次", {}).get("类") != 活:
            fails.append(f"★ R49：逐(通道,模型)判定缺失：{_rep49.get('pairs')}")
        # ③ ★★★★★ R50：可用的**模型对**必须包含第二个模型
        #    （旧实现 `c.models[0]` ⇒ 这里只会得到 `首`，而 `首` 是硬死的）
        _对 = live_model_pairs([_双], _rep49)
        if [n for n, _, _ in _对] != ["双:次"]:
            fails.append(f"★ R50：可用模型对应为 ['双:次']（硬死的『首』须剔除、"
                         f"活的『次』须保留），实得 {[n for n, _, _ in _对]}")
        # ④ 无报告（probe=False）⇒ 配置里声明的**全部**模型都是候选
        if len(live_model_pairs([_双], None)) != 2:
            fails.append("★ R50：无探活报告时配置里的全部模型都应算候选")
    finally:
        globals()["_probe_once"] = 原

    # ══════════════════════════════════════════════════════════════
    # ★★★★★ R72：**渠道源 —— 持续加载 + 自适应重探**（2026-10-17）
    # ══════════════════════════════════════════════════════════════
    # 这一组齿锁的是"环境变了，产品自己跟上"，以及"跟不上时不许假装跟上"。
    import tempfile
    import time as _t
    _tmp = Path(tempfile.mkdtemp(prefix="orch_ch72_"))
    try:
        _f = _tmp / "channels.yml"
        _f.write_text(
            "channels:\n"
            "  甲:\n    base_url: http://a/v1\n    key_env: K_A\n"
            "    models:\n      - id: m1\n", encoding="utf-8")
        _装载次 = {"n": 0}
        _探次 = {"n": 0}

        # ★★ 夹具必须**自洽**（本库反复踩的坑，我又踩了一次）：
        #   第一版装载桩写死返回 `[假通道("甲", …)]`、**忽略文件内容** ⇒
        #   于是"配置改了"这件事在装载结果上**看不出来** ⇒ ㉓ 假红。
        #   这里改成**真的 `load_channels`**：配置改了，装载结果就真的变。
        def _装载72(名):
            _装载次["n"] += 1
            return load_channels(_f)

        def _探72(channels, timeout=25.0):
            _探次["n"] += 1
            return {"probed": len(channels), "probed_pairs": len(channels),
                    "live": [c.name for c in channels], "dead": {},
                    "unreadable": {},
                    "pairs": {f"{c.name}|{m.id}": {"类": 活, "因": "HTTP 200"}
                              for c in channels for m in c.models}}

        # ★★ 节流要用一个**真的会挡住**的间隔来测。
        #   第一版我设 `最小重探间隔=0.0` —— 那等于**不节流**，
        #   于是 ㉒ 测的其实是"没节流时确实会探"，**测错了对象**（假红）。
        src = 渠道源("", 最小重探间隔=999.0, 探活=_探72, 装载=_装载72, 路径=_f)
        # ㉑ 第一次刷新必须**真的装载 + 真的探**（强制 ⇒ 忽略节流）
        _对1 = src.刷新(强制=True)
        if [n for n, _, _ in _对1] != ["甲:m1"]:
            fails.append(f"R72：渠道源首次刷新应得 ['甲:m1']，实得 "
                         f"{[n for n, _, _ in _对1]}")
        if _装载次["n"] != 1 or _探次["n"] != 1:
            fails.append(f"R72：首次刷新应各做一次（装载 {_装载次['n']} / "
                         f"探 {_探次['n']}）")

        # ㉒ **节流**：配置没变 + 间隔不足 ⇒ 不重探（探活是网络动作）
        _探次["n"] = 0
        _装载次["n"] = 0
        src.刷新()
        if _探次["n"] != 0:
            fails.append(f"R72：节流没生效（配置没变却探了 {_探次['n']} 次）")
        if _装载次["n"] != 0:
            fails.append(f"R72：配置没变却重读了文件（{_装载次['n']} 次）")

        # ㉓ ★★ **配置一变就立刻重探**（节流不挡配置变更）
        #    —— 用户改 yaml 就是在说"现在按这个来"，让它等节流是错的
        _f.write_text(
            "channels:\n"
            "  甲:\n    base_url: http://a/v1\n    key_env: K_A\n"
            "    models:\n      - id: m1\n"
            "  乙:\n    base_url: http://b/v1\n    key_env: K_B\n"
            "    models:\n      - id: m2\n", encoding="utf-8")
        _t.sleep(0.05)          # 保证 mtime 真的变（同秒写入会看不出差别）
        os.utime(_f, None)
        _探次["n"] = 0
        _装载次["n"] = 0
        _对2 = src.刷新()
        if _装载次["n"] == 0:
            fails.append("R72：配置变了却**没有重读**渠道文件")
        if _探次["n"] == 0:
            fails.append("R72：配置变了却**被节流挡住没重探**"
                         "（用户改 yaml 应当立刻生效）")
        if "乙:m2" not in [n for n, _, _ in _对2]:
            fails.append(f"R72：新配的通道没进候选：{[n for n, _, _ in _对2]}")
        if src.新接纳 != 1:
            fails.append(f"R72：新接纳计数应为 1，实得 {src.新接纳}"
                         "（首次装载是初始化，不该算成接纳）")
        # ★ 反向齿：首次就全死、第二次才活 ⇒ **那种确实该算接纳**
        #   （不能简单用"`_对` 非空"来判断，会漏掉它）
        #   ★★ 桩必须用**硬死**才真的把槽排除掉 —— 按 R18/R50 口径，
        #      `pairs` 里查不到只算"读不到"，**读不到不是死**、槽仍在池子里。
        #      我第一版桩给的是空 `pairs` ⇒ 槽一个没少 ⇒ 假红。
        def _探全硬死(ch, timeout=25.0):
            return {"probed": len(ch), "probed_pairs": len(ch), "live": [],
                    "dead": {c.name: "HTTP 401" for c in ch}, "unreadable": {},
                    "pairs": {f"{c.name}|{m.id}": {"类": 硬死, "因": "401"}
                              for c in ch for m in c.models}}

        _空源 = 渠道源("", 最小重探间隔=0.0, 装载=_装载72, 路径=_f,
                       探活=_探全硬死)
        _空源.刷新(强制=True)
        if _空源.新接纳 != 0:
            fails.append(f"R72：首次全死时不该记接纳（实得 {_空源.新接纳}）")
        if _空源._对:
            fails.append(f"R72：全硬死时不该还有候选槽（得 "
                         f"{[n for n, _, _ in _空源._对]}）")
        _空源._探活 = _探72
        _空源.刷新(强制=True)
        if _空源.新接纳 != 2:
            fails.append(f"R72：首次全死后恢复应记 2 个接纳，实得 {_空源.新接纳}")

        # ㉔ `强制=True` 忽略节流（启动时用）
        src.最小重探间隔 = 999.0
        _探次["n"] = 0
        src.刷新(强制=True)
        if _探次["n"] != 1:
            fails.append(f"R72：强制刷新应忽略节流，实探 {_探次['n']} 次")

        # ㉕ ★ **探活抛异常不许把调用方搞崩**（探活是辅助，真调用才是判据）
        def _炸72(channels, timeout=25.0):
            raise RuntimeError("探活服务挂了")
        src._探活 = _炸72
        try:
            src.刷新(强制=True)
            fails.append("R72：探活抛异常时渠道源应抛出（不该静默返回旧表当成功）")
        except RuntimeError:
            pass

        # ㉖ 渠道文件位置可自适应（`ORCH_CHANNELS_FILE`）
        _旧env = os.environ.get(_CHANNELS_ENV)
        try:
            os.environ[_CHANNELS_ENV] = str(_f)
            if 渠道文件() != _f:
                fails.append(f"R72：{_CHANNELS_ENV} 未生效（得 {渠道文件()}）")
            # ★★★ **光"知道路径"不算生效 —— 必须真的从那个路径装载。**
            #   第一版就是"只在 mtime 检查上生效、装载仍读默认文件"，
            #   这个齿就是为它写的（否则 `ORCH_CHANNELS_FILE` 是个摆设）。
            _源env = 渠道源("")
            _源env.装载配置()          # 返回值是"是否真的重读了"，不是通道
            if [c.name for c in _源env._channels] != ["甲", "乙"]:
                fails.append(
                    f"R72：{_CHANNELS_ENV} 指向的文件**没有被真的装载**"
                    f"（得 {[c.name for c in _源env._channels]}）"
                    "—— 路径成了摆设")
            os.environ.pop(_CHANNELS_ENV, None)
            if 渠道文件() != _CHANNELS_FILE:
                fails.append(f"R72：不设 {_CHANNELS_ENV} 时应回到仓库自带配置")
        finally:
            if _旧env is None:
                os.environ.pop(_CHANNELS_ENV, None)
            else:
                os.environ[_CHANNELS_ENV] = _旧env

        # ㉗ `全部对` 必须给出**配置声明的全部**（含当前不可用的）
        #    —— 否则"恢复回来的槽"会被当成从来没出现过，日志里看不出它回来过
        src._channels = [假通道("丙", 模型表=("x", "y"))]
        if len(src.全部对()) != 2:
            fails.append(f"R72：`全部对` 应给配置里的全部模型（2 个），"
                         f"实得 {len(src.全部对())}")
        # ㉘ 状态里必须是**事实**，不是判断
        _状 = src.状态()
        for _k in ("渠道文件", "配置装载次数", "重探次数", "新接纳槽数", "当前可用槽"):
            if _k not in _状:
                fails.append(f"R72：状态缺事实字段 {_k}")

        # ════════════════════════════════════════════════════════════
        # ★★★★★ R181：**已确诊硬死的槽，不因「这次读不到」重回池子**
        # ════════════════════════════════════════════════════════════
        # 真证据（第 86 次真跑 · `_真跑R186_err.log`）：启动时已确诊
        #   `dddddd|kimi-k3`（503 model_not_found）等硬死，槽表只有 2 个；
        #   后续刷新它们探活**超时**（读不到）⇒ 按 R18「读不到仍用」又回池子
        #   （日志：`池子现有 4 槽`）⇒ 每轮白烧 4 次必然失败的调用 + 30 秒等待
        #   ⇒ 6 轮预算在轮1 烧光（整趟 6 分钟归零）。
        # ★ 判据需要「这个槽有没有被确诊过」，手里拿到的却是「这次探到了什么」。
        _丙181 = 假通道("丙", 模型表=("坏",))
        _序181 = {"n": 0}

        def _探181(channels, timeout=25.0):
            _序181["n"] += 1
            if _序181["n"] == 1:
                # 第一次：**确诊硬死**
                return {"probed": 1, "probed_pairs": 1, "live": [],
                        "dead": {"丙": "HTTP 503 model_not_found"},
                        "unreadable": {},
                        "pairs": {"丙|坏": {"类": 硬死,
                                             "why": "HTTP 503 model_not_found"}}}
            # 第二次：**只是超时**（读不到）—— 不是死，但结论没被推翻
            return {"probed": 1, "probed_pairs": 1, "live": [],
                    "dead": {}, "unreadable": {"丙": "timeout"},
                    "pairs": {"丙|坏": {"类": 读不到, "why": "timeout"}}}

        _源181 = 渠道源("", 最小重探间隔=0.0, 探活=_探181,
                       装载=lambda 名: [_丙181])
        if [n for n, _, _ in _源181.刷新(强制=True)]:
            fails.append("★ R181：首次探到硬死的槽不该进池子")
        _第2 = [n for n, _, _ in _源181.刷新(强制=True)]
        if _第2:
            fails.append(f"★ R181：**已确诊硬死**的槽只因这次超时又回池子（{_第2}）"
                         " ⇒ 每轮白烧必然失败的调用 + 等待 ⇒ 预算在轮1 烧光")
        # ★ 反向：真探到活 ⇒ 结论被推翻 ⇒ **必须回来**（这才叫「恢复」）

        def _探活181(channels, timeout=25.0):
            return {"probed": 1, "probed_pairs": 1, "live": ["丙"],
                    "dead": {}, "unreadable": {},
                    "pairs": {"丙|坏": {"类": 活, "why": "HTTP 200"}}}

        _源181._探活 = _探活181
        if not [n for n, _, _ in _源181.刷新(强制=True)]:
            fails.append("★ R181：真探到活时槽必须回到池子（恢复不许被台账挡住）")
        # ★ 边界：**从没确诊过**的槽，读不到仍必须用（R18 口径不许被改坏）

        def _探慢181(channels, timeout=25.0):
            return {"probed": 1, "probed_pairs": 1, "live": [], "dead": {},
                    "unreadable": {"丁": "timeout"},
                    "pairs": {"丁|慢": {"类": 读不到, "why": "timeout"}}}

        _源181b = 渠道源("", 最小重探间隔=0.0, 探活=_探慢181,
                        装载=lambda 名: [假通道("丁", 模型表=("慢",))])
        if not [n for n, _, _ in _源181b.刷新(强制=True)]:
            fails.append("★ R181：**从没确诊过**的槽读不到时仍须进池子"
                         "（R18「超时 ≠ 死」不许被改坏）")
    finally:
        import shutil as _sh
        _sh.rmtree(_tmp, ignore_errors=True)

    if fails:
        print(f"gateway 自检：失败 {len(fails)} · 判据点 {N}（下限 {_判据下限}）"
              f"—— 红 {len(fails)}")
        for x in fails:
            print(f"  ✗ {x}")
        return 1
    print(f"gateway 自检：全绿 · 判据点 {N}（下限 {_判据下限}）· 失败 0"
          "（探活三类判定 · 慢≠死 · 只有硬死才排除 · 瞬时重试"
          " · ★R49/R50 可用性按(通道,模型)对判定 · ★R72 渠道源持续加载+自适应重探）")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("通道网关（--selftest 跑自检）")
