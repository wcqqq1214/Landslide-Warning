"""Continuous prediction from daily rain and reservoir level, using frozen B+ parameters."""
import argparse,hashlib,json
from pathlib import Path
import numpy as np,pandas as pd
from physical_model import ROOT,POINTS,Context,forward
def read_input(path):
 d=pd.read_csv(path,usecols=['Date','Rainfall','RWL'],parse_dates=['Date'])
 if d.empty or d.Date.isna().any() or d.Date.duplicated().any() or not d.Date.is_monotonic_increasing:raise ValueError('Dates must be ordered, nonempty and unique.')
 if not (d.Date.diff().iloc[1:]==pd.Timedelta(days=1)).all():raise ValueError('Supply all daily dates; no automatic interpolation.')
 f=d[['Rainfall','RWL']].to_numpy(float)
 if not np.isfinite(f).all() or (f[:,0]<0).any():raise ValueError('Rain/RWL must be finite; rainfall cannot be negative.')
 return d
def predict(history,future,output):
 lock=json.loads((ROOT/'results/calibration_lock.json').read_text())
 for n,v in lock.items():
  if hashlib.sha256((ROOT/n).read_bytes()).hexdigest()!=v:raise ValueError('Frozen file changed: '+n)
 c=json.loads((ROOT/'results/calibrated.json').read_text());h=read_input(history);f=read_input(future)
 if h.Date.iloc[0]!=pd.Timestamp('2016-07-01') or h.Date.iloc[-1]<pd.Timestamp('2019-09-11'):raise ValueError('History must start on 2016-07-01 and include the full fitting period.')
 if f.Date.iloc[0]!=h.Date.iloc[-1]+pd.Timedelta(days=1):raise ValueError('Future dates must immediately follow history.')
 d=pd.concat([h,f],ignore_index=True);ctx=Context(d[['Rainfall','RWL']].to_numpy())
 with np.load(ROOT/'results/geometry_matrices.npz') as g:
  for name,now in [('T',ctx.T),('observation',ctx.obs),('kt',ctx.kt),('bulk',ctx.bulk),('length',ctx.length)]:
   if not np.allclose(g[name],now,rtol=1e-12,atol=1e-12):raise ValueError('Active geometry differs from the frozen model.')
 p=forward(c['theta'],ctx)+np.array(c['y0']);p=p[len(h):]
 out=pd.DataFrame({'Date':f.Date.dt.strftime('%Y-%m-%d')})
 for j,point in enumerate(POINTS):out[point+'_predicted_mm']=p[:,j]
 output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);out.to_csv(output,index=False,encoding='utf-8-sig',float_format='%.15g')
 print(f'Exported {len(out)} days; four points; frozen parameters; no observed displacement read.')
 return p
if __name__=='__main__':
 a=argparse.ArgumentParser(description=__doc__);a.add_argument('--history',type=Path,default=ROOT/'inputs/history_forcing.csv');a.add_argument('--future',type=Path,default=ROOT/'inputs/future_forcing.csv');a.add_argument('--output',type=Path,default=ROOT/'output/future_prediction.csv');x=a.parse_args();predict(x.history,x.future,x.output)
