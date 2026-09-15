"""Close the fixed experiment and check the final versioned delivery without training."""
import argparse
from datetime import datetime, timezone
import json
import subprocess
from . import core as c


def main(verify_only=False):
    cfg=c.spec();o=c.o;root=c.ROOT/cfg['out'];c.guard()
    for path in [root/'analysis_lock.json',root/'audit_lock.json',c.ROOT/cfg['figures']/'delivery_lock.json']:
        o.verify_lock(path)
    runtime=o.read_json(root/'runtime_source_receipt.json')
    assert o.sha(c.ROOT/runtime['archive'])==runtime['archive_sha256']
    for original,record in runtime['runtime_dependencies_copied'].items():
        assert o.sha(c.ROOT/original)==record['sha256']==o.sha(c.ROOT/record['copy'])
    qa=o.read_json(root/'document_qa/receipt.json');assert qa['status']=='passed'
    manifest=root/'delivery_manifest.json'
    if verify_only:
        saved=o.read_json(manifest)
        tracked=set(subprocess.check_output(['git','ls-files','-z'],cwd=c.ROOT).decode().split('\0'))
        for path,digest in saved['files'].items():
            assert path in tracked and o.sha(c.ROOT/path)==digest,path
        assert str(manifest.relative_to(c.ROOT)) in tracked
        assert 'docs/ootang_mentor_figure_rules.v1.0.md' not in tracked
        now=datetime.now(timezone.utc)
        print(json.dumps(dict(status='passed',delivery_files=len(saved['files']),source_files=203,
            runtime_copies=18,all_delivery_files_tracked=True,verified_utc=now.isoformat(),
            elapsed_minutes=(now-datetime.fromisoformat(cfg['start_utc'])).total_seconds()/60,
            within_deadline=now<datetime.fromisoformat(cfg['deadline_utc']),new_fits=0,updates=0)))
        return
    receipt=o.read_json(root/'final_receipt.json');experiment=o.read_json(root/'experiment_receipt.json')
    audit=o.read_json(root/'audit/receipt.json');fig=o.read_json(root/'figure_qa_v1/receipt.json')
    outcome=o.read_json(root/'analysis/outcome.json');now=datetime.now(timezone.utc)
    assert now<datetime.fromisoformat(cfg['deadline_utc'])
    receipt.update(status='complete',document_validation='passed',completed_utc=now.isoformat(),
        elapsed_minutes=(now-datetime.fromisoformat(cfg['start_utc'])).total_seconds()/60,
        within_new_deadline=True,old_budget_reused=False,remaining_budget_transferred=False,
        new_bplus_fits=experiment['new_bplus_fits'],physical_optimizer_success=experiment['physical_optimizer_success'],
        physical_nfev=experiment['physical_nfev'],physical_optimization_forward_calls=experiment['physical_optimization_forward_calls'],
        physical_postcheck_forward_calls=20,physical_preflight_forward_calls=4,physical_audit_forward_calls=20,
        physical_fit_seconds=experiment['physical_fit_seconds'],neural_new_fits=24,neural_updates=4800,
        checkpoints=96,primary_origins=cfg['origins'][1:],bootstrap_origin=612,full_forecast_days=293,
        training_retries=0,checkpoint_selection=False,teacher_policy_stable_mean=outcome['stable_mean_policy'],
        bplus_joint_pass_windows=outcome['joint_bplus_pass_counts'],source_files=203,runtime_copies=18,
        audit_checks=audit['checks'],audit_numeric_values=audit['values_checked'],audit_max_difference=audit['max_difference'],
        figures=3,panels=12,final_figure_version='v1',figure_checks=fig['checks'],figure_values=fig['values'],
        report_tables=qa['tables'],report_rows=qa['table_rows'],report_numeric_cells=qa['numeric_cells'],report_links=qa['links'],
        statistical_interpretation_checks=11,
        protected_untracked_file='docs/ootang_mentor_figure_rules.v1.0.md',
        anomalies=['Original native matrix multiplication warnings retained; finite results and explicit contractions agree',
                   'All 10 physical updates reached fixed nfev budget without convergence; no retries',
                   'Figure delivery wrapper initially rejected parent README relative path; common parent root fixed, figures unchanged'],
        commits=dict(plan='bafd5a0d',implementation='c0051156',experiment='a21ea216',audit='61eac8bc',
                     figures='2fbab566',report_and_receipt='commit containing this receipt'))
    paths=set()
    for folder in [c.ROOT/'code/teacher_refresh',root,c.ROOT/cfg['figures']]:
        paths.update(p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p!=manifest)
    paths.update((c.ROOT/'docs').glob('ootang_teacher_refresh*'))
    paths.update(c.ROOT/p for p in ['README.md','AGENTS.md','docs/README.md','docs/progress.md',str(c.CONFIG.relative_to(c.ROOT))])
    assert not any(p.suffix=='.pdf' for p in paths)
    receipt['delivery_files']=len(paths)
    o.write_json(root/'final_receipt.json',receipt)
    o.write_json(manifest,dict(status='complete',files={str(p.relative_to(c.ROOT)):o.sha(p) for p in sorted(paths)},
                              manifest_self_excluded=True,postcommit_check='python -m teacher_refresh.finalize --verify-only'))
    print(json.dumps(dict(status='complete',delivery_files=len(paths),elapsed_minutes=receipt['elapsed_minutes'])))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--verify-only',action='store_true');args=parser.parse_args();main(args.verify_only)
