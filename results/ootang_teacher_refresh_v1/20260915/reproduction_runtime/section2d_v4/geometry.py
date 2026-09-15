"""Plane-strain Ritz assembly on the digitized, nested A-A' geological section."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parent
PARENT=ROOT.parent
ORDER=['O3','O2','O1']
POINTS=['ATU1','ATU5','MJ3','MJ1']
PX=np.array([295.,1000.,1250.,1680.]);PB=[0,1,2,2]
NU=.30

def interp(a,x): return np.interp(x,a[:,0],a[:,1])

def build(step=25.):
    ground=pd.read_csv(PARENT/'results/geometry_ground_interpretation.csv').to_numpy(dtype=float)
    slip=[pd.read_csv(PARENT/f'results/geometry_{b}_interpretation.csv').to_numpy(dtype=float) for b in ORDER]
    bodies=[];bulk=np.zeros((9,9));gravity=np.zeros(9);rain=np.zeros((3,9));reserve=np.zeros((3,9))
    mesh=[];triangles=[];labels=[];offset=0
    D=np.array([[1-NU,NU,0],[NU,1-NU,0],[0,0,(1-2*NU)/2]])/((1+NU)*(1-2*NU))
    for i,base in enumerate(slip):
        knots=np.r_[ground[:,0],slip[i-1][:,0] if i else []]
        knots=knots[(knots>=base[0,0])&(knots<=base[-1,0])]
        xs=np.unique(np.r_[np.arange(base[0,0],base[-1,0],step),base[:,0],knots,base[-1,0]])
        lo=interp(base,xs);hi=interp(ground,xs)
        if i>0:
            above=slip[i-1];mask=(xs>=above[0,0])&(xs<=above[-1,0])
            hi[mask]=np.minimum(hi[mask],interp(above,xs[mask]))
        hi=np.maximum(hi,lo)
        polygon=np.vstack([np.c_[xs,lo],np.c_[xs[::-1],hi[::-1]]]);nxt=np.roll(polygon,-1,axis=0)
        cross=polygon[:,0]*nxt[:,1]-nxt[:,0]*polygon[:,1]
        area=float(cross.sum()/2)
        cx=float(((polygon[:,0]+nxt[:,0])*cross).sum()/(6*area))
        cz=float(((polygon[:,1]+nxt[:,1])*cross).sum()/(6*area))
        tangent=np.array([base[-1,0]-base[0,0],base[-1,1]-base[0,1]])
        length=float(np.linalg.norm(tangent));tangent/=length;normal=np.array([-tangent[1],tangent[0]])
        info={'name':ORDER[i],'index':i,'area_m2':area,'centroid':[cx,cz], 'length_m':length,'tangent':tangent.tolist(),'normal':normal.tolist(),'x':xs.tolist(),'lower':lo.tolist(),'upper':hi.tolist()}
        bodies.append(info)
        # Triangular integration of nine spatial modes; mesh nodes are not independent DOFs.
        nodes=np.array([[x,z] for x,zl,zh in zip(xs,lo,hi) for z in np.linspace(zl,zh,5)])
        elems=[]
        for k in range(len(xs)-1):
            for m in range(4):
                a=k*5+m;b=(k+1)*5+m
                elems.extend([[a,b,b+1],[a,b+1,a+1]])
        for ids in elems:
            xy=nodes[ids];A=abs(np.linalg.det(np.column_stack([np.ones(3),xy])))/2
            if A<1e-6:continue
            grad=np.linalg.inv(np.column_stack([np.ones(3),xy]))[1:,:]
            Be=np.zeros((3,9));Phi=np.vstack([basis(info,x,z) for x,z in xy])
            for k in range(3):
                dx,dz=grad[:,k];Be+=np.array([[dx,0],[0,dz],[dz,dx]])@Phi[2*k:2*k+2]
            bulk+=A*Be.T@D@Be/1000 # multiply by E[kPa] => kN/mm, unit out-of-plane width.
            gravity+=A*np.mean(Phi.reshape(3,2,9),axis=0).T@np.array([0.,-21.58])
            triangles.append([offset+n for n in ids]);labels.append(i)
        mesh.extend(nodes.tolist());offset+=len(nodes)
        for a,b in zip(base[:-1],base[1:]):
            vec=b-a;L=np.linalg.norm(vec);tt=vec/L
            for xi in [.2113248654,.7886751346]:
                x,z=a+xi*vec;B=basis(info,x,z)
                rain[i]+=L/2*(B.T@tt)
        reserve[i]=rain[i]
    interfaces=[]
    for upper,lower in [(0,1),(1,2)]:
        a=slip[upper];b=slip[lower]
        x0=max(a[0,0],b[0,0]);x1=min(a[-1,0],b[-1,0])
        xx=np.unique(np.r_[x0,a[(a[:,0]>x0)&(a[:,0]<x1),0],x1])
        line=np.c_[xx,interp(a,xx)];Kt=np.zeros((9,9));Kn=np.zeros((9,9))
        for aa,bb in zip(line[:-1],line[1:]):
            v=bb-aa;L=np.linalg.norm(v);t=v/L;n=np.array([-t[1],t[0]])
            for xi in [.2113248654,.7886751346]:
                x,z=aa+xi*v;G=basis(bodies[upper],x,z)-basis(bodies[lower],x,z)
                bt=t@G;bn=n@G;Kt+=L/2*np.outer(bt,bt);Kn+=L/2*np.outer(bn,bn)
        interfaces.append({'upper':upper,'lower':lower,'line':line.tolist(),'length_m':float(np.linalg.norm(np.diff(line,axis=0),axis=1).sum()),'Kt':Kt,'Kn':Kn})
    observation=np.array([np.array(bodies[i]['tangent'])@basis(bodies[i],x,interp(ground,x)) for x,i in zip(PX,PB)])
    arrays=dict(bulk_unit=bulk,gravity=gravity,rain=rain,reserve=reserve,observation=observation,
                nodes=np.array(mesh),triangles=np.array(triangles),triangle_body=np.array(labels),ground=ground,
                Kt=np.array([g['Kt'] for g in interfaces]),Kn=np.array([g['Kn'] for g in interfaces]))
    metadata={'bodies':bodies,'interfaces':[{k:v for k,v in g.items() if k not in ['Kt','Kn']} for g in interfaces],
              'points':dict(zip(POINTS,PX.tolist())),'nu':NU,'integration_spacing_m':step,
              'geometry_provenance':'Manual digitization of Wang2025 Figure4d original A-A section; red dashed potential surfaces; no geodetic accuracy claimed.',
              'mechanical_discretization':'2D plane-strain virtual-work Ritz reduction, translation plus linear and quadratic deformation in each body, integrated with linear triangles. Not a full unrestricted finite-element solution.'}
    return arrays,metadata,slip

def basis(body,x,z):
    i=body['index'];t=np.array(body['tangent']);n=np.array(body['normal'])
    pos=np.array([x,z])-np.array(body['centroid']);L=body['length_m']
    xi=t@pos/L;zeta=n@pos/L;out=np.zeros((2,9));out[:,i]=t
    out[:,3+i]=xi*t-(NU/(1-NU))*zeta*n
    out[:,6+i]=xi*xi*t-(NU/(1-NU))*2*xi*zeta*n
    return out

def reservoir_force(level,metadata,slip,ground,kind):
    """Unit-width incremental boundary/pore force; level is elevation in metres."""
    out=np.zeros(9)
    for i,b in enumerate(metadata['bodies']):
        # Direct reservoir hydraulic action is confined to the lower geological body O1.
        if i!=2:continue
        line=slip[i] if kind=='pore' else ground[(ground[:,0]>=1100)&(ground[:,0]<=2070)]
        for aa,bb in zip(line[:-1],line[1:]):
            v=bb-aa;L=np.linalg.norm(v);t=v/L;n=np.array([-t[1],t[0]])
            for xi in [.1127016654,.5,.8872983346]:
                weight={.1127016654:5/18,.5:4/9,.8872983346:5/18}[xi]
                x,z=aa+xi*v;pressure=9.81*max(level-z,0)
                traction=pressure*np.tan(np.deg2rad(9.3))*t if kind=='pore' else -pressure*n
                out+=L*weight*(basis(b,x,z).T@traction)
    return out

if __name__=='__main__':
    a,m,s=build();np.savez_compressed(ROOT/'results/geometry_matrices.npz',**a)
    (ROOT/'results/geometry.json').write_text(json.dumps(m,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps({b['name']:{k:b[k] for k in ['area_m2','length_m','tangent']} for b in m['bodies']},indent=2))
    print('triangles',len(a['triangles']),'bulk unit diagonal',np.diag(a['bulk_unit']))
