"""Regression tests for _parse_yahoo_forecast_text in update_data.py.

update_data.py has import-time side effects (it requires IRBANK_API_KEY and
reads/writes watchlist.json), so this test does NOT import the module.
Instead it extracts the single _parse_yahoo_forecast_text function with ast
and execs only that function.

It also guards against the historical bug this file was written for:
_parse_yahoo_forecast_text used to be defined twice in update_data.py
(only the later definition was effective, and the two regexes differed
slightly). There must be exactly one definition, and it must keep handling
the PER（会社予想） / EPS（会社予想） patterns that both old definitions
targeted.
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / 'update_data.py').read_text(encoding='utf-8')


def load_parser():
    tree = ast.parse(SOURCE)
    defs = [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == '_parse_yahoo_forecast_text']
    assert len(defs) == 1, f'_parse_yahoo_forecast_text must be defined exactly once, found {len(defs)}'
    namespace = {'re': re}
    exec(compile(ast.Module(body=[defs[0]], type_ignores=[]), 'update_data.py', 'exec'), namespace)
    return namespace['_parse_yahoo_forecast_text']


parse = load_parser()


def test_basic_forecast_with_date():
    text = 'PER（会社予想） (連)11.23倍 EPS（会社予想） (連)265.55 直近の決算発表日は2026年2月13日です'
    pe, eps, switch_date = parse(text)
    assert pe == 11.23, pe
    assert eps == 265.55, eps
    assert switch_date == '2026-02-13', switch_date


def test_fullwidth_connector_and_yogo():
    text = 'PER（会社予想）用語 （連）9.87倍 EPS（会社予想）用語 （単）123.4'
    pe, eps, switch_date = parse(text)
    assert pe == 9.87, pe
    assert eps == 123.4, eps
    assert switch_date is None, switch_date


def test_spaced_variant_from_later_definition():
    # Only the later (effective) definition tolerated spaces inside the
    # full-width parentheses and a thousands separator in EPS.
    text = 'PER （ 会社予想 ） 12.5倍 EPS （ 会社予想 ） 1,234.5'
    pe, eps, switch_date = parse(text)
    assert pe == 12.5, pe
    assert eps == 1234.5, eps
    assert switch_date is None, switch_date


def test_html_markup_is_stripped():
    text = '<div>PER（会社予想）</div> <span>(連)15.00倍</span> <div>EPS（会社予想）</div> <span>(連)300.0</span>'
    pe, eps, switch_date = parse(text)
    assert pe == 15.00, pe
    assert eps == 300.0, eps


def test_nbsp_and_no_match():
    pe, eps, switch_date = parse('PER\u00a0（会社予想） (連)10.0倍')
    assert pe == 10.0, pe
    assert parse('関係ないテキスト') == (None, None, None)
    assert parse('') == (None, None, None)
    assert parse(None) == (None, None, None)


if __name__ == '__main__':
    test_basic_forecast_with_date()
    test_fullwidth_connector_and_yogo()
    test_spaced_variant_from_later_definition()
    test_html_markup_is_stripped()
    test_nbsp_and_no_match()
    print('YAHOO FORECAST PARSER REGRESSION OK')
