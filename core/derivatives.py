def ob_imbalance(ob, depth_pct=0.01):
    if not ob or not ob.get("bids") or not ob.get("asks"): return {"ob_imbalance":0.0,"ob_spread_bps":0.0}
    try: bb=float(ob["bids"][0][0]); ba=float(ob["asks"][0][0])
    except: return {"ob_imbalance":0.0,"ob_spread_bps":0.0}
    if bb<=0 or ba<=0: return {"ob_imbalance":0.0,"ob_spread_bps":0.0}
    m=(bb+ba)/2; lo=m*(1-depth_pct); hi=m*(1+depth_pct); bu=au=0.0
    for p,q in ob["bids"]:
        try:
            if float(p)>=lo: bu+=float(p)*float(q)
        except: pass
    for p,q in ob["asks"]:
        try:
            if float(p)<=hi: au+=float(p)*float(q)
        except: pass
    t=bu+au
    return {"ob_imbalance":round((bu-au)/t if t>0 else 0.0,6),"ob_spread_bps":round(((ba-bb)/m)*1e4 if m>0 else 0.0,4)}

def funding_zscore(h,w=8):
    r=[float(x["fundingRate"]) for x in (h or []) if x.get("fundingRate") is not None]
    if len(r)<3: return {"funding_z":0.0,"funding_last":0.0}
    w=r[-w:]; m=sum(w)/len(w); sd=(sum((x-m)**2 for x in w)/max(1,len(w)-1))**0.5
    return {"funding_z":round((r[-1]-m)/sd if sd>1e-12 else 0.0,4),"funding_last":round(r[-1],8)}

def oi_change(h,w=6):
    v=[float(x.get("openInterestValue") or x.get("openInterestAmount")) for x in (h or []) if (x.get("openInterestValue") or x.get("openInterestAmount")) is not None]
    if len(v)<2: return {"oi_change":0.0}
    ref=v[max(0,len(v)-1-min(w,len(v)-1))]
    return {"oi_change":round((v[-1]-ref)/ref if ref>0 else 0.0,6)}

def liquidity_change(o,w=12):
    q=[float(b[4])*float(b[5]) for b in (o or []) if len(b)>=6]
    if len(q)<2: return {"liq_change":0.0}
    ref=q[max(0,len(q)-1-min(w,len(q)-1))]
    return {"liq_change":round((q[-1]-ref)/ref if ref>0 else 0.0,6)}

FEATURE_KEYS=["ob_imbalance","funding_z","oi_change","liq_change"]

def derivatives_features(ohlcv=None,order_book=None,funding_history=None,oi_history=None):
    out={}
    out.update(ob_imbalance(order_book))
    out.update(funding_zscore(funding_history))
    out.update(oi_change(oi_history))
    out.update(liquidity_change(ohlcv))
    return {k:out[k] for k in FEATURE_KEYS+["ob_spread_bps","funding_last"]}
