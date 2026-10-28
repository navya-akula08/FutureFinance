from services.anomaly_detection import detect_anomalies

def test_insufficient_history_is_unscored():
    result=detect_anomalies([{'transaction_id':i,'amount':10,'category':'Food'} for i in range(4)])
    assert all(not row['scored'] for row in result)

def test_anomaly_output_is_reproducible_and_descriptive():
    rows=[{'transaction_id':i,'amount':100,'category':'Food'} for i in range(12)]+[{'transaction_id':20,'amount':20000,'category':'Food'}]
    first=detect_anomalies(rows,random_state=7); second=detect_anomalies(rows,random_state=7)
    assert first==second and first[-1]['is_unusual'] and 'historical median' in first[-1]['explanation']

def test_results_have_scores_and_comparison_for_eligible_history():
    rows=[{'transaction_id':i,'transaction_date':f'2026-01-{i+1:02d}','amount':100+i%3,'category':'Food'} for i in range(10)]
    result=detect_anomalies(rows)
    assert len(result)==10 and all(x['scored'] and x['score']>=0 and x['category_median']>0 for x in result)
