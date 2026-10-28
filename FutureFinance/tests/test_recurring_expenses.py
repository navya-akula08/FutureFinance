from datetime import date, timedelta
from services import recurring_expenses


def test_detects_regular_similar_payments_from_payee_history(monkeypatch):
    today=date.today()
    rows=[{"transaction_date":today-timedelta(days=30*i),"amount":499,"merchant_name":"MusicCo","description":None,"category":"Subscriptions"} for i in range(4)]
    monkeypatch.setattr(recurring_expenses,"query",lambda sql,params:rows)
    result=recurring_expenses.detect_recurring_expenses(12)
    assert len(result)==1
    assert result[0]["cadence"]=="monthly"
    assert result[0]["average_amount"]==499
    assert "4 similar payments" in result[0]["explanation"]


def test_ignores_category_only_rows_to_avoid_false_recurring_claims(monkeypatch):
    today=date.today()
    rows=[{"transaction_date":today-timedelta(days=7*i),"amount":10,"merchant_name":None,"description":"","category":"Food"} for i in range(5)]
    monkeypatch.setattr(recurring_expenses,"query",lambda sql,params:rows)
    assert recurring_expenses.detect_recurring_expenses(12)==[]
