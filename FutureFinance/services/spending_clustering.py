import numpy as np
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score

def cluster_months(profiles, random_state=42):
    x=np.asarray(profiles,dtype=float)
    if x.ndim != 2 or len(x)<3: return {"available":False,"reason":"At least three monthly profiles are needed."}
    x=np.nan_to_num(x,nan=0,posinf=0,neginf=0); scaler=StandardScaler(); scaled=scaler.fit_transform(x)
    candidates=[]
    # Repeated identical monthly profiles cannot form additional meaningful clusters.
    unique_profiles=len(np.unique(scaled,axis=0))
    max_k=min(6,len(x)-1,unique_profiles)
    for k in range(2,max_k+1):
        model=KMeans(n_clusters=k,n_init=10,random_state=random_state); labels=model.fit_predict(scaled)
        if len(set(labels))>1: candidates.append((silhouette_score(scaled,labels),k,labels,model))
    if not candidates: return {"available":False,"reason":"Profiles do not support distinct clusters."}
    score,k,labels,model=max(candidates,key=lambda a:a[0])
    return {"available":True,"labels":labels.tolist(),"k":k,"silhouette":float(score),"centers":scaler.inverse_transform(model.cluster_centers_).tolist()}
