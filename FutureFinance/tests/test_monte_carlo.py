import pytest
from services.monte_carlo import simulate, simulate_scenarios

def test_simulation_is_reproducible_and_ordered():
    a=simulate(50000,30000,12,1000,target=250000,seed=19); b=simulate(50000,30000,12,1000,target=250000,seed=19)
    assert a==b and a['p10']<=a['median']<=a['p90'] and 0<=a['target_probability']<=1

def test_invalid_horizon_rejected():
    with pytest.raises(ValueError): simulate(100,80,horizon_months=0)

def test_history_based_scenarios_are_paired_reproducible_and_reach_goal_probability():
    history=[[50000,35000],[52000,36000],[48000,34000],[51000,37000]]
    first=simulate_scenarios(history,12,1000,10000,100000,.2,seed=8)
    again=simulate_scenarios(history,12,1000,10000,100000,.2,seed=8)
    assert first==again and first['history_based'] and first['baseline']['target_probability'] is not None
    assert first['reduced']['median']>first['baseline']['median']

def test_short_history_is_labeled_assumption_based():
    result=simulate_scenarios([[50000,35000]],6,100,0,seed=5)
    assert not result['history_based'] and 'Assumption-based' in result['assumption']
