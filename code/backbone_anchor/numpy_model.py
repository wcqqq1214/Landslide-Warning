"""Independent saved-weight replay; no production forward or attention calls."""
import numpy as np
from scipy.special import ndtr
from overnight_graph.audit import numpy_forward as gru_forward


def numpy_forward(saved, tensors, baseline):
    if saved['arm'].startswith('G'):
        return gru_forward(saved, tensors, baseline)
    w={k:v.detach().numpy() for k,v in saved['state_dict'].items()}
    hist,lengths,future,distance=tensors
    assert len(lengths)==1
    def linear(x,name):
        return np.einsum('...i,oi->...o',x,w[name+'.weight'],optimize=False)+w[name+'.bias']
    def norm(x,name):
        centered=x-x.mean(-1,keepdims=True)
        return centered/np.sqrt(np.mean(centered**2,axis=-1,keepdims=True)+1e-5)*w[name+'.weight']+w[name+'.bias']
    length=int(lengths[0]);sequence=hist[0,:,:length]
    phase=np.arange(length)[:,None]*np.exp(-np.log(10000.)*np.arange(0,8,2)[None,:]/8)
    pe=np.empty((length,8));pe[:,::2]=np.sin(phase);pe[:,1::2]=np.cos(phase)
    x=linear(sequence,'transformer.project')+pe
    packed=linear(norm(x,'transformer.norm1'),'transformer.qkv')
    query,key,value=np.split(packed,3,axis=-1)
    query=query[:,-1].reshape(4,2,4)
    key,value=[z.reshape(4,length,2,4).transpose(0,2,1,3) for z in (key,value)]
    logits=np.einsum('phd,phtd->pht',query,key,optimize=False)/np.sqrt(4.)
    weights=np.exp(logits-logits.max(-1,keepdims=True));weights/=weights.sum(-1,keepdims=True)
    attended=np.einsum('pht,phtd->phd',weights,value,optimize=False).reshape(4,8)
    h=x[:,-1]+linear(attended,'transformer.out')
    f=linear(norm(h,'transformer.norm2'),'transformer.ff1')
    h=h+linear(f*ndtr(f),'transformer.ff2')
    context=np.tanh(linear(np.einsum('pq,qk->pk',w['adjacency'],h,optimize=False),'message'))
    q=future.shape[1]
    z=np.concatenate([np.broadcast_to(h,(q,4,8)),np.broadcast_to(context,(q,4,8)),future[0],
                      np.broadcast_to(sequence[:,-1,[11,12]],(q,4,2)),
                      np.broadcast_to(distance[0,:,None],(q,4,2)),np.broadcast_to(w['point.weight'],(q,4,4))],axis=-1)
    z=linear(z,'decode');components=linear(z*ndtr(z),'head')*np.asarray(saved['scaling']['unit'])[None,:,None]
    return baseline+components.sum(-1),components
