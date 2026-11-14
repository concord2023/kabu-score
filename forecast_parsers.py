"""Pure text parsers for Yahoo! Finance Japan valuation data.

Extracted from update_data.py (Phase B1). These functions are pure: they take
raw page text and return parsed values, with no network, file, or global
state access.
"""
import calendar
import re


def fiscal_year_end_date(fiscal_year):
    """Convert IRBANK fiscal-year label YYYY/MM to the fiscal month-end date."""
    m = re.fullmatch(r'(\d{4})/(\d{2})', str(fiscal_year or ''))
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    day = calendar.monthrange(year, month)[1]
    return f'{year:04d}-{month:02d}-{day:02d}'

def _parse_yahoo_forecast_text(raw):
    from html import unescape
    text = unescape(re.sub(r'<[^>]+>', ' ', raw or ''))
    text = text.replace('\u00a0',' ').replace('\u200b','').replace('\ufeff','')
    text = re.sub(r'\s+', ' ', text)
    pe_m = re.search(r'PER\s*（\s*会社予想\s*）\s*(?:用語\s*)?(?:(?:\([^)]*\))|(?:（[^）]*）))?\s*([0-9]{1,5}(?:\.[0-9]+)?)\s*倍', text)
    eps_m = re.search(r'EPS\s*（\s*会社予想\s*）\s*(?:用語\s*)?(?:(?:\([^)]*\))|(?:（[^）]*）))?\s*([0-9][0-9,]*(?:\.[0-9]+)?)', text)
    date_m = re.search(r'直近の決算発表日は\s*(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日', text)
    pe = float(pe_m.group(1)) if pe_m else None
    eps = float(eps_m.group(1).replace(',','')) if eps_m else None
    switch_date = f'{date_m.group(1)}-{int(date_m.group(2)):02d}-{int(date_m.group(3)):02d}' if date_m else None
    return pe, eps, switch_date

def _parse_yahoo_pbr_dividend_text(raw):
    """Parse PBR and company-forecast dividend yield from Yahoo Japan quote text."""
    from html import unescape
    text = unescape(re.sub(r'<[^>]+>', ' ', raw or ''))
    text = text.replace('\u00a0', ' ').replace('\u200b', '').replace('\ufeff', '')
    text = re.sub(r'\s+', ' ', text)

    # Yahoo Japan currently exposes these as:
    # 配当利回り（会社予想） ... 0.77%
    # PBR（実績） ... (連)13.42倍
    div_m = re.search(
        r'配当利回り\s*（\s*会社予想\s*）\s*(?:用語\s*)?([0-9]{1,4}(?:\.[0-9]+)?)\s*%',
        text
    )
    pbr_m = re.search(
        r'PBR\s*（\s*実績\s*）\s*(?:用語\s*)?(?:(?:\([^)]*\))|(?:（[^）]*）))?\s*([0-9]{1,6}(?:\.[0-9]+)?)\s*倍',
        text
    )
    # Fallback for markup/text variants that omit the parenthetical label.
    if div_m is None:
        div_m = re.search(r'配当利回り[^%]{0,80}?([0-9]{1,4}(?:\.[0-9]+)?)\s*%', text)
    if pbr_m is None:
        pbr_m = re.search(r'PBR[^0-9]{0,80}?([0-9]{1,6}(?:\.[0-9]+)?)\s*倍', text)

    pbr = float(pbr_m.group(1)) if pbr_m else None
    dividend_yield = float(div_m.group(1)) if div_m else None
    if pbr is not None and pbr <= 0:
        pbr = None
    if dividend_yield is not None and dividend_yield < 0:
        dividend_yield = None
    return pbr, dividend_yield
