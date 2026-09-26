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


def _num(v):
    try:
        n=float(v)
        return n if n == n else None
    except Exception:
        return None

def material_category(title):
    """Translate a source headline into a compact, factual 'what is moving it' tag."""
    t=clean(title)
    rules=[
        (r'ストップ高|Ｓ高|S高|急騰|急上昇|大幅高|続伸|上昇|反発', '株価上昇・需給/値動き'),
        (r'急落|大幅安|反落|下落|売られ|暴落', '株価下落・警戒'),
        (r'受注|受注高|契約|案件', '受注・案件材料'),
        (r'決算|業績|増益|減益|上方修正|下方修正|利益|売上', '決算・業績材料'),
        (r'NVIDIA|エヌビディア|AI|ＡＩ|データセンター|半導体', 'AI・半導体/データセンターテーマ'),
        (r'自社株買い|株主還元|増配|配当', '株主還元材料'),
        (r'提携|協業|買収|TOB|ＴＯＢ|M&A|Ｍ＆Ａ', '提携・M&A材料'),
        (r'需給|信用|貸借|空売り|買い残|売り残', '需給材料'),
        (r'懸念|警戒|問題|不透明|悪化', '懸念・警戒材料'),
    ]
    for pat,label in rules:
        if re.search(pat,t,re.I): return label
    return '投資家の話題・材料'

def explain_attention(b, score_row):
    """Explain both *why it was selected* and *what is actually happening*."""
    d=score_row.get('details',score_row) if isinstance(score_row,dict) else {}
    ch=_num(d.get('change',score_row.get('change') if isinstance(score_row,dict) else None))
    vr=_num(d.get('volume_ratio',score_row.get('volume_ratio') if isinstance(score_row,dict) else None))
    rsi=_num(d.get('rsi14',score_row.get('rsi14') if isinstance(score_row,dict) else None))
    rs=_num(d.get('relative_strength',score_row.get('relative_strength') if isinstance(score_row,dict) else None))
    srcs=sorted(b.get('source_types',[]))
    events=b.get('events',[])
    ranked=sorted(events,key=lambda x:x.get('rank',999))
    top=ranked[0] if ranked else {}
    title=clean(top.get('title',''))
    rank=top.get('rank')
    parts=[]
    if rank:
        parts.append(f'当日の投資家話題ランキング{int(rank)}位')
    if len(srcs)>=3: parts.append(f'{len(srcs)}種類の情報源で言及')
    elif len(srcs)==2: parts.append('複数の情報源で言及')
    elif len(srcs)==1:
        src_label={'investor':'投資家話題','ニュース':'ニュース','アナリスト':'アナリスト','YouTube':'YouTube'}.get(srcs[0],srcs[0])
        parts.append(f'{src_label}で話題化')
    if title:
        parts.append(f'材料は「{title[:90]}」')
    # Quantitative confirmation from the stock-score data, when available.
    if ch is not None:
        if abs(ch)>=5: parts.append(f'株価も前日比{ch:+.1f}%と大きく動いた')
        elif abs(ch)>=2: parts.append(f'株価も前日比{ch:+.1f}%と動意')
    if vr is not None and vr>=1.5:
        parts.append(f'出来高は平常比{vr:.1f}倍')
    if rs is not None and abs(rs)>=2:
        parts.append(f'日経平均比の相対強度は{rs:+.1f}pt')
    if rsi is not None and rsi>=70:
        parts.append(f'RSI{rsi:.1f}で過熱警戒')
    elif rsi is not None and rsi<=30:
        parts.append(f'RSI{rsi:.1f}で売られ過ぎ圏')
    if not parts:
        parts.append('当日の外部情報で言及が増えた')
    return '。'.join(parts)+'。'

