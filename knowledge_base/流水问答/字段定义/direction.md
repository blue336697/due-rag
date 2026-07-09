# 收支方向

收支方向标记该笔交易为收入还是支出。系统内使用以下四种值：

- `income`：收入，金额增加账户余额
- `expense`：支出，金额减少账户余额
- `unknown`：无法从原始数据判断收支方向，系统默认值
- `not_counted`：不计入收支统计，如内部转账、同名账户互转、退款原路返回等

## 判定方式

- 单列金额（amount_type=A）：金额为正 → income，金额为负 → expense
- 收支分列（amount_type=B/B_with_label）：借方列 → expense，贷方列 → income
- 配置可指定 `determineIncomeExpenseByAmount` 来通过金额正负号判定方向

## 常见别名

- 收/支
- 收支
- 借贷状态
- 收/支/其他
- 方向
- 贷/借
