import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

def detect_anomalies(transactions, contamination=0.08, random_state=42):
    df = pd.DataFrame(transactions).copy()
    if len(df) < 8:
        return [{"transaction_id":r.get("transaction_id"),"scored":False,"explanation":"At least 8 historical transactions are needed for anomaly analysis."} for r in transactions]
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    df["log_amount"] = np.log1p(df["amount"].clip(lower=0))
    # Use only earlier transactions to form each reference median, avoiding a
    # transaction influencing its own comparison when timestamps are available.
    if "transaction_date" in df:
        df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
        df = df.sort_values(["transaction_date", "transaction_id"], kind="stable").reset_index(drop=True)
    df["category_median"] = df.groupby("category")["amount"].transform(lambda s: s.shift().expanding().median())
    df["category_median"] = df["category_median"].fillna(df.groupby("category")["amount"].transform("median")).replace(0,np.nan).fillna(df["amount"].median())
    df["relative_amount"] = df["amount"] / df["category_median"].clip(lower=1)
    x = df[["log_amount","relative_amount"]].replace([np.inf,-np.inf],0).fillna(0)
    model = IsolationForest(contamination=min(max(contamination,0.01),0.2),random_state=random_state,n_estimators=150)
    labels=model.fit_predict(x); scores=-model.score_samples(x)
    out=[]
    for i,row in df.iterrows():
        flagged=labels[i] == -1
        explanation=(f"Amount ₹{row['amount']:,.2f} is {row['relative_amount']:.1f}× the historical median for {row.get('category','this category')}." if flagged else "No unusual amount pattern detected.")
        out.append({"transaction_id":row.get("transaction_id"),"scored":True,"is_unusual":bool(flagged),"score":float(scores[i]),"category_median":float(row['category_median']),"explanation":explanation})
    return out
