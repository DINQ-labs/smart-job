# Billing 部署配置指南

> 服务：`job-api-gateway`（port 8767）
> 模块：`billing/`（pricing / credit_ledger / stripe_client / routes）
> 实现 PRD §订阅+Credit / GAP_ANALYSIS Sprint B 系列。

---

## 1. Stripe Dashboard 一次性配置

### 1.1 创建 Products + Prices

在 [Dashboard → Products](https://dashboard.stripe.com/products) 创建三个 Product，每个 Product 下挂一个 Price。

| Product 名 | Price 模式 | 金额 | Billing period | 备注 |
|---|---|---|---|---|
| Jobseeker Pro | Recurring | $9.00 USD | Monthly | 求职端订阅 |
| Recruiter Pro | Recurring | $29.00 USD | Monthly | 招聘端订阅 |
| Credit 100 Pack | One-time | $2.00 USD | — | 一次性追加包，100 credits，永不过期 |

> 建议先在 **Test mode** 完成，确认无误后再切 Live mode 重做一次。

记下三个 Price ID（形如 `price_1Q...`），填入 `.env`：

```bash
STRIPE_PRICE_JOBSEEKER_PRO=price_xxxxxxxxxxxxxxxxxxxxxxxx
STRIPE_PRICE_RECRUITER_PRO=price_xxxxxxxxxxxxxxxxxxxxxxxx
STRIPE_PRICE_CREDIT_100=price_xxxxxxxxxxxxxxxxxxxxxxxx
```

### 1.2 API Keys

[Dashboard → Developers → API keys](https://dashboard.stripe.com/apikeys)：
- 复制 **Secret key**（`sk_test_...` 或 `sk_live_...`）→ `.env` 的 `STRIPE_SECRET_KEY`
- Publishable key 给前端用（不在本服务）

### 1.3 Webhook Endpoint

[Dashboard → Developers → Webhooks → Add endpoint](https://dashboard.stripe.com/webhooks)：

| 字段 | 值 |
|---|---|
| Endpoint URL | `https://<your-domain>/billing/webhook`（生产）或 `https://<ngrok-url>/billing/webhook`（本地） |
| Events to send | 见下表 |

**必须订阅的事件**：
- `checkout.session.completed`     —— Checkout 完成（订阅或追加）→ grant credits / 创建订阅记录
- `customer.subscription.created`  —— 订阅创建（多数情况和 checkout.session.completed 同步）
- `customer.subscription.updated`  —— 状态变化（active / past_due / canceled）
- `customer.subscription.deleted`  —— 彻底失效（取消 + 过期后）→ 降级到 free
- `invoice.paid`                   —— 续费成功 → grant 当月 credits
- `charge.refunded`                —— 退款（仅记日志，运营人工处理）

创建后页面会显示 **Signing secret**（形如 `whsec_...`）→ `.env` 的 `STRIPE_WEBHOOK_SECRET`。

### 1.4 Customer Portal

[Dashboard → Settings → Billing → Customer Portal](https://dashboard.stripe.com/settings/billing/portal)：
- 启用并配置允许的操作（建议开启：更新支付方式 / 查看账单 / 取消订阅 / 切换订阅）
- 设置 Business information（用户在 Portal 看到的品牌）

---

## 2. 环境变量清单

`.env` 关键字段（详见 `.env.example`）：

```bash
# 强制配置（任一缺失则 /billing/* 路由返回 503）
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_JOBSEEKER_PRO=price_...
STRIPE_PRICE_RECRUITER_PRO=price_...
STRIPE_PRICE_CREDIT_100=price_...

# Checkout 回跳
BILLING_SUCCESS_URL=https://control.dinq.me/billing/success
BILLING_CANCEL_URL=https://control.dinq.me/billing/cancel
BILLING_PORTAL_RETURN_URL=https://control.dinq.me/billing
```

**回跳 URL 约定**：
- `BILLING_SUCCESS_URL` 会拼上 `?session_id={CHECKOUT_SESSION_ID}`；前端从 URL 取出 session_id 调 `/billing/status` 拉新订阅状态。
- `BILLING_CANCEL_URL` 不带参数，纯回跳。

---

## 3. 本地开发：Stripe CLI 转发 webhook

生产环境的 webhook 直接走公网回调；本地开发用 [Stripe CLI](https://docs.stripe.com/stripe-cli) 转发：

```bash
# 一次性安装
brew install stripe/stripe-cli/stripe   # macOS
# 登录
stripe login

# 启动转发（保持运行）
stripe listen --forward-to localhost:8767/billing/webhook
```

输出会打印一个 **Webhook signing secret**（`whsec_xxx`）—— 注意：这个 secret 和 Dashboard 上的 endpoint secret **不一样**。本地开发期间用这个 CLI 的 secret 填 `STRIPE_WEBHOOK_SECRET`，部署到生产再换 Dashboard endpoint 的。

触发测试事件：

```bash
stripe trigger checkout.session.completed
stripe trigger invoice.paid
stripe trigger customer.subscription.deleted
```

---

## 4. 数据流总览

```
用户在前端选套餐
   │
   ▼
POST /billing/checkout  {app_user_id, plan: "jobseeker_pro"}
   │
   ▼
api-gw  ensure_customer(app_user_id) → Stripe Customer
       create_subscription_checkout()       → checkout.Session
   │
   ▼
返回 session.url → 前端 redirect 到 Stripe Checkout 页
   │
   ▼ 用户填卡 / 完成支付
   │
   ├─ Stripe 重定向到 BILLING_SUCCESS_URL?session_id=cs_test_...
   │     前端拉 /billing/status 显示新订阅
   │
   └─ Stripe 异步 POST /billing/webhook
         event = checkout.session.completed
         ├─ 幂等：record_stripe_event() 写入 stripe_events 表
         ├─ upsert_subscription(plan, period_end, ...)
         └─ grant_subscription() → user_credits.balance += monthly_grant + credit_ledger 写一条
```

---

## 5. 模块 / 路由对照

| 路径 | 方法 | 模块 | 用途 |
|---|---|---|---|
| `/billing/plans`    | GET  | `billing/routes.py:billing_plans`    | 套餐 + Credit 费率（前端定价页用，无需鉴权） |
| `/billing/status`   | GET  | `billing/routes.py:billing_status`   | 当前订阅 + Credit 余额（Header / 我的页用，自动 refill） |
| `/billing/ledger`   | GET  | `billing/routes.py:billing_ledger`   | Credit 流水（前端账单用） |
| `/billing/checkout` | POST | `billing/routes.py:billing_checkout` | 创建 Checkout Session（subscription 或 topup） |
| `/billing/portal`   | GET  | `billing/routes.py:billing_portal`   | Customer Portal 重定向（默认 303，`?json=1` 拿 URL） |
| `/billing/webhook`  | POST | `billing/routes.py:billing_webhook`  | Stripe 事件回调（必须原始 body 验签） |

---

## 6. Credit 扣费集成（agent-gateway 侧）

`job-api-gateway/billing/credit_ledger.py` 暴露三个高层 API，供 agent-gateway 跨服务调用：

```python
# 模型对话扣费（在 agent_loop.py 每轮调 LLM 前后）
from billing.credit_ledger import charge_for_model
result = await charge_for_model(app_user_id, model_id="claude-opus-4-7", plan=current_plan)
if not result["allowed"]:
    # 返回升级提示卡片给用户
    ...

# 批量任务扣费（在 tasks/engine.py 每个 item 完成时）
from billing.credit_ledger import charge_for_sku
await charge_for_sku(app_user_id, "batch_analyze", count=item_count, plan=current_plan)

# 重新分析扣费（已在 bookmark_routes.reanalyze_bookmark 内集成）
await charge_for_sku(app_user_id, "action_reanalyze", 1, plan=current_plan)
```

> agent-gateway 与 api-gateway 是不同进程，调用方式需走 HTTP（或后续抽 Python 包）。**当前阶段建议在 api-gateway 内部消费，agent-gateway 通过 HTTP API 报告"已完成的 LLM 调用 + model_id"，由 api-gateway 扣费。**新增端点 `POST /billing/charge` 见 Sprint B 后续任务。

---

## 7. 测试卡片 / 验证清单

Stripe Test mode 支持的测试卡片（[完整列表](https://docs.stripe.com/testing)）：

| 卡号 | 行为 |
|---|---|
| 4242 4242 4242 4242 | 成功 |
| 4000 0000 0000 9995 | 余额不足，拒绝 |
| 4000 0027 6000 3184 | 3DS 验证 |

部署后人工验证清单：

- [ ] `GET /billing/plans` 返回 3 个 plan + 4 个 SKU 费率
- [ ] `POST /billing/checkout {plan:"jobseeker_pro", app_user_id:"test_001"}` 返回 stripe URL
- [ ] 完成测试支付后 `GET /billing/status?app_user_id=test_001` 返回 plan=jobseeker_pro, balance=500
- [ ] DB `user_subscriptions` 有一行 plan=jobseeker_pro
- [ ] DB `user_credits` balance=500
- [ ] DB `credit_ledger` 有 grant_subscription 一行
- [ ] DB `stripe_events` 有 checkout.session.completed 一行 processed_at 非 NULL
- [ ] `GET /billing/portal?app_user_id=test_001` 重定向到 Stripe Customer Portal
- [ ] 在 Portal 取消订阅 → 周期结束后 webhook 触发 → DB plan=free, balance=0（topup 部分保留）
- [ ] `POST /billing/checkout {topup:"credit_100"}` 完成后 balance += 100，topup_balance += 100

---

## 8. 已知边界 / 后续

- **退款**：当前 `charge.refunded` 仅记日志，不自动扣回 credit（避免误伤）。运营手工调账走 `POST /admin/billing/adjust`（待加）。
- **多币种**：目前价格写死 USD。后续如需 CNY，需在 Stripe Dashboard 复制 Product 并新增对应 Price + env。
- **试用期 / Trial**：未实现，可在 `create_subscription_checkout` 加 `subscription_data={"trial_period_days": 7}`。
- **促销码**：`allow_promotion_codes=True` 已开，Stripe Dashboard 配 Coupon 即可，无需改代码。
- **发票自动开具**：默认 Stripe 会发，关闭路径走 Dashboard → Settings → Emails。
- **Webhook 重试 backoff**：Stripe 默认重试 3 天，由于 `stripe_events` 表有 `process_error` 字段，任务巡检脚本可扫"processed_at != NULL AND process_error != ''"做兜底。
