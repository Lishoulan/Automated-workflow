# 供应商账单导出（billing exports）

`scripts/reconcile_generation_costs.py` 只接受人工提供的账单导出 CSV，
**不调用任何供应商账单 API**（这类接口通常没有、或需要财务权限）。

## 放置位置

```
billing_exports/robot_notes.csv       # 与 --account / receipt.account_key 一致
```

工作流 `Cost Reconciliation` 每周一 11:23（北京时间）读取 `billing_exports/robot_notes.csv`，
**默认预览、不落盘**；文件不存在时直接跳过（退出码 0，不报警）。

## CSV 列（必须完全一致，缺列或多列都会被拒绝）

| 列名 | 说明 |
|---|---|
| `account_key` | 必须等于 `--account` 传入的值，例如 `robot_notes` |
| `provider_request_id` | 与回执里 `provider_request_id` 完全相同的请求号 |
| `amount` | 实扣金额，Decimal，≥0 且 ≤1e12 |
| `currency` | 只接受 `CNY` / `USD` |
| `charged_at` | **必须带时区**（ISO 8601，例如 `2026-10-21T03:23:00+08:00`） |
| `status` | 只能是 `charged` |

示例：

```csv
account_key,provider_request_id,amount,currency,charged_at,status
robot_notes,req_abc123,0.0021,CNY,2026-10-21T03:23:00+08:00,charged
```

## 硬性校验（任一不满足即整份拒绝，不会部分导入）

- 同一个 `provider_request_id` 出现金额/币种/时间冲突 → 拒绝
- `amount` 精度超过回执格式能承载的范围 → 拒绝
- 请求号以 `sk-` / `cfut_` 开头（疑似密钥）→ 拒绝
- 导入前会先做全量 preflight，中途崩溃可重复导入恢复（写入是原子的）

## 隐私提醒

这个文件只包含请求号与金额，**不要**放入 API Key、Cookie、提示词正文。
仓库里已用 `.gitignore` 默认忽略除本 README 与 `.gitkeep` 以外的所有内容。

## 落盘（--apply）只在人工手动触发时可用

```bash
# 本地对静止快照执行（不要在正在生产的目录上跑）
python scripts/reconcile_generation_costs.py \
  --snapshot-dir /path/to/quiescent/output/accounts/robot_notes \
  --csv billing_exports/robot_notes.csv \
  --account robot_notes --apply
```
