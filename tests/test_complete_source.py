import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_app.review_source import GitSource
from agent_app.review_analysis import analyze, make_context, static_review, source_excerpt
from agent_app.review_batches import complete_contexts, missing_chunks
from tests.test_review import snapshot


class CompleteSourceTests(unittest.TestCase):
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
            result=analyze(data,max_workers=1)
        self.assertFalse(result['coverage']['full_source_review'])
        self.assertEqual(result['coverage']['completed_batches'],1)
        self.assertEqual(result['ai_error'],'fixture limit')

    def test_branch_versions_remain_while_identical_copies_share_a_pass(self):
        data=snapshot({'same.py':'value = 1\n','base.py':'enabled = True\n'})
        data['views']={'base':{**data,'label':'main','kind':'base'},'branch:other':{**data,'files':{'same.py':'value = 2\n'},'label':'other','kind':'branch'}}
        contexts,coverage=complete_contexts(data,[],{},'',source_excerpt)
        entries=[entry for context in contexts for entry in context['files']]
        self.assertEqual(coverage['source_versions'],3)
        self.assertTrue(any('value = 1' in entry['code'] and 'base' in entry['same_content_in'] for entry in entries))
        self.assertTrue(any(entry['view']=='branch:other' and 'value = 2' in entry['code'] for entry in entries))
