from werkzeug.security import generate_password_hash, check_password_hash
from app import create_app
import oracledb


def _client():
    app=create_app({"TESTING":True,"SECRET_KEY":"test-secret","WTF_CSRF_ENABLED":False})
    return app.test_client()


def test_registration_hashes_password_and_creates_starter_categories(monkeypatch):
    import app as app_module
    creates=[]
    def fake_query(sql,params,one=False):
        if 'account_exists' in sql: return None
        raise AssertionError(sql)
    def fake_create(email,password_hash,name,categories):
        creates.append((email,password_hash,name,categories))
        return 42
    monkeypatch.setattr(app_module,"query",fake_query)
    monkeypatch.setattr(app_module,"create_user_with_categories",fake_create)
    client=_client()
    response=client.post('/register',data={"name":"A Student","email":"student@example.test","password":"a-long-test-password"})
    assert response.status_code==302
    assert creates[0][0]=='student@example.test' and creates[0][2]=='A Student'
    assert check_password_hash(creates[0][1],'a-long-test-password')
    assert len(creates[0][3])==9
    with client.session_transaction() as session:
        assert session['user_id']==42 and session['display_name']=='A Student'


def test_registration_existing_email_gets_friendly_response_without_insert(monkeypatch):
    import app as app_module
    writes=[]
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:{"account_exists":1})
    monkeypatch.setattr(app_module,"execute",lambda sql,params:writes.append((sql,params)))
    client=_client()
    response=client.post('/register',data={"name":"A Student","email":"student@example.test","password":"a-long-test-password"})
    assert response.status_code==409
    assert b'An account with this email already exists' in response.data
    assert writes==[]


def test_registration_duplicate_race_is_classified_by_email_lookup(monkeypatch):
    import app as app_module
    query_calls=[]; writes=[]
    def fake_query(sql,params,one=False):
        query_calls.append(sql)
        if 'account_exists' in sql:
            return None if sum('account_exists' in q for q in query_calls)==1 else {"account_exists":1}
        return {"user_id":42}
    monkeypatch.setattr(app_module,"query",fake_query)
    def fake_create(email,password_hash,name,categories):
        writes.append((email,password_hash,name,categories))
        raise oracledb.IntegrityError('ORA-00001: unique constraint (FUTUREFINANCE.SYS_C008339) violated')
    monkeypatch.setattr(app_module,"create_user_with_categories",fake_create)
    client=_client()
    response=client.post('/register',data={"name":"A Student","email":"student@example.test","password":"a-long-test-password"})
    assert response.status_code==409
    assert b'An account with this email already exists' in response.data
    assert check_password_hash(writes[0][1],'a-long-test-password')


def test_registration_does_not_mislabel_other_unique_constraint(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module,"query",lambda sql,params,one=False:None)
    monkeypatch.setattr(app_module,"create_user_with_categories",lambda *args,**kwargs:(_ for _ in ()).throw(oracledb.IntegrityError('ORA-00001: unique constraint (FUTUREFINANCE.SYS_C008340) violated')))
    client=_client()
    response=client.post('/register',data={"name":"A Student","email":"new@example.test","password":"a-long-test-password"})
    assert response.status_code==503
    assert b'An account with this email already exists' not in response.data
    assert b'ORA-00001' not in response.data


def test_registration_database_error_returns_service_unavailable_without_session(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('Oracle unavailable')))
    client=_client()
    response=client.post('/register',data={"name":"A Student","email":"new@example.test","password":"a-long-test-password"})
    assert response.status_code==503
    with client.session_transaction() as session:
        assert 'user_id' not in session


def test_login_and_logout_manage_session(monkeypatch):
    import app as app_module
    hashed=generate_password_hash('a-long-test-password')
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:{"user_id":7,"email":"student@example.test","password_hash":hashed,"display_name":"Student"})
    client=_client()
    with client.session_transaction() as session:
        session['user_id']=99
    response=client.post('/login',data={"email":"Student@example.test","password":"a-long-test-password"})
    assert response.status_code==302 and response.location.endswith('/dashboard')
    with client.session_transaction() as session:
        assert session['user_id']==7 and session['display_name']=='Student'
    assert client.post('/logout').status_code==302
    assert client.get('/dashboard').status_code==302