def build_attention_explanation(b):
    """Return structured explanation used by both the compact card and detail page."""
    events=sorted(b.get('events',[]),key=lambda x:x.get('rank',999))
    top=events[0] if events else {}
    title=clean(top.get('title',''))
    category=material_category(title)
    reasons=[]
    rank=top.get('rank')
    if rank: reasons.append(f'投資家話題ランキング{int(rank)}位')
    if len(b.get('source_types',[]))>1: reasons.append(f"{len(b['source_types'])}種類の情報源")
    elif len(events)>1: reasons.append(f'{len(events)}件の同日言及')
    else: reasons.append('当日の話題化')
    why='＋'.join(reasons)
    score_parts={
        'source_types':len(b.get('source_types',[])),
        'mentions':len(events),
        'investor_rank':rank,
    }
    return {
        'why_selected':why,
        'what_is_happening':title or '当日の外部情報で話題化',
        'material_category':category,
        'score_breakdown':score_parts,
    }

def attention_type(b):
    d=b.get('_score_row',{}); d=d.get('details',d) if isinstance(d,dict) else {}
    ch=d.get('change',b.get('change')); vr=d.get('volume_ratio')
    try: ch=float(ch) if ch is not None else None
    except Exception: ch=None
    try: vr=float(vr) if vr is not None else None
    except Exception: vr=None
    if ch is not None and ch>=10 and vr is not None and vr>=2: return '急騰＋出来高集中'
    if ch is not None and ch>=5: return '上昇・動意'
    if ch is not None and ch<=-5: return '下落・急変'
    if vr is not None and vr>=2: return '出来高集中'
    if len(b.get('source_types',[]))>=2: return '複数情報源で話題'
    return '話題集中'

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
        c=e['code']; info=master.get(c,{})
        b=by.setdefault(c,{'code':c,'name':e['name'],'market':info.get('market'),'industry':info.get('industry'),'events':[],'source_types':set(),'score':0.0})
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
        # A name appearing in a news title is not enough: require a real
        # listed-company master entry, and when daily score data exists use it
        # to validate that the code has an actual market price.
        if b['code'] not in master:
            continue
        if s and s.get('price') is None and d.get('price') is None:
            continue
        b['kabu_score']=d.get('score',s.get('score'))
        b['signal']=s.get('signal',d.get('signal'))
        b['rsi14']=d.get('rsi14',s.get('rsi14'))
        b['change']=d.get('change',s.get('change'))
        b['_score_row']=s
        ev=sorted(b['events'], key=lambda x:x.get('rank',999))
        b['reason']=explain_attention({**b, 'events': ev}, s)
        b['movement_type']=attention_type(b)
        b['material_summary']=ev[0]['title'] if ev else ''
        b['explanation']=build_attention_explanation({**b, 'events': ev})
        b['sources']=[{'name':e['source_name'],'type':e['source_type'],'title':e['title'],'url':e['url']} for e in ev[:6]]
        b['source_types']=sorted(b['source_types'])
        b.pop('events',None); b.pop('_score_row',None)
        result.append(b)
    result.sort(key=lambda x:(-x['score'],x['code']))
    top=result[:5]
    previous=None
    if OUT.exists():
        try:
            previous=json.loads(OUT.read_text(encoding='utf-8'))
        except Exception:
            previous=None
    if not top and previous and previous.get('candidates'):
        top=previous.get('candidates',[])[:5]
        status='stale'
    else:
        status='ok' if top else 'no_data'
    payload={
        'updated_at':now.isoformat(),
        'date':now.strftime('%Y-%m-%d'),
        'title':'今日の注目5選',
        'status':status,
        'method':'同日公開の投資家話題・ニュース・アナリスト言及・YouTube関連情報を銘柄単位に集約。情報源の種類と複数言及を加点し、上位5銘柄を表示。これはBUY判定ではない。',
        'source_policy':'各記事・動画へのリンクと発信元を保存。外部情報の注目度とkabu-scoreの総合BUY判定は別物として表示する。',
        'errors':errors,
        'candidates':top,
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':payload['status'],'count':len(top),'errors':errors},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
