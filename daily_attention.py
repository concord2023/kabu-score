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
    """Extract stock codes only when the evidence is unambiguous.

    Plain 4-digit codes must exist in the current JPX/company master.  A
    letter-suffixed code (e.g. 285A) is accepted only when it looks like a
    JPX-style code and is not embedded in another number.  Company-name
    matching is handled separately and never guesses from generic words.
    """
    found=[]
    for m in re.finditer(r'(?<![0-9A-Z])([0-9]{4}[A-Z]?)(?![0-9A-Z])', text or '', re.I):
        c=m.group(1).upper()
        if c.isdigit() and c not in master:
            # Without the master, accept a 4-digit code only when the source
            # itself labels it as a security code (e.g. Company(6853), 6853.T).
            lo=max(0,m.start()-12); hi=min(len(text),m.end()+12)
            ctx=(text[lo:hi] or '')
            explicit=(f'({c})' in ctx or f'（{c}）' in ctx or f'{c}.T' in ctx.upper() or re.search(r'コード.{0,4}'+re.escape(c),ctx))
            if not explicit:
                continue
        if c not in found:
            found.append(c)
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
            # Company-name matching is deliberately conservative.  Generic words
            # such as 「キング」「インデックス」 must never become a ticker merely
            # because they occur in an unrelated headline.
            generic_names={'キング','インデックス','INDEX','指数','株価','日本株','投資','ニュース','テクノロジー','アドバンス','市場','銘柄'}
            candidates=[]
            for c,info in master.items():
                n=clean(str(info.get('name') or ''))
                if not n or n.upper() in generic_names or len(n)<5:
                    continue
                # Require the exact company name as a standalone-ish phrase and
                # prefer longer names so substrings do not win over the real name.
                if n in title:
                    candidates.append((len(n),c,n))
            for _,c,_ in sorted(candidates, reverse=True)[:3]:
                codes.append(c)
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
    d=score_row.get('details',score_row) if isinstance(score_row,dict) else {}
    ch=_num(d.get('change',score_row.get('change') if isinstance(score_row,dict) else None))
    vr=_num(d.get('volume_ratio',score_row.get('volume_ratio') if isinstance(score_row,dict) else None))
    srcs=sorted(b.get('source_types',[])); events=b.get('events',[])
    basis=_selection_basis(b)
    if not basis: basis=['同日の外部情報で言及']
    return '＋'.join(basis)+f'（話題強度{b.get("heat_score",0):.1f}）。'


def build_research_memo(b):
    """Turn collected evidence into a simple, auditable research note."""
    events=sorted(b.get('events',[]),key=lambda x:x.get('rank',999))
    titles=[]
    for e in events:
        t=clean(e.get('title',''))
        if t and t not in titles: titles.append(t)
    rank=_rank_value(events); srcs=sorted(b.get('source_types',[]))
    labels={'investor':'投資家話題','ニュース':'ニュース','アナリスト':'アナリスト','YouTube':'YouTube'}
    source_labels=[labels.get(x,x) for x in srcs]
    category=material_category(titles[0] if titles else '')
    ch=_num(b.get('change')); vr=_num(b.get('volume_ratio')); heat=b.get('heat_score')

    # Evidence is presented in the same order a human researcher would check it:
    # attention -> material -> market confirmation -> remaining uncertainty.
    attention=[]
    if rank is not None: attention.append(f'投資家話題ランキングは{rank}位')
    if len(srcs): attention.append(f'情報源は{len(srcs)}種類（{"・".join(source_labels)}）')
    if len(events): attention.append(f'同日言及は{len(events)}件')
    attention_text='。'.join(attention)+'。' if attention else '同日の外部情報で言及が確認されています。'

    material=[]
    for t in titles[:3]: material.append(t)
    material_text=' / '.join(material) if material else '具体的な材料を特定できませんでした。'

    confirmation=[]
    if ch is not None: confirmation.append(f'株価は前日比{ch:+.2f}%')
    if vr is not None: confirmation.append(f'出来高は平常比{vr:.1f}倍')
    confirmation_text='。'.join(confirmation)+'。' if confirmation else '日次株価データからの確認材料はありません。'

    if rank is not None and rank<=5:
        conclusion='投資家話題の上位に入り、上記の情報量・材料・値動きの確認がそろったため、当日の注目候補として残しました。'
    elif len(srcs)>=2:
        conclusion='複数種類の情報源で同日に言及が重なったため、単一記事だけの話題ではないと判断して残しました。'
    else:
        conclusion='外部情報だけで十分な熱量を確認できるかを追加条件で確認したうえで掲載しています。'
    check='見出しだけでは因果関係を断定しません。決算短信・適時開示・原記事本文など、リンク先の一次情報を確認してください。'
    return {
        'headline': titles[0] if titles else '当日の外部情報で話題化',
        'summary': f'【話題の事実】{attention_text}【材料】{material_text}【値動き確認】{confirmation_text}',
        'why': f'【選定理由】{conclusion}',
        'category': category,
        'evidence': attention + confirmation,
        'check_point': check,
        'source_count': len(srcs),
        'mention_count': len(events),
        'sources': source_labels,
        'detail': material_text,
        'heat_score': heat,
    }


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


