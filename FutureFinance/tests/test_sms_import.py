from services.sms_import import parse_bank_sms, split_sms_messages
from services.transaction_service import import_transactions_batch, sms_import_hash


def test_parses_indian_bank_debit_sms_and_balance_is_not_mistaken_for_amount():
    result=parse_bank_sms('INR 750.00 debited from A/c XX1234 to Zomato on 28-10-2026. Avl Bal INR 2,500.00')
    assert result['status']=='READY'
    assert result['amount']==750 and result['type']=='EXPENSE'
    assert result['date']=='2026-10-28' and result['merchant_name']=='Zomato'


def test_parses_credit_sms_and_source_merchant():
    result=parse_bank_sms('Rs. 2,500.00 credited to your account from ACME LTD on 2026-10-28. Avl Bal INR 5,000.00')
    assert result['status']=='READY'
    assert result['amount']==2500 and result['type']=='INCOME'
    assert result['date']=='2026-10-28' and result['merchant_name']=='ACME LTD'


def test_regression_parses_reported_debit_and_credit_messages():
    debit=parse_bank_sms('Dear Customer, your account has been debited by INR 250.00 on 28-10-2026 for a UPI payment to PVR Cinemas. UPI Ref No: 123456789012.')
    credit=parse_bank_sms('Dear Customer, your account has been credited with INR 5000.00 on 27-10-2026. Transaction details: Salary Credit. Ref No: 987654321012.')
    assert debit['status']=='READY'
    assert (debit['type'],debit['amount'],debit['date'],debit['description'])==('EXPENSE',250.0,'2026-10-28','PVR Cinemas')
    assert credit['status']=='READY'
    assert (credit['type'],credit['amount'],credit['date'],credit['description'])==('INCOME',5000.0,'2026-10-27','Salary Credit')


def test_direction_verbs_map_to_expense_and_income():
    for verb in ('debited by','spent','paid','withdrawn'):
        parsed=parse_bank_sms(f'INR 250.00 {verb} at Store on 28-10-2026')
        assert parsed['type']=='EXPENSE'
    for verb in ('credited with','received from','deposited to'):
        parsed=parse_bank_sms(f'INR 250.00 {verb} ACME on 28-10-2026')
        assert parsed['type']=='INCOME'


def test_conflicting_debit_and_credit_words_require_user_correction():
    parsed=parse_bank_sms('INR 250.00 debited from account and INR 500.00 credited to account on 28-10-2026')
    assert parsed['status']=='REVIEW'
    assert parsed['type'] is None
    assert any('Both debit and credit' in issue for issue in parsed['issues'])


def test_parses_supported_date_formats():
    for text,expected in [('on 28/10/2026','2026-10-28'),('on 28 Oct 2026','2026-10-28'),('on October 28, 2026','2026-10-28')]:
        result=parse_bank_sms(f'Rs 99 debited at Shop {text}')
        assert result['date']==expected and result['status']=='READY'


def test_otp_promotional_and_nonfinancial_messages_are_rejected():
    for message in (
        'Your OTP for UPI transaction is 123456. Do not share.',
        'Exclusive offer: shop today for Rs 500 discount.',
        'Your account statement is ready to view online.',
    ):
        assert parse_bank_sms(message)['status']=='REJECTED'


def test_ambiguous_or_incomplete_transaction_is_flagged_not_filled_in():
    ambiguous=parse_bank_sms('Rs 100 debited at Shop and Rs 50 debited at Store on 28-10-2026')
    incomplete=parse_bank_sms('UPI transaction for INR 500 on 28-10-2026')
    assert ambiguous['status']=='REVIEW' and ambiguous['amount'] is None
    assert incomplete['status']=='REVIEW' and incomplete['type'] is None and incomplete['merchant_name'] is None


def test_pasted_messages_split_on_blank_lines_and_explicit_separator():
    assert split_sms_messages('first message\n\nsecond message\n---\nthird message')==['first message','second message','third message']


def test_repeated_sms_digest_is_skipped_by_import_batch(monkeypatch):
    digest=sms_import_hash('Rs 100 debited at Shop on 28-10-2026')
    assert digest==sms_import_hash('  Rs 100\n debited at Shop on 28-10-2026  ')
    executed=[]
    monkeypatch.setattr('services.transaction_service.query',lambda *args,**kwargs:[{'import_hash':digest}])
    monkeypatch.setattr('services.transaction_service.execute_many',lambda *args,**kwargs:executed.append(args))
    row={'date':'2026-10-28','amount':100,'type':'EXPENSE','category':'Food','category_id':1,'merchant_name':'Shop','description':None,'digest':digest}
    inserted,duplicates=import_transactions_batch(7,[row])
    assert (inserted,duplicates)==(0,1)
    assert executed==[]
