"""Build a daily external-information attention list.

This is intentionally separate from the kabu-score BUY model.  It collects
same-day public discussion from several Japanese-market source types and
selects up to five stocks by transparent information-intensity, not by a
claim that they are good buys.
"""
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from pathlib import Path

OUT = Path('data/daily_recommendations.json')
TIMEOUT = 15
UA = 'kabu-score/1.0 (+daily market attention collector)'
JST = timezone(timedelta(hours=9))

class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts=[]
        self.links=[]
        self._href=None
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self._href = dict(attrs).get('href')
        elif tag == 'link':
            self._href = '__RSS_LINK__'
    def handle_endtag(self, tag):
        if tag in ('a','link'):
            self._href = None
    def handle_data(self, data):
        s=' '.join(data.split())
        if s:
            self.parts.append(s)
            if self._href:
                self.links.append((s, self._href))
    @property
    def text(self):
        return ' '.join(self.parts)

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA})
    with urllib.request.urlopen(req,timeout=TIMEOUT) as r:
        raw=r.read()
        charset=r.headers.get_content_charset() or 'utf-8'
        return raw.decode(charset,'replace')

def clean(s):
    return re.sub(r'\s+',' ',html.unescape(s or '')).strip()

def load_master():
    p=Path('data/company_master.json')
    if not p.exists(): return {}
    try:
        d=json.loads(p.read_text(encoding='utf-8'))
        rows=d.get('companies',d if isinstance(d,list) else [])
        out={}
        for x in rows:
            c=str(x.get('code','')).strip().upper()
            if c: out[c]={'code':c,'name':x.get('name',''),'market':x.get('market'),'industry':x.get('industry')}
        return out
    except Exception:
        return {}

def code_from_text(text, master):
    """Extract JP stock codes even when the company master is stale.

    New listings can appear in same-day source pages before company_master.json
    is refreshed.  Therefore code extraction must not require a master hit.
    """
    found=[]
    for m in re.finditer(r'(?<!\d)(\d{3,4}[A-Z]?)(?!\d)', text or ''):
        c=m.group(1).upper()
        # A plain 4-digit number can be a date/price.  Accept plain 4-digit
        # codes only when the master confirms them; letter-suffixed codes such
        # as 285A are accepted directly because they are unambiguous.
        if len(c)==4 and c.isdigit() and c not in master:
            # ChartNavi/news commonly writes company codes as (7203).
            # Accept that explicit ticker notation even if the local master is stale.
            after=text[m.end():m.end()+1]
            before=text[max(0,m.start()-1):m.start()]
            if after not in (')','）') and before not in ('(','（'):
                continue
        if c not in found: found.append(c)
    return found

def name_from_chart_window(window, code, master):
    if code in master and master[code].get('name'):
        return master[code]['name']
    # ChartNavi uses the visible pattern "会社名(code)".
    m=re.search(r'([^\n]{1,60})\('+re.escape(code)+r'\)', window)
    if m:
        name=clean(m.group(1)).strip(' 0123456789.-')
        if name: return name[-50:]
    return code

def chartnavi(master, date):
    url=f'https://chartnavi.com/scan/wadai/date/_{date:%Y%m%d}/'
    p=TextParser(); p.feed(fetch(url))
    text=p.text
    items=[]
    codes=code_from_text(text,master)
    for rank,code in enumerate(codes[:20],1):
        pos=text.find(f'({code})')
        if pos < 0: pos=text.find(code)
        window=text[max(0,pos-220):pos+520] if pos >= 0 else ''
        name=name_from_chart_window(window,code,master)
        items.append({'code':code,'name':name,'source':'投資家話題','source_type':'investor','source_name':'チャートなび','url':url,'title':clean(window)[:420],'weight':4.0,'rank':rank})
    return items

