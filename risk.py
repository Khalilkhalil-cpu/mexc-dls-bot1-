from config import settings


def position_size(client, symbol: str, entry: float, stop: float) -> float:
    balance = client.balance_usdt()
    if settings.sizing_mode.lower() == "fixed_margin":
        # MEXC amount mode requested before: $1 margin at 50x sends amount 50.
        amount = float(settings.trade_margin_usdt) * float(settings.leverage)
        return client.amount_to_precision(symbol, amount)

    risk_usdt = balance * float(settings.risk_per_trade)
    distance = abs(float(entry) - float(stop))
    if distance <= 0 or risk_usdt <= 0:
        return 0.0
    coin_amount = risk_usdt / distance
    contracts = coin_amount / client.contract_size(symbol)
    amount = client.amount_to_precision(symbol, contracts)
    return amount
