"""Upload/restore audit data and record workflow outcomes, using stdlib only."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.request import Request, build_opener

from marketfeed.archive import Archive, private_path, NoRedirect
from marketfeed.audit import Journal, TEHRAN


def failure(path, exc):
    """Append safely without truncating the useful journal on archive failure."""
    path=private_path(path)
    if path.exists():
        lines=path.read_text(encoding='utf-8').splitlines()
        if not lines:return
        first=json.loads(lines[0])
        context={k:first[k] for k in ('log_version','run_id','run_attempt','phase','batch_id','commit','run_url')}
        now=datetime.now(timezone.utc)
        record={**context,'at':now.isoformat(),'at_tehran':now.astimezone(TEHRAN).isoformat(),
                'event':'archive_upload_failed','severity':'error','error_type':type(exc).__name__,
                'http_status':getattr(exc,'code',None)}
        with path.open('a',encoding='utf-8') as handle:handle.write(json.dumps(record)+'\n')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['upload','restore','workflow'])
    parser.add_argument('--file',default='.validation/incidents.jsonl')
    parser.add_argument('--state')
    args=parser.parse_args()
    if args.command=='workflow':
        journal=Journal(args.file,phase='workflow')
        statuses={name:os.environ.get(name,'unknown') for name in ('COLLECT_RESULT','DEPLOY_RESULT')}
        statuses={k:v if v in ('success','failure','cancelled','skipped') else 'unknown' for k,v in statuses.items()}
        journal.emit('workflow_finished','error' if 'failure' in statuses.values() else 'warning' if any(v!='success' for v in statuses.values()) else 'info',stages=statuses)
        token=os.environ.get('AUDIT_GITHUB_TOKEN')
        run=os.environ.get('GITHUB_RUN_ID','')
        if token and run.isdigit():
            try:
                req=Request(f'https://api.github.com/repos/hamed-zeidabadi/iran-market-feed/actions/runs/{run}/jobs?per_page=100',headers={
                    'Authorization':'Bearer '+token,'User-Agent':'IranMarketFeed/0.1 audit',
                    'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
                with build_opener(NoRedirect).open(req,timeout=20) as response:
                    raw=response.read(1_000_001)
                    if len(raw)>1_000_000:raise ValueError('job metadata too large')
                    jobs=json.loads(raw)['jobs']
                for job in jobs:
                    for step in job.get('steps',[]):
                        if step.get('conclusion') in ('failure','cancelled','timed_out'):
                            journal.emit('workflow_step_failed','error',job=job['name'],step=step['name'],
                                         conclusion=step['conclusion'],started_at=step.get('started_at'),completed_at=step.get('completed_at'))
            except Exception as exc:
                journal.emit('workflow_details_unavailable','warning',error_type=type(exc).__name__,http_status=getattr(exc,'code',None))
    try:
        archive=Archive()
        if args.command=='restore':
            restored=archive.restore(args.state or '.validation/monitoring.json')
            status=private_path('.validation/audit-restore.json')
            if status.exists():status.unlink()
            print(json.dumps({'archive_state_restored':restored}))
        else:
            print(json.dumps(archive.upload(args.file,args.state)))
    except Exception as exc:
        if args.command!='restore':failure(args.file,exc)
        else:
            status=private_path('.validation/audit-restore.json')
            status.parent.mkdir(parents=True,exist_ok=True)
            status.write_text(json.dumps({'error_type':type(exc).__name__,'http_status':getattr(exc,'code',None)}))
        print(json.dumps({'archive_error':type(exc).__name__,'http_status':getattr(exc,'code',None)}))
        raise SystemExit(1)

if __name__=='__main__':main()
