import json, os, statistics, urllib.parse, urllib.request, re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser

API = "https://api.irbank.net/v1"
TOKEN = os.environ.get("IRBANK_API_KEY")
if not TOKEN:
    raise SystemExit("IRBANK_API_KEY is not set")

with open("watchlist.json", encoding="utf-8") as f:
    codes = json.load(f)["stocks"]

class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables=[]; self.in_table=False; self.in_tr=False; self.in_cell=False
        self.rows=[]; self.row=[]; self.buf=[]
    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.in_table=True; self.rows=[]
        elif self.in_table and tag == "tr":
            self.in_tr=True; self.row=[]
        elif self.in_tr and tag in ("td","th"):
            self.in_cell=True; self.buf=[]
    def handle_endtag(self, tag):
        if self.in_tr and tag in ("td","th") and self.in_cell:
            self.row.append(" ".join("".join(self.buf).split()))
            self.in_cell=False
        elif self.in_table and tag == "tr":
            if self.row: self.rows.append(self.row)
            self.in_tr=False
        elif tag == "table" and self.in_table:
            if self.rows: self.tables.append(self.rows)
            self.in_table=False
    def handle_data(self, data):
        if self.in_cell: self.buf.append(data)

def fetch(url, headers=None):
    req=urllib.request.Request(url, headers=headers or {"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def get(path, params=None):
    url=API+path
    if params: url += "?" + urllib.parse.urlencode(params)
    data=fetch(url, {"Authorization":f"Bearer {TOKEN}", "User-Agent":"kabu-score/3.0"})
    return json.loads(data)

def pct(a,b):
    return ((a/b)-1)*100 if b not in (None,0) and a is not None else None

def rsi14(closes):
    if len(closes)<15: return None
    gains=[]; losses=[]
    for i in range(1,15):
        d=closes[i-1]-closes[i]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/14; al=sum(losses)/14
    if al==0: return 100.0
    return 100-(100/(1+ag/al))

def parse_num(s):
    if not s: return None
    s=s.replace(",","").replace("％","%").strip()
    m=re.search(r"[-+]?\d+(?:\.\d+)?",s)
    return float(m.group()) if m else None

def get_market_context():
    # The site publishes daily Prime-market breadth (6/10/15/25-day)
    # and Nikkei 225 daily return in a daily report.
    jst=timezone(timedelta(hours=9))
    now=datetime.now(jst)
    url=f"https://www.teitenkansoku.online/{now:%Y/%m/%d}/{now.year}年{now.month}月{now.day}日/"
    raw=fetch(url, {"User-Agent":"Mozilla/5.0 (compatible; kabu-score/3.0)"})
    p=TableParser(); p.feed(raw.decode("utf-8","ignore"))

    breadth=None
    nikkei_change=None
    report_date=None
    for rows in p.tables:
        for i,row in enumerate(rows):
            normalized=[x.replace(" ","") for x in row]
            if len(normalized)>=4 and normalized[:4]==["日付","25日","15日","10日"] and len(normalized)>=5 and "6日" in normalized:
                # Find the first data row under the header.
                idx=normalized.index("6日")
                if i+1 < len(rows):
                    d=rows[i+1]
                    if len(d)>idx:
                        report_date=d[0]
                        try:
                            breadth={"date":d[0],"25d":parse_num(d[1]),"15d":parse_num(d[2]),"10d":parse_num(d[3]),"6d":parse_num(d[idx])}
                        except Exception:
                            pass
            if row and row[0].strip()=="日経225" and len(row)>=5:
                nikkei_change=parse_num(row[4])
    return {"source_url":url,"date":report_date,"breadth":breadth,"nikkei_change":nikkei_change}

def breadth_points(b):
    if not b: return {"6d":0,"10d":0,"15d":0,"25d":0}
    p6=5 if b.get("6d") is not None and b["6d"]<=60 else 4 if b.get("6d")<=70 else 3 if b.get("6d")<=80 else 2 if b.get("6d")<=90 else 1 if b.get("6d")<=100 else 0
    p10=4 if b.get("10d") is not None and b["10d"]<=70 else 3 if b.get("10d")<=80 else 2 if b.get("10d")<=90 else 1 if b.get("10d")<=100 else 0
    p15=3 if b.get("15d") is not None and b["15d"]<=80 else 2 if b.get("15d")<=90 else 1 if b.get("15d")<=100 else 0
    p25=4 if b.get("25d") is not None and b["25d"]>=130 else 3 if b.get("25d")>=125 else 2 if b.get("25d")>=120 else 1 if b.get("25d")>=115 else 0
    return {"6d":p6,"10d":p10,"15d":p15,"25d":p25}

def score_stock(p, market):
    valid=[x for x in p if x.get("close") is not None]
    if len(valid)<61: return None
    closes=[x["close"] for x in valid]
    vols=[x.get("volume") for x in valid]
    close=closes[0]; prev=closes[1]
    change=pct(close,prev) or 0
    vol20_vals=[v for v in vols[1:21] if v is not None]
    vol_ratio=(vols[0]/statistics.mean(vol20_vals)) if vols[0] is not None and vol20_vals else None
    ma20=statistics.mean(closes[1:21]); ma60=statistics.mean(closes[1:61])
    dist20=pct(close,ma20); dist60=pct(close,ma60)
    ret5=pct(close,closes[5]); ret10=pct(close,closes[10]); ret20=pct(close,closes[20])
    rsi=rsi14(closes)

    daily_pts=18 if change<=-7 else 15 if change<=-5 else 11 if change<=-3 else 7 if change<=-2 else 3 if change<=-1 else 0
    vol_pts=12 if vol_ratio is not None and vol_ratio>=1.8 else 10 if vol_ratio is not None and vol_ratio>=1.5 else 8 if vol_ratio is not None and vol_ratio>=1.3 else 5 if vol_ratio is not None and vol_ratio>=1.15 else 0
    weak_pts=15 if dist20 is not None and dist20<=-12 else 12 if dist20 is not None and dist20<=-8 else 9 if dist20 is not None and dist20<=-5 else 5 if dist20 is not None and dist20<=-3 else 0
    rsi_pts=15 if rsi is not None and rsi<=25 else 12 if rsi is not None and rsi<=30 else 8 if rsi is not None and rsi<=35 else 4 if rsi is not None and rsi<=40 else 0
    trend_pts=15 if dist60 is not None and dist60<=-10 else 10 if dist60 is not None and dist60<=-5 else 6 if dist60 is not None and dist60<=0 else 0
    stock_pts=daily_pts+vol_pts+weak_pts+rsi_pts+trend_pts

    bp=breadth_points(market.get("breadth"))
    breadth_pts=sum(bp.values())
    nikkei=market.get("nikkei_change")
    rel=(change-nikkei) if nikkei is not None else None
    rel_pts=10 if rel is not None and rel<=-7 else 8 if rel is not None and rel<=-5 else 6 if rel is not None and rel<=-3 else 3 if rel is not None and rel<=-2 else 0

    total=stock_pts+breadth_pts+rel_pts
    return {
      "date":valid[0]["date"],"price":close,"change":round(change,2),"volume":vols[0],
      "volume_ratio":round(vol_ratio,2) if vol_ratio is not None else None,
      "ma20":round(ma20,2),"ma60":round(ma60,2),"vs20":round(dist20,2),"vs60":round(dist60,2),
      "ret5":round(ret5,2) if ret5 is not None else None,"ret10":round(ret10,2) if ret10 is not None else None,
      "ret20":round(ret20,2) if ret20 is not None else None,"rsi14":round(rsi,1) if rsi is not None else None,
      "score":min(total,100),"score_max":100,
      "score_breakdown":{"daily_drop":daily_pts,"volume":vol_pts,"vs20":weak_pts,"rsi14":rsi_pts,"vs60":trend_pts,"breadth_6d":bp["6d"],"breadth_10d":bp["10d"],"breadth_15d":bp["15d"],"breadth_25d":bp["25d"],"nikkei_relative":rel_pts},
      "breadth":market.get("breadth"),"nikkei_change":nikkei,"relative_strength":round(rel,2) if rel is not None else None,
      "breadth_source":market.get("source_url")
    }

market={"breadth":None,"nikkei_change":None,"source_url":None,"date":None}
try:
    market=get_market_context()
except Exception as e:
    market["error"]=str(e)

out={"updated_at":datetime.now(timezone(timedelta(hours=9))).isoformat(),"source":"IRBANK API + 何でも定点観測","market_context":market,"stocks":{}}
for code in codes:
    try:
        info=get(f"/securities/{code}")
        prices=get(f"/securities/{code}/prices",{"limit":100})
        s=score_stock(prices.get("prices",[]),market)
        if s:
            s.update({"code":code,"name":info.get("name",code),"market":info.get("market"),"industry":info.get("industry"),"attribution":prices.get("attribution",{})})
            out["stocks"][code]=s
    except Exception as e:
        out["stocks"][code]={"code":code,"name":code,"error":str(e)}

with open("data/stocks.json","w",encoding="utf-8") as f:
    json.dump(out,f,ensure_ascii=False,indent=2)
