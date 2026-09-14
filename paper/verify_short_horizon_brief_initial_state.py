"""Verify the localized PDF update against saved metrics, without model imports."""

import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pandas as pd
import pymupdf


ROOT = Path(__file__).resolve().parents[1]
BASE = 'e898ad5'
PRESENTATION_INPUTS = {
    'paper/figures/short_horizon_zh/horizon_comparison.pdf',
    'paper/figures/short_horizon_zh/sources.json',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_file(name):
    return subprocess.check_output(['git', 'show', BASE + ':' + name], cwd=ROOT)


def ordered(text, values):
    cursor = 0
    for value in values:
        pattern = re.compile(r'(?<![\d.])' + re.escape(value) + r'(?![\d.])')
        found = pattern.search(text, cursor)
        if found is None:
            raise AssertionError('Missing or disordered PDF number: ' + value)
        cursor = found.end()


def main():
    metadata_path = ROOT / 'paper/ootang_short_horizon_brief.v4.1.sources.json'
    metadata = json.loads(metadata_path.read_text())
    for name, digest in metadata['input_sha256'].items():
        assert sha(ROOT / name) == digest, name
    assert sha(ROOT / 'paper/ootang_short_horizon_brief.v4.1.tex') == metadata['tex_sha256']
    old = json.loads(git_file('paper/ootang_short_horizon_brief.v4.1.sources.json'))
    common = set(old['input_sha256']) & set(metadata['input_sha256'])
    unchanged_sources = common - PRESENTATION_INPUTS
    assert all(old['input_sha256'][name] == metadata['input_sha256'][name] for name in unchanged_sources)
    expected = metadata['displayed_numeric_cells']
    assert len(expected) == 102
    assert expected == old['displayed_numeric_cells']
    initial = ROOT / 'results/ootang_neural_initial_state_v1_1/20260914'
    receipt = json.loads((initial / 'verification/receipt.json').read_text())
    assert receipt['status'] == 'passed' and receipt['physics_pass']
    assert receipt['counts']['trajectories'] == 4707
    assert receipt['strict_diagnostic_failures'] == 3
    assert all(not p['passed'] for p in receipt['effect_gates'])
    development = pd.read_csv(initial/'development/summary_by_horizon.csv').set_index(['model','horizon'])
    later = pd.read_csv(initial/'later_exploratory/summary_by_horizon.csv').set_index(['model','horizon'])
    d, a = development.loc[('NIS_BPLUS',7)], later.loc[('NIS_BPLUS',7)]
    cells = [f'{d.rmse:.4f}', f'{a.rmse:.4f}', f'{a.crps:.4f}', f'{100*a.coverage90:.2f}',
             f'{a.width90:.4f}', f'{a.interval_score90:.4f}']
    assert cells == expected[64:70]
    # Independently check every plotted overview value against frozen CSV sources.
    figures = json.loads((ROOT/'paper/figures/short_horizon_zh/sources.json').read_text())
    assert sha(ROOT/'paper/build_short_horizon_brief_figures_zh.py') == figures['builder_sha256']
    old_figures = json.loads(git_file('paper/figures/short_horizon_zh/sources.json'))
    assert figures['overview_series'] == old_figures['overview_series']
    for name, digest in figures['input_sha256'].items():
        assert sha(ROOT/name) == digest, name
    for name, values in figures['figures'].items():
        assert sha(ROOT/f'paper/figures/short_horizon_zh/{name}.pdf') == values['pdf_sha256'], name
    run = ROOT/'results/ootang_short_horizon_v4/20260913_short_horizon/analysis'
    families = pd.read_csv(run/'family_representatives.csv')
    scores = pd.read_csv(run/'summary_by_horizon.csv')
    expected_keys = {(phase,family,metric) for phase in ('development','later_exploratory')
                     for family in ('B+','ConvLSTM','PINN','Ridge','DRIFT1','NIS_BPLUS')
                     for metric in ('rmse','crps')}
    seen = set()
    for series in figures['overview_series']:
        phase, family, metric = series['phase'], series['family'], series['metric']
        key = phase,family,metric
        assert key not in seen and key in expected_keys
        seen.add(key)
        if family == 'NIS_BPLUS':
            source = (development if phase == 'development' else later).loc[family].sort_index()
        elif family == 'DRIFT1':
            source = scores[(scores.phase == phase) & (scores.model == family)].set_index('horizon').sort_index()
        else:
            source = families[(families.phase == phase) & (families.family == family)].set_index('horizon').sort_index()
        assert series['horizon'] == source.index.tolist() == list(range(1,8))
        assert series['value'] == source[metric].tolist()
    assert seen == expected_keys
    # Check every data row in the new Markdown tables against its saved CSV.
    report = (ROOT/'docs/ootang_neural_initial_state_results.v1.1.md').read_text()
    documentation_cells = 0
    for s in (development,later):
        for h in range(1,8):
            candidate, base = s.loc[('NIS_BPLUS',h)], s.loc[('B_ANCHOR',h)]
            row = [str(h)] + [f'{v:.6f}' for v in (base.mae,candidate.mae,base.rmse,candidate.rmse,base.crps,candidate.crps)]
            assert '| '+' | '.join(row)+' |' in report
            row = [str(h),f'{100*base.coverage90:.2f}%',f'{100*candidate.coverage90:.2f}%'] + [f'{v:.6f}' for v in (base.width90,candidate.width90,base.interval_score90,candidate.interval_score90)]
            assert '| '+' | '.join(row)+' |' in report
            documentation_cells += 12
    names = {'B_ANCHOR':'锚定 B+','DRIFT1':'当天速度外推','CL_DIRECT':'ConvLSTM 直接','CL_BRES':'ConvLSTM 残差',
             'C16_CORE_RULES':'在线回归＋反馈','C16_PHYS_RULES':'在线回归＋反馈＋物理误差','NIS_BPLUS':'神经初态＋B+严格递推'}
    for name,label in names.items():
        d,a=development.loc[(name,7)],later.loc[(name,7)]
        row=[label]+[f'{v:.6f}' for v in (d.rmse,d.crps,a.rmse,a.crps)]
        assert '| '+' | '.join(row)+' |' in report
        documentation_cells += 4
    pdf = ROOT / 'output/pdf/ootang_short_horizon_brief.v4.1.pdf'
    document = pymupdf.open(pdf)
    previous = pymupdf.open(stream=git_file('output/pdf/ootang_short_horizon_brief.v4.1.pdf'), filetype='pdf')
    assert len(document)==len(previous)==4
    texts=[p.get_text() for p in document]
    ordered(texts[0].split('兼顾均值与区间的推荐方法')[1],expected[:28])
    ordered(texts[1].split('模型\n开发')[1],expected[28:94])
    ordered(texts[2]+texts[3],expected[94:])
    assert '4707' in texts[1] and '尚未评价' not in texts[1]
    assert '逐点均值门未过' in texts[1] and '软约束状态PINN：物理一致性未达标' in texts[1]
    compact = [''.join(t.split()) for t in texts]
    assert '神经初态估计与B+严格递推（数值检查通过）' in compact[0]
    assert '软约束PINN（物理未达标）' in compact[0]
    assert all('物理数值检查通过' in compact[i] or '通过物理数值检查' in compact[i] for i in (0,1))
    all_text='\n'.join(texts)
    assert '神经初态＋' not in all_text and 'B＋' not in all_text
    bplus_fonts = []
    for page in document:
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                for span in line['spans']:
                    if 'B+' in span['text']:
                        assert span['font'] == 'ArialUnicodeMS', span
                        bplus_fonts.append(span['font'])
    assert len(bplus_fonts) == 14
    assert all(s not in all_text for s in ('v4.1','2026-09-14','旧版','新增','冻结'))
    unchanged=[]
    for i in (2,3):
        oldpix=previous[i].get_pixmap(matrix=pymupdf.Matrix(1.5,1.5))
        newpix=document[i].get_pixmap(matrix=pymupdf.Matrix(1.5,1.5))
        assert oldpix.samples==newpix.samples
        unchanged.append(i+1)
    for page in document:
        header=page.get_pixmap(clip=pymupdf.Rect(0,0,page.rect.width,42))
        assert set(header.samples)=={255}
    source=ROOT/'code/short_horizon/qa_report.py'
    tree=ast.parse(source.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='geometry')
    namespace={'pymupdf':pymupdf}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
    geometry=namespace['geometry'](document)
    sizes=[span['size'] for p in document for block in p.get_text('dict')['blocks'] if 'lines' in block
           for line in block['lines'] for span in line['spans']]
    figure_paths=sorted(name for name in unchanged_sources if name.startswith('paper/figures/'))
    result=dict(checked_at_utc=datetime.now(timezone.utc).isoformat(),scope='B+ font and coupled-model wording consistency in text and legend only',
                pdf=str(pdf.relative_to(ROOT)),pdf_sha256=sha(pdf),page_count=4,base_commit=BASE,
                csv_numeric_cells_verified_in_pdf_order=102,unchanged_previous_numeric_cells=102,initial_state_numeric_cells=6,
                overview_series_verified=24,unchanged_original_overview_ordinates=140,initial_state_overview_ordinates=28,
                unchanged_previous_overview_ordinates=168,bplus_font='ArialUnicodeMS',bplus_spans_verified=len(bplus_fonts),
                figure_input_hashes_verified=len(figures['input_sha256']),figure_pdf_hashes_verified=len(figures['figures']),
                documentation_numeric_cells_verified=documentation_cells,additional_result=metadata['initial_state_result'],
                current_report_input_hashes_verified=len(metadata['input_sha256']),common_original_source_hashes_unchanged=len(unchanged_sources),
                original_figure_source_files_unchanged=figure_paths,unchanged_pages_pixel_identical=unchanged,
                tex_sha256_verified=True,minimum_font_size_pt=min(sizes),page_geometry=geometry,
                geometry_method='original geometry function extracted without importing experiment or statistics',
                geometry_source_sha256=sha(source),blank_header_check='all four headers white through 42 pt',
                visible_wording_check='no display version, production date, old/new version terminology',
                numerical_method='saved CSV values and independent run verification receipt; no model or score recalculation',
                new_training=0,new_model_selection=0,new_prediction_or_scoring=0,new_physical_calls=0,
                verifier_sha256=sha(Path(__file__)))
    target=ROOT/'paper/ootang_short_horizon_brief.v4.1.qa.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('page_count','csv_numeric_cells_verified_in_pdf_order','documentation_numeric_cells_verified',
                      'current_report_input_hashes_verified','common_original_source_hashes_unchanged','unchanged_pages_pixel_identical','minimum_font_size_pt')},indent=2))


if __name__=='__main__':
    main()