def test_login_rejects_incorrect_password_without_creating_session(monkeypatch):
    import app as app_module
    hashed=generate_password_hash('correct-password')
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:{"user_id":7,"email":"student@example.test","password_hash":hashed,"display_name":"Student"})
    client=_client()
    response=client.post('/login',data={"email":"student@example.test","password":"wrong-password"})
    assert response.status_code==200
    assert b'Email or password is incorrect.' in response.data
    with client.session_transaction() as session:
        assert 'user_id' not in session


def test_login_unknown_email_uses_same_message_and_does_not_create_session(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:None)
    client=_client()
    response=client.post('/login',data={"email":"missing@example.test","password":"wrong-password"})
    assert response.status_code==200
    assert b'Email or password is incorrect.' in response.data
    with client.session_transaction() as session:
        assert 'user_id' not in session


def test_oracle_driver_error_returns_service_unavailable_not_internal_server_error(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:(_ for _ in ()).throw(oracledb.DatabaseError('ORA-00942: test failure')))
    client=_client()
    response=client.post('/login',data={"email":"student@example.test","password":"correct-password"})
    assert response.status_code==503
    assert b'Oracle could not complete this request.' in response.data
    assert b'ORA-00942' not in response.data


def test_transaction_post_checks_category_ownership_before_saving(monkeypatch):
    import app as app_module
    saved=[]
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:{"category_id":3,"category_type":"EXPENSE"})
    monkeypatch.setattr(app_module,"add_transaction",lambda uid,form:saved.append((uid,form.copy())))
    monkeypatch.setattr(app_module,"list_transactions",lambda *args,**kwargs:[])
    monkeypatch.setattr(app_module,"analyze_user_anomalies",lambda *args,**kwargs:{"available":False,"message":"not enough history","results":[]})
    client=_client()
    with client.session_transaction() as session:
        session['user_id']=5
    response=client.post('/transactions',data={"date":"2026-06-15","amount":"12.50","type":"EXPENSE","category_id":"3"})
    assert response.status_code==302 and len(saved)==1 and saved[0][0]==5
    saved.clear()
    response=client.post('/transactions',data={"date":"2026-06-15","amount":"NaN","type":"EXPENSE","category_id":"3"})
    assert response.status_code==200 and saved==[]


def test_dashboard_renders_charts_from_service_results(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module,"analyze_user_anomalies",lambda uid:{"available":False,"message":"Need more transactions","results":[]})
    monkeypatch.setattr(app_module,"monthly_spending_profiles",lambda uid:{"available":False,"message":"Need more months","months":[],"labels":[],"descriptions":[]})
    monkeypatch.setattr(app_module,"dashboard_recommendations",lambda uid:[])
    monkeypatch.setattr(app_module,"summary",lambda *args:{"income":1000,"expense":400,"net":600,"savings_rate":60})
    monkeypatch.setattr(app_module,"category_spend",lambda *args:[])
    monkeypatch.setattr(app_module,"list_transactions",lambda *args,**kwargs:[])
    monkeypatch.setattr(app_module,"daily_spending",lambda uid:[{"date":"2026-06-01","expense":10}])
    monkeypatch.setattr(app_module,"weekly_spending",lambda uid:[{"week_start":"2026-06-01","expense":50}])
    monkeypatch.setattr(app_module,"monthly_trends",lambda uid:[{"month":"2026-06","income":100,"expense":50}])
    client=_client()
    with client.session_transaction() as session:
        session['user_id']=5; session['display_name']='Student'
    response=client.get('/dashboard')
    assert response.status_code==200
    assert b'categoryChart' not in response.data and b'dailySpendChart' not in response.data
    assert b'Income' in response.data and b'Expenses' in response.data and b'Net cash flow' in response.data
    assert b'No transactions recorded for this period yet' in response.data


