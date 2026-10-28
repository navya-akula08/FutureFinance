"""Database orchestration for user-scoped anomaly and monthly cluster analyses."""
import json
from datetime import date
from database.db import query, execute
from services.anomaly_detection import detect_anomalies
from services.spending_clustering import cluster_months

def analyze_user_anomalies(user_id):
    records=query("SELECT t.transaction_id,t.transaction_date,t.amount,t.transaction_type,c.name category FROM transactions t JOIN categories c ON c.category_id=t.category_id WHERE t.user_id=:u AND t.transaction_type='EXPENSE' ORDER BY t.transaction_date,t.transaction_id",{'u':user_id})
    if len(records)<8:
        return {'available':False,'message':f'Anomaly analysis needs at least 8 expense transactions; found {len(records)}.','results':[]}
    scored=detect_anomalies(records)
    conn_results=[]
    # Merge keeps one current analysis record per transaction.
    for row in scored:
        execute("MERGE INTO anomaly_results a USING (SELECT :u user_id,:t transaction_id FROM dual) s ON (a.transaction_id=s.transaction_id) WHEN MATCHED THEN UPDATE SET a.score=:score,a.is_unusual=:flag,a.explanation=:explanation,a.compared_amount=:median,a.analyzed_at=SYSTIMESTAMP WHEN NOT MATCHED THEN INSERT(user_id,transaction_id,score,is_unusual,explanation,compared_amount) VALUES(:u,:t,:score,:flag,:explanation,:median)",{'u':user_id,'t':row['transaction_id'],'score':row['score'],'flag':'Y' if row['is_unusual'] else 'N','explanation':row['explanation'],'median':row['category_median']})
    found=query("SELECT t.transaction_id,t.transaction_date,t.amount,c.name category,a.score,a.explanation,a.compared_amount FROM anomaly_results a JOIN transactions t ON t.transaction_id=a.transaction_id JOIN categories c ON c.category_id=t.category_id WHERE a.user_id=:u AND a.is_unusual='Y' ORDER BY a.analyzed_at DESC,t.transaction_date DESC FETCH FIRST 20 ROWS ONLY",{'u':user_id})
    return {'available':True,'message':None,'results':found}

def monthly_spending_profiles(user_id):
    data=query("SELECT TO_CHAR(TRUNC(t.transaction_date,'MM'),'YYYY-MM') month_key,c.name category,c.is_essential,SUM(CASE WHEN t.transaction_type='EXPENSE' THEN t.amount ELSE 0 END) expense,SUM(CASE WHEN t.transaction_type='INCOME' THEN t.amount ELSE 0 END) income FROM transactions t JOIN categories c ON c.category_id=t.category_id WHERE t.user_id=:u GROUP BY TRUNC(t.transaction_date,'MM'),c.name,c.is_essential ORDER BY month_key",{'u':user_id})
    months=sorted({r['month_key'] for r in data}); cats=sorted({r['category'] for r in data if any(x['category']==r['category'] and x['expense'] for x in data)})
    if len(months)<3: return {'available':False,'message':f'Monthly pattern analysis needs 3 months of transactions; found {len(months)}.','months':[],'labels':[],'descriptions':[]}
    by={(r['month_key'],r['category']):r for r in data}; profiles=[]
    for month in months:
        expenses=[float(by.get((month,c),{}).get('expense') or 0) for c in cats]
        income=sum(float(by.get((month,c),{}).get('income') or 0) for c in cats); total=sum(expenses)
        essential=sum(float(by[(month,c)]['expense']) for c in cats if (month,c) in by and by[(month,c)]['is_essential']=='Y')
        discretionary=max(total-essential,0)
        profiles.append([total,*expenses,essential/total if total else 0,discretionary/total if total else 0,(income-total)/income if income else 0])
    result=cluster_months(profiles)
    if not result['available']: return {**result,'months':months,'labels':[],'descriptions':[]}
    descriptions={}
    # Describe the actual cluster center relative to the dataset median profile.
    matrix=result['centers']; totals=[p[0] for p in profiles]; median_total=sorted(totals)[len(totals)//2]
    for label in set(result['labels']):
        center=matrix[label]; parts=[]
        parts.append('higher total spending than the dataset median' if center[0]>median_total else 'lower total spending than the dataset median')
        if len(cats):
            shares=[center[1+i]/center[0] if center[0]>0 else 0 for i in range(len(cats))]
            if shares:
                top=max(range(len(shares)),key=shares.__getitem__)
                parts.append(f"largest category share in {cats[top]}")
        if center[-3] > .7: parts.append('essential categories represent most recorded spending')
        elif center[-2] > .5: parts.append('discretionary categories represent a larger spending share')
        descriptions[int(label)]='; '.join(parts).capitalize()+'.'
    for month,label,profile in zip(months,result['labels'],profiles):
        execute("MERGE INTO spending_clusters d USING (SELECT :u user_id,TO_DATE(:m||'-01','YYYY-MM-DD') month_start FROM dual) s ON (d.user_id=s.user_id AND d.month_start=s.month_start) WHEN MATCHED THEN UPDATE SET d.cluster_label=:l,d.description=:x,d.profile_json=:j,d.created_at=SYSTIMESTAMP WHEN NOT MATCHED THEN INSERT(user_id,month_start,cluster_label,description,profile_json) VALUES(:u,s.month_start,:l,:x,:j)",{'u':user_id,'m':month,'l':int(label),'x':descriptions[int(label)],'j':json.dumps({'features':profile,'silhouette':result['silhouette'],'cluster_count':result['k']})})
    return {'available':True,'months':months,'labels':result['labels'],'descriptions':[descriptions[int(x)] for x in result['labels']],'cluster_count':result['k'],'silhouette':result['silhouette'],'centers':result['centers']}

def dashboard_recommendations(user_id, year=None, month=None):
    """Refresh observations once per user/current month and return persisted rows."""
    from services.recommendation_engine import generate_monthly_recommendations
    today=date.today(); year=year or today.year; month=month or today.month
    generate_monthly_recommendations(user_id,year,month)
    return query("SELECT recommendation_id,title,explanation,supporting_data,comparison_period,suggested_action,priority,created_at FROM recommendations WHERE user_id=:u AND created_at>=TRUNC(SYSDATE,'MM') ORDER BY CASE priority WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,created_at DESC FETCH FIRST 12 ROWS ONLY",{'u':user_id})
