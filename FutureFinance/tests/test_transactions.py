from services.analytics_service import summary
from services import transaction_service
from services import analytics_service

def test_financial_summary_calculation(monkeypatch):
    calls=[]
    def fake_query(sql,params):
        calls.append((sql,params))
        return [{'transaction_type':'INCOME','total':1000},{'transaction_type':'EXPENSE','total':650}]
    monkeypatch.setattr('services.analytics_service.query',fake_query)
    result=summary(3,'2026-01-01','2026-01-31')
    assert result=={'income':1000.0,'expense':650.0,'net':350.0,'savings_rate':35.0}
    assert ':p_user_id' in calls[0][0] and ':uid' not in calls[0][0]
    assert calls[0][1]['p_user_id']==3

def test_transaction_insert_uses_non_reserved_description_bind(monkeypatch):
    calls=[]
    monkeypatch.setattr(transaction_service,'execute',lambda sql,params:calls.append((sql,params)))
    transaction_service.add_transaction(4,{'date':'2026-06-01','amount':'100','type':'EXPENSE','category_id':'2'})
    sql,params=calls[0]
    import re
    assert ':descr' in sql and not re.search(r':desc\b',sql)
    assert ':p_user_id' in sql and ':uid' not in sql
    assert params['p_user_id']==4 and params['descr'] is None

def test_filter_query_binds_transaction_type_with_safe_name(monkeypatch):
    calls=[]
    monkeypatch.setattr(transaction_service,'query',lambda sql,params:calls.append((sql,params)) or [])
    transaction_service.list_transactions(4,tx_type='EXPENSE')
    sql,params=calls[0]
    assert ':p_tx_type' in sql and params['p_tx_type']=='EXPENSE'
    assert ':p_user_id' in sql and ':uid' not in sql

def test_daily_spending_fills_days_without_transactions(monkeypatch):
    from datetime import date, datetime
    today=date.today()
    monkeypatch.setattr(analytics_service,'query',lambda sql,params:[{'spending_day':datetime.combine(today,datetime.min.time()),'expense':25}])
    result=analytics_service.daily_spending(4,days=3)
    assert len(result)==3 and result[-1]['expense']==25 and result[0]['expense']==0

def test_weekly_spending_uses_bounded_window_and_returns_amounts(monkeypatch):
    from datetime import date, timedelta
    monday=date.today()-timedelta(days=date.today().weekday())
    monkeypatch.setattr(analytics_service,'query',lambda sql,params:[{'week_start':monday,'expense':80}])
    result=analytics_service.weekly_spending(4,weeks=2)
    assert len(result)==1 and result[0]['expense']==80

def test_monthly_trends_fills_missing_months_and_uses_safe_user_bind(monkeypatch):
    calls=[]
    monkeypatch.setattr(analytics_service,'query',lambda sql,params:calls.append((sql,params)) or [])
    result=analytics_service.monthly_trends(14,months=3)
    assert len(result)==3 and all(row['income']==0 and row['expense']==0 for row in result)
    sql,params=calls[0]
    assert ':p_user_id' in sql and ':uid' not in sql and params['p_user_id']==14


def test_import_batch_skips_existing_and_repeated_hashes_atomically(monkeypatch):
    calls=[]
    monkeypatch.setattr(transaction_service,'query',lambda sql,params:[{'import_hash':'already'}])
    monkeypatch.setattr(transaction_service,'execute_many',lambda sql,rows:calls.append((sql,rows)) or len(rows))
    base={'date':'2026-01-01','amount':10.0,'type':'EXPENSE','category_id':2,'category':'Food','digest':'new'}
    rows=[{**base,'digest':'already'},{**base},{**base}]
    inserted,skipped=transaction_service.import_transactions_batch(5,rows)
    assert (inserted,skipped)==(1,2)
    assert len(calls)==1 and len(calls[0][1])==1 and calls[0][1][0]['p_user_id']==5


def test_category_suggestion_uses_transparent_keywords_and_user_categories():
    from services.transaction_service import suggest_category
    categories=[{'category_id':4,'name':'Dining','category_type':'EXPENSE'},{'category_id':5,'name':'Other','category_type':'BOTH'}]
    result,reason=suggest_category({'merchant_name':'Cafe One','description':'Lunch','type':'EXPENSE'},categories)
    assert result['category_id']==4 and 'keywords' in reason
    result,reason=suggest_category({'merchant_name':'Unknown Shop','type':'EXPENSE'},categories)
    assert result['category_id']==5 and 'Please review' in reason


def test_budget_status_calculates_remaining_and_overrun(monkeypatch):
    monkeypatch.setattr(analytics_service,'query',lambda *args,**kwargs:[
        {'category':'Food','budget':100,'actual':80},
        {'category':'Transport','budget':50,'actual':75},
    ])
    rows=analytics_service.budget_status(9,'2026-01-01')
    assert rows[0]['remaining']==20 and rows[0]['utilization']==80
    assert rows[1]['remaining']==-25 and rows[1]['utilization']==150


def test_csv_decoder_accepts_utf8_utf8_bom_and_windows_encodings():
    from services.transaction_service import decode_csv_bytes
    utf8='date,amount,merchant_name\n2026-01-01,10,Café\n'.encode('utf-8')
    utf8_bom=b'\xef\xbb\xbf'+utf8
    windows='date,amount,merchant_name\n2026-01-01,10,Café œ €\n'.encode('cp1252')
    expected='date,amount,merchant_name\n2026-01-01,10,Café\n'
    assert decode_csv_bytes(utf8)==expected
    assert decode_csv_bytes(utf8_bom)==expected
    assert decode_csv_bytes(windows)=='date,amount,merchant_name\n2026-01-01,10,Café œ €\n'
    assert decode_csv_bytes('date,amount,merchant_name\n2026-01-01,10,Café\n'.encode('latin-1'))==expected


def test_csv_decoder_rejects_binary_and_utf16_with_actionable_error():
    from services.transaction_service import decode_csv_bytes
    import pytest
    with pytest.raises(ValueError,match='unsupported control characters'):
        decode_csv_bytes(b'date,amount\n2026-01-01,10\x00\n')
    with pytest.raises(ValueError,match='UTF-16/UTF-32'):
        decode_csv_bytes('date,amount\n'.encode('utf-16'))