def test_spending_section_preserves_budgets_categories_and_trend_features(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module,"summary",lambda *args:{"income":100,"expense":40,"net":60,"savings_rate":60})
    monkeypatch.setattr(app_module,"category_spend",lambda *args:[{"category":"Food","amount":40}])
    monkeypatch.setattr(app_module,"budget_status",lambda *args:[{"category":"Food","actual":40,"budget":100,"remaining":60,"utilization":40}])
    monkeypatch.setattr(app_module,"daily_spending",lambda *args:[{"date":"2026-01-01","expense":10}])
    monkeypatch.setattr(app_module,"weekly_spending",lambda *args:[{"week_start":"2026-01-01","expense":10}])
    monkeypatch.setattr(app_module,"monthly_trends",lambda *args:[{"month":"2026-01","income":100,"expense":40}])
    monkeypatch.setattr(app_module,"monthly_spending_profiles",lambda *args:{"available":False,"message":"Need more monthly history","months":[]})
    client=_client()
    with client.session_transaction() as session: session['user_id']=5
    response=client.get('/spending')
    assert response.status_code==200 and b'Manage budgets' in response.data and b'Manage categories' in response.data
    assert b'monthlyTrendChart' in response.data and b'dailySpendChart' in response.data and b'weeklySpendChart' in response.data


def test_planning_page_keeps_simulator_and_reports_reachable():
    import re
    client=_client()
    with client.session_transaction() as session: session['user_id']=5
    response=client.get('/planning')
    assert response.status_code==200 and b'Open simulator' in response.data and b'Open reports' in response.data
    assert b'href="/simulator"' in response.data and b'href="/reports"' in response.data
    nav=re.search(rb'<div class="navbar-nav ms-lg-3 me-auto">(.*?)</div>',response.data,re.S).group(1)
    assert [label.decode() for label in re.findall(rb'>(Dashboard|Transactions|Spending|Goals|Insights|Planning)</a>',nav)]==['Dashboard','Transactions','Spending','Goals','Insights','Planning']


def test_csv_import_requires_preview_and_skips_invalid_rows(monkeypatch):
    import io
    import app as app_module
    categories=[{"category_id":1,"name":"Food","category_type":"EXPENSE"},{"category_id":2,"name":"Salary","category_type":"INCOME"},{"category_id":3,"name":"Other","category_type":"BOTH"}]
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:categories)
    monkeypatch.setattr(app_module,"find_existing_import_hashes",lambda uid,digests:set())
    monkeypatch.setattr(app_module,"list_transactions",lambda *args,**kwargs:[])
    monkeypatch.setattr(app_module,"analyze_user_anomalies",lambda *args,**kwargs:{"available":False,"message":"not enough history","results":[]})
    writes=[]
    monkeypatch.setattr(app_module,"import_transactions_batch",lambda uid,rows:writes.append((uid,rows)) or (len(rows),0))
    app=app_module.create_app({"TESTING":True,"SECRET_KEY":"test-secret","WTF_CSRF_ENABLED":False})
    client=app.test_client()
    with client.session_transaction() as session: session['user_id']=12
    response=client.post('/transactions/import',data={"file":(io.BytesIO(b"posted date,transaction amount,direction,payee,memo\n2026-01-01,12.50,DEBIT,Local Cafe,Lunch\nbad-date,7,DEBIT,Local Cafe,Oops\n"),"bank.csv")},content_type='multipart/form-data')
    assert response.status_code==200 and b'Map bank statement columns' in response.data
    assert writes==[]
    token=next(iter(app.extensions['csv_imports']))
    response=client.post('/transactions/import/preview',data={"token":token,"map_date":"posted date","map_amount":"transaction amount","map_type":"direction","map_category":"","map_merchant_name":"payee","map_description":"memo","map_payment_method":"","default_type":"EXPENSE"})
    assert response.status_code==200 and b'1' in response.data and b'Invalid date' in response.data
    assert b'Food' in response.data and writes==[]
    response=client.post('/transactions/import/confirm',data={"token":token})
    assert response.status_code==302 and len(writes)==1
    assert writes[0][0]==12 and len(writes[0][1])==1
    assert writes[0][1][0]['category_id']==1
    assert b'Import complete: 1 imported, 0 duplicate rows skipped, 1 invalid rows not imported.' in client.get('/transactions').data


