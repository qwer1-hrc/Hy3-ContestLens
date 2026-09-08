"""Reproducible, non-destructive normalization of the supplied contest collection.

Original files remain intact. Copies are verified; duplicate names must have
identical bytes. No downloaded or supplied C++/Python programs are executed.
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile

from contest_inventory import SESSIONS, LIMITS, SPECIAL
from hy3_contestlens.statement_assets import localize_images

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT.parent
DEST = SOURCE / 'contest_data'
CACHE = PROJECT / 'data/luogu_cache'

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def copy_verified(source, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    sha = digest(source)
    if dest.exists():
        if digest(dest) != sha:
            raise ValueError(f'Conflicting destination: {dest}')
    else:
        shutil.copy2(source, dest)
    if digest(dest) != sha:
        raise ValueError(f'Copy verification failed: {dest}')
    return sha

def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def extract_missing():
    # Read archive inventories before extraction; only regular, contained names.
    extraction = PROJECT / 'tmp/contest_extract'
    extraction.mkdir(parents=True,exist_ok=True)
    for year, filename in [(2020,'CSP-JS2020第二轮认证测试数据.rar'),(2021,'CSP2021-S2-Data.zip')]:
        archive = SOURCE / 'CSP-S_data' / str(year) / filename
        target = extraction / str(year)
        if year == 2020:
            senior_zips = list(archive.parent.rglob('CSP-S.zip'))
            if senior_zips:
                senior_target = target / 'CSP-S'
                senior_target.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(senior_zips[0]) as z:
                    for member in z.infolist():
                        if not (senior_target/member.filename).resolve().is_relative_to(senior_target.resolve()):
                            raise ValueError('Unsafe archive path')
                    z.extractall(senior_target)
                continue
        if (target/'.complete').exists():
            continue
        target.mkdir(parents=True,exist_ok=True)
        if year == 2021:
            with zipfile.ZipFile(archive) as z:
                for member in z.infolist():
                    if not (target/member.filename).resolve().is_relative_to(target.resolve()):
                        raise ValueError('Unsafe archive path')
                z.extractall(target)
        else:
            names = subprocess.check_output(['tar','-tf',str(archive)],text=True,encoding='utf-8').splitlines()
            if any(not (target/name).resolve().is_relative_to(target.resolve()) for name in names):
                raise ValueError('Unsafe archive path')
            subprocess.run(['tar','-xf',str(archive),'-C',str(target)],check=True)
        (target/'.complete').write_text('ok')
    return extraction

def main():
    extracted = extract_missing()
    records = []
    difficulty_config = json.loads((CACHE/'difficulty_config.json').read_text(encoding='utf-8'))
    difficulty_labels = {row['id']:row['name'] for row in difficulty_config['labels']}
    for contest,year,group,day,pdf,pairs in SESSIONS:
        source = SOURCE / ('NOI-NOIP_data' if contest=='NOIP' else 'CSP-S_data') / str(year)
        files = list(source.rglob('*'))
        if contest=='CSP-S' and year in (2020,2021):
            files += list((extracted/str(year)).rglob('*'))
        dataset_id = 'noip2018' if contest=='NOIP' and year==2018 else f'{contest.lower().replace("-", "")}{year}_{group}'
        session_path = Path(contest.lower().replace('-','')) / str(year) / group
        if (contest=='NOIP' and year<2020 and group=='senior') or (contest=='CSP-S' and year==2019):
            session_path /= f'day{day}'
        paper = DEST/session_path/'statements'/f'{contest.lower().replace("-", "")}{year}_{group}_day{day}.pdf'
        copy_verified(source/pdf,paper)
        for number,pair in enumerate(pairs.split(),1):
            basename,pid=pair.split(':')
            problem_id = basename if dataset_id=='noip2018' else f'{dataset_id}_{basename}'
            relative = session_path/f'{number:02d}_{basename}'
            folder = DEST/relative
            luogu = json.loads((CACHE/f'{pid}.json').read_text(encoding='utf-8'))['problem']
            checked = datetime.fromtimestamp((CACHE/f'{pid}.json').stat().st_mtime, timezone.utc).isoformat()
            title = luogu['name'].split(']',1)[1].strip()
            assert str(year) in luogu['name'] and contest in luogu['name'], (pid,luogu['name'])
            # Some 2014 archives/directories contain duplicates and wrong group labels.
            # Group by the actual problem basename and verify all duplicate bytes.
            inputs = [p for p in files if p.is_file() and p.suffix=='.in' and re.fullmatch(re.escape(basename)+r'[\d_.-]+',p.stem)]
            if contest=='NOIP' and year==2014:
                # The senior archive also includes a different 10-case submatrix
                # set. The junior paper specifies 20 cases: retain its own set.
                correct = source / group
                inputs = [p for p in inputs if p.is_relative_to(correct)]
            unique = {}
            for p in sorted(inputs):
                key = p.stem
                answers=[q for q in (p.with_suffix('.ans'),p.with_suffix('.out')) if q.exists()]
                if not answers: raise ValueError(f'Missing answer: {p}')
                if len({digest(q) for q in answers})!=1: raise ValueError(f'Conflicting answers: {p}')
                signature=(digest(p),digest(answers[0]))
                if key in unique and unique[key][2]!=signature: raise ValueError(f'Conflicting duplicate: {p}')
                unique[key]=(p,answers[0],signature)
            if not unique:
                raise ValueError(f'Unexpected missing tests: {problem_id}')
            folder.mkdir(parents=True,exist_ok=True)
            tests=[]
            private=PROJECT/'data/private'/dataset_id/f'day{day}'/problem_id
            for key,(inp,ans,sha) in sorted(unique.items()):
                for root,subdir in [(folder,'tests'),(private,'tests')]:
                    copy_verified(inp,root/subdir/f'{key}.in')
                copy_verified(ans,folder/'tests'/f'{key}.out')
                copy_verified(ans,private/'expected'/f'{key}.out')
                tests.append({'test_id':key,'input_sha256':sha[0],'answer_sha256':sha[1],'source_input':str(inp.relative_to(SOURCE)),'source_answer':str(ans.relative_to(SOURCE))})
            for cpp in [p for p in files if p.suffix in ('.cpp','.cc','.cxx') and p.stem.startswith(basename)]:
                copy_verified(cpp,folder/'std'/cpp.name)
            content=luogu.get('contenu') or luogu['content']
            statement=[f'# {title}',f'来源：https://www.luogu.com.cn/problem/{pid}',f'原始比赛 PDF：{paper.relative_to(DEST).as_posix()}','']
            for key,label in [('background','题目背景'),('description','题目描述'),('formatI','输入格式'),('formatO','输出格式')]:
                if content.get(key): statement += [f'## {label}',content[key],'']
            for index,(inp,out) in enumerate(luogu.get('samples',[]),1):
                statement += [f'## 样例 {index}','```input',inp,'```','```output',out,'```']
            statement += ['## 说明与数据范围',content.get('hint','')]
            (folder/'statement.md').write_text(localize_images('\n\n'.join(statement)+'\n', folder),encoding='utf-8')
            memory=128 if year<=2015 else 256 if contest=='CSP-S' and year<=2020 else 512
            seconds,memory=LIMITS.get(pid,(1,memory))
            m={'schema_version':1,'dataset_id':dataset_id,'problem_id':problem_id,'title_zh':title,'day':day,'contest':contest,'year':year,'group':group,'task_number':number,
               'luogu_id':pid,'luogu_difficulty':difficulty_labels[luogu['difficulty']],'difficulty_checked_at':checked,
               'data_status':'missing' if not tests else 'samples' if year==2025 else 'partial' if pid=='P2312' else 'official','test_count':len(tests),'judge_note':'缺少提高级测试数据' if not tests else SPECIAL.get(pid),
               'statement_relative_path':(relative/'statement.md').as_posix(),
               'resource_limits':{'time_ms':int(seconds*1000),'memory_mb':memory,'output_bytes':16777216},
               'io':{'basename':basename,'input_mode':'stdin_and_file','output_mode':'stdout_or_file'},
               'judge':{'comparator':'noip_fulltext','input_glob':'tests/*.in','expected_glob':'expected/*.out','score_per_test':100/max(1,len(tests))}}
            dump(PROJECT/'data/manifests'/dataset_id/f'{problem_id}.json',m)
            dump(folder/'metadata.json',{**m,'original_paper':paper.relative_to(DEST).as_posix(),'difficulty_source':f'https://www.luogu.com.cn/problem/{pid}','difficulty_id':luogu['difficulty'],'scoring_note':'本地测试点等权通过率，非官方子任务得分','tests':tests})
            records.append({**m,'folder':relative.as_posix()})
            print(problem_id,len(tests),m['luogu_difficulty'],flush=True)
    dump(DEST/'catalog.json',records)
    dump(DEST/'organization_report.json',{'problem_count':len(records),'test_count':sum(r['test_count'] for r in records),'problems':records,'notes':['NOIP2018 junior absent in source','2025 collections contain supplementary samples only','2014 mixed/duplicate directories regrouped by basename; hashes checked','CSP-S2019 has day1 and day2','Scores are normalized local case pass rates, not official subtask scores']})
    print('TOTAL',len(records),sum(r['test_count'] for r in records))

if __name__=='__main__': main()
