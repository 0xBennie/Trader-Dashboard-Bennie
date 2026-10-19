"""Indicators and candle patterns over closed candles only. Missing history stays None."""


def _f(candles, key):
    return [float(c[key]) for c in candles]


def ema(values, n):
    out=[None]*len(values)
    if len(values)<n:
        return out
    k=2/(n+1);prev=sum(values[:n])/n;out[n-1]=prev
    for i in range(n,len(values)):
        prev=values[i]*k+prev*(1-k);out[i]=prev
    return out


def rsi(closes, n=14):
    out=[None]*len(closes)
    if len(closes)<=n:
        return out
    diffs=[b-a for a,b in zip(closes,closes[1:])]
    gain=sum(max(d,0) for d in diffs[:n])/n;loss=sum(max(-d,0) for d in diffs[:n])/n
    def value(g,l):
        return 50.0 if g==l==0 else 100.0 if l==0 else 100-100/(1+g/l)
    out[n]=value(gain,loss)
    for i in range(n,len(diffs)):
        d=diffs[i];gain=(gain*(n-1)+max(d,0))/n;loss=(loss*(n-1)+max(-d,0))/n
        out[i+1]=value(gain,loss)
    return out


def macd(closes, fast=12, slow=26, signal=9):
    a,b=ema(closes,fast),ema(closes,slow)
    line=[x-y if x is not None and y is not None else None for x,y in zip(a,b)]
    start=next((i for i,x in enumerate(line) if x is not None),len(line))
    sig=[None]*start+ema(line[start:],signal)
    hist=[x-y if x is not None and y is not None else None for x,y in zip(line,sig)]
    return dict(line=line,signal=sig,hist=hist)


def atr(candles, n=14):
    out=[None]*len(candles)
    if len(candles)<=n:
        return out
    h,l,c=_f(candles,'high'),_f(candles,'low'),_f(candles,'close')
    tr=[None]+[max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])) for i in range(1,len(candles))]
    prev=sum(tr[1:n+1])/n;out[n]=prev
    for i in range(n+1,len(candles)):
        prev=(prev*(n-1)+tr[i])/n;out[i]=prev
    return out


def swings(candles, left=2, right=2):
    h,l=_f(candles,'high'),_f(candles,'low');highs=[];lows=[]
    for i in range(left,len(candles)-right):
        side=list(range(i-left,i))+list(range(i+1,i+right+1))
        if all(l[i]<l[j] for j in side):lows.append((i,l[i]))
        if all(h[i]>h[j] for j in side):highs.append((i,h[i]))
    return dict(highs=highs,lows=lows)


def patterns(candles):
    if not candles:
        return []
    found=[];c=candles[-1]
    o,h,l,cl=(float(c[k]) for k in ('open','high','low','close'))
    rng=h-l
    if rng<=0:
        return found
    body=abs(cl-o);upper=h-max(o,cl);lower=min(o,cl)-l
    if lower>=2*body and upper<=body:found.append('hammer')
    if upper>=2*body and lower<=body:found.append('shooting_star')
    if lower>=rng*2/3:found.append('bull_pin')
    if upper>=rng*2/3:found.append('bear_pin')
    if body<=rng*0.1:found.append('doji')
    if len(candles)>=2:
        p=candles[-2];po,ph,pl,pc=(float(p[k]) for k in ('open','high','low','close'))
        if pc<po and cl>o and o<=pc and cl>=po:found.append('bull_engulfing')
        if pc>po and cl<o and o>=pc and cl<=po:found.append('bear_engulfing')
        if h<=ph and l>=pl:found.append('inside')
    return found


def divergence(candles, rsi_values, kind):
    points=swings(candles)['lows' if kind=='bull' else 'highs']
    if len(points)<2:
        return None
    (i1,p1),(i2,p2)=points[-2:]
    r1,r2=rsi_values[i1],rsi_values[i2]
    if r1 is None or r2 is None:
        return None
    return p2<p1 and r2>r1 if kind=='bull' else p2>p1 and r2<r1


def snapshot(candles):
    keys=('close','high','low','ema20','ema50','rsi','rsi_prev','macd_hist','macd_hist_prev','atr',
          'volume_ratio','swing_low','swing_high','bull_div','bear_div','prev_high','prev_low','recent_low','recent_high','price_decimals')
    out=dict.fromkeys(keys);out.update(bars=len(candles),patterns=[],prev_patterns=[])
    if not candles:
        return out
    closes=_f(candles,'close');vol=_f(candles,'volume')
    r=rsi(closes);m=macd(closes)['hist'];s=swings(candles)
    out.update(close=closes[-1],high=float(candles[-1]['high']),low=float(candles[-1]['low']),
               ema20=ema(closes,20)[-1],ema50=ema(closes,50)[-1],rsi=r[-1],rsi_prev=r[-2] if len(r)>1 else None,
               macd_hist=m[-1],macd_hist_prev=m[-2] if len(m)>1 else None,atr=atr(candles)[-1],
               volume_ratio=vol[-1]/(sum(vol[-21:-1])/20) if len(vol)>20 and sum(vol[-21:-1])>0 else None,
               swing_low=s['lows'][-1][1] if s['lows'] else None,swing_high=s['highs'][-1][1] if s['highs'] else None,
               patterns=patterns(candles),prev_patterns=patterns(candles[:-1]),
               prev_high=float(candles[-2]['high']) if len(candles)>1 else None,prev_low=float(candles[-2]['low']) if len(candles)>1 else None,
               price_decimals=max(len(x.rstrip('0').split('.')[1]) if '.' in x else 0 for x in (str(c['close']) for c in candles[-20:])),
               recent_low=min(_f(candles[-3:],'low')),recent_high=max(_f(candles[-3:],'high')),bull_div=divergence(candles,r,'bull'),bear_div=divergence(candles,r,'bear'))
    return out
