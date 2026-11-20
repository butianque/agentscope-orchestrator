---
id: 口径-计数解析key前缀优先
类别: 规则
强度: 必须
来源: 第210轮真事故：passed=10 failed=3 被解成 failed=10 skipped=3
等级: SOLID
版本: 1
更新: 2026-11-03T12:05:41
---
症状：计数错位 —— `passed=10 failed=3 skipped=0` 被解析成 `failed=10 skipped=3`。修法：解析测试计数必须把 key-前缀形式放**最前面**并加 `(?<!\w=)` 负向环视，否则 `(\d+)\s+failed` 会跨过等号吞掉 passed 的数字
