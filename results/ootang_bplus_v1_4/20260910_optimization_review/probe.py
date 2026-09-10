"""Read-only probes around eight saved parameter vectors; no optimizer is called."""
from pathlib import Path
import json
import sys
import platform
import importlib.metadata
import subprocess
import shutil
import time
import numpy as np
import pandas as pd

ROOT=Path.cwd()
sys.path.insert(0,str(ROOT/'code'))
from physics_guided.reference import load,sha,save_json
from physics_guided_diagnostics.core import read_prefix
from physics_guided_diagnostics.run import verify_reference,REFERENCE
from physics_guided_increment.objective import residual,components
from scipy.optimize._lsq.common import CL_scaling_vector

OLD=ROOT/'results/ootang_bplus_v1_3/20260910_increment'
OUT=ROOT/'results/ootang_bplus_v1_4/20260910_optimization_review'
OUT.mkdir(parents=True,exist_ok=False)
protected=json.loads((OLD/'protected_before.json').read_text())
for p in sorted(OLD.rglob('*')):
    if p.is_file():protected[str(p.relative_to(ROOT))]=sha(p)
for name in ['ootang_bplus_increment_plan.v1.3.md','ootang_bplus_increment_results.v1.3.md']:
    p=ROOT/'docs'/name;protected[str(p.relative_to(ROOT))]=sha(p)
scientific=json.loads((OLD/'manifest.json').read_text())['sources']
for path,digest in protected.items():assert sha(ROOT/path)==digest,path
for path,digest in scientific.items():assert sha(ROOT/path)==digest,path
provenance=verify_reference()
ref=load(REFERENCE)
drivers,labels,_=read_prefix(OLD/'input_prefix_792.csv')
counts=0
started=time.monotonic()
records=[]
columns=[]
spectra=[]
scales=[0.1,1.,10.]
for n in [432,612]:
    ctx=ref.Context(drivers.forcing[:n])
    target=labels[:n]-drivers.y0
    for arm in ['C0','C1']:
        for start in ['A','B']:
            if arm=='C0':path=ROOT/f'results/ootang_bplus_v1_2/20260910_diagnostics/fit_{n}/{start}_continued.json'
            else:path=OLD/f'fit_{n}/{start}/stage4.json'
            saved=json.loads(path.read_text())
            x=np.array(saved['theta']);width=ref.HI-ref.LO
            calls_before=counts
            def prediction(z):
                global counts
                counts+=1
                if counts>5000:raise RuntimeError('Fixed diagnostic forward cap exceeded')
                assert (z>=ref.LO).all() and (z<=ref.HI).all()
                return ref.forward(z,ctx)
            p=prediction(x)
            repeat=max(float(abs(prediction(x)-p).max()) for _ in range(2))
            r=residual(p,target,100*n)
            terms=components(p,target,100*n)
            expected=terms['original_objective' if arm=='C0' else 'joint_objective']
            assert abs(expected-saved['objective'])<1e-7
            h=2e-6*np.maximum(1.,abs(x))
            jacobians={}
            central=np.zeros((len(r),54))
            central_available=[]
            for scale in scales:
                values=[]
                for j in range(54):
                    step=h[j]*scale
                    if x[j]+step>=ref.HI[j]:step=-step
                    z=x.copy();z[j]+=step
                    values.append((residual(prediction(z),target,100*n)-r)/step)
                jacobians[scale]=np.column_stack(values)
            for j in range(54):
                if x[j]-h[j]>ref.LO[j] and x[j]+h[j]<ref.HI[j]:
                    z=x.copy();z[j]+=h[j]
                    zz=x.copy();zz[j]-=h[j]
                    central[:,j]=(residual(prediction(z),target,100*n)-residual(prediction(zz),target,100*n))/(2*h[j])
                    central_available.append(j)
            jac=jacobians[1.]
            norms=np.linalg.norm(jac,axis=0)
            lower=(x-ref.LO)/width;upper=(ref.HI-x)/width
            gradients={}
            for scale in scales:
                gradient=np.einsum('ij,i->j',jacobians[scale],r)
                vv,_=CL_scaling_vector(x,gradient,ref.LO,ref.HI)
                gradients[scale]=dict(optimality=float(np.max(abs(gradient*vv))),gradient=gradient)
            # Local column spectra are scale- and step-dependent diagnostics, not identifiability proofs.
            scaled=jac*width
            sv=np.linalg.svd(scaled,compute_uv=False)
            spectra.append(dict(fit_days=n,arm=arm,start=start,normalization='parameter bound width',singular_values=sv.tolist(),
                                relative_rank_1e_8=int(np.sum(sv>sv[0]*1e-8)),relative_rank_1e_10=int(np.sum(sv>sv[0]*1e-10))))
            for j,name in enumerate(ref.NAMES):
                def relative(other):
                    return float(np.linalg.norm(other[:,j]-jac[:,j])/max(np.linalg.norm(other[:,j]),norms[j],1e-12))
                columns.append(dict(fit_days=n,arm=arm,start=start,parameter=name,theta=float(x[j]),
                    distance_to_bound_fraction=float(min(lower[j],upper[j])),
                    base_column_l2=float(norms[j]),scaled_column_l2=float(norms[j]*width[j]),
                    smaller_step_relative_difference=relative(jacobians[.1]),larger_step_relative_difference=relative(jacobians[10.]),
                    central_relative_difference=relative(central) if j in central_available else None,
                    terminal_curvature_fraction=float(np.sum(jac[4*n:4*n+4,j]**2)/max(norms[j]**2,1e-300)),
                    gradient=float(gradients[1.]['gradient'][j])))
            subset=columns[-54:]
            row=dict(fit_days=n,arm=arm,start=start,parameter_source=str(path.relative_to(ROOT)),parameter_source_sha256=sha(path),
                **terms,forward_repeat_max_mm=repeat,recorded_optimality=saved['optimality'],
                joint_optimality_h=gradients[1.]['optimality'],joint_optimality_h_over10=gradients[.1]['optimality'],
                joint_optimality_10h=gradients[10.]['optimality'],
                relative_rank_1e_8=spectra[-1]['relative_rank_1e_8'],
                jacobian_terminal_energy_fraction=float(np.sum(scaled[4*n:4*n+4]**2)/np.sum(scaled**2)),
                median_column_terminal_fraction=float(np.median([a['terminal_curvature_fraction'] for a in subset])),
                near_bound_1percent=[a['parameter'] for a in subset if a['distance_to_bound_fraction']<.01],
                small_step_columns_over_10percent=[a['parameter'] for a in subset if a['smaller_step_relative_difference']>.1],
                central_columns_over_10percent=[a['parameter'] for a in subset if a['central_relative_difference'] is not None and a['central_relative_difference']>.1],
                forward_calls=counts-calls_before)
            if arm=='C1':
                before=json.loads((OLD/f'fit_{n}/{start}/stage3.json').read_text())
                change=abs(x-np.array(before['theta']))/width
                j=int(change.argmax())
                row.update(stage4_objective_reduction_percent=100*(before['objective']-saved['objective'])/before['objective'],
                           stage4_max_parameter=ref.NAMES[j],stage4_max_change_bound_fraction=float(change[j]))
                assert abs(row['joint_optimality_h']-saved['optimality'])<1e-6*max(1,saved['optimality'])
            records.append(row)
            print(n,arm,start,'J1',round(terms['joint_objective'],6),'optimality',round(row['joint_optimality_h'],4),
                  'small_step_sensitive_columns',len(row['small_step_columns_over_10percent']),flush=True)

