from services import insight_service

def test_anomaly_orchestration_persists_each_eligible_transaction(monkeypatch):
    rows=[{'transaction_id':i,'transaction_date':f'2026-01-{i+1:02d}','amount':100 if i<9 else 15000,'transaction_type':'EXPENSE','category':'Food'} for i in range(10)]
    saved=[]
    def fake_query(sql,params,one=False):
        if 'ORDER BY t.transaction_date,t.transaction_id' in sql: return rows
        if 'FROM anomaly_results' in sql: return [{'transaction_id':9,'transaction_date':'2026-01-10','amount':15000,'category':'Food','score':.8,'explanation':'unusual','compared_amount':100}]
        raise AssertionError(sql)
    monkeypatch.setattr(insight_service,'query',fake_query)
    monkeypatch.setattr(insight_service,'execute',lambda sql,params:saved.append(params))
    result=insight_service.analyze_user_anomalies(3)
    assert result['available'] and len(saved)==10 and result['results'][0]['transaction_id']==9

def test_month_profile_clustering_is_persisted_from_observed_rows(monkeypatch):
    rows=[]
    for i,month in enumerate(['2026-01','2026-02','2026-03','2026-04','2026-05','2026-06']):
        high=i%2==0
        rows.extend([
            {'month_key':month,'category':'Food','is_essential':'Y','expense':10000 if high else 2500,'income':0},
            {'month_key':month,'category':'Salary','is_essential':'N','expense':0,'income':50000},
            {'month_key':month,'category':'Shopping','is_essential':'N','expense':8000 if high else 1200,'income':0},
        ])
    saved=[]
    monkeypatch.setattr(insight_service,'query',lambda sql,params:rows)
    monkeypatch.setattr(insight_service,'execute',lambda sql,params:saved.append(params))
    result=insight_service.monthly_spending_profiles(3)
    assert result['available'] and result['cluster_count']>=2 and len(saved)==6
    assert all('higher total spending' in x.lower() or 'lower total spending' in x.lower() for x in result['descriptions'])