def test_csv_import_preview_is_bound_to_owning_user(monkeypatch):
    import io
    import app as app_module
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:[])
    app=app_module.create_app({"TESTING":True,"SECRET_KEY":"test-secret","WTF_CSRF_ENABLED":False})
    client=app.test_client()
    with client.session_transaction() as session: session['user_id']=12
    response=client.post('/transactions/import',data={"file":(io.BytesIO(b"date,amount\n2026-01-01,1\n"),"bank.csv")},content_type='multipart/form-data')
    assert response.status_code==200
    token=next(iter(app.extensions['csv_imports']))
    with client.session_transaction() as session: session['user_id']=13
    response=client.post('/transactions/import/preview',data={"token":token,"map_date":"date","map_amount":"amount"})
    assert response.status_code==302 and token not in app.extensions['csv_imports']


def test_csv_preview_marks_previously_imported_transaction_as_duplicate(monkeypatch):
    import io
    import app as app_module
    from services.transaction_service import import_hash
    categories=[{"category_id":1,"name":"Food","category_type":"EXPENSE"}]
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:categories)
    expected=import_hash({'date':'2026-01-01','amount':10.0,'type':'EXPENSE','category':'Food','description':'','merchant_name':''})
    monkeypatch.setattr(app_module,"find_existing_import_hashes",lambda uid,digests:{expected})
    app=app_module.create_app({"TESTING":True,"SECRET_KEY":"test-secret","WTF_CSRF_ENABLED":False})
    client=app.test_client()
    with client.session_transaction() as session: session['user_id']=12
    response=client.post('/transactions/import',data={"file":(io.BytesIO(b"date,amount,type,category\n2026-01-01,10,EXPENSE,Food\n"),"bank.csv")},content_type='multipart/form-data')
    token=next(iter(app.extensions['csv_imports']))
    response=client.post('/transactions/import/preview',data={"token":token,"map_date":"date","map_amount":"amount","map_type":"type","map_category":"category"})
    assert response.status_code==200
    assert b'Duplicates detected' in response.data and b'skipped' in response.data


def test_windows_encoded_csv_survives_upload_and_preview(monkeypatch):
    import io
    import app as app_module
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:[{"category_id":1,"name":"Food","category_type":"EXPENSE"}])
    monkeypatch.setattr(app_module,"find_existing_import_hashes",lambda uid,digests:set())
    app=app_module.create_app({"TESTING":True,"SECRET_KEY":"test-secret","WTF_CSRF_ENABLED":False})
    client=app.test_client()
    with client.session_transaction() as session: session['user_id']=12
    csv_bytes='date,amount,type,merchant_name,description\n2026-01-01,10,EXPENSE,Café œ Shop €,Café lunch – crème\n'.encode('cp1252')
    response=client.post('/transactions/import',data={"file":(io.BytesIO(csv_bytes),"statement.csv")},content_type='multipart/form-data')
    assert response.status_code==200 and b'Map bank statement columns' in response.data
    token=next(iter(app.extensions['csv_imports']))
    response=client.post('/transactions/import/preview',data={"token":token,"map_date":"date","map_amount":"amount","map_type":"type","map_category":"","map_merchant_name":"merchant_name","map_description":"description"})
    assert response.status_code==200
    assert 'Café œ Shop €'.encode('utf-8') in response.data
    assert 'Café lunch – crème'.encode('utf-8') in response.data


