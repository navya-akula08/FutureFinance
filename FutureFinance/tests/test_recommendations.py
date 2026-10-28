from services import recommendation_engine as engine

def test_month_over_month_recommendation_uses_actual_values(monkeypatch):
    current=[{'category':'Food','amount':1240.0}]
    previous=[{'category':'Food','amount':1000.0}]
    budgets=[]; calls=[]
    def fake_query(sql,params,one=False):
        if 'GROUP BY c.name' in sql and 'b.amount' not in sql: return current if "ADD_MONTHS(TO_DATE(:m,'YYYY-MM-DD'),-1)" not in sql else previous
        if 'FROM budgets' in sql: return budgets
        if 'SELECT recommendation_id' in sql: return None
        raise AssertionError(sql)
    monkeypatch.setattr(engine,'query',fake_query)
    monkeypatch.setattr(engine,'execute',lambda sql,params:calls.append(params))
    result=engine.generate_monthly_recommendations(7,2026,2)
    assert result[0]['explanation'].find('24.0%')>=0
    assert calls and calls[0]['d']=='{"current":1240.00,"previous":1000.00}'

def test_budget_recommendation_uses_budget_utilization(monkeypatch):
    calls=[]
    def fake_query(sql,params,one=False):
        if 'FROM budgets' in sql: return [{'category':'Transport','budget':100,'actual':85}]
        if 'SELECT recommendation_id' in sql: return None
        if 'GROUP BY c.name' in sql and "ADD_MONTHS(TO_DATE(:m,'YYYY-MM-DD'),-1)" in sql: return [{'category':'Transport','amount':5}]
        if 'GROUP BY c.name' in sql: return [{'category':'Transport','amount':5}]
        raise AssertionError(sql)
    monkeypatch.setattr(engine,'query',fake_query)
    monkeypatch.setattr(engine,'execute',lambda sql,params:calls.append(params))
    engine.generate_monthly_recommendations(7,2026,2)
    assert any('nearly reached' in c['t'] for c in calls)
