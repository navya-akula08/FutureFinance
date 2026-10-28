from database.db import query, execute

def generate_monthly_recommendations(user_id, year, month):
    """Generate transparent month-over-month and budget observations from Oracle rows."""
    current=f"{year:04d}-{month:02d}-01"
    rows=query("SELECT c.name category,SUM(t.amount) amount FROM transactions t JOIN categories c ON c.category_id=t.category_id WHERE t.user_id=:u AND t.transaction_type='EXPENSE' AND t.transaction_date>=TO_DATE(:m,'YYYY-MM-DD') AND t.transaction_date<ADD_MONTHS(TO_DATE(:m,'YYYY-MM-DD'),1) GROUP BY c.name",{'u':user_id,'m':current})
    prev=query("SELECT c.name category,SUM(t.amount) amount FROM transactions t JOIN categories c ON c.category_id=t.category_id WHERE t.user_id=:u AND t.transaction_type='EXPENSE' AND t.transaction_date>=ADD_MONTHS(TO_DATE(:m,'YYYY-MM-DD'),-1) AND t.transaction_date<TO_DATE(:m,'YYYY-MM-DD') GROUP BY c.name",{'u':user_id,'m':current})
    old={r['category']:float(r['amount']) for r in prev}; out=[]
    for r in rows:
        name=r['category']; amount=float(r['amount']); before=old.get(name,0)
        if before>0 and amount>before*1.2:
            pct=(amount-before)/before*100
            item={'title':f'{name} expenses increased','explanation':f'{name} expenses were ₹{amount:,.2f}, up {pct:.1f}% from ₹{before:,.2f} in the previous month.','supporting_data':f'{amount:.2f} vs {before:.2f}','comparison_period':'Current month vs previous month','action':f'Review recent {name.lower()} purchases and consider a category limit.','priority':'MEDIUM'}
            exists=query("SELECT recommendation_id FROM recommendations WHERE user_id=:u AND title=:t AND created_at>=TRUNC(SYSDATE,'MM') FETCH FIRST 1 ROWS ONLY",{'u':user_id,'t':item['title']},one=True)
            if not exists:
                execute("INSERT INTO recommendations(user_id,title,explanation,supporting_data,comparison_period,suggested_action,priority) VALUES(:u,:t,:e,:d,:p,:a,:l)",{'u':user_id,'t':item['title'],'e':item['explanation'],'d':'{"current":%.2f,"previous":%.2f}'%(amount,before),'p':item['comparison_period'],'a':item['action'],'l':item['priority']}); out.append(item)
    budgets=query("SELECT c.name category,b.amount budget,NVL(SUM(t.amount),0) actual FROM budgets b JOIN categories c ON c.category_id=b.category_id LEFT JOIN transactions t ON t.user_id=b.user_id AND t.category_id=b.category_id AND t.transaction_type='EXPENSE' AND t.transaction_date>=b.month_start AND t.transaction_date<ADD_MONTHS(b.month_start,1) WHERE b.user_id=:u AND b.month_start=TO_DATE(:m,'YYYY-MM-DD') GROUP BY c.name,b.amount",{'u':user_id,'m':current})
    for b in budgets:
        if float(b['actual'])>=float(b['budget'])*.8:
            exceeded=float(b['actual'])>=float(b['budget']); title=f"{b['category']} budget {'exceeded' if exceeded else 'nearly reached'}"
            exists=query("SELECT recommendation_id FROM recommendations WHERE user_id=:u AND title=:t AND created_at>=TRUNC(SYSDATE,'MM') FETCH FIRST 1 ROWS ONLY",{'u':user_id,'t':title},one=True)
            if not exists:
                execute("INSERT INTO recommendations(user_id,title,explanation,supporting_data,comparison_period,suggested_action,priority) VALUES(:u,:t,:e,:d,:p,:a,:l)",{'u':user_id,'t':title,'e':f"Recorded spending is ₹{float(b['actual']):,.2f} against a ₹{float(b['budget']):,.2f} monthly budget.",'d':'{"actual":%.2f,"budget":%.2f}'%(float(b['actual']),float(b['budget'])),'p':current[:7],'a':'Review remaining planned purchases in this category.','l':'HIGH' if exceeded else 'MEDIUM'})
    return out
