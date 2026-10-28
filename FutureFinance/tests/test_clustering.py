from services.spending_clustering import cluster_months

def test_clustering_handles_too_little_data():
    assert not cluster_months([[1,2],[2,3]])['available']

def test_cluster_month_profiles():
    result=cluster_months([[1,1],[1.1,1],[.9,1.1],[10,10],[11,9],[9,11]],random_state=4)
    assert result['available'] and result['k']>=2 and len(result['labels'])==6
