#include <math.h>
#include <string.h>
#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif
static void inverse(int n,double *a,double *v){
 memset(v,0,16*sizeof(double));for(int i=0;i<n;i++)v[i*n+i]=1;
 for(int k=0;k<n;k++){
  int p=k;for(int i=k+1;i<n;i++)if(fabs(a[i*n+k])>fabs(a[p*n+k]))p=i;
  for(int j=0;j<n;j++){double z=a[k*n+j];a[k*n+j]=a[p*n+j];a[p*n+j]=z;z=v[k*n+j];v[k*n+j]=v[p*n+j];v[p*n+j]=z;}
  double z=a[k*n+k];for(int j=0;j<n;j++){a[k*n+j]/=z;v[k*n+j]/=z;}
  for(int i=0;i<n;i++)if(i!=k){z=a[i*n+k];for(int j=0;j<n;j++){a[i*n+j]-=z*a[k*n+j];v[i*n+j]-=z*v[k*n+j];}}
 }
}
API int integrate_initial(int nt,int sub,const double *f,const double *elastic,const double *eta,const double *kr,const double *tr,
 const double *tm,const double *kc,double tc,const double *ke,double te,
 const double *background,
 const double *initial,double *uout,double *pout,double *rcout,double *reout,double *rrout,double *audit,int *tape){
 double dt=1./sub,ac=1/(1+dt/tc),ae=1/(1+dt/te),ar[4],be[4],A[16],M[16][16];
 double u[4]={0},s[4]={0},p[4]={0},rc[4]={0},re[4]={0},rr[4]={0},bg[4]={0};
 for(int i=0;i<4;i++){ar[i]=1/(1+dt/tr[i]);be[i]=dt/(tm[i]+dt);}
 for(int i=0;i<4;i++)for(int j=0;j<4;j++)A[4*i+j]=ac*kc[4*i+j]+ae*ke[4*i+j]+(i==j?(eta[i]/dt+ar[i]*kr[i])/be[i]:0);
 for(int mask=0;mask<16;mask++){
  int ids[4],nf=0;for(int i=0;i<4;i++)if(!(mask&(1<<i)))ids[nf++]=i;
  double b[16]={0},inv[16];for(int i=0;i<nf;i++)for(int j=0;j<nf;j++)b[i*nf+j]=A[4*ids[i]+ids[j]];
  inverse(nf,b,inv);memset(M[mask],0,sizeof(M[mask]));
  for(int i=0;i<nf;i++)for(int j=0;j<nf;j++)M[mask][4*ids[i]+ids[j]]=inv[i*nf+j];
 }
 int prev=0,bad=0;
 for(int i=0;i<4;i++){
 s[i]=initial[i];p[i]=initial[4+i];rr[i]=initial[8+i];rc[i]=initial[12+i];re[i]=initial[16+i];bg[i]=initial[20+i];u[i]=s[i]+bg[i];
 uout[i]=u[i];pout[i]=p[i];rcout[i]=rc[i];reout[i]=re[i];rrout[i]=rr[i];}
 audit[0]=audit[1]=audit[3]=1e300;audit[2]=audit[4]=0;
 for(int t=1;t<nt;t++){
  for(int step=0;step<sub;step++){
   double now=t-1+(step+1)*dt,k0[4],newbg[4],rhs[4],dx[4],du[4];
   for(int i=0;i<4;i++){
    newbg[i]=background[4*(t-1)+i]+(step+1)*dt*(background[4*t+i]-background[4*(t-1)+i]);
    k0[i]=be[i]*(p[i]+elastic[4*t+i]-s[i])+newbg[i]-bg[i];
   }
   for(int i=0;i<4;i++){
    rhs[i]=f[4*t+i]-ar[i]*rr[i]-ac*rc[i]-ae*re[i];
    for(int j=0;j<4;j++)rhs[i]-=(ac*kc[4*i+j]+ae*ke[4*i+j])*k0[j];
   }
   int chosen=0,valid=0;
   for(int it=0;it<16;it++){
    int mask=(prev+it)%16,ok=1;
    for(int i=0;i<4;i++){dx[i]=0;for(int j=0;j<4;j++)dx[i]+=M[mask][4*i+j]*rhs[j];}
    for(int i=0;i<4;i++){
     if(!(mask&(1<<i))&&dx[i]<-1e-8)ok=0;
     if(mask&(1<<i)){double g=-rhs[i];for(int j=0;j<4;j++)g+=A[4*i+j]*dx[j];if(g < -1e-7)ok=0;}
    }
    if(ok){valid=1;chosen=mask;break;}
   }
   if(!valid)bad++;
 if(prev!=chosen)audit[4]++;prev=chosen;tape[(t-1)*sub+step]=chosen;
 double mx=0,mw=0,mp=0;
 for(int i=0;i<4;i++){
  double gap=-rhs[i];for(int j=0;j<4;j++)gap+=A[4*i+j]*dx[j];
  audit[0]=fmin(audit[0],dx[i]);audit[1]=fmin(audit[1],gap);audit[3]=fmin(audit[3],dx[i]/be[i]);
  mx=fmax(mx,fabs(dx[i]));mw=fmax(mw,fabs(gap));mp=fmax(mp,fabs(dx[i]*gap));}
 audit[2]=fmax(audit[2],mp/(1+mx*mw));
   for(int i=0;i<4;i++)du[i]=k0[i]+dx[i];
   for(int i=0;i<4;i++){
    p[i]+=dx[i]/be[i];s[i]+=be[i]*(p[i]+elastic[4*t+i]-s[i]);u[i]=s[i]+newbg[i];bg[i]=newbg[i];
    rr[i]=ar[i]*(rr[i]+kr[i]*dx[i]/be[i]);
    double c=0,e=0;for(int j=0;j<4;j++){c+=kc[4*i+j]*du[j];e+=ke[4*i+j]*du[j];}
    rc[i]=ac*(rc[i]+c);re[i]=ae*(re[i]+e);
   }
  }
  for(int i=0;i<4;i++){uout[4*t+i]=u[i];pout[4*t+i]=p[i];rcout[4*t+i]=rc[i];reout[4*t+i]=re[i];rrout[4*t+i]=rr[i];}
 }
 return bad;
}

API void moisture(int nt,const double *rain,const double *tau,double capacity,double initial,double *out){
 for(int j=0;j<4;j++)out[j]=initial;
 for(int t=1;t<nt;t++)for(int j=0;j<4;j++){
  double rate=rain[t]/capacity+1/tau[j];double eq=(rain[t]/capacity)/rate;
  out[4*t+j]=eq+(out[4*(t-1)+j]-eq)*exp(-rate);
 }
}
