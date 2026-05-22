# 自有高表现样本库使用说明

1. 先把你认为真正高表现的文章记录到 `branding/self_benchmark_articles.yaml`。
2. 真实样本请设置 `enabled: true`。
3. 草稿槽位、模板槽位请保持 `enabled: false`，或者 `status: template`。
4. 补全 `observed_metrics`，至少填写 `impression_count` 和 `read_count`。
5. 补充你确认有效的 `inferred_patterns`。
6. 运行 `python .\main.py --build-benchmark-report` 重新生成策略报告。
7. 如果要自动采集真实高表现样本，运行 `python .\main.py --collect-self-benchmark-samples`。
8. 这个命令现在默认优先从在线热点源抓取高分参考样本，抓不到时才回退到本地 performance ledger。

注意：`enabled: false` 的条目不会参与标题、正文和审核策略学习。
