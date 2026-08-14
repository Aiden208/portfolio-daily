# portfolio-daily (私有)

每日持仓资产日报，由 GitHub Actions 云端自动运行，不依赖任何本地电脑开机。

- 运行时间：北京时间 周一至周五 21:10（可能有几分钟延迟）
- 流程：拉取最新净值/行情 → 计算市值盈亏 → 生成 HTML 日报存入 `daily/` → Server酱 推送微信摘要
- 手动触发：仓库页 **Actions → Daily Portfolio Report → Run workflow**（手机浏览器也可以）
- 历史日报：`daily/` 目录按日期存档，`index.html` 为导航页

## 修改持仓

编辑 `portfolio_config.json`（份额 shares / 成本 cost），提交后自动触发一次刷新。

> 注意：仓库含微信推送密钥（`serverchan_key`），务必保持 Private，不要设为 Public。
