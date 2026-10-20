import sys
from core.derivatives import FEATURE_KEYS,derivatives_features,funding_zscore,liquidity_change,ob_imbalance,oi_change
def main():
    ok=True
    r=ob_imbalance({"bids":[[100.0,10.0]],"asks":[[100.5,10.0]]},depth_pct=0.02)
    ok&=abs(r["ob_imbalance"])<1e-9 and r["ob_spread_bps"]>0
    ok&=ob_imbalance({"bids":[[100.0,100.0]],"asks":[[100.5,1.0]]},depth_pct=0.02)["ob_imbalance"]>0.9
    ok&=ob_imbalance({})["ob_imbalance"]==0.0
    h=[{"fundingRate":0.0001,"timestamp":i} for i in range(29)]+[{"fundingRate":0.0010,"timestamp":29}]
    ok&=funding_zscore(h)["funding_z"]>1.0 and funding_zscore([])["funding_z"]==0.0
    h=[{"openInterestValue":100.0+i,"timestamp":i} for i in range(10)]
    ok&=oi_change(h)["oi_change"]>0
    ok&=abs(liquidity_change([[i,100,100,100,100.0,1.0] for i in range(20)])["liq_change"])<1e-9
    r=derivatives_features()
    ok&=all(k in r for k in FEATURE_KEYS) and all(r[k]==0.0 for k in FEATURE_KEYS)
    print("derivatives_check: "+("ALL PASSED" if ok else "FAILURES"))
    return 0 if ok else 1
if __name__=="__main__": sys.exit(main())
