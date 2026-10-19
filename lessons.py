"""Short, generic lesson cards. Public copy only; personal evidence stays in the ledger."""

LESSONS = {
    'trigger_vs_location': dict(title='到位不等于该进场',
        body='价格到了支撑只是「位置」。触发是市场给出的回应：收盘收回关键位，或突破信号 K 线的高点。没等到触发就进，是在替市场做决定。',
        do='先挂提醒在触发价，触发出现再下单；提前进场就只开试单。'),
    'size_by_stop': dict(title='数量由止损决定',
        body='先找出判断错了会在哪里，再用「风险金额 ÷ 止损距离」算数量。止损宽就开小，止损窄才开大，单笔亏损才会一直稳定。',
        do='下单前在卡片里只填入场和止损，照着给出的数量开。'),
    'add_recalc': dict(title='加仓要重算整笔风险',
        body='加仓后整笔仓位共用一个止损。原来亏 60 的单，加一次可能就变成亏 140，不是每次加仓都重新拿到一份额度。',
        do='加仓前勾选「这是加仓」，看合计风险有没有超上限。'),
    'correlated_risk': dict(title='同方向的单会一起亏',
        body='BTC 和山寨同时做多，市场一跌往往一起打止损。两笔各亏 100，其实是一个判断押了 200。',
        do='看顶部的「合计风险」，同方向合计不超过账户上限。'),
    'cooldown': dict(title='亏完先停一停',
        body='止损后马上再开，多半是想把亏的赚回来，而不是看到了新机会。冷静期内的单，判断质量通常更差。',
        do='冷静期内只观察和写复盘，期满再按三步流程重新检查。'),
    'keep_targets': dict(title='别把止盈越改越近',
        body='开仓后把止盈往回收，盈亏比会从 2R 掉到 1R 以下：赢的时候赚得少，输的时候照样亏满。',
        do='止盈只在出现新的结构理由时调整，并写一句为什么。'),
    'early_exit': dict(title='提前离场要有信号',
        body='「感觉要跌破」不是信号。没到止损就跑，长期会把好单也切成小亏。',
        do='提前离场前确认一个可见的失效信号，比如决策周期收盘跌破结构位。'),
    'discretionary_limit': dict(title='盘感单要有时限',
        body='盘感可以做，但要说清楚押的是什么反应、多久没出现就走。没有时限的盘感单，最后常常变成扛单。',
        do='写下「押什么」和「几根 K 线没反应就走」，并且只开半仓。'),
    'closed_bars': dict(title='只看已收盘的 K 线',
        body='还没收盘的 K 线随时会变：看着像锤子线，收盘可能变成大阴线。所有形态都以收盘为准。',
        do='等决策周期这一根收盘后再判断，不在 K 线中途追单。'),
    'good_loss': dict(title='好的亏损和运气赚钱',
        body='按计划止损的亏损是好交易；没计划却赚了是运气。只看盈亏会让你奖励坏习惯、惩罚好习惯。',
        do='复盘时先问「我按计划做了吗」，再看赚没赚。'),
}


def pick(codes):
    seen = []
    for code in codes:
        if code in LESSONS and code not in seen:
            seen.append(code)
    return [dict(LESSONS[c], code=c) for c in seen[:3]]
