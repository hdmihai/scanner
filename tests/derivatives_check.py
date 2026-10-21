import sys
from core.derivatives import FEATURE_KEYS, derivatives_features, funding_zscore, liquidity_change, ob_imbalance, oi_change

def main():
    ok = True

    # book echilibrat: 99.9*100.1 == 100.1*99.9
    r = ob_imbalance({"bids": [[99.9, 100.1]], "asks": [[100.1, 99.9]]}, depth_pct=0.05)
    ok &= abs(r["ob_imbalance"]) < 1e-3
    ok &= r["ob_spread_bps"] > 0

    # book cu bid dominant
    r = ob_imbalance({"bids": [[100.0, 100.0]], "asks": [[100.5, 1.0]]}, depth_pct=0.02)
    ok &= r["ob_imbalance"] > 0.8

    # book gol
    ok &= ob_imbalance({})["ob_imbalance"] == 0.0

    # funding
    h = [{"fundingRate": 0.0001, "timestamp": i} for i in range(29)] + [{"fundingRate": 0.0010, "timestamp": 29}]
    ok &= funding_zscore(h)["funding_z"] > 1.0
    ok &= funding_zscore([])["funding_z"] == 0.0

    # open interest
    h = [{"openInterestValue": 100.0 + i, "timestamp": i} for i in range(10)]
    ok &= oi_change(h)["oi_change"] > 0

    # liquidity plat
    ok &= abs(liquidity_change([[i,100,100,100,100.0,1.0] for i in range(20)])["liq_change"]) < 1e-9

    # agregator
    r = derivatives_features()
    ok &= all(k in r for k in FEATURE_KEYS)
    ok &= all(r[k] == 0.0 for k in FEATURE_KEYS)

    print("derivatives_check: " + ("ALL PASSED" if ok else "FAILURES"))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
