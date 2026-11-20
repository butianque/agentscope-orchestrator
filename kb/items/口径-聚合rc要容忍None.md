---
id: 口径-聚合rc要容忍None
类别: 规则
强度: 必须
来源: 第210轮真事故：TypeError 且门数=0，看着像门没跑
等级: SOLID
版本: 1
更新: 2026-11-03T12:05:41
---
症状：`TypeError: '>' not supported between instances of 'NoneType' and 'int'`，且 `门数=0` 看着像门根本没跑。修法：聚合各门 `rc` 必须用 `isinstance` 判类型；`rc=None` 是合法语义（该门不跑命令），`get(k, 默认值)` 在键在值为 None 时不生效
