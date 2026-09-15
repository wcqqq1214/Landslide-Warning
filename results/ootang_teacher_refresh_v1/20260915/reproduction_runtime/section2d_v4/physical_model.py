"""Final B+: four-coordinate plane-strain Ritz field with moisture-dependent creep."""
import ctypes,json,sys
from pathlib import Path
import numpy as np,pandas as pd
from scipy.signal import lfilter
from threadpoolctl import threadpool_limits
from geometry import build,POINTS,basis
threadpool_limits(1);sys.stdout.reconfigure(encoding='utf-8')
ROOT=Path(__file__).resolve().parent;PARENT=ROOT.parent
PTR=np.ctypeslib.ndpointer(dtype=np.float64,flags='C_CONTIGUOUS')
BLOCK=['O3','O2','O1_up','O1_down']
NAMES=[f'{p}_{b}' for p in ['log_tauP','margin','log_eta','log_hardening','log_tauRest','log_tauMotion','creep','initial_relaxation','log_tauKelvin'] for b in BLOCK]+['log_tauR','reservoir_gradient_gain','reservoir_support_gain','log_k32','log_k21','log_E_MPa','log_tauContact','log_tauBulk']
LO=np.r_[np.log([.5]*4),[0]*4,np.log([.0005]*4),np.log([1e-5]*4),np.log([30]*4),np.log([.5]*4),[0]*4,[-80]*4,np.log([2]*4),np.log(1),0,0,np.log([1e-6,1e-6,.01,2,2])]
HI=np.r_[np.log([90]*4),[60]*4,np.log([30]*4),np.log([1]*4),np.log([20000]*4),np.log([180]*4),[.2]*4,[80]*4,np.log([240]*4),np.log(180),5,5,np.log([.5,.5,325,5000,5000])]
NAMES+=['rain_compliance_O3','rain_compliance_O2','rain_compliance_O1'];LO=np.r_[LO,0,0,0];HI=np.r_[HI,15,15,15]
def memory(x,tau,x0=0):
 a=np.exp(-1/tau);return np.r_[x0,lfilter([1-a],[1,-a],np.asarray(x)[1:],zi=[a*x0])[0]]
class Context:
 def __init__(self,forcing,step=25):
  self.P,self.R=np.asarray(forcing,float).T
  self.a,self.meta,self.slip=build(step)
  self.T=np.zeros((9,4));self.T[0,0]=self.T[1,1]=1
  b=self.meta['bodies'][2];t=np.array(b['tangent']);cen=np.array(b['centroid']);le=b['length_m']
  end=np.array([[1100,np.interp(1100,self.a['ground'][:,0],self.a['ground'][:,1])],[2070,np.interp(2070,self.a['ground'][:,0],self.a['ground'][:,1])]])
  xi=(end-cen)@t/le;dx=xi[1]-xi[0]
  self.T[2,2:]=[xi[1]/dx,-xi[0]/dx];self.T[5,2:]=[-1/dx,1/dx]
  self.obs=self.a['observation']@self.T
  self.rain=self.a['rain']@self.T;self.length=self.rain.sum(axis=0)
  self.kt=np.array([self.T.T@(kt+3*kn)@self.T for kt,kn in zip(self.a['Kt'],self.a['Kn'])])
  self.bulk=self.T.T@self.a['bulk_unit']@self.T
  tab=np.load(ROOT/'results/reservoir_force_lookup_final.npz');self.levels=tab['levels'];self.pore=tab['pore']@self.T;self.ext=tab['external']@self.T
  self.support=self.lookup(self.R,self.ext)-self.lookup([175],self.ext)
 def lookup(self,x,table):
  if np.min(x)<130 or np.max(x)>190:raise ValueError('RWL outside 130-190 m.')
  return np.column_stack([np.interp(x,self.levels,table[:,j]) for j in range(4)])

OLD_NAMES=NAMES.copy();OLD_LO=LO.copy();OLD_HI=HI.copy()
NAMES=OLD_NAMES+['wet_creep_O3','wet_creep_O2','wet_creep_O1_up','wet_creep_O1_down','log_tauMoisture_O3','log_tauMoisture_O2','log_tauMoisture_O1']
LO=np.r_[OLD_LO,0,0,0,0,np.log([3,3,3])]
HI=np.r_[OLD_HI,2,2,2,2,np.log([180,180,180])]
LIB=ctypes.CDLL(str(ROOT/'physical_solver.dll'))
LIB.integrate.argtypes=[ctypes.c_int,ctypes.c_int,PTR,PTR,PTR,PTR,PTR,PTR,PTR,ctypes.c_double,PTR,ctypes.c_double,PTR,PTR,PTR,PTR,PTR,PTR]
LIB.integrate.restype=ctypes.c_int
LIB.moisture.argtypes=[ctypes.c_int,PTR,PTR,ctypes.c_double,ctypes.c_double,PTR]
CAPACITY=40.;MOISTURE0=.5
def forward(theta,ctx,substeps=64,interact=True,states=False):
 theta=np.asarray(theta);th=theta[:47]
 n=len(ctx.P);le=ctx.length
 head=np.column_stack([.5*memory(memory(ctx.P,np.exp(th[j])),.5)/100 for j in range(4)])
 H=memory(ctx.R,np.exp(th[36]),ctx.R[0])
 hydro=ctx.lookup(H,ctx.pore)-ctx.lookup(ctx.R,ctx.pore)
 force=(head*(9.81*np.tan(np.deg2rad([9.5,9.3,9.3,9.3])))-th[4:8])*le+th[37]*hydro+th[38]*ctx.support
 kc=sum(np.exp(th[39+i])*ctx.kt[i] for i in range(2)) if interact else np.zeros((4,4));ke=1000*np.exp(th[41])*ctx.bulk
 moist=np.empty((n,4));taus=np.ascontiguousarray(np.exp(theta[[51,52,53,53]]))
 LIB.moisture(n,np.ascontiguousarray(ctx.P),taus,CAPACITY,MOISTURE0,moist)
 creep=theta[24:28]+theta[47:51]*moist
 tt=np.arange(n)[:,None]
 bg=np.vstack([np.zeros(4),np.cumsum(creep[1:],axis=0)])+th[28:32]*(1-np.exp(-tt/np.exp(th[32:36])))
 args=[np.ascontiguousarray(v,float) for v in [force,head*th[[44,45,46,46]],np.exp(th[8:12])*le,np.exp(th[12:16])*le,np.exp(th[16:20]),np.exp(th[20:24]),kc,ke,bg]]
 u=np.empty((n,4));p=np.empty_like(u);rc=np.empty_like(u);re=np.empty_like(u);rr=np.empty_like(u)
 bad=LIB.integrate(n,substeps,*args[:7],float(np.exp(th[42])),args[7],float(np.exp(th[43])),args[8],u,p,rc,re,rr)
 if bad:raise ArithmeticError(f'Complementarity failed {bad}')
 pred=u@ctx.obs.T
 if not np.isfinite(pred).all():raise ArithmeticError('Nonfinite mechanics')
 if states:return pred,dict(coordinates=u,plastic=p,contact=rc,bulk_reaction=re,basal_reaction=rr,rain_head=head,reservoir_head=H,force=force,kc=kc,ke=ke,moisture=moist,background=bg,background_rate=creep)
 return pred