def _rank_value(events):
    ranks=[e.get('rank') for e in events if isinstance(e.get('rank'),int)]
    return min(ranks) if ranks else None

def _heat_score(b):
    """Measure *evidence of attention*, not stock attractiveness.

    A single weak headline is intentionally unable to fill one of the five
    slots.  The score rewards independent evidence, same-day repetition and
    market confirmation.  It is paired with hard eligibility rules below.
    """
    events=b.get('events',[]); src=len(b.get('source_types',[])); rank=_rank_value(events)
    score=0.0
    if rank is not None:
        if rank<=3: score+=5
        elif rank<=5: score+=4
        elif rank<=10: score+=2
    if src>=3: score+=5
    elif src==2: score+=3
    if len(events)>=4: score+=3
    elif len(events)>=3: score+=2
    elif len(events)>=2: score+=1
    ch=_num(b.get('change')); vr=_num(b.get('volume_ratio'))
    if ch is not None:
        if abs(ch)>=7: score+=4
        elif abs(ch)>=5: score+=3
        elif abs(ch)>=3: score+=2
        elif abs(ch)>=1.5: score+=1
    if vr is not None:
        if vr>=3: score+=4
        elif vr>=2: score+=3
        elif vr>=1.5: score+=2
    # Concrete material words are stronger than generic "注目/話題" wording.
    material=sum(1 for e in events if material_category(e.get('title',''))!='投資家の話題・材料')
    if material>=2: score+=2
    elif material==1: score+=1
    return round(score,1)

def _eligible_for_top5(b):
    """Use several independent ways to find real same-day attention.

    The list should be selective, but it must not become empty merely because
    one source or the price snapshot is unavailable.  A top-10 investor-topic
    result with a concrete reason is enough to enter the pool; cross-source
    evidence and strong market movement are additional routes.
    """
    events=b.get('events',[])
    src=len(b.get('source_types',[]))
    rank=_rank_value(events)
    ch=_num(b.get('change'))
    vr=_num(b.get('volume_ratio'))
    concrete_material=any(material_category(e.get('title',''))!='投資家の話題・材料' for e in events)
    top10 = rank is not None and rank<=10
    market_confirm=(ch is not None and abs(ch)>=2) or (vr is not None and vr>=1.3)
    cross_source=src>=2
    repeated=len(events)>=2
    strong_move=(ch is not None and abs(ch)>=5) or (vr is not None and vr>=2)
    # Four complementary routes.  This is deliberately much broader than the
    # previous "top-5 + confirmation" rule.
    return (
        (top10 and concrete_material) or
        (top10 and (market_confirm or repeated)) or
        (cross_source and (repeated or concrete_material)) or
        (strong_move and concrete_material)
    )