def rss_items(query, source_type, source_name, master, weight):
    url='https://news.google.com/rss/search?'+urllib.parse.urlencode({'q':query,'hl':'ja','gl':'JP','ceid':'JP:ja'})
    raw=fetch(url)
    out=[]
    # Google News RSS uses <item><title>...</title><link>...</link>.
    # Parse those pairs directly; HTMLParser's <a> logic cannot see RSS links.
    for item in re.findall(r'<item\b[^>]*>(.*?)</item>', raw, flags=re.I|re.S):
        tm=re.search(r'<title\b[^>]*>(.*?)</title>', item, flags=re.I|re.S)
        lm=re.search(r'<link\b[^>]*>(.*?)</link>', item, flags=re.I|re.S)
        if not tm or not lm: continue
        title=clean(re.sub(r'<[^>]+>',' ',html.unescape(tm.group(1))))
        href=html.unescape(lm.group(1)).strip()
        codes=code_from_text(title,master)
        if not codes and master:
            # Many news/video titles show the company name but omit the ticker.
            # Match only reasonably distinctive master names.
            for c,info in master.items():
                n=clean(str(info.get('name') or ''))
                if len(n)>=3 and n in title:
                    codes.append(c)
                    if len(codes)>=3: break
        if not codes: continue
        for code in codes[:3]:
            name=master.get(code,{}).get('name') or code
            out.append({'code':code,'name':name,'source':source_type,'source_type':source_type,'source_name':source_name,'url':href,'title':title,'weight':weight})
    return out[:40]

def load_scores():
    out={}
    for fn in ('data/stocks.json','data/decision_ranking.json'):
        p=Path(fn)
        if not p.exists(): continue
        try:
            d=json.loads(p.read_text(encoding='utf-8'))
            rows=d.get('stocks',d.get('ranking',d if isinstance(d,list) else []))
            if isinstance(rows,dict): rows=list(rows.values())
            for x in rows:
                c=str(x.get('code',x.get('security_code',''))).strip().upper()
                if c: out[c]=x
        except Exception: pass
    return out

def main():
    now=datetime.now(JST); master=load_master(); events=[]; errors=[]
    sources=[
        ('chartnavi', lambda: chartnavi(master,now)),
        ('kabutan', lambda: rss_items('日本株 話題株 OR 注目株 OR 決算', 'ニュース','株探・Google News',master,2.5)),
        ('analyst', lambda: rss_items('日本株 アナリスト 注目 銘柄', 'アナリスト','Google News',master,2.0)),
        ('youtube', lambda: rss_items('site:youtube.com 日本株 投資 株式 銘柄', 'YouTube','YouTube/Google News',master,2.0)),
    ]
    for label,fn in sources:
        try: events.extend(fn())
        except Exception as e: errors.append(f'{label}: {type(e).__name__}: {e}')
    by={}
    for e in events:
        c=e['code']; b=by.setdefault(c,{'code':c,'name':e['name'],'market':master[c].get('market'),'industry':master[c].get('industry'),'events':[],'source_types':set(),'score':0.0})
        b['events'].append(e); b['source_types'].add(e['source_type'])
        b['score'] += float(e.get('weight',1))
        if e.get('rank'):
            b['score'] += max(0, 5.0-(e['rank']-1)*0.4)
    scores=load_scores()
    result=[]
    for b in by.values():
        # Diversity bonus prevents a single noisy source from dominating.
        b['score'] += max(0,len(b['source_types'])-1)*2.5
        s=scores.get(b['code'],{})
        d=s.get('details',s)
        b['kabu_score']=d.get('score',s.get('score'))
        b['signal']=s.get('signal',d.get('signal'))
        b['rsi14']=d.get('rsi14',s.get('rsi14'))
        b['change']=d.get('change',s.get('change'))
        ev=sorted(b['events'], key=lambda x:x.get('rank',999))
        b['reason']=ev[0]['title'] if ev else ''
        b['sources']=[{'name':e['source_name'],'type':e['source_type'],'title':e['title'],'url':e['url']} for e in ev[:6]]
        b['source_types']=sorted(b['source_types'])
        b.pop('events',None)
        result.append(b)
    result.sort(key=lambda x:(-x['score'],x['code']))
    top=result[:5]
    payload={
        'updated_at':now.isoformat(),
        'date':now.strftime('%Y-%m-%d'),
        'title':'今日の注目5選',
        'status':'ok' if top else 'no_data',
        'method':'同日公開の投資家話題・ニュース・アナリスト言及・YouTube関連情報を銘柄単位に集約。情報源の種類と複数言及を加点し、上位5銘柄を表示。これはBUY判定ではない。',
        'source_policy':'各記事・動画へのリンクと発信元を保存。外部情報の注目度とkabu-scoreの総合BUY判定は別物として表示する。',
        'errors':errors,
        'candidates':top,
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':payload['status'],'count':len(top),'errors':errors},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
