"""Conservative, explainable recurring-expense detection from transaction history."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from database.db import query


def detect_recurring_expenses(user_id, lookback_days=400):
    """Find repeated expense payees with regular dates and similar amounts.

    This is a heuristic, not a predictive model. Rows without a merchant or
    description are excluded to avoid falsely treating every category purchase
    as a subscription.
    """
    lookback_days=max(90,min(int(lookback_days),730))
    start=date.today()-timedelta(days=lookback_days)
    rows=query("""SELECT t.transaction_date,t.amount,t.merchant_name,t.description,c.name category
                 FROM transactions t JOIN categories c ON c.category_id=t.category_id
                 WHERE t.user_id=:u AND t.transaction_type='EXPENSE'
                   AND t.transaction_date>=TO_DATE(:p_start,'YYYY-MM-DD')
                 ORDER BY t.transaction_date""",
               {"u":user_id,"p_start":start.isoformat()})
    groups=defaultdict(list)
    for row in rows:
        label=(row.get('merchant_name') or row.get('description') or '').strip()
        if not label:
            continue
        key=(row['category'],label.casefold())
        groups[key].append((row['transaction_date'],float(row['amount']),label))

    found=[]
    for (category,_), entries in groups.items():
        if len(entries)<3:
            continue
        entries.sort(key=lambda x:x[0])
        dates=[x[0].date() if isinstance(x[0],datetime) else x[0] for x in entries]
        gaps=[(b-a).days for a,b in zip(dates,dates[1:])]
        if not gaps or min(gaps)<5:
            continue
        mean_gap=sum(gaps)/len(gaps)
        if mean_gap>45 or (max(gaps)-min(gaps))/max(mean_gap,1)>.65:
            continue
        amounts=[x[1] for x in entries]
        avg=sum(amounts)/len(amounts)
        if avg<=0 or (max(amounts)-min(amounts))/avg>.5:
            continue
        cadence='weekly' if mean_gap<=10 else 'monthly' if mean_gap<=40 else 'regular'
        found.append({"merchant":entries[0][2],"category":category,"occurrences":len(entries),
                      "average_amount":round(avg,2),"cadence":cadence,
                      "last_seen":dates[-1].isoformat(),
                      "explanation":f"{len(entries)} similar payments averaging ₹{avg:,.2f}, about every {mean_gap:.0f} days."})
    return sorted(found,key=lambda x:(x['average_amount'],x['occurrences']),reverse=True)