def test_sms_import_previews_edits_and_saves_only_confirmed_transaction(monkeypatch):
    import app as app_module
    categories=[{'category_id':1,'name':'Food','category_type':'EXPENSE'},{'category_id':2,'name':'Salary','category_type':'INCOME'}]
    monkeypatch.setattr(app_module,'query',lambda *args,**kwargs:categories)
    monkeypatch.setattr(app_module,'find_existing_import_hashes',lambda uid,digests:set())
    writes=[]
    monkeypatch.setattr(app_module,'import_transactions_batch',lambda uid,rows:writes.append((uid,rows)) or (len(rows),0))
    app=app_module.create_app({'TESTING':True,'SECRET_KEY':'test-secret','WTF_CSRF_ENABLED':False})
    client=app.test_client()
    with client.session_transaction() as session: session['user_id']=12
    messages='Rs 100 debited at Cafe on 28-10-2026\n\nYour OTP for UPI transaction is 123456. Do not share.'
    response=client.post('/transactions/import/sms',data={'messages':messages})
    assert response.status_code==200 and b'Review pasted messages' in response.data
    assert b'Cafe' in response.data and b'OTP or verification messages are not imported' in response.data
    assert writes==[]
    token=next(iter(app.extensions['sms_imports']))
    response=client.post('/transactions/import/sms/confirm',data={'token':token,'date_0':'2026-10-28','amount_0':'110','type_0':'EXPENSE','category_0':'1','merchant_0':'Updated Cafe','description_0':''})
    assert response.status_code==302 and len(writes)==1
    assert writes[0][0]==12 and len(writes[0][1])==1
    assert writes[0][1][0]['amount']==110 and writes[0][1][0]['merchant_name']=='Updated Cafe'
    assert writes[0][1][0]['payment_method']=='SMS'


def test_sms_import_preview_flags_same_message_already_imported(monkeypatch):
    import app as app_module
    from services.transaction_service import sms_import_hash
    message='Rs 100 debited at Cafe on 28-10-2026'
    digest=sms_import_hash(message)
    monkeypatch.setattr(app_module,'query',lambda *args,**kwargs:[{'category_id':1,'name':'Food','category_type':'EXPENSE'}])
    monkeypatch.setattr(app_module,'find_existing_import_hashes',lambda uid,digests:{digest})
    app=app_module.create_app({'TESTING':True,'SECRET_KEY':'test-secret','WTF_CSRF_ENABLED':False})
    client=app.test_client()
    with client.session_transaction() as session: session['user_id']=12
    response=client.post('/transactions/import/sms',data={'messages':message})
    assert response.status_code==200 and b'Duplicate' in response.data and b'skipped' in response.data


def test_reported_sms_samples_confirm_as_expense_and_income(monkeypatch):
    import app as app_module
    categories=[{'category_id':1,'name':'Food','category_type':'EXPENSE'},{'category_id':2,'name':'Salary','category_type':'INCOME'}]
    monkeypatch.setattr(app_module,'query',lambda *args,**kwargs:categories)
    monkeypatch.setattr(app_module,'find_existing_import_hashes',lambda uid,digests:set())
    saved=[]
    monkeypatch.setattr(app_module,'import_transactions_batch',lambda uid,rows:saved.extend(rows) or (len(rows),0))
    app=app_module.create_app({'TESTING':True,'SECRET_KEY':'test-secret','WTF_CSRF_ENABLED':False})
    client=app.test_client()
    with client.session_transaction() as session: session['user_id']=12
    messages=('Dear Customer, your account has been debited by INR 250.00 on 28-10-2026 for a UPI payment to PVR Cinemas. UPI Ref No: 123456789012.\n\n'
              'Dear Customer, your account has been credited with INR 5000.00 on 27-10-2026. Transaction details: Salary Credit. Ref No: 987654321012.')
    preview=client.post('/transactions/import/sms',data={'messages':messages})
    assert preview.status_code==200 and b'PVR Cinemas' in preview.data and b'Salary Credit' in preview.data
    token=next(iter(app.extensions['sms_imports']))
    response=client.post('/transactions/import/sms/confirm',data={'token':token,'date_0':'2026-10-28','amount_0':'250','type_0':'EXPENSE','category_0':'1','merchant_0':'PVR Cinemas','description_0':'PVR Cinemas','date_1':'2026-10-27','amount_1':'5000','type_1':'INCOME','category_1':'2','merchant_1':'','description_1':'Salary Credit'})
    assert response.status_code==302 and len(saved)==2
    assert [(row['type'],row['amount'],row['date'],row['description']) for row in saved]==[
        ('EXPENSE',250.0,'2026-10-28','PVR Cinemas'),('INCOME',5000.0,'2026-10-27','Salary Credit')]


