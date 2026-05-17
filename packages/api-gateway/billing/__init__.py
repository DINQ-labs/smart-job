"""Billing 子包：Stripe Checkout / Customer Portal / Credit 账户。

模块结构：
    pricing       — Plan / SKU / Credit 费率定义（纯配置）
    credit_ledger — Credit 入账/扣费/refill 高层 API
    stripe_client — Stripe SDK 配置 + Checkout/Portal helper
    routes        — Starlette HTTP 处理器（在 http_routes.register_routes 内注册）

入口：
    from billing import routes as billing_routes
    from billing.credit_ledger import charge_for_sku, refill_if_due
"""
