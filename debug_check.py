import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r'c:\work\github\perf-analysis-tools\src')
from perf_analysis_tools import cobalt_stats_tally as cst


tmp = Path(tempfile.mkdtemp())
print('tmp=', tmp)

for rel in [
    'ttl_hpx_Test2/1.esimd/cobalt_sim_stats.7z',
    'ttl_hpx_Test13/2.esimd/cobalt_sim_stats.7z',
]:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('', encoding='utf-8')

file_patterns = tmp / 'stats_files.txt'
text = '\n'.join([
    r'^ttl_hpx_Test2[\\/]\d+\.esimd[^\\/]*[\\/]cobalt_sim_stats\.(?:7z|txt)$',
    r'^ttl_hpx_Test13[\\/]\d+\.esimd[^\\/]*[\\/]cobalt_sim_stats\.(?:7z|txt)$',
]) + '\n'
file_patterns.write_text(text, encoding='utf-8')
print('text =', repr(text))
print('patterns =', cst.read_file_patterns(file_patterns))
for p in cst.read_file_patterns(file_patterns):
    print('regex', p.regex.pattern)

os.chdir(tmp)
for candidate in Path.cwd().rglob('*'):
    if candidate.is_file():
        print('cand', candidate)
        print('norm', cst.normalize_path(candidate))
        for p in cst.read_file_patterns(file_patterns):
            print('  match?', any(p.regex.search(x) for x in cst.normalize_path(candidate)))
print('discover', cst.discover_stats_files(cst.read_file_patterns(file_patterns)))
