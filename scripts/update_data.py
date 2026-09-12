import json, os, statistics, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

API = "https://api.irbank.net/v1"
TOKEN = os.environ.get("IRBANK_API_KEY")
if not TOKEN:
    raise SystemExit("IRBANK_API_KEY is not set")

with open("watchlist.json", encoding="utf-8") as f:
    codes = json.load(f)["stocks"]

def get(path, params=None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}", "User-Agent":"kabu-score/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def score_stock(p):
    closes = [x["close"] for x in p if x.get("close") is not None]
    vols = [x["volume"] for x in p if x.get("volume") is not None]
    if len(closes) < 21:
        return None
    close = closes[0]
    prev = closes[1]
    change = (close / prev - 1) * 100 if prev else 0
    vol20 = statistics.mean(vols[1:21]) if len(vols) >= 21 else None
    vol_ratio = vols[0] / vol20 if vol20 else None

    # 現在は「株価・出来高・相対強弱」の実データ部分を自動化。
    # 市場全体の騰落レシオは別ソースの整合性確認後に加点する。
    points = 0
    if change <= -7: points += 30
    elif change <= -5: points += 24
    elif change <= -3: points += 17
    elif change <= -2: points += 9
    if vol_ratio is not None:
        if vol_ratio >= 1.5: points += 20
        elif vol_ratio >= 1.3: points += 16
        elif vol_ratio >= 1.15: points += 10
    # 20日平均に対する株価位置を「相対弱さ」の代替指標として使用
    avg20 = statistics.mean(closes[1:21])
    dist20 = (close / avg20 - 1) * 100 if avg20 else 0
    if dist20 <= -10: points += 30
    elif dist20 <= -7: points += 24
    elif dist20 <= -5: points += 18
    elif dist20 <= -3: points += 10
    return {
        "date": p[0]["date"], "price": close, "change": round(change,2),
        "volume": vols[0], "volume_ratio": round(vol_ratio,2) if vol_ratio else None,
        "vs20": round(dist20,2), "score": min(points,80),
        "breadth_pending": True
    }

out = {"updated_at": datetime.now(timezone(timedelta(hours=9))).isoformat(), "source":"IRBANK API", "stocks":{}}
for code in codes:
    info = get(f"/securities/{code}")
    prices = get(f"/securities/{code}/prices", {"limit": 100})
    p = prices.get("prices", [])
    s = score_stock(p)
    if s:
        s["code"] = code
        s["name"] = info.get("name", code)
        s["market"] = info.get("market")
        s["industry"] = info.get("industry")
        s["attribution"] = prices.get("attribution", {})
        out["stocks"][code] = s

with open("data/stocks.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
