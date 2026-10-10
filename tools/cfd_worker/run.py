"""Only two pinned public educational fixtures; never accepts application input."""
import argparse
import json
from pathlib import Path
import sys
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'services/cfd_worker/src'))
from untirta_cfd_worker.contracts import WorkerDiagnostic,digest,write_json
from untirta_cfd_worker.capabilities import capability_report
from untirta_cfd_worker.runner import execute_job
def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capabilities',action='store_true')
    parser.add_argument('--benchmark',choices=('transient-cavity-v1','cavitation-bullet-v1'))
    parser.add_argument('--restart-smoke',action='store_true')
    parser.add_argument('--workspace',type=Path,default=ROOT/'outputs/cfd-worker')
    parser.add_argument('--report',type=Path)
    args=parser.parse_args(argv)
    if args.capabilities: print(json.dumps(capability_report()));return 0
    if not args.benchmark: parser.error('Select a fixed educational --benchmark.')
    if args.restart_smoke and args.benchmark!='transient-cavity-v1': parser.error('Restart smoke is only approved for the stationary cavity.')
    reference={'schemaVersion':'untirta.cfd-benchmark-reference.v1','kind':'development-benchmark','caseProfile':args.benchmark,'sourceCommit':'0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd'}
    source={'schemaVersion':'untirta.cfd-worker-job.v1','studyId':args.benchmark,'inputRevision':'fixture-v1','adapter':'openfoam','caseProfile':args.benchmark,'referenceSha256':digest(reference),'limits':{'memoryBytes':2147483648,'timeoutSeconds':1200,'diskBytes':268435456,'logBytes':2097152,'threads':1},'parameters':{}}
    try:
        result=execute_job(source,reference,args.workspace,ROOT/'tools/cfd_worker/fixtures/openfoam12',restart_smoke=args.restart_smoke)
    except WorkerDiagnostic as exc:
        result={'schemaVersion':'untirta.cfd-worker-result.v1','status':'InputRequired','actualCfdRun':False,'reasonCode':exc.code,'action':exc.action}
    if args.report: write_json(args.report,result)
    print(json.dumps(result,indent=2,allow_nan=False))
    return 0 if result.get('actualCfdRun') else 2
if __name__=='__main__': raise SystemExit(main())
