import numpy as np
import math

def simulate(monthly_income, monthly_expense, horizon_months=12, simulations=5000, starting_balance=0, expense_reduction=0.0, target=None, seed=42):
    if not 1 <= int(horizon_months) <= 60: raise ValueError("horizon_months must be between 1 and 60")
    if not 100 <= int(simulations) <= 100000: raise ValueError("simulations must be between 100 and 100000")
    values=[monthly_income,monthly_expense,starting_balance,expense_reduction]
    if any(not math.isfinite(float(value)) for value in values) or min(monthly_income,monthly_expense,starting_balance)<0 or not 0<=expense_reduction<=0.9: raise ValueError("Amounts must be finite and nonnegative and expense_reduction must be 0..0.9")
    if target is not None and (not math.isfinite(float(target)) or target<0): raise ValueError("target must be a finite nonnegative amount")
    rng=np.random.default_rng(seed); n=int(simulations); h=int(horizon_months)
    # Fallback assumptions: 10% income and 12% expense coefficient of variation.
    inc=rng.normal(monthly_income,max(monthly_income*.10,1),(n,h)).clip(0)
    exp=rng.normal(monthly_expense,max(monthly_expense*.12,1),(n,h)).clip(0)*(1-expense_reduction)
    terminal=starting_balance+np.cumsum(inc-exp,axis=1)[:,-1]
    return summarize(terminal,h,n,target,"Assumption-based: monthly income CV 10%, expense CV 12%; independent normal variation clipped at zero; no investment returns, taxes, or inflation.")

def summarize(terminal,horizon,simulations,target,assumption):
    terminal=np.asarray(terminal,dtype=float)
    return {"horizon_months":int(horizon),"simulations":int(simulations),"median":float(np.percentile(terminal,50)),"p10":float(np.percentile(terminal,10)),"p90":float(np.percentile(terminal,90)),"target_probability":float(np.mean(terminal>=target)) if target is not None and target>=0 else None,"terminal_samples":terminal.tolist(),"assumption":assumption}

def simulate_scenarios(monthly_history,horizon_months=12,simulations=5000,starting_balance=0,target=None,expense_reduction=0.1,seed=42):
    """Paired bootstrap projections; history rows are (monthly_income, monthly_expense)."""
    h=int(horizon_months); n=int(simulations)
    if not 1<=h<=60 or not 100<=n<=100000: raise ValueError('Horizon must be 1..60 months and simulations 100..100000.')
    if not math.isfinite(float(starting_balance)) or not math.isfinite(float(expense_reduction)) or starting_balance<0 or not 0<=expense_reduction<=0.9: raise ValueError('Starting balance and reduction must be finite; balance must be nonnegative and reduction 0..90%.')
    if target is not None and (not math.isfinite(float(target)) or target<0): raise ValueError('Target must be a finite nonnegative amount.')
    history=np.asarray(monthly_history,dtype=float)
    if history.ndim!=2 or history.shape[1]!=2: raise ValueError('monthly_history must contain income and expense pairs.')
    if np.any(~np.isfinite(history)) or np.any(history<0): raise ValueError('Historical amounts must be finite and nonnegative.')
    enough=len(history)>=3
    if enough:
        rng=np.random.default_rng(seed); selected=history[rng.integers(0,len(history),size=(n,h))]
        # Small residual jitter avoids pretending each future month repeats exactly.
        income=np.maximum(0,selected[:,:,0]+rng.normal(0,max(float(history[:,0].mean())*.025,1),(n,h)))
        expense=np.maximum(0,selected[:,:,1]+rng.normal(0,max(float(history[:,1].mean())*.04,1),(n,h)))
        method=f"History-based bootstrap from {len(history)} observed complete months, with 2.5% income and 4% expense jitter; no returns, taxes, or inflation."
    else:
        avg=np.mean(history,axis=0) if len(history) else np.array([0.,0.])
        rng=np.random.default_rng(seed)
        income=rng.normal(avg[0],max(avg[0]*.10,1),(n,h)).clip(0)
        expense=rng.normal(avg[1],max(avg[1]*.12,1),(n,h)).clip(0)
        method=f"Assumption-based because only {len(history)} complete observed months are available (3 required); 10% income and 12% expense CV, normal variation clipped at zero."
    base=starting_balance+np.cumsum(income-expense,axis=1)[:,-1]
    reduced=starting_balance+np.cumsum(income-expense*(1-expense_reduction),axis=1)[:,-1]
    return {'history_based':enough,'history_months':len(history),'expense_reduction':expense_reduction,'baseline':summarize(base,h,n,target,method),'reduced':summarize(reduced,h,n,target,method),'assumption':method}
