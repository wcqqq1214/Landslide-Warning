"""Instrument a hash-pinned copy of the supplied integrator; never patch its source."""

import ctypes
import hashlib
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile

import numpy as np

from physics_guided.reference import ARCHIVE_PREFIX, ARCHIVE_SHA, ROOT
from physics_guided_forecast_error.artifacts import seal, sha

SOURCE_SHA = "1abe8a83e1566d18b0f89404f018c8ec30d0e9eb8a3ce9add05ca8a65698ff95"
SOURCE_PATH = ARCHIVE_PREFIX + "section2d_v4/physical_solver.c"
OLD_DECLARATION = (
    "double *uout,double *pout,double *rcout,double *reout,double *rrout){"
)
NEW_DECLARATION = (
    "double *uout,double *pout,double *rcout,double *reout,double *rrout,"
    "double *trace_old,double *trace_new,double *trace_loads,"
    "double *trace_lcp,int *trace_masks){"
)


def source_bytes():
    archive = ROOT / "section2d_v4.zip"
    if sha(archive) != ARCHIVE_SHA:
        raise ValueError("Unexpected original archive")
    with ZipFile(archive) as z:
        raw = z.read(SOURCE_PATH)
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
        raise ValueError("Unexpected original C source")
    return raw


def original_function(canonical):
    return canonical[
        canonical.index("API int integrate(") : canonical.index("\nAPI void moisture(")
    ]


def restore_function(traced):
    restored = re.sub(
        r"/\* TRACE_BEGIN \*/.*?/\* TRACE_END \*/\n", "", traced, flags=re.DOTALL
    )
    return restored.replace("API int integrate_trace(", "API int integrate(").replace(
        NEW_DECLARATION, OLD_DECLARATION
    )


def instrument(raw):
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA:
        raise ValueError("Instrumentation only accepts the frozen original source")
    canonical = raw.decode("utf-8").replace("\r\n", "\n")
    original = original_function(canonical)
    traced = original.replace("API int integrate(", "API int integrate_trace(")
    if traced.count(OLD_DECLARATION) != 1:
        raise ValueError("Original integrator declaration is not unique")
    traced = traced.replace(OLD_DECLARATION, NEW_DECLARATION)

    def insert_before(marker, block):
        nonlocal traced
        if traced.count(marker) != 1:
            raise ValueError("Instrumentation marker is not unique")
        traced = traced.replace(
            marker, "/* TRACE_BEGIN */\n" + block + "/* TRACE_END */\n" + marker
        )

    insert_before(
        "   for(int i=0;i<4;i++){\n    newbg[i]=",
        "   int trace_k=(t-1)*sub+step;\n"
        "   for(int i=0;i<4;i++){\n"
        "    trace_old[24*trace_k+i]=s[i];trace_old[24*trace_k+4+i]=p[i];\n"
        "    trace_old[24*trace_k+8+i]=rr[i];trace_old[24*trace_k+12+i]=rc[i];\n"
        "    trace_old[24*trace_k+16+i]=re[i];trace_old[24*trace_k+20+i]=bg[i];\n"
        "    trace_loads[12*trace_k+i]=f[4*t+i];\n"
        "    trace_loads[12*trace_k+4+i]=elastic[4*t+i];\n"
        "   }\n",
    )
    insert_before(
        "   for(int i=0;i<4;i++){\n    p[i]+=dx[i]/be[i];",
        "   trace_masks[2*trace_k]=chosen;trace_masks[2*trace_k+1]=valid;\n"
        "   for(int i=0;i<4;i++){\n"
        "    double trace_g=-rhs[i];\n"
        "    for(int j=0;j<4;j++)trace_g+=A[4*i+j]*dx[j];\n"
        "    trace_lcp[16*trace_k+i]=dx[i];trace_lcp[16*trace_k+4+i]=trace_g;\n"
        "    trace_lcp[16*trace_k+8+i]=rhs[i];trace_lcp[16*trace_k+12+i]=du[i];\n"
        "    trace_loads[12*trace_k+8+i]=newbg[i]-bg[i];\n"
        "   }\n",
    )
    insert_before(
        "  }\n  for(int i=0;i<4;i++){uout[4*t+i]=",
        "   for(int i=0;i<4;i++){\n"
        "    trace_new[24*trace_k+i]=s[i];trace_new[24*trace_k+4+i]=p[i];\n"
        "    trace_new[24*trace_k+8+i]=rr[i];trace_new[24*trace_k+12+i]=rc[i];\n"
        "    trace_new[24*trace_k+16+i]=re[i];trace_new[24*trace_k+20+i]=bg[i];\n"
        "   }\n",
    )
    if restore_function(traced) != original:
        raise ValueError("Instrumentation changed original arithmetic or control flow")
    return canonical + "\n" + traced


