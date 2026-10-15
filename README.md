# Trader-Dashboard(Bennie)

**交易手记 · 自托管的交易记录与复盘工作台**

个人交易记录与复盘工作台。使用 Python、SQLite 和原生 JavaScript，自托管运行；支持 Binance USDⓈ-M 只读采集、外部交易所截图归档、可选 AI 服务商分析和事前计划审核。

## 当前能力

- 后台采集成交、持仓、挂单、账单和行情快照；网页关闭不影响服务端采集。
- 总览分别展示 Binance 数据状态与外部记录待核对状态，按真实挂单核对固定止损覆盖。
- 交易日志按交易所、状态筛选；个人笔记分段，草稿和历史版本保留。
- 本人复盘与 AI 报告分别统计，覆盖近 30 天已确认平仓的交易，不受风险政策开关影响。
- 收益按真实账单的平仓盈亏、手续费及资金费计算；不包含划转，不冒充账户权益。
- 三个主入口：总览、交易、复盘。手机采用固定底部导航。
- 账号密码登录、服务端密钥保存；页面与账户数据不进行离线缓存。

本应用不创建、修改或取消交易订单。风险政策由使用者确认后启用，历史交易不追溯判为违规。Variational 目前只支持独立记录，尚未自动同步个人仓位或盈亏。

## 本机运行

需要 Linux 或 macOS、Python 3.10+。运行 Node.js 测试时需要 Node.js 20+。服务使用 Unix 文件锁；Windows 可在 WSL 中运行。

```sh
git clone https://github.com/0xBennie/Trader-Dashboard-Bennie.git
cd Trader-Dashboard-Bennie
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 server.py
```

打开 `http://127.0.0.1:8765`。全新实例默认仅监听本机，账本为空。可以先新建手动日志；需要自动同步时，再在连接设置中填写自己的 Binance 凭证。AI 是可选项，不配置也可以记录和复盘交易。生产实例不能使用无认证的本机启动方式直接暴露公网。

凭证、账本和附件属于部署实例，不属于开源源码：

- `.env.local`：Binance 凭证，也可从页面配置。
- `data/journal.sqlite3`：交易、账单、日志、历史和报告。
- `data/ai-config.json`：各 AI 服务商的独立配置和密钥（仅服务端存储，权限 0600）。
- `data/auth.json`：登录配置与密码哈希。
- `static/assets/`：私人附件；源码只发布 favicon 和两个通用应用图标。
- `research/`：本实例生成的研究数据及结果。

同一账本只运行一个服务进程。生产数据是唯一主账本；开发和浏览器验收使用独立数据库，关闭采集和自动 AI，不能用开发副本覆盖线上数据。

## 第一次使用

1. 打开「交易 → 新建交易日志」，填写自己的交易信息和入场判断。网页新建默认是 Binance 日志。
2. 如需自动采集，在「连接设置」保存 Binance API Key 和 Secret。使用只读权限，避免为本应用授予交易或提现权限。
3. 如需 AI，在同一设置中选择服务商、模型并填写自己的 Key。模型调用由所选服务商按其规则计费。
4. 在「复盘」查看已结束的交易，先写本人复盘，也可生成 AI 报告。数据时间、未知状态和证据缺口会分别显示。

Variational 已支持独立存储、展示与复盘，但当前还没有网页导入入口或自动同步。已有外部记录通过登录后的 `POST /api/journals` 接口创建，并与 Binance 账本分开；请求需要合法会话及 `X-Journal-Token`，不能直接导入为已验证成交。

仓库中的 [合成示例](examples/synthetic-journal.json) 仅用于说明字段，不会自动导入或冒充真实成交。

## 数据与复盘口径

自动采样不是逐笔行情。服务停机、网络断开或设备休眠期间的持仓和挂单过程不能回造；恢复后仅补读交易所可查询的成交与账单。

订单、撮合明细和完整开平仓周期分别展示。止损覆盖只说明挂单的方向、数量和平仓属性与持仓匹配，不保证实际成交价格。外部截图不计入实时覆盖。

本人复盘只有在确认平仓后新增或修改对应复盘文字才计入；只修改标题不算完成。AI 报告按平仓后生成的报告单独统计，不能代替本人记录，也不能倒填原始开仓理由。

