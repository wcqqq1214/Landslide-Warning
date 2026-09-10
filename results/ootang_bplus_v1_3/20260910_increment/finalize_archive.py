"""Archive completed v1.3 checks; run only after reading both exported figures."""
import json
from pathlib import Path
import shutil
import sys
import hashlib

ROOT = Path.cwd()
OUT = ROOT / 'results/ootang_bplus_v1_3/20260910_increment'
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def read(name):
    return json.loads((OUT/name).read_text())
def write(name, value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')

if '--figures-checked' not in sys.argv:
    raise SystemExit('Run only after visually checking both exported figures')
completion = read('completion.json')
summary = read('summary.json')
assert completion['status'] == summary['execution_status'] == 'complete'
assert summary['new_optimizer_nfev'] <= 13200
assert summary['selected_total'] == 8
manifest = read('manifest.json')
for path, value in read('protected_before.json').items():
    assert digest(ROOT/path) == value, path
for path, value in manifest['sources'].items():
    assert digest(ROOT/path) == digest(OUT/'source_snapshot'/path) == value, path
assert digest(ROOT/manifest['specification']['plan']) == manifest['plan_sha256']
assert digest(ROOT/'config/ootang_bplus_increment.v1_3.json') == manifest['config_sha256']
checks = read('verification_execution.json')
assert checks['test_exit_code'] == checks['ruff_exit_code'] == checks['format_exit_code'] == 0
assert checks['tests_passed'] == 7 and checks['skipped'] == 0
figures = ['increment_432.png','increment_612.png']
assert all((OUT/p).is_file() for p in figures)
shutil.copy2(ROOT/'runtime/ootang_bplus_increment_execution.log', OUT/'execution.log')
test = ROOT/'tests/test_physics_guided_increment.py'
(OUT/'source_snapshot/tests').mkdir(exist_ok=True)
shutil.copy2(test, OUT/'source_snapshot/tests'/test.name)
if Path(__file__).resolve() != (OUT/'finalize_archive.py').resolve():
    shutil.copy2(Path(__file__), OUT/'finalize_archive.py')
import pandas as pd
rows = {p:len(pd.read_csv(OUT/(p+'.csv'))) for p in ['predictions','metrics','increment_metrics','growth','comparison','acceptance','convergence','objective_components']}
audits = [json.loads(p.read_text()) for p in sorted(OUT.glob('fit_*/*_audit.json'))]
assert len(audits) == 12 and all(a['valid'] for a in audits)
write('delivery_verification.json', dict(status='passed',tests_passed=7,skipped=0,row_counts=rows,
    source_and_protected_hashes_rechecked=True,protected_file_count=len(read('protected_before.json')),
    scientific_source_count=len(manifest['sources']),test_sha256=digest(test),
    figures_visually_checked=figures,ruff='passed',format='passed',
    trajectory_count=len(audits),substeps_checked=sum(a['substeps_checked'] for a in audits),
    zero_trajectory_max_error_mm=max(a['zero_trajectory_max_error_mm'] for a in audits),
    prefix_max_error_mm=max(a['prefix_error_mm'] for a in audits),
    max_normalized_complementarity=max(a['max_normalized_complementarity'] for a in audits),
    selected_simultaneously_improved=summary['selected_improved'],selected_total=summary['selected_total'],
    user_acceptance='pending'))
files = {str(p.relative_to(OUT)):{'sha256':digest(p),'bytes':p.stat().st_size}
    for p in sorted(OUT.rglob('*')) if p.is_file() and p.name != 'artifact_manifest.json'}
write('artifact_manifest.json',dict(algorithm='sha256',scope='all files except artifact_manifest.json',files=files))
print(json.dumps(dict(files=len(files),bytes=sum(x['bytes'] for x in files.values()),rows=rows),indent=2))
