"""Conservative backtest for the current reversal BUY hypothesis.
Input: a CSV with newest row first and a close/adj_close column.
This is a diagnostic, not proof of profitability.
"""
import csv, sys
from statistics import mean

def pct(a,b): return (a/b-1)*100 if b else None

def main(path):
    with open(path,encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    rows=rows[::-1]
    vals=[]
    for r in rows:
        v=r.get('adj_close') or r.get('close')
        try: vals.append(float(v))
        except: vals.append(None)
    sig=[]
    for i in range(60,len(vals)-20):
        if any(vals[j] is None for j in range(i-60,i+21)): continue
        ma60=mean(vals[i-60:i])
        vs60=pct(vals[i],ma60); ret1=pct(vals[i],vals[i-1]); ret5=pct(vals[i],vals[i-5]); ret10=pct(vals[i],vals[i-10])
        if vs60<=-5 and ret1>0 and ret5>=-5 and ret10>=-10:
            sig.append((pct(vals[i+5],vals[i]),pct(vals[i+10],vals[i]),pct(vals[i+20],vals[i])))
    print('signals',len(sig))
    if sig:
        for n,k in ((5,0),(10,1),(20,2)):
            xs=[x[k] for x in sig]
            print(f'fwd{n}_avg',round(mean(xs),2),'win',round(sum(x>0 for x in xs)/len(xs)*100,1))
if __name__=='__main__': main(sys.argv[1])
