"""Public standard-runner guard; no billing claims and no credential read."""
import json
import os
from pathlib import Path
import urllib.request
def validate_event(event,repository):
    source=event.get('repository',{}) if isinstance(event,dict) else {}
    if source.get('private') is not False or source.get('visibility') not in (None,'public') or source.get('full_name')!=repository:
        raise ValueError('This release only runs in the exact PUBLIC repository.')
def main():
    try:
        if os.environ.get('GITHUB_ACTIONS')!='true' or os.environ.get('RUNNER_OS')!='Linux' or os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch': raise ValueError('Use a disposable standard Linux manual GitHub runner.')
        repository=os.environ.get('GITHUB_REPOSITORY','')
        if repository!='simcalc/OpenFoam': raise ValueError('Only the reviewed public educational repository is allowed.')
        event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        validate_event(event,repository)
        request=urllib.request.Request('https://api.github.com/repos/'+repository,headers={'Accept':'application/vnd.github+json','User-Agent':'openfoam-student-benchmarks-public-guard'})
        with urllib.request.urlopen(request,timeout=20) as response:
            raw=response.read(1024*1024+1)
        if len(raw)>1024*1024: raise ValueError('Public API response is outside the bounded guard.')
        validate_event({'repository':json.loads(raw)},repository)
    except Exception as exc:
        print(json.dumps({'accepted':False,'reasonCode':'CFD_PUBLIC_RUNNER_REQUIRED','action':'Verify the reviewed repository remains public and this is a manual standard Linux runner.','exceptionType':type(exc).__name__,'paidFallback':False}));return 2
    print(json.dumps({'accepted':True,'publicRepositoryVerified':True,'billingApiVerification':False,'paidFallback':False,'customRunnerAllowed':False}));return 0
if __name__=='__main__': raise SystemExit(main())
