from datetime import date, datetime, timedelta, timezone
import math
import secrets
import oracledb
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config
from database.db import query, execute, create_user_with_categories
from services.transaction_service import list_transactions, add_transaction, import_hash, sms_import_hash, import_transactions_batch, find_existing_import_hashes, suggest_category, decode_csv_bytes
from services.analytics_service import summary, category_spend, budget_status, monthly_cashflows, monthly_trends, daily_spending, weekly_spending
from services.monte_carlo import simulate, simulate_scenarios
from services.recommendation_engine import generate_monthly_recommendations
from services.insight_service import analyze_user_anomalies, monthly_spending_profiles, dashboard_recommendations
from services.recurring_expenses import detect_recurring_expenses
from services.sms_import import parse_bank_sms, split_sms_messages

def create_app(test_config=None):
    app=Flask(__name__); app.config.from_object(Config)
    if test_config: app.config.update(test_config)
    app.config.setdefault('MAX_CONTENT_LENGTH', 2 * 1024 * 1024)
    app.extensions['csv_imports'] = {}
    app.extensions['sms_imports'] = {}
    CSRFProtect(app)

    @app.template_filter('inr')
    def inr(value): return f"₹{float(value or 0):,.2f}"

    def login_required(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            if not session.get('user_id'):
                flash('Please sign in to continue.','warning'); return redirect(url_for('login'))
            return fn(*args,**kwargs)
        return wrapped

    @app.errorhandler(RuntimeError)
    def db_error(exc):
        return render_template('error.html',message=str(exc)),503

    @app.errorhandler(oracledb.Error)
    def oracle_error(exc):
        # Driver-level SQL errors must not become an unhandled HTTP 500 or leak
        # database details into the page. Keep the full exception in server logs.
        app.logger.exception('Oracle operation failed while handling a request')
        return render_template('error.html',message='Oracle could not complete this request. Check the application log for details.'),503

    @app.route('/')
    def index(): return redirect(url_for('dashboard' if session.get('user_id') else 'login'))

    @app.route('/register',methods=['GET','POST'])
    def register():
        if request.method=='POST':
            name=request.form.get('name','').strip(); email=request.form.get('email','').strip().lower(); password=request.form.get('password','')
            if not name or len(name)>100 or '@' not in email or len(password)<10:
                flash('Enter a name, valid email, and password with at least 10 characters.','danger')
            else:
                existing=query('SELECT 1 AS account_exists FROM users WHERE email=:e',{'e':email},one=True)
                if existing:
                    flash('An account with this email already exists. Please sign in instead.','warning')
                    return render_template('register.html'),409
                try:
                    user_id=create_user_with_categories(email,generate_password_hash(password),name,[('Salary','INCOME','N'),('Food','EXPENSE','Y'),('Housing','EXPENSE','Y'),('Transport','EXPENSE','Y'),('Shopping','EXPENSE','N'),('Utilities','EXPENSE','Y'),('Health','EXPENSE','Y'),('Entertainment','EXPENSE','N'),('Other','BOTH','N')])
                except oracledb.IntegrityError as exc:
                    # The pre-check gives a friendly response in the usual case.
                    # This follow-up handles a concurrent registration racing it.
                    # Only classify ORA-00001 as a duplicate account if this email
                    # now exists; unrelated unique-constraint failures must surface.
                    error=exc.args[0] if exc.args else None
                    is_unique=(getattr(error,'code',None)==1 or 'ORA-00001' in str(exc))
                    if not is_unique:
                        raise
                    existing=query('SELECT 1 AS account_exists FROM users WHERE email=:e',{'e':email},one=True)
                    if not existing:
                        raise
                    flash('An account with this email already exists. Please sign in instead.','warning')
                    return render_template('register.html'),409
                session['user_id']=user_id; session['display_name']=name
                return redirect(url_for('dashboard'))
        return render_template('register.html')

    @app.route('/login',methods=['GET','POST'])
    def login():
        if request.method=='POST':
            user=query('SELECT user_id,email,password_hash,display_name FROM users WHERE email=:e',{'e':request.form.get('email','').strip().lower()},one=True)
            if user and check_password_hash(user['password_hash'],request.form.get('password','')):
                session.clear(); session['user_id']=user['user_id']; session['display_name']=user['display_name']; return redirect(url_for('dashboard'))
            flash('Email or password is incorrect.','danger')
        return render_template('login.html')

    @app.route('/logout',methods=['POST'])
    def logout(): session.clear(); return redirect(url_for('login'))

    @app.route('/dashboard')
    @login_required
    def dashboard():
        start=request.args.get('start',date.today().replace(day=1).isoformat()); end=request.args.get('end',date.today().isoformat()); uid=session['user_id']
        try:
            anomalies=analyze_user_anomalies(uid); recommendations=dashboard_recommendations(uid)
        except RuntimeError: raise
        except Exception: app.logger.exception('Dashboard analysis failed'); flash('Some insights could not be refreshed.','warning'); anomalies={'available':False,'message':'Analysis refresh failed.','results':[]}; recommendations=[]
        try: selected_month=date.fromisoformat(start).replace(day=1).isoformat()
        except ValueError: selected_month=date.today().replace(day=1).isoformat()
        return render_template('dashboard.html',summary=summary(uid,start,end),categories=category_spend(uid,start,end),transactions=list_transactions(uid)[:6],start=start,end=end,anomalies=anomalies,recommendations=recommendations)

    @app.route('/spending')
    @login_required
    def spending():
        uid=session['user_id']; today=date.today()
        start=request.args.get('start',today.replace(day=1).isoformat()); end=request.args.get('end',today.isoformat())
        try: month_start=date.fromisoformat(start).replace(day=1).isoformat()
        except ValueError: month_start=today.replace(day=1).isoformat()
        return render_template('spending.html',start=start,end=end,summary=summary(uid,start,end),categories=category_spend(uid,start,end),budgets=budget_status(uid,month_start),daily=daily_spending(uid),weekly=weekly_spending(uid),monthly=monthly_trends(uid),profiles=monthly_spending_profiles(uid))

    @app.route('/planning')
    @login_required
    def planning():
        return render_template('planning.html')

    @app.route('/transactions',methods=['GET','POST'])
    @login_required
    def transactions():
        uid=session['user_id']
        if request.method=='POST':
            try:
                amount=float(request.form['amount']); cat=int(request.form['category_id']); date.fromisoformat(request.form['date'])
                typ=request.form.get('type')
                if not math.isfinite(amount) or amount<=0 or typ not in ('INCOME','EXPENSE'): raise ValueError()
                own=query('SELECT category_id,category_type FROM categories WHERE category_id=:c AND user_id=:u',{'c':cat,'u':uid},one=True)
                if not own: raise ValueError()
                if own['category_type'] not in ('BOTH',typ): raise ValueError()
                add_transaction(uid,request.form); flash('Transaction saved.','success'); return redirect(url_for('transactions'))
            except (ValueError,KeyError): flash('Check the date, category, type, and positive amount.','danger')
        cats=query('SELECT category_id,name,category_type FROM categories WHERE user_id=:u ORDER BY name',{'u':uid})
        rows=list_transactions(uid,request.args.get('start'),request.args.get('end'),request.args.get('category_id'),request.args.get('type'),request.args.get('search'))
        anomalies=analyze_user_anomalies(uid)
        return render_template('transactions.html',transactions=rows,categories=cats,anomalies=anomalies)

    @app.route('/transactions/import',methods=['POST'])
    @login_required
    def import_transactions():
        import csv,io
        file=request.files.get('file')
        if not file or not file.filename.lower().endswith('.csv'):
            flash('Choose a CSV file.','danger'); return redirect(url_for('transactions'))
        try:
            text=decode_csv_bytes(file.stream.read()); reader=csv.DictReader(io.StringIO(text),strict=True)
            if not reader.fieldnames or len(reader.fieldnames)>50: raise ValueError('The CSV must have a header row with no more than 50 columns.')
            if len(reader.fieldnames) != len(set(reader.fieldnames)): raise ValueError('The CSV has duplicate column names; rename them and try again.')
            raw_rows=list(reader)
            if not raw_rows: raise ValueError('The CSV contains no transaction rows.')
            if len(raw_rows)>5000: raise ValueError('CSV is limited to 5,000 rows.')
            if any(None in row for row in raw_rows): raise ValueError('Some rows have more values than the header. Check the CSV columns.')
            token=secrets.token_urlsafe(24)
            cache=app.extensions['csv_imports']; now=datetime.now(timezone.utc).timestamp()
            for old_key in [key for key,value in cache.items() if value['expires'] < now]: cache.pop(old_key,None)
            while len(cache)>=64: cache.pop(next(iter(cache)))
            cache[token]={'user_id':session['user_id'],'expires':now+1200,'headers':reader.fieldnames,'rows':raw_rows}
            cats=query('SELECT category_id,name,category_type FROM categories WHERE user_id=:u ORDER BY name',{'u':session['user_id']})
            aliases={'date':('date','transaction date','txn date','posted date'),'amount':('amount','transaction amount','txn amount','value'),'type':('type','transaction type','txn type','direction'),'category':('category','transaction category'),'merchant_name':('merchant','merchant name','payee','vendor'),'description':('description','memo','details','narration'),'payment_method':('payment method','method','account')}
            suggestions={}
            for field,names in aliases.items():
                suggestions[field]=next((h for h in reader.fieldnames if h.strip().lower() in names), '')
            return render_template('import_mapping.html',token=token,headers=reader.fieldnames,suggestions=suggestions,categories=cats)
        except (UnicodeDecodeError,ValueError,KeyError,csv.Error) as exc:
            flash(f'CSV could not be prepared: {exc}','danger')
            return redirect(url_for('transactions'))

    @app.route('/transactions/import/preview',methods=['POST'])
    @login_required
    def preview_import_transactions():
        token=request.form.get('token',''); entry=app.extensions['csv_imports'].get(token)
        if not entry or entry['user_id']!=session['user_id'] or entry['expires']<datetime.now(timezone.utc).timestamp():
            app.extensions['csv_imports'].pop(token,None)
            flash('This import preview expired. Upload the CSV again.','warning')
            return redirect(url_for('transactions'))
        fields=('date','amount','type','category','merchant_name','description','payment_method')
        mapping={field:request.form.get(f'map_{field}','') for field in fields}
        if not mapping['date'] or not mapping['amount'] or any(value and value not in entry['headers'] for value in mapping.values()):
            flash('Choose valid CSV columns for date and amount.','danger')
            return redirect(url_for('transactions'))
        default_type=request.form.get('default_type','EXPENSE').upper()
        if default_type not in ('INCOME','EXPENSE'): default_type='EXPENSE'
        categories=query('SELECT category_id,name,category_type FROM categories WHERE user_id=:u ORDER BY name',{'u':session['user_id']})
        by_name={c['name'].strip().lower():c for c in categories}
        valid=[]; preview=[]; invalid=0
        for index,raw in enumerate(entry['rows'],start=2):
            errors=[]
            val=lambda field: (raw.get(mapping[field]) or '').strip() if mapping[field] else ''
            date_text=val('date')
            parsed_date=None
            for fmt in ('%Y-%m-%d','%m/%d/%Y','%d/%m/%Y','%Y/%m/%d'):
                try: parsed_date=datetime.strptime(date_text,fmt).date(); break
                except ValueError: pass
            amount_text=val('amount').replace(',','').replace('₹','').replace('$','').strip()
            try: raw_amount=float(amount_text)
            except ValueError: raw_amount=float('nan')
            if not parsed_date: errors.append('Invalid date')
            if not math.isfinite(raw_amount) or raw_amount==0: errors.append('Amount must be a valid non-zero number')
            raw_type=val('type').upper()
            if raw_type in ('CREDIT','CR','DEPOSIT','INCOME'): tx_type='INCOME'
            elif raw_type in ('DEBIT','DR','WITHDRAWAL','EXPENSE'): tx_type='EXPENSE'
            elif not raw_type: tx_type='EXPENSE' if raw_amount<0 else default_type
            else: tx_type=''
            if tx_type not in ('INCOME','EXPENSE'): errors.append('Type must be income or expense')
            row={'date':parsed_date.isoformat() if parsed_date else date_text,'amount':abs(raw_amount) if math.isfinite(raw_amount) else 0,'type':tx_type,'description':val('description'),'merchant_name':val('merchant_name'),'payment_method':val('payment_method')}
            category_text=val('category')
            category=by_name.get(category_text.lower()) if category_text else None
            reason='Matched CSV category to your existing category.' if category else ''
            if category and tx_type and category['category_type'] not in ('BOTH',tx_type): category=None
            if not category and tx_type:
                category,reason=suggest_category(row,categories)
            if not category: errors.append('No compatible user category found')
            else:
                row['category']=category['name']; row['category_id']=category['category_id']; row['category_reason']=reason
            if errors: invalid+=1
            else:
                row['digest']=import_hash(row)
                valid.append(row)
            if len(preview)<50:
                preview.append({'line':index,'row':row,'errors':errors,'valid':not errors})
        known=find_existing_import_hashes(session['user_id'],[row['digest'] for row in valid])
        seen=set(known); duplicates=0
        for row in valid:
            row['duplicate']=row['digest'] in seen
            if row['duplicate']: duplicates+=1
            seen.add(row['digest'])
        for item in preview:
            item['duplicate']=item['row'].get('duplicate',False)
        entry.update({'valid_rows':valid,'invalid_count':invalid,'duplicate_count':duplicates,'mapping':mapping,'default_type':default_type})
        return render_template('import_preview.html',token=token,preview=preview,total=len(entry['rows']),valid_count=len(valid),duplicate_count=duplicates,invalid_count=invalid,headers=entry['headers'])

    @app.route('/transactions/import/confirm',methods=['POST'])
    @login_required
    def confirm_import_transactions():
        token=request.form.get('token',''); cache=app.extensions['csv_imports']; entry=cache.get(token)
        if not entry or entry['user_id']!=session['user_id'] or entry['expires']<datetime.now(timezone.utc).timestamp() or 'valid_rows' not in entry:
            cache.pop(token,None); flash('This import preview expired. Upload the CSV again.','warning'); return redirect(url_for('transactions'))
        rows=[{**row,'digest':row['digest'],'category_id':row['category_id']} for row in entry['valid_rows']]
        imported,duplicates=import_transactions_batch(session['user_id'],rows)
        invalid=entry['invalid_count']; cache.pop(token,None)
        flash(f'Import complete: {imported} imported, {duplicates} duplicate rows skipped, {invalid} invalid rows not imported.','success' if invalid==0 else 'warning')
        return redirect(url_for('transactions'))

    @app.route('/transactions/import/sms',methods=['POST'])
    @login_required
    def import_sms_transactions():
        messages=split_sms_messages(request.form.get('messages',''))
        if not messages:
            flash('Paste one or more bank transaction messages to preview.','danger')
            return redirect(url_for('transactions'))
        if len(messages)>50 or sum(map(len,messages))>50000:
            flash('For one import, paste no more than 50 messages or 50,000 characters.','danger')
            return redirect(url_for('transactions'))
        uid=session['user_id']
        categories=query('SELECT category_id,name,category_type FROM categories WHERE user_id=:u ORDER BY name',{'u':uid})
        candidates=[]
        for raw_message in messages:
            parsed=parse_bank_sms(raw_message)
            row={**parsed,'raw_message':raw_message,'digest':sms_import_hash(raw_message),'payment_method':'SMS'}
            if parsed['type']:
                category,reason=suggest_category(row,categories)
                row['category_id']=category['category_id'] if category else None
                row['category_name']=category['name'] if category else None
                row['category_reason']=reason if category else 'Choose a category belonging to your account.'
            else:
                row['category_id']=None; row['category_name']=None; row['category_reason']='Choose a transaction direction before selecting a category.'
            candidates.append(row)
        existing=find_existing_import_hashes(uid,[row['digest'] for row in candidates if row['status']!='REJECTED'])
        seen=set(existing)
        for row in candidates:
            row['duplicate']=row['status']!='REJECTED' and row['digest'] in seen
            if row['status']!='REJECTED': seen.add(row['digest'])
        token=secrets.token_urlsafe(24); cache=app.extensions['sms_imports']; now=datetime.now(timezone.utc).timestamp()
        for old_key in [key for key,value in cache.items() if value['expires']<now]: cache.pop(old_key,None)
        while len(cache)>=64: cache.pop(next(iter(cache)))
        cache[token]={'user_id':uid,'expires':now+1200,'rows':candidates}
        return render_template('sms_preview.html',token=token,rows=candidates,categories=categories)

    @app.route('/transactions/import/sms/confirm',methods=['POST'])
    @login_required
    def confirm_sms_transactions():
        token=request.form.get('token',''); cache=app.extensions['sms_imports']; entry=cache.get(token)
        if not entry or entry['user_id']!=session['user_id'] or entry['expires']<datetime.now(timezone.utc).timestamp():
            cache.pop(token,None)
            flash('This SMS preview expired. Paste the messages again.','warning')
            return redirect(url_for('transactions'))
        uid=session['user_id']
        categories=query('SELECT category_id,name,category_type FROM categories WHERE user_id=:u',{'u':uid})
        owned={int(category['category_id']):category for category in categories}
        rows=[]; skipped=0; rejected=sum(row['status']=='REJECTED' for row in entry['rows'])
        for index,source in enumerate(entry['rows']):
            if source['status']=='REJECTED':
                continue
            if source['status']=='REVIEW' and request.form.get(f'review_{index}')!='yes':
                skipped+=1
                continue
            try:
                tx_date=date.fromisoformat(request.form.get(f'date_{index}','').strip())
                amount=float(request.form.get(f'amount_{index}',''))
                tx_type=request.form.get(f'type_{index}','').upper()
                category_id=int(request.form.get(f'category_{index}',''))
                category=owned.get(category_id)
                merchant=request.form.get(f'merchant_{index}','').strip()
                description=request.form.get(f'description_{index}','').strip()
                if not math.isfinite(amount) or amount<=0 or tx_type not in ('INCOME','EXPENSE') or not category or category['category_type'] not in ('BOTH',tx_type) or not (merchant or description):
                    raise ValueError()
                if len(merchant)>120 or len(description)>500: raise ValueError()
            except (ValueError,KeyError):
                skipped+=1
                continue
            rows.append({'date':tx_date.isoformat(),'amount':amount,'type':tx_type,'category':category['name'],'category_id':category_id,'merchant_name':merchant or None,'description':description or None,'payment_method':'SMS','digest':source['digest']})
        imported,duplicates=import_transactions_batch(uid,rows)
        cache.pop(token,None)
        flash(f'SMS import: {imported} saved, {duplicates} duplicate messages skipped, {skipped} messages needing correction or confirmation skipped, {rejected} non-transaction messages rejected.','success' if skipped==0 and rejected==0 else 'warning')
        return redirect(url_for('transactions'))

    @app.route('/transactions/<int:tid>/delete',methods=['POST'])
    @login_required
    def delete_transaction(tid):
        execute('DELETE FROM transactions WHERE transaction_id=:t AND user_id=:u',{'t':tid,'u':session['user_id']}); flash('Transaction deleted.','info'); return redirect(url_for('transactions'))

    @app.route('/transactions/<int:tid>/edit',methods=['GET','POST'])
    @login_required
    def edit_transaction(tid):
        uid=session['user_id']; row=query('SELECT transaction_id,transaction_date,amount,transaction_type,category_id,description,payment_method,merchant_name FROM transactions WHERE transaction_id=:t AND user_id=:u',{'t':tid,'u':uid},one=True)
        if not row: return 'Not found',404
        cats=query('SELECT category_id,name,category_type FROM categories WHERE user_id=:u ORDER BY name',{'u':uid})
        if request.method=='POST':
            try:
                amount=float(request.form['amount']); date.fromisoformat(request.form['date']); category=int(request.form['category_id']); typ=request.form['type']
                selected=next((c for c in cats if c['category_id']==category),None)
                if not math.isfinite(amount) or amount<=0 or typ not in ('INCOME','EXPENSE') or not selected or selected['category_type'] not in ('BOTH',typ): raise ValueError()
                execute("UPDATE transactions SET transaction_date=TO_DATE(:d,'YYYY-MM-DD'),amount=:a,transaction_type=:t,category_id=:c,description=:x,payment_method=:p,merchant_name=:m WHERE transaction_id=:id AND user_id=:u",{'d':request.form['date'],'a':amount,'t':request.form['type'],'c':category,'x':request.form.get('description'),'p':request.form.get('payment_method'),'m':request.form.get('merchant_name'),'id':tid,'u':uid})
                flash('Transaction updated.','success'); return redirect(url_for('transactions'))
            except (ValueError,KeyError): flash('Check the date, category, type, and positive amount.','danger')
        return render_template('edit_transaction.html',transaction=row,categories=cats)

    @app.route('/transactions/export')
    @login_required
    def export_transactions():
        rows=list_transactions(session['user_id'],request.args.get('start'),request.args.get('end'),request.args.get('category_id'),request.args.get('type'),request.args.get('search'))
        import csv,io
        out=io.StringIO(); w=csv.writer(out); w.writerow(['date','amount','type','category','description','payment_method','merchant_name'])
        def safe_text(value):
            text='' if value is None else str(value)
            if text.lstrip().startswith(('=','+','-','@','\t','\r')): return "'"+text
            return text
        for r in rows: w.writerow([r['transaction_date'],r['amount'],r['transaction_type'],r['category'],safe_text(r['description']),safe_text(r['payment_method']),safe_text(r['merchant_name'])])
        return Response(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=transactions.csv'})

    @app.route('/budgets',methods=['GET','POST'])
    @login_required
    def budgets():
        uid=session['user_id']
        month_value=request.values.get('month') or date.today().strftime('%Y-%m')
        try:
            date.fromisoformat(month_value+'-01')
        except ValueError:
            month_value=date.today().strftime('%Y-%m')
            flash('Choose a valid budget month.','danger')
        if request.method=='POST':
            try:
                category_id=int(request.form['category_id']); amount=float(request.form['amount']); month_value=request.form['month']
                date.fromisoformat(month_value+'-01')
                category=query("SELECT category_id FROM categories WHERE user_id=:u AND category_id=:c AND category_type IN ('EXPENSE','BOTH')",{'u':uid,'c':category_id},one=True)
                if not category or not math.isfinite(amount) or amount<=0: raise ValueError()
                execute("MERGE INTO budgets b USING (SELECT :u user_id,:c category_id,TO_DATE(:m,'YYYY-MM-DD') month_start FROM dual) s ON (b.user_id=s.user_id AND b.category_id=s.category_id AND b.month_start=s.month_start) WHEN MATCHED THEN UPDATE SET b.amount=:a WHEN NOT MATCHED THEN INSERT(user_id,category_id,month_start,amount) VALUES(s.user_id,s.category_id,s.month_start,:a)",{'u':uid,'c':category_id,'m':month_value+'-01','a':amount}); flash('Budget saved.','success')
                return redirect(url_for('budgets',month=month_value))
            except (ValueError,KeyError):
                month_value=date.today().strftime('%Y-%m')
                flash('Choose an expense category, valid month, and positive budget amount.','danger')
        month=month_value+'-01'
        rows=query("SELECT b.budget_id,c.name category,b.amount budget,NVL(SUM(t.amount),0) actual FROM budgets b JOIN categories c ON c.category_id=b.category_id LEFT JOIN transactions t ON t.user_id=b.user_id AND t.category_id=b.category_id AND t.transaction_type='EXPENSE' AND t.transaction_date>=b.month_start AND t.transaction_date<ADD_MONTHS(b.month_start,1) WHERE b.user_id=:u AND b.month_start=TO_DATE(:m,'YYYY-MM-DD') GROUP BY b.budget_id,c.name,b.amount",{'u':uid,'m':month})
        return render_template('budgets.html',budgets=rows,categories=query("SELECT category_id,name FROM categories WHERE user_id=:u AND category_type IN ('EXPENSE','BOTH') ORDER BY name",{'u':uid}),month=month[:7])

    @app.route('/categories',methods=['GET','POST'])
    @login_required
    def categories():
        uid=session['user_id']
        if request.method=='POST':
            name=request.form.get('name','').strip(); category_type=request.form.get('category_type',''); essential=request.form.get('is_essential','N')
            if not name or len(name)>80 or category_type not in ('INCOME','EXPENSE','BOTH') or essential not in ('Y','N'):
                flash('Enter a category name, type, and essential setting.','danger')
            else:
                try:
                    execute('INSERT INTO categories(user_id,name,category_type,is_essential) VALUES(:u,:n,:t,:e)',{'u':uid,'n':name,'t':category_type,'e':essential})
                    flash('Category created.','success')
                except Exception as exc:
                    if 'ORA-00001' in str(exc): flash('That category name already exists.','warning')
                    else: raise
        rows=query('SELECT category_id,name,category_type,is_essential FROM categories WHERE user_id=:u ORDER BY name',{'u':uid})
        return render_template('categories.html',categories=rows)

    @app.route('/goals',methods=['GET','POST'])
    @login_required
    def goals():
        uid=session['user_id']
        if request.method=='POST':
            try:
                name=request.form['name'].strip(); target=float(request.form['target_amount']); starting=float(request.form.get('starting_amount') or 0); target_date=request.form.get('target_date') or '2099-12-31'; date.fromisoformat(target_date)
                if not name or len(name)>120 or not math.isfinite(target) or not math.isfinite(starting) or target<=0 or starting<0: raise ValueError()
                execute("INSERT INTO financial_goals(user_id,name,target_amount,starting_amount,target_date) VALUES(:u,:n,:a,:s,TO_DATE(:d,'YYYY-MM-DD'))",{'u':uid,'n':name,'a':target,'s':starting,'d':target_date}); flash('Goal created.','success'); return redirect(url_for('goals'))
            except (ValueError,KeyError): flash('Enter a goal name, positive target, nonnegative starting amount, and valid date.','danger')
        rows=query("SELECT g.goal_id,g.name,g.target_amount,g.starting_amount,g.target_date,g.status,g.starting_amount+NVL(SUM(c.amount),0) saved FROM financial_goals g LEFT JOIN goal_contributions c ON c.goal_id=g.goal_id WHERE g.user_id=:u GROUP BY g.goal_id,g.name,g.target_amount,g.starting_amount,g.target_date,g.status,g.created_at ORDER BY g.created_at DESC",{'u':uid})
        return render_template('goals.html',goals=rows)

    @app.route('/goals/<int:gid>/edit',methods=['GET','POST'])
    @login_required
    def edit_goal(gid):
        uid=session['user_id']
        goal=query('SELECT goal_id,name,target_amount,starting_amount,target_date FROM financial_goals WHERE goal_id=:g AND user_id=:u',{'g':gid,'u':uid},one=True)
        if not goal: return 'Not found',404
        if request.method=='POST':
            try:
                name=request.form['name'].strip(); target=float(request.form['target_amount']); starting=float(request.form.get('starting_amount') or 0); target_date=request.form.get('target_date') or '2099-12-31'; date.fromisoformat(target_date)
                if not name or len(name)>120 or not math.isfinite(target) or not math.isfinite(starting) or target<=0 or starting<0: raise ValueError()
                execute("UPDATE financial_goals SET name=:n,target_amount=:a,starting_amount=:s,target_date=TO_DATE(:d,'YYYY-MM-DD') WHERE goal_id=:g AND user_id=:u",{'n':name,'a':target,'s':starting,'d':target_date,'g':gid,'u':uid})
                flash('Goal updated.','success'); return redirect(url_for('goals'))
            except (ValueError,KeyError): flash('Enter a goal name, positive target, nonnegative starting amount, and valid date.','danger')
        return render_template('edit_goal.html',goal=goal)

    @app.route('/goals/<int:gid>/delete',methods=['POST'])
    @login_required
    def delete_goal(gid):
        execute('DELETE FROM financial_goals WHERE goal_id=:g AND user_id=:u',{'g':gid,'u':session['user_id']})
        flash('Goal and its recorded contributions were deleted.','info')
        return redirect(url_for('goals'))

    @app.route('/goals/<int:gid>/contribute',methods=['POST'])
    @login_required
    def contribute(gid):
        own=query('SELECT goal_id FROM financial_goals WHERE goal_id=:g AND user_id=:u',{'g':gid,'u':session['user_id']},one=True)
        if not own: return 'Not found',404
        try:
            amount=float(request.form['amount'])
            if not math.isfinite(amount) or amount<=0: raise ValueError()
            execute("INSERT INTO goal_contributions(goal_id,amount,contribution_date,note) VALUES(:g,:a,TRUNC(SYSDATE),:n)",{'g':gid,'a':amount,'n':request.form.get('note')}); flash('Contribution recorded.','success')
        except (ValueError,KeyError): flash('Enter a positive contribution amount.','danger')
        return redirect(url_for('goals'))

    @app.route('/insights')
    @login_required
    def insights():
        recs=query('SELECT title,explanation,supporting_data,comparison_period,suggested_action,priority,created_at FROM recommendations WHERE user_id=:u ORDER BY created_at DESC FETCH FIRST 100 ROWS ONLY',{'u':session['user_id']})
        return render_template('insights.html',recommendations=recs,recurring=detect_recurring_expenses(session['user_id']))

    @app.route('/insights/generate',methods=['POST'])
    @login_required
    def generate_insights():
        today=date.today(); count=len(generate_monthly_recommendations(session['user_id'],today.year,today.month)); flash(f'Generated {count} data-backed recommendation(s).','success'); return redirect(url_for('insights'))

    @app.route('/simulator',methods=['GET','POST'])
    @login_required
    def simulator():
        result=None; uid=session['user_id']; goals=query("SELECT g.goal_id,g.name,g.target_amount,g.starting_amount+NVL(SUM(c.amount),0) saved FROM financial_goals g LEFT JOIN goal_contributions c ON c.goal_id=g.goal_id WHERE g.user_id=:u GROUP BY g.goal_id,g.name,g.target_amount,g.starting_amount",{'u':uid})
        history=monthly_cashflows(uid)
        if request.method=='POST':
            try:
                reduction=float(request.form.get('reduction') or 0)/100; goal_id=int(request.form['goal_id']) if request.form.get('goal_id') else None
                goal=next((g for g in goals if g['goal_id']==goal_id),None)
                if goal_id is not None and goal is None: raise ValueError('Select one of your listed savings goals.')
                target=max(float(goal['target_amount'])-float(goal['saved']),0) if goal else None
                horizon=int(request.form['horizon']); n=int(request.form['simulations']); balance=float(request.form.get('balance') or 0)
                if len(history)>=3: scenarios=simulate_scenarios(history,horizon,n,balance,target,reduction)
                else:
                    # Use actual averages where available; form values are an explicit fallback only for a new/empty history.
                    inc=float(request.form.get('income') or (sum(x[0] for x in history)/len(history) if history else 0)); exp=float(request.form.get('expense') or (sum(x[1] for x in history)/len(history) if history else 0))
                    scenarios=simulate_scenarios(history or [[inc,exp]],horizon,n,balance,target,reduction)
                result={**scenarios,'goal_name':goal['name'] if goal else None}
                import json
                inputs={'history_months':len(history),'historical_monthly_cashflows':history,'starting_balance':balance,'goal_id':goal_id,'target_remaining':target,'expense_reduction':reduction,'seed':42,'method':'paired bootstrap with documented assumption fallback'}
                execute('INSERT INTO simulation_results(user_id,horizon_months,simulations,scenario_inputs,outputs) VALUES(:u,:h,:n,:i,:o)',{'u':uid,'h':horizon,'n':n,'i':json.dumps(inputs), 'o':json.dumps({k:v for k,v in result.items() if k not in ('baseline','reduced') } | {'baseline':{k:v for k,v in scenarios['baseline'].items() if k!='terminal_samples'},'reduced':{k:v for k,v in scenarios['reduced'].items() if k!='terminal_samples'}})})
            except (ValueError,KeyError) as exc: flash(str(exc),'danger')
        fallback_income=round(sum(x[0] for x in history)/len(history),2) if history else 0
        fallback_expense=round(sum(x[1] for x in history)/len(history),2) if history else 0
        return render_template('simulator.html',result=result,goals=goals,fallback_income=fallback_income,fallback_expense=fallback_expense,fallback_required=not history)

    @app.route('/reports')
    @login_required
    def reports():
        s=request.args.get('start',date.today().replace(day=1).isoformat()); e=request.args.get('end',date.today().isoformat()); uid=session['user_id']
        return render_template('reports.html',summary=summary(uid,s,e),categories=category_spend(uid,s,e),start=s,end=e,daily=daily_spending(uid),weekly=weekly_spending(uid),monthly=monthly_trends(uid))

    @app.route('/reports/export')
    @login_required
    def export_report():
        import csv,io
        start=request.args.get('start',date.today().replace(day=1).isoformat()); end=request.args.get('end',date.today().isoformat()); uid=session['user_id']
        report=summary(uid,start,end); categories=category_spend(uid,start,end)
        output=io.StringIO(); writer=csv.writer(output)
        writer.writerow(['FutureFinance report',start,end])
        writer.writerow(['Metric','Amount'])
        writer.writerow(['Income',report['income']]); writer.writerow(['Expenses',report['expense']]); writer.writerow(['Net cash flow',report['net']])
        writer.writerow(['Savings rate (%)',report['savings_rate'] if report['savings_rate'] is not None else ''])
        writer.writerow([]); writer.writerow(['Expense category','Amount'])
        def safe_text(value):
            text='' if value is None else str(value)
            return "'"+text if text.lstrip().startswith(('=','+','-','@','\t','\r')) else text
        for category in categories: writer.writerow([safe_text(category['category']),category['amount']])
        filename=f"futurefinance-report-{start}-to-{end}.csv"
        return Response(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':f'attachment; filename="{filename}"'})

    return app

app=create_app()
if __name__=='__main__': app.run(debug=False)
