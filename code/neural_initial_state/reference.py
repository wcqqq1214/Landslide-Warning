"""Original supplied C solver with only a resumable-state/audit interface added."""

import ctypes
import json
import subprocess
import sys

import numpy as np

from physics_guided.reference import load
from rolling_probability.data import SOURCES
from short_horizon.common import ROOT, CALLS, save_json, sha


def original_with_initial(source):
    replacements = [
        ("API int integrate(", "API int integrate_initial("),
        ("double *uout,double *pout,double *rcout,double *reout,double *rrout){",
         "const double *initial,double *uout,double *pout,double *rcout,double *reout,double *rrout,double *audit,int *tape){"),
        ("for(int i=0;i<4;i++)uout[i]=pout[i]=rcout[i]=reout[i]=rrout[i]=0;",
         """for(int i=0;i<4;i++){
 s[i]=initial[i];p[i]=initial[4+i];rr[i]=initial[8+i];rc[i]=initial[12+i];re[i]=initial[16+i];bg[i]=initial[20+i];u[i]=s[i]+bg[i];
 uout[i]=u[i];pout[i]=p[i];rcout[i]=rc[i];reout[i]=re[i];rrout[i]=rr[i];}
 audit[0]=audit[1]=audit[3]=1e300;audit[2]=audit[4]=0;"""),
        ("if(!valid)bad++;prev=chosen;", """if(!valid)bad++;
 if(prev!=chosen)audit[4]++;prev=chosen;tape[(t-1)*sub+step]=chosen;
 double mx=0,mw=0,mp=0;
 for(int i=0;i<4;i++){
  double gap=-rhs[i];for(int j=0;j<4;j++)gap+=A[4*i+j]*dx[j];
  audit[0]=fmin(audit[0],dx[i]);audit[1]=fmin(audit[1],gap);audit[3]=fmin(audit[3],dx[i]/be[i]);
  mx=fmax(mx,fabs(dx[i]));mw=fmax(mw,fabs(gap));mp=fmax(mp,fabs(dx[i]*gap));}
 audit[2]=fmax(audit[2],mp/(1+mx*mw));"""),
    ]
    output = source
    for before, after in replacements:
        if output.count(before) != 1:
            raise ValueError("Original C interface template no longer matches")
        output = output.replace(before, after)
    # Reversing all interface changes must recover every original computation byte.
    restored = output
    for before, after in reversed(replacements):
        restored = restored.replace(after, before)
    if restored != source:
        raise ArithmeticError("Original C body changed")
    return output, [dict(before=a, after=b) for a, b in replacements]


class OriginalResume:
    def __init__(self, spec):
        path = ROOT / spec["output_root"] / "native"
        path.mkdir(parents=True, exist_ok=True)
        original = ROOT / spec["physics"]["original_source"]
        source, changes = original_with_initial(original.read_text())
        generated = path / "original_resume.c"
        if generated.exists() and generated.read_text() != source:
            raise ValueError("Original resume interface changed")
        generated.write_text(source)
        save_json(path / "interface_changes.json", dict(original_sha256=sha(original), generated_sha256=sha(generated), replacements=changes, original_computation_recovered_exactly=True))
        binary_root = ROOT / "runtime/ootang_neural_initial_state_v1"
        binary_root.mkdir(parents=True, exist_ok=True)
        binary = binary_root / ("original_resume_" + sha(generated)[:16] + (".dylib" if sys.platform == "darwin" else ".so"))
        if not binary.exists():
            subprocess.run(["cc", "-O3", "-fPIC", "-shared", str(generated), "-o", str(binary)], check=True, capture_output=True)
        self.lib = ctypes.CDLL(str(binary))
        ptr = np.ctypeslib.ndpointer(dtype=np.float64, flags="C_CONTIGUOUS")
        iptr = np.ctypeslib.ndpointer(dtype=np.int32, flags="C_CONTIGUOUS")
        self.lib.integrate_initial.argtypes = [ctypes.c_int, ctypes.c_int] + [ptr] * 7 + [ctypes.c_double, ptr, ctypes.c_double] + [ptr] * 8 + [iptr]
        self.lib.integrate_initial.restype = ctypes.c_int
        ref = load(ROOT / "runtime/ootang_rolling_v3/reference")
        context = ref.Context(np.asarray([[0., 175.], [0., 175.]]))
        self.parameters = {}
        for q in spec["teacher_prefixes"]:
            if q in SOURCES:
                with np.load(SOURCES[q]) as a:
                    theta = a["theta"].copy()
            else:
                theta = np.asarray(json.loads((ref.ROOT / "results/calibrated.json").read_text())["theta"])
            kc = sum(np.exp(theta[39 + i]) * context.kt[i] for i in range(2))
            ke = 1000 * np.exp(theta[41]) * context.bulk
            self.parameters[q] = [
                *[np.ascontiguousarray(v) for v in [np.exp(theta[8:12])*context.length, np.exp(theta[12:16])*context.length, np.exp(theta[16:20]), np.exp(theta[20:24]), kc]],
                float(np.exp(theta[42])), np.ascontiguousarray(ke), float(np.exp(theta[43])),
            ]

    def replay(self, initial, background, force, elastic, teacher):
        n = len(background) + 1
        f = np.ascontiguousarray(np.vstack([np.zeros(4), force]))
        e = np.ascontiguousarray(np.vstack([np.zeros(4), elastic]))
        b = np.ascontiguousarray(np.vstack([initial[20:], background]))
        u, p, rc, re, rb = [np.empty((n, 4)) for _ in range(5)]
        audit, tape = np.empty(5), np.empty((n - 1, 64), np.int32)
        bad = self.lib.integrate_initial(n, 64, f, e, *self.parameters[int(teacher)], b, np.ascontiguousarray(initial), u, p, rc, re, rb, audit, tape)
        CALLS["original_resume_forward"] = CALLS.get("original_resume_forward", 0) + 1
        if bad or not np.isfinite(u).all():
            raise ArithmeticError("Independent original C continuation failed")
        packed = np.concatenate([u - b, p, rb, rc, re, b], axis=1)
        return packed[1:], audit, tape