AI 只接收所选交易的相关记录、摘要和可选截图，不发送交易所密钥。支持 DeepSeek、OpenAI、Anthropic Claude、Google Gemini、OpenRouter，以及使用 OpenAI Chat Completions / Responses 或 Anthropic Messages 协议的自定义公网 HTTPS 接口；其他私有协议需另行适配。

在「连接设置 → AI 服务」选择服务商，填写模型 ID 和对应 API Key 后保存。可读取服务商模型列表，也可手填 ID。列表测试不调用模型，只验证列表接口，实际生成和图片能力需首次复盘验证。纯文本模型不会收到图片，报告会标记未读取截图。不同服务商的密钥独立保存，空值只保留同一服务商、同一地址的已有密钥；更换自定义地址需要重新填 Key。旧 DeepSeek 配置会兼容迁移。

策略实验与历史回测功能已停用，相关 API 返回 410，旧实验数据保留归档。开仓计划直接记录理由、失效条件和风险，仍需通过账户风险检查、AI 审核及本人确认；历史绑定实验的计划需重新保存和审核。单笔审核不能证明策略有效。本仓库测试使用隔离数据和模拟模型响应，不证明任何模型预测能力。

## 部署

以单个常驻服务运行，置于 HTTPS 反向代理之后。公网部署必须通过 `auth.py` 配置登录，并设置 `--public-origin`；使用 `python3 auth.py --help` 和 `python3 server.py --help` 查看本版本参数。`data/`、附件和研究结果使用持久化目录，密钥单独管理。

例如，在 HTTPS 反向代理后的单用户实例中：

```sh
# 交互设置密码，至少 12 位；不会把密码写进命令历史。
python3 auth.py --username admin
python3 server.py --host 127.0.0.1 --port 8765 --public-origin https://journal.example.com
```

将你自己的 HTTPS 域名反向代理到 `127.0.0.1:8765`，由 systemd 或其他进程管理器保持服务常驻。应用适合个人单用户部署，目前不提供多租户隔离或多人账户管理。登录后修改设置前，确保浏览器访问的地址与 `--public-origin` 一致。

密码、API Key 与账本都存储在实例磁盘上，API Key 不做磁盘加密；需要依靠主机文件权限和加密备份保护它们。不要把运行目录、真实截图或密钥添加到 Git。备份和恢复由部署者负责。

更新前使用 SQLite backup API 创建一致快照并备份旧源码。只发布本次变更的源码，不覆盖线上数据库、认证文件或附件；保留回滚版本。具体主机、证书、账号与运维记录应保存在部署者的私密文档中。

## 验证

```sh
python3 -m unittest discover -s tests -v
node --test tests/*.cjs
node --check static/app.js
node --check static/training.js
```

浏览器验收使用隔离账本，验证保存后刷新、筛选、AI 未配置引导、复盘进度、手机布局。不要在生产账本中创建测试交易。

## 项目结构

- `server.py` / `auth.py`：HTTP 服务、登录会话与请求校验。
- `journal.py` / `recorder.py`：SQLite 账本、只读同步、成交周期与过程快照。
- `trade_context.py` / `stop_coverage.py`：行情、挂单与止损覆盖核对。
- `ai_provider.py` / `ai_review.py`：可选模型服务与证据复盘。
- `training.py` / `pretrade_*.py`：事前计划、账户风险规则和审核记录。
- `static/`：原生 JavaScript 界面、样式和通用图标。
- `tests/`：隔离数据库与合成测试数据，不访问真实账户。
- `backtest.py`：停用功能的历史兼容模块；无可用页面或 API 入口。

## 贡献与许可证

欢迎通过 Issue 或 Pull Request 提交问题和改进。请使用合成数据复现问题；提交前运行上方测试，避免上传真实账本、交易截图和凭证。仓库 CI 会在 Python 3.10 与 3.12 上运行后端和前端测试。

本项目采用 [MIT License](LICENSE)。仓库包含应用源码与合成测试，不包含维护者的线上数据、个人密钥、部署主机配置或历史私人文件。
