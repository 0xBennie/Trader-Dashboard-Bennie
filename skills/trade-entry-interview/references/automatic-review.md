# 自动记录与每日复盘

这是用户已授权的私人账本工作流；只读取交易页面，不操作买卖、撤单、钱包签名或转账。

## 浏览器采集

1. 先使用当前可用的 Browser skill。优先已有已登录 Variational 页面，读取仓位、交易历史、订单历史、已实现 PNL。保存可见文本和必要截图到私有目录；不读取 cookies、存储或未公开接口。
2. 登录失效或扩展不可用时记录 `login_required` / `unavailable`。读不到仓位不代表空仓；不要标记旧仓平仓。日常无新交易、无异常则不推送，避免打扰。
3. 先读取线上已有 Variational 日志及其 `browser_observation.identity`，用成交标识、合约和实际开仓时间查重。截图补记若缺少开仓时间，用均价、数量和原截图逐项核对后显式传 `journal_id`；不能只按币种合并。新一轮交易必须用新的稳定 identity。
4. 页面事实转为以下 JSON，调用应用自带 `browser_journal.ingest(payload)` 或 `python browser_journal.py observation.json`，最后回读日志和状态。入口只更新事实，保留用户的理由与计划，不伪装成交易所 API 对账。

```json
{
  "status": "complete",
  "source_url": "https://omni.variational.io/perpetual/DEMO",
  "observed_at": 1790000000000,
  "records": [{
    "identity": "DEMO:actual-entry-fill-id:actual-entry-time",
    "symbol": "DEMO",
    "direction": "LONG",
    "entry_price": "100",
    "quantity": "2",
    "position": {"mark_price": "99", "unrealized_pnl": "-2"},
    "orders": [{"kind": "stop_loss", "price": "95", "close_position": true}]
  }]
}
```

示例是合成数据，不能导入用户账本。`opened_at` 可选，必须是真实成交时间且带时区。`orders:null` 为未读取，`orders:[]` 才是成功读到空挂单表。持仓数量必须是页面观察数量，不从名义价值猜测。

全仓平仓需要 `close`：`closed_ms`、`quantity`、`close_price`、`trade_display_id`、`trade_status:"confirmed"`、`order_status:"Completed"`、`reduce_only:true`。必须核对完整轮次、全仓数量，不能从一笔部分卖出或持仓消失推断全平。页面已实现盈亏可加 `realized_pnl_display` 和 `realized_pnl_asset`；手续费、资金费、最终净额未知保留空值。加减仓导致数量不匹配时保留待核对，先还原过程再明确关联。

## 每日 21:00（Asia/Hong_Kong）

- 每次文字复盘先读取 `trader_memory.context(symbol, exclude=journal_id)`，再调用 `market_replay.capture(journal_id)`。引用已保存 K 线、当时可获取的 OI / 资金费与相对 BTC 表现，明确数据来源、时间范围和缺口。缺数据只报告未完成，不用今天行情替代当时行情。
- 先用 `before_anchor` 审视入场依据，再用 `after_anchor` 讨论结果和执行；未知入场时间的 `recorded_observation` 不能当成精确入场回放。跨交易所参考不用于证明真实成交、止损触发或收益。
- 个人记忆由已确认规则、原始理由及个人复盘生成，位于私有 `data/trader-memory.md`；可用 `trader_memory.save()` 经用户确认增改，保留来源和版本。新结论先标 `hypothesis`，不自动升级为已验证经验。每天复盘最多提出三项可验证改进。

- 服务器 `review_automation.Worker` 每 30 秒检查到期日报，保存前一日 21:00 至当日 21:00 的事实摘要。关闭网页不停止服务器任务；服务中断后补存最近到期的一份。已存日报保留生成时的证据，不静默重写。
- 日报不依赖 AI Key。区分平仓、新归档、旧记录待复盘、未知平仓时间、未填写入场理由；持仓清单是生成时最近观察，不伪造 21:00 持仓快照。
- 服务器自动将最近 30 天有证据且无对应报告的记录及已确认平仓加入 AI 队列。无 Key 等待，连接后处理。失败最多三次，页面支持重试；AI 输出不写入个人复盘。
- Codex 的每日推送任务读取 `review_automation.listing()`，只总结实际保存的数据；必要时先通过浏览器补核对。报告香港时间窗口、数据更新时间、各交易所覆盖、已确认交易与最多三条下一步。美元、USDT、USDC 不混加，浮盈亏不当最终收益。缺模型 Key 不影响 Codex 对已提供证据做文字复盘，但网站不能声称已调用外部模型。
- 本地 Codex 定时浏览器任务依赖电脑、应用、扩展及登录可用；不宣称是服务器上的持续 Variational API。页面有中断状态，不能自动越过登录或签名。
