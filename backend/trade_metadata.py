import json
from typing import Any


def parse_trade_metadata(notes: str | None) -> dict[str, Any]:
    if not notes:
        return {}

    try:
        value = json.loads(notes)
    except (TypeError, json.JSONDecodeError):
        return {"legacy_notes": notes}

    return value if isinstance(value, dict) else {"legacy_notes": notes}


def serialize_trade_metadata(metadata: dict[str, Any]) -> str:
    return json.dumps(metadata, separators=(",", ":"), sort_keys=True)


def is_option_execution(metadata: dict[str, Any]) -> bool:
    return (
        metadata.get("execution_instrument") == "OPTION"
        and metadata.get("opt_type") in {"CE", "PE"}
        and metadata.get("strike") is not None
    )


def trade_direction(trade_type: str, metadata: dict[str, Any]) -> str:
    direction = metadata.get("signal_direction")
    return direction if direction in {"BUY", "SELL"} else trade_type


def display_symbol(symbol: str, metadata: dict[str, Any]) -> str:
    if is_option_execution(metadata):
        return f"{symbol} {metadata['strike']} {metadata['opt_type']}"
    return symbol
