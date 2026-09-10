/* Daily B+ operation and exact branchwise reverse mode. State: z,p,rb,rc,rE,b.
 * Equations/order/tolerances follow the supplied physical_solver.c. No clipping.
 * coeff: beta[4], ar[4], kr[4], ac,ae,kc[16],ke[16],A[16],M[16][16]. */
#include <math.h>
#include <string.h>
#define KC (c+14)
#define KE (c+30)
#define AA (c+46)
#define MAP (c+62)
int day_forward(const double *c,const double *old,const double *end,
 const double *force,const double *elastic,int previous,
 double *out,int *masks,double *audit){
 double s[24];memcpy(s,old,24*sizeof(double));
 int prev=previous;
 /* audit: min x, min w, complementarity, min dp tolerance margin, switches */
 audit[0]=audit[1]=audit[3]=1e300;audit[2]=audit[4]=0;
 for(int step=0;step<64;step++){
  double a=(step+1)/64.,bg[4],k[4],rhs[4],x[4],du[4];
  for(int i=0;i<4;i++){
   bg[i]=old[20+i]+a*(end[i]-old[20+i]);
   k[i]=c[i]*(s[4+i]+elastic[i]-s[i])+bg[i]-s[20+i];
  }
  for(int i=0;i<4;i++){
   rhs[i]=force[i]-c[4+i]*s[8+i]-c[12]*s[12+i]-c[13]*s[16+i];
   for(int j=0;j<4;j++)rhs[i]-=(c[12]*KC[4*i+j]+c[13]*KE[4*i+j])*k[j];
  }
  int chosen=-1;
  for(int it=0;it<16;it++){
   int mask=(prev+it)%16,ok=1;
   for(int i=0;i<4;i++){x[i]=0;for(int j=0;j<4;j++)x[i]+=MAP[16*mask+4*i+j]*rhs[j];}
   for(int i=0;i<4;i++){
    if(!(mask&(1<<i))&&x[i]<-1e-8)ok=0;
    if(mask&(1<<i)){
     double w=-rhs[i];for(int j=0;j<4;j++)w+=AA[4*i+j]*x[j];
     if(w < -1e-7)ok=0;
    }
   }
   if(ok){chosen=mask;break;}
  }
  if(chosen<0)return 1;
  masks[step]=chosen; if(chosen!=prev)audit[4]++;prev=chosen;
  double mx=0,mw=0,mp=0;
  for(int i=0;i<4;i++){
   double w=-rhs[i];for(int j=0;j<4;j++)w+=AA[4*i+j]*x[j];
   audit[0]=fmin(audit[0],x[i]);audit[1]=fmin(audit[1],w);
   audit[3]=fmin(audit[3],(x[i]+1e-8)/c[i]);
   mx=fmax(mx,fabs(x[i]));mw=fmax(mw,fabs(w));mp=fmax(mp,fabs(x[i]*w));
   du[i]=k[i]+x[i];
  }
  audit[2]=fmax(audit[2],mp/(1+mx*mw));
  for(int i=0;i<4;i++){
   s[4+i]+=x[i]/c[i];s[i]+=c[i]*(s[4+i]+elastic[i]-s[i]);
   s[8+i]=c[4+i]*(s[8+i]+c[8+i]*x[i]/c[i]);
   double v=0,e=0;
   for(int j=0;j<4;j++){v+=KC[4*i+j]*du[j];e+=KE[4*i+j]*du[j];}
   s[12+i]=c[12]*(s[12+i]+v);s[16+i]=c[13]*(s[16+i]+e);s[20+i]=bg[i];
  }
 }
 memcpy(out,s,24*sizeof(double));return 0;
}
void day_backward(const double *c,const int *masks,const double *grad,double *g_old,double *g_end){
 double g[24],gbegin[4]={0};memcpy(g,grad,24*sizeof(double));memset(g_end,0,4*sizeof(double));
 for(int step=63;step>=0;step--){
  double v[24]={0},gx[4],gk[4],grhs[4],a=(step+1)/64.;
  for(int i=0;i<4;i++){
   v[i]=(1-c[i])*g[i];v[4+i]=g[4+i]+c[i]*g[i];
   v[8+i]=c[4+i]*g[8+i];v[12+i]=c[12]*g[12+i];v[16+i]=c[13]*g[16+i];
   double du=0;
   for(int j=0;j<4;j++)du+=c[12]*KC[4*j+i]*g[12+j]+c[13]*KE[4*j+i]*g[16+j];
   gk[i]=du;gx[i]=du+g[4+i]/c[i]+g[i]+c[4+i]*c[8+i]/c[i]*g[8+i];
  }
  for(int i=0;i<4;i++){
   grhs[i]=0;for(int j=0;j<4;j++)grhs[i]+=MAP[16*masks[step]+4*j+i]*gx[j];
  }
  for(int i=0;i<4;i++){
   for(int j=0;j<4;j++)gk[i]-=(c[12]*KC[4*j+i]+c[13]*KE[4*j+i])*grhs[j];
   v[8+i]-=c[4+i]*grhs[i];v[12+i]-=c[12]*grhs[i];v[16+i]-=c[13]*grhs[i];
   v[i]-=c[i]*gk[i];v[4+i]+=c[i]*gk[i];v[20+i]=-gk[i];
   gbegin[i]+=(1-a)*(g[20+i]+gk[i]);g_end[i]+=a*(g[20+i]+gk[i]);
  }
  memcpy(g,v,24*sizeof(double));
 }
 for(int i=0;i<4;i++)g[20+i]+=gbegin[i];
 memcpy(g_old,g,24*sizeof(double));
}
