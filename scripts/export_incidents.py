"""Export private retained batches as JSONL and a source/event count report."""
import argparse
from collections import Counter
from datetime import date, datetime
import gzip
import json
import os
from pathlib import Path
from urllib.parse import urlencode
from marketfeed.archive import Archive, private_path, unpack


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--from',dest='start',required=True)
    parser.add_argument('--to',dest='end',required=True)
    parser.add_argument('--output',default='.validation/export/incidents.jsonl.gz')
    parser.add_argument('--source',default=os.environ.get('SOURCE_ID') or None)
    parser.add_argument('--issues-only',action='store_true')
    args=parser.parse_args()
    args.issues_only = args.issues_only or os.environ.get('ISSUES_ONLY')=='true'
    if date.fromisoformat(args.start)>date.fromisoformat(args.end):parser.error('invalid date range')
    output=private_path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    archive=Archive();cursor=None;upper=None;counts=Counter();total=0;batches=0
    availability={};heartbeats=set();collections=set()
    temporary=output.with_suffix(output.suffix+'.tmp')
    with gzip.open(temporary,'wt',encoding='utf-8') as handle:
        while True:
            params={'from':args.start,'to':args.end}
            if cursor is not None:params['cursor']=cursor
            if upper is not None:params['upper']=upper
            page=json.loads(archive.call('/export?'+urlencode(params)))
            upper=page['upper']
            for row in page['rows']:
                batches+=1
                for record in unpack(bytes.fromhex(row['packed_hex'])):
                    if record['event']=='watchdog_heartbeat':heartbeats.add(record['at'])
                    if record['event']=='collection_finished':collections.add(record['at'])
                    if args.source and record.get('source')!=args.source:continue
                    if record['event']=='source_result':
                        stats=availability.setdefault(record['source'],{'samples':0,'failures':0})
                        stats['samples']+=1
                        stats['failures']+=record['severity']=='error'
                    if args.issues_only and record['severity']=='info':continue
                    handle.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n')
                    counts[(record.get('source','-'),record['event'],record['severity'])]+=1
                    total+=1
            new_cursor=page['next_cursor']
            if new_cursor is None:break
            if new_cursor==cursor:raise ValueError('archive cursor did not advance')
            cursor=new_cursor
    temporary.replace(output)
    def gaps(stamps):
        times=sorted(datetime.fromisoformat(s.replace('Z','+00:00')) for s in stamps)
        found=[{'from':a.isoformat(),'to':b.isoformat(),'seconds':(b-a).total_seconds()} for a,b in zip(times,times[1:]) if (b-a).total_seconds()>1200]
        return {'count':len(found),'examples':sorted(found,key=lambda r:r['seconds'],reverse=True)[:100]}
    summary={'from':args.start,'to':args.end,'batches':batches,'records':total,
             'source_availability':availability,'collector_gaps_over_20_minutes':gaps(collections),
             'watchdog_gaps_over_20_minutes':gaps(heartbeats),
             'counts':[{'source':k[0],'event':k[1],'severity':k[2],'count':n} for k,n in counts.most_common()]}
    output.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'exported_records':total,'batches':batches,'output':str(output)}))

if __name__=='__main__':main()
