# Market Context v1

## Policy
Market conditions are **not a veto/filter**. A weak market must not automatically suppress a stock BUY candidate.

The market is used as context and, where available, to calculate **relative strength**:

`relative_strength = stock_return - market_return`

Examples:
- Market -2%, stock -0.5% => relative strength +1.5pp: relatively strong.
- Market -2%, stock +2% => +4pp: very strong.
- Market +2%, stock -2% => -4pp: relatively weak.

## Bottom-fishing principle
A broad market decline can coincide with a major bottom. Therefore, falling prices are not automatically treated as a negative filter. The model must distinguish:
- continued decline,
- stabilization,
- confirmed reversal,
- healthy uptrend,
- and pullback.

## Signal use
Market context may change the explanation/ranking priority, but it does not by itself change BUY_CANDIDATE to WATCH or AVOID.

Any stronger use of market-relative strength must be historically validated before becoming a hard gate.