def _selection_basis(b):
    rank=_rank_value(b.get('events',[])); src=len(b.get('source_types',[])); ch=_num(b.get('change')); vr=_num(b.get('volume_ratio'))
    basis=[]
    if rank is not None and rank<=5: basis.append(f'投資家話題{rank}位以内')
    if src>=2: basis.append(f'{src}種類の情報源')
    if len(b.get('events',[]))>=2: basis.append(f'同日{len(b["events"])}件の言及')
    if ch is not None and abs(ch)>=3: basis.append(f'前日比{ch:+.2f}%')
    if vr is not None and vr>=1.5: basis.append(f'出来高平常比{vr:.1f}倍')
    return basis

def main():
    now=datetime.now(JST); master=load_master(); events=[]; errors=[]
    sources=[
        ('chartnavi', lambda: chartnavi(master,now)),
        # News is searched from several angles instead of relying on one query.
        ('news_material', lambda: rss_items('日本株 今日 材料 OR 適時開示 OR 決算 OR 受注', 'ニュース','ニュース・材料検索',master,2.5)),
        ('news_attention', lambda: rss_items('日本株 今日 注目株 OR 話題株 OR 急騰 OR 急落', 'ニュース','ニュース・話題検索',master,2.5)),
        ('large_holder', lambda: rss_items('日本株 大量保有報告 変更 株主 自社株買い', '開示','大量保有・開示検索',master,2.2)),
        ('analyst', lambda: rss_items('日本株 アナリスト 注目 銘柄 レポート', 'アナリスト','アナリスト・レポート検索',master,2.0)),
        ('theme', lambda: rss_items('日本株 AI 半導体 データセンター 防衛 量子電池 注目', 'テーマ','テーマ・業界検索',master,1.8)),
        ('youtube', lambda: rss_items('site:youtube.com 日本株 投資 株式 銘柄 今日', 'YouTube','YouTube検索',master,2.0)),
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
        b['volume_ratio']=d.get('volume_ratio',s.get('volume_ratio'))
        b['heat_score']=_heat_score(b)
        b['selection_basis']=_selection_basis(b)
        b['_score_row']=s
        ev=sorted(b['events'], key=lambda x:x.get('rank',999))
        b['reason']=explain_attention({**b, 'events': ev}, s)
        b['movement_type']=attention_type(b)
        b['material_summary']=ev[0]['title'] if ev else ''
        b['explanation']=build_attention_explanation({**b, 'events': ev})
        b['research_memo']=build_research_memo({**b, 'events': ev})
        b['sources']=[{'name':e['source_name'],'type':e['source_type'],'title':e['title'],'url':e['url']} for e in ev[:6]]
        b['source_types']=sorted(b['source_types'])
        b['selection_eligible']=_eligible_for_top5(b)
        b.pop('events',None); b.pop('_score_row',None)
        result.append(b)
    result.sort(key=lambda x:(-x.get('heat_score',0),-x['score'],x['code']))
    eligible=[x for x in result if x.get('selection_eligible') and x.get('heat_score',0)>=4]
    top=eligible[:5]
    # Never fill today's list with yesterday's names.  A missing/weak day is
    # intentionally shown as "十分な話題なし" rather than manufacturing five picks.
    status='ok' if top else ('no_data' if not result else 'insufficient_heat')
    payload={
        'updated_at':now.isoformat(),
        'date':now.strftime('%Y-%m-%d'),
        'title':'今日の注目5選',
        'status':status,
        'method':'同日公開の投資家話題・ニュース/開示・大量保有・アナリスト・テーマ・YouTubeを複数の検索角度から収集。投資家話題順位、情報源の種類、同日言及数、株価/出来高、具体的材料を組み合わせ、複数のルートのいずれかを満たす銘柄を最大5件表示する。無理に5件へ水増ししない。これはBUY判定ではない。',
        'source_policy':'各記事・動画へのリンクと発信元を保存。外部情報の注目度とkabu-scoreの総合BUY判定は別物として表示する。',
        'errors':errors,
        'candidate_pool':len(result),
        'eligible_count':len(eligible),
        'candidates':top,
    }
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':payload['status'],'count':len(top),'errors':errors},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