pd.DataFrame(columns).to_csv(OUT/'jacobian_columns.csv',index=False)
save_json(OUT/'candidates.json',records)
save_json(OUT/'singular_values.json',spectra)
save_json(OUT/'protected_before.json',protected)
shutil.copy2(Path(__file__),OUT/'probe.py')
manifest=dict(type='read_only_saved_parameter_diagnostics',optimization_nfev=0,neural_training=False,
    numerical_inputs='only training prefixes n=432/612 of the first 792 dates',
    forward_cap=5000,forward_calls=counts,seconds=time.monotonic()-started,
    derivative_steps='h=2e-6*max(1,abs(theta)); h/10, h, 10h; central h only inside bounds',
    python=sys.version,platform=platform.platform(),git_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
    sources=scientific,probe_sha256=sha(Path(__file__)),original_reference_provenance=provenance,
    dependencies={k:importlib.metadata.version(k) for k in ['numpy','scipy','pandas']},
    protected_file_count=len(protected),protected_unchanged=all(sha(ROOT/p)==h for p,h in protected.items()),
    scientific_source_unchanged=all(sha(ROOT/p)==h for p,h in scientific.items()))
assert manifest['protected_unchanged'] and manifest['scientific_source_unchanged']
save_json(OUT/'manifest.json',manifest)
print('complete',counts,'forward evaluations, zero optimizer steps',flush=True)
