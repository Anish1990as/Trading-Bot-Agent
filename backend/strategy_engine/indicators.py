import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    relative_strength = avg_gain / avg_loss.replace(0, float("nan"))
    result = 100 - (100 / (1 + relative_strength))
    return result.where(avg_loss != 0, 100.0)


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(
        span=signal,
        adjust=False,
        min_periods=signal,
    ).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def true_range(df: pd.DataFrame) -> pd.Series:
    previous_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    return true_range(df).ewm(
        alpha=1 / length,
        adjust=False,
        min_periods=length,
    ).mean()


def bollinger_bands(
    series: pd.Series,
    length: int = 20,
    standard_deviations: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = series.rolling(length, min_periods=length).mean()
    deviation = series.rolling(length, min_periods=length).std(ddof=0)
    upper = middle + (deviation * standard_deviations)
    lower = middle - (deviation * standard_deviations)
    return lower, middle, upper


def stochastic_rsi(
    series: pd.Series,
    rsi_length: int = 14,
    stochastic_length: int = 14,
    smooth_k: int = 3,
    smooth_d: int = 3,
) -> tuple[pd.Series, pd.Series]:
    rsi_values = rsi(series, rsi_length)
    rolling_low = rsi_values.rolling(stochastic_length).min()
    rolling_high = rsi_values.rolling(stochastic_length).max()
    denominator = (rolling_high - rolling_low).replace(0, float("nan"))
    raw = 100 * (rsi_values - rolling_low) / denominator
    k_line = raw.rolling(smooth_k).mean()
    d_line = k_line.rolling(smooth_d).mean()
    return k_line, d_line


def supertrend_direction(
    df: pd.DataFrame,
    length: int = 10,
    multiplier: float = 3.0,
) -> pd.Series:
    atr_values = atr(df, length)
    midpoint = (df["high"] + df["low"]) / 2
    upper_band = midpoint + (multiplier * atr_values)
    lower_band = midpoint - (multiplier * atr_values)
    final_upper = upper_band.copy()
    final_lower = lower_band.copy()
    direction = pd.Series(1, index=df.index, dtype="int64")

    for position in range(1, len(df)):
        current = df.index[position]
        previous = df.index[position - 1]

        if pd.isna(atr_values.loc[current]):
            direction.loc[current] = direction.loc[previous]
            continue

        if (
            upper_band.loc[current] < final_upper.loc[previous]
            or df["close"].loc[previous] > final_upper.loc[previous]
        ):
            final_upper.loc[current] = upper_band.loc[current]
        else:
            final_upper.loc[current] = final_upper.loc[previous]

        if (
            lower_band.loc[current] > final_lower.loc[previous]
            or df["close"].loc[previous] < final_lower.loc[previous]
        ):
            final_lower.loc[current] = lower_band.loc[current]
        else:
            final_lower.loc[current] = final_lower.loc[previous]

        if direction.loc[previous] == -1:
            direction.loc[current] = (
                1 if df["close"].loc[current] > final_upper.loc[current] else -1
            )
        else:
            direction.loc[current] = (
                -1 if df["close"].loc[current] < final_lower.loc[current] else 1
            )

    return direction
