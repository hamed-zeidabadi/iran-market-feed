from copy import deepcopy
from datetime import timedelta
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from marketfeed.audit import Journal, record_collection
from marketfeed.archive import Archive, unpack
from marketfeed.__main__ import collect
from marketfeed.report import build_report
from marketfeed.monitoring import enrich
from test_report import NOW, config, quote


class AuditTests(unittest.TestCase):
    def records(self,quotes,previous=None,health=None):
        cfg=config()
        cfg['tolerances']['gold_18k']=1
        for source in cfg['sources']:
            source['assets']=['gold_18k']
            source['enabled']=True
        if health is None:
            health=[{'id':s['id'],'status':'parsed' if any(q.source==s['id'] for q in quotes) else 'no_quotes'} for s in cfg['sources']]
        report=build_report(cfg,quotes,health or [],NOW)
        enrich(report,previous or {},cfg,NOW)
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'events.jsonl',config=cfg)
            record_collection(journal,report,cfg,health or [],previous or {})
            return [json.loads(line) for line in journal.path.read_text(encoding='utf-8').splitlines()]

    def test_outlier_uses_other_publishers_and_keeps_reference_unchanged(self):
        quotes=[quote('a',100),quote('b',101),quote('c',300)]
        records=self.records(quotes)
        outliers=[r for r in records if r['event']=='price_outlier_candidate' and r['source']=='c']
        self.assertEqual(outliers[0]['peer_prices'],[100,101])
        self.assertEqual([r['source'] for r in records if r['event']=='price_outlier_candidate'],['c'])
        self.assertTrue(outliers[0]['requires_review'])
        self.assertTrue(any(r['event']=='reference_result' and r['status']=='disputed' for r in records))
        self.assertEqual(next(r for r in records if r['event']=='reference_result')['price'],101)

    def test_retries_do_not_hide_recovered_failures(self):
        records=self.records([quote('a')],health=[{'id':'a','status':'parsed','http_status':200,'transports':[
            {'occurred_at':NOW.isoformat(),'route':'direct_https','http_status':403,'error_type':'HTTPError','reason_type':'str'},
            {'occurred_at':NOW.isoformat(),'route':'private_proxy','http_status':200,'error_type':None,'reason_type':None}]}])
        self.assertTrue(any(r['event']=='transport_failed' and r['http_status']==403 for r in records))
        self.assertTrue(any(r['event']=='source_recovered_after_retry' for r in records))

    def test_stale_missing_future_and_unknown_time_are_logged(self):
        for changes,reason in [({'published_at':(NOW-timedelta(days=1)).isoformat()},'stale'),
                               ({'published_at':(NOW+timedelta(hours=1)).isoformat()},'future_timestamp'),
                               ({'published_at':None,'time_basis':'unknown'},'unknown_time')]:
            records=self.records([quote('a',**changes)])
            self.assertTrue(any(r['event']=='quote_rejected' and r['reason']==reason for r in records))

    def test_price_jump_is_candidate_and_stable_price_is_not_error(self):
        old={'sources':{'a':{'assets':{'gold_18k':{'price':100,'last_published_at':(NOW-timedelta(minutes=5)).isoformat(),'time_basis':'source'}}}}}
        self.assertTrue(any(r['event']=='price_jump_candidate' for r in self.records([quote('a',200)],old)))
        self.assertFalse(any(r['event']=='price_jump_candidate' for r in self.records([quote('a',100)],old)))

    def test_secrets_redacted_and_records_have_timezone_and_run_context(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'AUDIT_SECRET':'very-private-secret'}):
            journal=Journal(Path(directory)/'events.jsonl')
            journal.emit('test',text='very-private-secret')
            text=journal.path.read_text()
            self.assertNotIn('very-private-secret',text)
            record=json.loads(text)
            self.assertIn('at_tehran',record)
            self.assertIn('batch_id',record)

    def test_collect_preserves_failed_and_successful_attempts(self):
        source={'id':'a','url':'https://example.org','adapter':'auto','assets':['gold_18k'],'group':'a'}
        response=type('Response',(),{'status':200,'read':lambda self,n:b'<p>empty</p>','__enter__':lambda self:self,'__exit__':lambda *a:None})()
        with patch('marketfeed.__main__.urlopen',side_effect=[HTTPError(source['url'],503,'Unavailable',{},None),response]),patch('marketfeed.__main__.time.sleep'):
            _,state=collect(source,1,2)
        self.assertEqual(state['attempts'],2)
        self.assertEqual([r['http_status'] for r in state['transports']],[503,200])

    def test_archive_redirects_and_public_paths_rejected(self):
        for origin in ['http://bad','https://user:pass@example.org','https://example.org/?token=x']:
            with self.assertRaises(ValueError):Archive(origin,'secret')
        with self.assertRaises(ValueError):Journal('public/log.jsonl')

    def test_gzip_size_bounds(self):
        with self.assertRaises(ValueError):unpack(gzip.compress(b'x'*1000),limit=100)

    def test_extremely_large_finite_price_does_not_break_audit(self):
        records=self.records([quote('a',100),quote('b',101),quote('c',10**309)])
        self.assertTrue(any(r['event']=='price_outlier_candidate' and r['source']=='c' for r in records))
        json.dumps(records,allow_nan=False)

    def test_archive_upload_acknowledgement_and_state_restore(self):
        archive=Archive('https://archive.test','secret')
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'log.jsonl')
            journal.emit('collection_finished')
            with patch.object(archive,'call',return_value=json.dumps({'stored':journal.batch_id}).encode()) as send:
                result=archive.upload(journal.path)
                body=unpack(send.call_args.args[1])
            self.assertEqual(body['records'][0]['event'],'collection_finished')
            self.assertEqual(result['stored'],journal.batch_id)
            state={'version':1,'sources':{}}
            with patch.object(archive,'call',return_value=gzip.compress(json.dumps(state).encode())):
                path=Path(directory)/'state.json'
                self.assertTrue(archive.restore(path))
                self.assertEqual(json.loads(path.read_text()),state)

    def test_interrupted_collection_is_archived_without_replacing_state(self):
        archive=Archive('https://archive.test','secret')
        with tempfile.TemporaryDirectory() as directory:
            journal=Journal(Path(directory)/'log.jsonl')
            journal.emit('collection_started')
            with patch.object(archive,'call',return_value=json.dumps({'stored':journal.batch_id}).encode()) as send:
                archive.upload(journal.path,Path(directory)/'missing-state.json')
            batch=unpack(send.call_args.args[1])
            self.assertNotIn('state',batch)
            self.assertEqual(batch['records'][-1]['event'],'collection_incomplete')
