"""Insert fictional demonstration history for a dedicated demo account."""
import os
from datetime import date, timedelta
from werkzeug.security import generate_password_hash
from database.db import query, execute

def seed():
    email=os.getenv('DEMO_USER_EMAIL','demo@futurefinance.local').lower()
    password=os.getenv('DEMO_USER_PASSWORD')
    if not password: raise RuntimeError('Set DEMO_USER_PASSWORD in .env before seeding demo records.')
    user=query('SELECT user_id FROM users WHERE email=:e',{'e':email},one=True)
    if user: raise RuntimeError('Demo account already exists; refusing duplicate seed.')
    execute('INSERT INTO users(email,password_hash,display_name) VALUES(:e,:p,:n)',{'e':email,'p':generate_password_hash(password),'n':'Demo User'})
    user=query('SELECT user_id FROM users WHERE email=:e',{'e':email},one=True); uid=user['user_id']
    categories={}
    for name,typ,essential in [('Salary','INCOME','N'),('Food','EXPENSE','Y'),('Housing','EXPENSE','Y'),('Transport','EXPENSE','Y'),('Shopping','EXPENSE','N'),('Utilities','EXPENSE','Y'),('Health','EXPENSE','Y'),('Entertainment','EXPENSE','N'),('Other','BOTH','N')]:
        execute('INSERT INTO categories(user_id,name,category_type,is_essential) VALUES(:u,:n,:t,:x)',{'u':uid,'n':name,'t':typ,'x':essential})
        categories[name]=query('SELECT category_id FROM categories WHERE user_id=:u AND name=:n',{'u':uid,'n':name},one=True)['category_id']
    # Fictional monthly records for a three-month classroom demonstration.
    for offset in range(89,-1,-1):
        d=date.today()-timedelta(days=offset)
        if d.day==1: execute("INSERT INTO transactions(user_id,transaction_date,amount,transaction_type,category_id,description,merchant_name) VALUES(:u,:d,85000,'INCOME',:c,'Monthly salary','Demo Employer')",{'u':uid,'d':d,'c':categories['Salary']})
        if offset%7 in (0,3):
            name='Food' if offset%7==0 else 'Transport'; amount=620 if name=='Food' else 240
            execute("INSERT INTO transactions(user_id,transaction_date,amount,transaction_type,category_id,description,merchant_name) VALUES(:u,:d,:a,'EXPENSE',:c,:x,:m)",{'u':uid,'d':d,'a':amount,'c':categories[name],'x':'Fictional demo purchase','m':'Demo merchant'})
        if d.day==5:
            for name,amount in [('Housing',22000),('Utilities',2300),('Shopping',3900),('Entertainment',1400)]:
                execute("INSERT INTO transactions(user_id,transaction_date,amount,transaction_type,category_id,description,merchant_name) VALUES(:u,:d,:a,'EXPENSE',:c,'Fictional demo purchase','Demo merchant')",{'u':uid,'d':d,'a':amount,'c':categories[name]})
    return email

if __name__=='__main__': print('Created demo records for',seed())
