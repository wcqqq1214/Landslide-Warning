"""Parameter calibration using only the fitting trajectory and its terminal displacement."""
import json,time
import numpy as np,pandas as pd
from scipy.optimize import least_squares
from physical_model import ROOT,PARENT,POINTS,Context,forward,LO,HI
d=pd.read_csv(PARENT/'work/monitoring.csv',usecols=['Rainfall','RWL',*POINTS],nrows=1168)
Y=d[POINTS].to_numpy();y0=Y[0];Y=Y-y0;ctx=Context(d[['Rainfall','RWL']].to_numpy())
cfg=json.loads((ROOT/'work/unconstrained_calibrated.json').read_text());th=np.array(cfg['theta']);start=time.time();records=[]
for weight in [1168.,116800.]:
 calls=0
 def fun(x):
  global calls
  p=forward(x,ctx);r=(p-Y)/100
  calls+=1
  if calls%10000==0:print('progress',weight,calls,round(time.time()-start,1),flush=True)
  return np.r_[r.ravel(),np.sqrt(weight)*r[-1]]
 def jac(x):
  r=fun(x);cols=[]
  for j in range(len(x)):
   h=2e-6*max(1,abs(x[j]));h=-h if x[j]+h>=HI[j] else h;z=x.copy();z[j]+=h;cols.append((fun(z)-r)/h)
  return np.column_stack(cols)
 op=least_squares(fun,np.clip(th,LO+1e-9,HI-1e-9),jac=jac,bounds=(LO,HI),x_scale='jac',max_nfev=800,ftol=1e-9,xtol=1e-10,gtol=1e-7)
 th=op.x;p=forward(th,ctx);rec=dict(weight=weight,fit_rmse=np.sqrt(np.mean((p-Y)**2,axis=0)).tolist(),end_bias=(p[-1]-Y[-1]).tolist(),nfev=op.nfev,optimality=op.optimality,success=bool(op.success),seconds=time.time()-start);records.append(rec)
 cfg.update(theta=th.tolist(),rmse=rec['fit_rmse'],mse=float(np.mean((p-Y)**2)),optimizer=rec,terminal_calibration_records=records,terminal_weight=weight)
 (ROOT/'work/boundary54.json').write_text(json.dumps(cfg,indent=2),'utf-8');print('RESULT',rec,flush=True)