def test_transaction_csv_export_escapes_formula_text(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module,"list_transactions",lambda *args,**kwargs:[{"transaction_date":"2026-01-01","amount":10,"transaction_type":"EXPENSE","category":"Food","description":"=HYPERLINK(\"x\")","payment_method":"@SUM(A1)","merchant_name":"Normal"}])
    client=_client()
    with client.session_transaction() as session: session['user_id']=12
    response=client.get('/transactions/export')
    assert response.status_code==200
    assert b"'=HYPERLINK" in response.data and b"'@SUM" in response.data


def test_report_export_includes_period_totals_and_escapes_category_text(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module,"summary",lambda *args:{"income":100,"expense":40,"net":60,"savings_rate":60})
    monkeypatch.setattr(app_module,"category_spend",lambda *args:[{"category":"=1+1","amount":40}])
    client=_client()
    with client.session_transaction() as session: session['user_id']=12
    response=client.get('/reports/export?start=2026-01-01&end=2026-01-31')
    assert response.status_code==200
    assert b"2026-01-01" in response.data and b"Savings rate (%)" in response.data
    assert b"'=1+1" in response.data


def test_edit_transaction_updates_only_for_own_category(monkeypatch):
    import app as app_module
    from datetime import datetime
    calls=[]
    def fake_query(sql,params,one=False):
        if 'FROM transactions' in sql:
            return {"transaction_id":9,"transaction_date":datetime(2026,6,1),"amount":10,"transaction_type":"EXPENSE","category_id":3,"description":None,"payment_method":None,"merchant_name":None}
        return [{"category_id":3,"name":"Food","category_type":"EXPENSE"}]
    monkeypatch.setattr(app_module,"query",fake_query)
    monkeypatch.setattr(app_module,"execute",lambda sql,params:calls.append((sql,params)))
    client=_client()
    with client.session_transaction() as session:
        session['user_id']=5
    response=client.post('/transactions/9/edit',data={"date":"2026-06-02","amount":"20","type":"EXPENSE","category_id":"3"})
    assert response.status_code==302 and len(calls)==1
    assert calls[0][1]['u']==5 and calls[0][1]['id']==9


def test_goal_edit_and_delete_are_user_scoped(monkeypatch):
    import app as app_module
    from datetime import datetime
    writes=[]
    monkeypatch.setattr(app_module,"query",lambda *args,**kwargs:{"goal_id":12,"name":"Trip","target_amount":1000,"starting_amount":100,"target_date":datetime(2027,1,1)})
    monkeypatch.setattr(app_module,"execute",lambda sql,params:writes.append((sql,params)))
    client=_client()
    with client.session_transaction() as session: session['user_id']=5
    response=client.post('/goals/12/edit',data={"name":"Updated trip","target_amount":"1200","starting_amount":"100","target_date":"2027-02-01"})
    assert response.status_code==302 and writes[0][1]['u']==5 and writes[0][1]['g']==12
    response=client.post('/goals/12/delete')
    assert response.status_code==302 and writes[1][1]=={'g':12,'u':5}
