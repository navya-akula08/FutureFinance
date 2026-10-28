from datetime import date, timedelta
from database.db import query

def summary(user_id, start, end):
    rows = query("SELECT transaction_type, NVL(SUM(amount),0) total FROM transactions WHERE user_id=:p_user_id AND transaction_date>=TO_DATE(:s,'YYYY-MM-DD') AND transaction_date<TO_DATE(:e,'YYYY-MM-DD')+1 GROUP BY transaction_type", {"p_user_id":user_id,"s":start,"e":end})
    totals = {r['transaction_type']: float(r['total']) for r in rows}
    income, expense = totals.get('INCOME',0), totals.get('EXPENSE',0)
    return {"income":income,"expense":expense,"net":income-expense,"savings_rate":((income-expense)/income*100 if income else None)}

def category_spend(user_id, start, end):
    return query("SELECT c.name category,SUM(t.amount) amount FROM transactions t JOIN categories c ON c.category_id=t.category_id WHERE t.user_id=:p_user_id AND t.transaction_type='EXPENSE' AND t.transaction_date>=TO_DATE(:s,'YYYY-MM-DD') AND t.transaction_date<TO_DATE(:e,'YYYY-MM-DD')+1 GROUP BY c.name ORDER BY amount DESC", {"p_user_id":user_id,"s":start,"e":end})

def budget_status(user_id, month_start):
    """Return budget amount, actual category expenses, remainder, and utilization."""
    rows=query("""SELECT c.name category,b.amount budget,NVL(SUM(t.amount),0) actual
                 FROM budgets b JOIN categories c ON c.category_id=b.category_id
                 LEFT JOIN transactions t ON t.user_id=b.user_id AND t.category_id=b.category_id
                   AND t.transaction_type='EXPENSE' AND t.transaction_date>=b.month_start
                   AND t.transaction_date<ADD_MONTHS(b.month_start,1)
                 WHERE b.user_id=:p_user_id AND b.month_start=TO_DATE(:p_month,'YYYY-MM-DD')
                 GROUP BY c.name,b.amount ORDER BY c.name""",
               {"p_user_id":user_id,"p_month":month_start})
    for row in rows:
        row['budget']=float(row['budget']); row['actual']=float(row['actual'])
        row['remaining']=row['budget']-row['actual']
        row['utilization']=row['actual']/row['budget']*100 if row['budget'] else 0.0
    return rows

def monthly_cashflows(user_id):
    rows=query("SELECT TO_CHAR(TRUNC(transaction_date,'MM'),'YYYY-MM') month_key,SUM(CASE WHEN transaction_type='INCOME' THEN amount ELSE 0 END) income,SUM(CASE WHEN transaction_type='EXPENSE' THEN amount ELSE 0 END) expense FROM transactions WHERE user_id=:u AND transaction_date<TRUNC(SYSDATE,'MM') GROUP BY TRUNC(transaction_date,'MM') ORDER BY TRUNC(transaction_date,'MM')",{'u':user_id})
    return [[float(r['income']),float(r['expense'])] for r in rows]

def monthly_trends(user_id, months=6):
    """Actual income and expense totals by calendar month, including empty months."""
    months=max(1,min(int(months),24))
    rows=query("""SELECT TO_CHAR(TRUNC(transaction_date,'MM'),'YYYY-MM') month_key,
                       SUM(CASE WHEN transaction_type='INCOME' THEN amount ELSE 0 END) income,
                       SUM(CASE WHEN transaction_type='EXPENSE' THEN amount ELSE 0 END) expense
                FROM transactions
                WHERE user_id=:p_user_id
                  AND transaction_date>=ADD_MONTHS(TRUNC(SYSDATE,'MM'),-:p_month_offset)
                  AND transaction_date<ADD_MONTHS(TRUNC(SYSDATE,'MM'),1)
                GROUP BY TRUNC(transaction_date,'MM') ORDER BY TRUNC(transaction_date,'MM')""",
               {"p_user_id":user_id,"p_month_offset":months-1})
    by_month={r['month_key']:{"income":float(r['income']),"expense":float(r['expense'])} for r in rows}
    today=date.today(); year=today.year; month=today.month; keys=[]
    for offset in range(months-1,-1,-1):
        index=year*12+(month-1)-offset; y,month_zero=divmod(index,12); keys.append(f"{y:04d}-{month_zero+1:02d}")
    return [{"month":key,"income":by_month.get(key,{}).get("income",0.0),"expense":by_month.get(key,{}).get("expense",0.0)} for key in keys]

def daily_spending(user_id, days=14):
    """Return actual daily expenses for the recent window, including empty days."""
    days=max(1,min(int(days),90)); today=date.today(); start=today-timedelta(days=days-1)
    rows=query("""SELECT TRUNC(transaction_date) spending_day, NVL(SUM(amount),0) expense
                 FROM transactions
                 WHERE user_id=:u AND transaction_type='EXPENSE'
                   AND transaction_date>=TO_DATE(:p_start,'YYYY-MM-DD')
                   AND transaction_date<TO_DATE(:p_end,'YYYY-MM-DD')+1
                 GROUP BY TRUNC(transaction_date) ORDER BY spending_day""",
               {"u":user_id,"p_start":start.isoformat(),"p_end":today.isoformat()})
    by_day={r['spending_day'].date() if hasattr(r['spending_day'],'date') else r['spending_day']:float(r['expense']) for r in rows}
    return [{"date":(today-timedelta(days=offset)).isoformat(),"expense":by_day.get(today-timedelta(days=offset),0.0)}
            for offset in range(days-1,-1,-1)]

def weekly_spending(user_id, weeks=8):
    """Return recorded expense totals grouped by ISO week."""
    weeks=max(1,min(int(weeks),52)); today=date.today(); current_week=today-timedelta(days=today.weekday()); start=current_week-timedelta(weeks=weeks-1); end=current_week+timedelta(days=6)
    rows=query("""SELECT TRUNC(transaction_date,'IW') week_start, SUM(amount) expense
                 FROM transactions WHERE user_id=:u AND transaction_type='EXPENSE'
                   AND transaction_date>=TO_DATE(:p_start,'YYYY-MM-DD')
                   AND transaction_date<TO_DATE(:p_end,'YYYY-MM-DD')+1
                 GROUP BY TRUNC(transaction_date,'IW') ORDER BY week_start""",
               {"u":user_id,"p_start":start.isoformat(),"p_end":end.isoformat()})
    return [{"week_start":r['week_start'].strftime('%Y-%m-%d') if hasattr(r['week_start'],'strftime') else str(r['week_start']),
             "expense":float(r['expense'])} for r in rows]