def compile_trace(directory, timeout=30):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    raw = source_bytes()
    (directory / "physical_solver.original.c").write_bytes(raw)
    derived = directory / "physical_solver.trace.c"
    derived.write_text(instrument(raw))
    library = directory / (
        "physical_solver.trace.dylib"
        if sys.platform == "darwin"
        else "physical_solver.trace.so"
    )
    command = ["cc", "-O3", "-fPIC", "-shared", str(derived), "-o", str(library)]
    built = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    compiler = subprocess.run(
        ["cc", "--version"], capture_output=True, text=True, check=True, timeout=timeout
    )
    seal(
        directory / "build.json",
        dict(
            original_sha256=sha(directory / "physical_solver.original.c"),
            derived_sha256=sha(derived),
            command=command,
            compiler=compiler.stdout,
            returncode=built.returncode,
            stdout=built.stdout,
            stderr=built.stderr,
            library_sha256=sha(library) if built.returncode == 0 else None,
            restored_function_matches_original=True,
        ),
    )
    built.check_returncode()
    return library


def inputs_from_saved(saved, length):
    theta = saved["theta"]
    return {
        "force": saved["force"],
        "elastic": saved["rain_head"] * theta[[44, 45, 46, 46]],
        "eta": np.exp(theta[8:12]) * length,
        "hardening": np.exp(theta[12:16]) * length,
        "tau_rest": np.exp(theta[16:20]),
        "tau_motion": np.exp(theta[20:24]),
        "kc": saved["kc"],
        "ke": saved["ke"],
        "tau_contact": np.exp(theta[42]),
        "tau_bulk": np.exp(theta[43]),
        "background": saved["background"],
    }


def integrate(library, inputs, traced=True):
    """One native call. The finite scientific budget is enforced by the runner."""
    n = len(inputs["force"])
    if n < 2 or n > 792:
        raise ValueError("Only finite prefixes through day 792 are supported")
    arrays = {}
    for name, shape in {
        "force": (n, 4),
        "elastic": (n, 4),
        "background": (n, 4),
        "eta": (4,),
        "hardening": (4,),
        "tau_rest": (4,),
        "tau_motion": (4,),
        "kc": (4, 4),
        "ke": (4, 4),
    }.items():
        value = np.ascontiguousarray(inputs[name], dtype=np.float64)
        if value.shape != shape or not np.isfinite(value).all():
            raise ValueError(f"Invalid native input: {name}")
        arrays[name] = value
    for name in ("eta", "hardening", "tau_rest", "tau_motion"):
        if (arrays[name] <= 0).any():
            raise ValueError(f"Positive coefficients required: {name}")
    tc, te = float(inputs["tau_contact"]), float(inputs["tau_bulk"])
    if not (0 < tc < float("inf") and 0 < te < float("inf")):
        raise ValueError("Positive finite memory constants required")
    lib = ctypes.CDLL(str(library))
    pointer = np.ctypeslib.ndpointer(dtype=np.float64, flags="C_CONTIGUOUS")
    signature = [ctypes.c_int, ctypes.c_int] + [pointer] * 7
    signature += [ctypes.c_double, pointer, ctypes.c_double] + [pointer] * 6
    daily = {
        key: np.empty((n, 4))
        for key in (
            "coordinates",
            "plastic",
            "contact",
            "bulk_reaction",
            "basal_reaction",
        )
    }
    args = [n, 64] + [
        arrays[k]
        for k in (
            "force",
            "elastic",
            "eta",
            "hardening",
            "tau_rest",
            "tau_motion",
            "kc",
        )
    ]
    args += [tc, arrays["ke"], te, arrays["background"], *daily.values()]
    recorded = {}
    if traced:
        steps = (n - 1) * 64
        recorded = {
            "previous": np.empty((steps, 24)),
            "current": np.empty((steps, 24)),
            "loads": np.empty((steps, 12)),
            "lcp": np.empty((steps, 16)),
            "masks": np.empty((steps, 2), dtype=np.int32),
        }
        signature += [pointer] * 4 + [
            np.ctypeslib.ndpointer(dtype=np.int32, flags="C_CONTIGUOUS")
        ]
        args += list(recorded.values())
    function = lib.integrate_trace if traced else lib.integrate
    function.argtypes, function.restype = signature, ctypes.c_int
    bad = function(*args)
    return {**recorded, **daily, "bad": np.asarray(bad)}
