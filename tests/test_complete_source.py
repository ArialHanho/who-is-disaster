import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_app.review_source import GitSource
from agent_app.review_analysis import analyze, make_context, static_review, source_excerpt
from agent_app.review_batches import complete_contexts, missing_chunks
from agent_app.review_runner import ReviewCache
from tests.test_review import snapshot


class CompleteSourceTests(unittest.TestCase):
    def test_saved_source_review_revalidates_evidence_and_runs_live_focused_pass(self):
        data = snapshot({"app.py": "value = 1\n"})
        saved = {"coverage": {"source_files": 1, "source_versions": 1, "source_chunks": 1, "source_pass_batches": 1},
                 "findings": []}
        response = {"result": {"summary": "", "findings": []}, "usage": {}}
        with patch("agent_app.review_analysis.codex_provider.status", return_value={"ready": True, "label": "ChatGPT"}), \
                patch("agent_app.review_analysis.codex_provider.generate", return_value=response) as generate:
            result = analyze(data, saved_source_review=saved)
        self.assertTrue(result["coverage"]["full_source_review"])
        self.assertTrue(result["coverage"]["saved_source_review"])
        self.assertEqual(result["coverage"]["reused_batches"], 1)
        self.assertNotIn("review_pass", generate.call_args.args[0])
        generate.assert_called_once()

    def test_doc_edit_reuses_every_unrelated_code_page(self):
        files = {f"src/module{i:03}.py": f"value = {i}\n" for i in range(210)}
        files["docs/notes.md"] = "Original notes\n"
        data = snapshot(files)
        calls = []
        def generate(context, **kwargs):
            calls.append(context)
            return {"result": {"summary": "", "findings": []}, "usage": {}}
        with tempfile.TemporaryDirectory() as temp, patch("agent_app.review_analysis.codex_provider.status",
                return_value={"ready": True, "label": "ChatGPT"}), patch("agent_app.review_analysis.codex_provider.generate", side_effect=generate):
            first = analyze(data, cache_dir=temp)
            calls.clear()
            changed = snapshot({**files, "docs/notes.md": "Expanded notes\n" * 1500}, changed=["docs/notes.md"], head="c" * 40)
            second = analyze(changed, cache_dir=temp)
        self.assertTrue(second["coverage"]["full_source_review"])
        self.assertEqual(second["coverage"]["reused_batches"], first["coverage"]["ai_batches"] - 2)
        self.assertEqual(len(calls), 2)
        pages = [c for c in calls if "review_pass" in c]
        self.assertEqual([f["path"] for c in pages for f in c["files"]], ["docs/notes.md"])

    def test_new_commit_reuses_unchanged_source_pages_but_runs_change_review(self):
        data = snapshot({f'module{i:03}.py':f'value = {i}\n' for i in range(240)})
        calls = []
        def generate(context, **kwargs):
            calls.append(context)
            return {'result': {'summary':'검토 완료', 'findings':[]}, 'usage':{}}
        with tempfile.TemporaryDirectory() as temp, patch('agent_app.review_analysis.codex_provider.status',
                return_value={'ready':True, 'label':'ChatGPT'}), patch('agent_app.review_analysis.codex_provider.generate', side_effect=generate):
            first = analyze(data, cache_dir=temp)
            calls.clear()
            data = {**data, 'head':'c'*40, 'base':data['head'], 'changed':['module050.py'],
                    'diff':'value = 50 -> value = 99', 'files':{**data['files'], 'module050.py':'value = 99\n'}}
            second = analyze(data, cache_dir=temp)
        self.assertTrue(second['coverage']['full_source_review'])
        self.assertEqual(second['coverage']['source_files'], 240)
        self.assertEqual(len(calls), 2)  # Focused changes plus the affected full-source page.
        self.assertEqual(second['coverage']['reused_batches'], first['coverage']['ai_batches']-2)
        self.assertTrue(any(c.get('commit') == 'c'*40 for c in calls))

    def test_source_cache_tracks_callers_outside_excerpt_and_project_contracts(self):
        data = snapshot({'a.py':'from z import work\nwork()\n', 'z.py':'def work():\n    pass\n',
                         'README.md':'Expected behaviour\n'})
        def contexts(value):
            graph = static_review(value)['graph']
            # Force separate pages and an intentionally short support excerpt.
            with patch('agent_app.review_batches.SOURCE_BATCH_FILES', 1):
                return complete_contexts(value, [], graph, '',
                    lambda p,b,n:{'code':'1: '+b.splitlines()[0], 'truncated':True})[0]
        cache = ReviewCache('.', ['policy'])
        before = contexts(data)
        changed_head = contexts({**data, 'head':'c'*40, 'changed':[]})
        self.assertEqual([cache.key(c) for c in before], [cache.key(c) for c in changed_head])
        target = next(i for i,c in enumerate(before) if c['files'][0]['path'] == 'z.py')
        for path, body in [('a.py','from z import work\nwork(1)\n'), ('README.md','Different contract\n')]:
            after = contexts({**data, 'files':{**data['files'], path:body}})
            if path == 'a.py': self.assertEqual(before[target]['files'], after[target]['files'])
            self.assertNotEqual(cache.key(before[target]), cache.key(after[target]))

    def test_collection_keeps_files_past_old_count_and_size_caps(self):
        bodies = {f'{i:040x}':f'value = {i}\n'.encode() for i in range(610)}
        bodies['f'*40]=b'value = "'+b'x'*2_100_000+b'"\n'
        paths={key:f'src/file{i}.py' for i,key in enumerate(bodies)}
        batches=[]
        def run(folder,*args,**kwargs):
            if args[0]=='ls-tree':
                return b'\0'.join(f'100644 blob {key} {len(body)}\t{paths[key]}'.encode() for key,body in bodies.items())+b'\0'
            if args[0]=='cat-file':
                keys=kwargs['input_data'].decode().splitlines();batches.append(keys)
                return b''.join(f'{key} blob {len(bodies[key])}\n'.encode()+bodies[key]+b'\n' for key in keys)
            if args[0]=='log':return b'Fixture'
            return b''
        with tempfile.TemporaryDirectory() as temp, patch('agent_app.review_source.git',side_effect=run):
            data=GitSource(Path(temp)).read({'folder':temp,'head':'a'*40,'name':'fixture','web':''},'b'*40)
        self.assertEqual(len(data['files']),611)
        self.assertEqual(data['omitted'],0)
        self.assertGreater(len(data['files'][paths['f'*40]]),2_000_000)
        self.assertGreater(len(batches),1)
        self.assertTrue(all(len(group)<=128 for group in batches))

    def test_every_source_line_reaches_a_batch_including_file_tails(self):
        files={f'module{i}.py':f'value = {i}\n' for i in range(610)}
        files['long.py']=''.join(f'value_{i} = {i}\n' for i in range(6000))
        data=snapshot(files);static=static_review(data)
        contexts,coverage=complete_contexts(data,[make_context(data,static,'')],static['graph'],'',source_excerpt)
        shown={}
        for context in contexts:
            for entry in context['files']:shown.setdefault(entry['path'],set()).update(entry['code'].splitlines())
        for path,body in files.items():
            self.assertTrue({f'{i}: {line}' for i,line in enumerate(body.splitlines(),1)}<=shown[path],path)
        self.assertEqual(coverage['source_files'],611)
        self.assertGreater(len(contexts),2)
        line='large-string-'*4000
        parts=list(missing_chunks('combined','big.json',line,set(),budget=500))
        self.assertEqual(''.join(p['code'].split(': ',1)[1] for p in parts),line)

    def test_failed_batch_cannot_report_full_review(self):
        data=snapshot({f'module{i}.py':f'value = {i}\n' for i in range(120)})
        calls=[]
        def generate(context,**kwargs):
            calls.append(context)
            if len(calls)==2:raise ValueError('fixture limit')
            return {'result':{'findings':[],'summary':'검토 완료'},'usage':{}}
        with patch('agent_app.review_analysis.codex_provider.status',return_value={'ready':True,'label':'ChatGPT'}), patch('agent_app.review_analysis.codex_provider.generate',side_effect=generate):
            updates=[]
            result=analyze(data,max_workers=1,progress=lambda stage,text:updates.append(text))
        self.assertFalse(result['coverage']['full_source_review'])
        self.assertEqual(result['coverage']['completed_batches'],1)
        self.assertEqual(result['ai_error'],'fixture limit')
        self.assertEqual(updates[0], '분석 중 · 0%')
        self.assertEqual(updates[-1], f"분석 중 · {100 // result['coverage']['ai_batches']}%")
        self.assertNotIn('분석 중 · 100%', updates)

    def test_branch_versions_remain_while_identical_copies_share_a_pass(self):
        data=snapshot({'same.py':'value = 1\n','base.py':'enabled = True\n'})
        data['views']={'base':{**data,'label':'main','kind':'base'},'branch:other':{**data,'files':{'same.py':'value = 2\n'},'label':'other','kind':'branch'}}
        contexts,coverage=complete_contexts(data,[],{},'',source_excerpt)
        entries=[entry for context in contexts for entry in context['files']]
        self.assertEqual(coverage['source_versions'],3)
        self.assertTrue(any('value = 1' in entry['code'] and 'base' in entry['same_content_in'] for entry in entries))
        self.assertTrue(any(entry['view']=='branch:other' and 'value = 2' in entry['code'] for entry in entries))
