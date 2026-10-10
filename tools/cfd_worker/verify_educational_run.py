"""Read-only verification of downloaded educational OpenFOAM run evidence.

Run metadata wrapper: {schemaVersion: untirta.github-cfd-run-evidence.v1,
repository: owner/repo, run: GitHub REST run object, jobs: REST jobs response or
array, artifact: REST artifact object, outerArchiveSha256: optional SHA-256}.
The GitHub artifact digest covers its uploaded outer ZIP, not the inner native
CFD ZIP. Supplied API JSON is associated evidence, not authenticated by this
offline tool. Pump status/Current/NPSHr are never promoted.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from html import escape
from io import BytesIO
import json
import math
from pathlib import Path
import re
import sys
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'services/cfd_worker/src'))
from untirta_cfd_worker.artifacts import ArtifactLimits, inspect_artifact, _workspace, _regular_source
from untirta_cfd_worker.checkpoint import strict_json
from untirta_cfd_worker.contracts import WorkerDiagnostic
from untirta_cfd_worker.openfoam import field_dimensions
from untirta_cfd_worker import impeller
from untirta_cfd_worker.profiles import IMPELLER_PROFILES, benchmark_reference

PIN = '0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd'
PROFILES = {
    'transient-cavity-v1': {'workflow': '.github/workflows/transient-cfd-validation.yml',
                          'artifact': 'transient-cfd-development-evidence', 'time': 0.05,
                          'solverStep': 'Real bounded physical-transient development fixture'},
    'cavitation-bullet-v1': {'workflow': '.github/workflows/cavitation-cfd-validation.yml',
                           'artifact': 'cavitation-cfd-development-evidence', 'time': 0.00001,
                           'solverStep': 'Real bounded cavitation development fixture'},
    'impeller-transient-v1': {'workflow': '.github/workflows/impeller-cfd-validation.yml',
                             'artifact': 'impeller-cfd-development-evidence', 'time': 0.01,
                             'solverStep': 'Real bounded fixed educational impeller fixture'},
    'impeller-mrf-v1': {'workflow': '.github/workflows/impeller-cfd-validation.yml',
                       'artifact': 'impeller-cfd-development-evidence', 'time': None,
                       'solverStep': 'Real bounded fixed educational impeller fixture'}}
NUM = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'
BULLET_MESH_V3={'surfaceLevel':0,'regionLevel':0,'maxCells':100000,'snap':False,'addLayers':False,
                'meshProfileRevision':'castellated-staircase-uniform-v3','geometryApproximation':'castellated-staircase'}
BULLET_INITIAL_ARCHIVE_MAPPING={
    'native-case/0/Phi':'native-initial-state/potential-initializer/Phi',
    'native-case/0/phi':'native-initial-state/0/phi'}


def reject(code, action):
    raise WorkerDiagnostic(code, action)


def github_association(metadata, profile):
    if profile not in PROFILES:
        reject('CFD_EDUCATIONAL_PROFILE_MISMATCH', 'Select one versioned fixed educational profile.')
    if not isinstance(metadata, dict) or metadata.get('schemaVersion') != 'untirta.github-cfd-run-evidence.v1':
        reject('CFD_GITHUB_METADATA_REQUIRED', 'Supply the bounded GitHub REST run/jobs/artifact wrapper for this downloaded archive.')
    repo, run, artifact = (metadata.get(k) for k in ('repository','run','artifact'))
    if not isinstance(repo,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo) or not isinstance(run,dict) or not isinstance(artifact,dict):
        reject('CFD_GITHUB_METADATA_INVALID', 'Use the exact repository identity and structured REST run/artifact metadata.')
    if run.get('status') != 'completed' or run.get('conclusion') != 'success' or run.get('event') != 'workflow_dispatch':
        reject('CFD_GITHUB_SUCCESSFUL_RUN_REQUIRED', 'Verify a completed successful manual educational workflow run; queued, skipped or unsuccessful jobs cannot establish run evidence.')
    if type(run.get('id')) is not int or run['id'] <= 0 or not re.fullmatch(r'[0-9a-f]{40}',str(run.get('head_sha',''))):
        reject('CFD_GITHUB_RUN_BINDING_INVALID', 'Use a positive run ID and exact immutable 40-character workflow head SHA.')
    repository=run.get('repository')
    if run.get('path') != PROFILES[profile]['workflow'] or not isinstance(repository,dict) or repository.get('full_name') != repo or run.get('html_url') != f'https://github.com/{repo}/actions/runs/{run["id"]}':
        reject('CFD_GITHUB_WORKFLOW_MISMATCH', 'Match this educational profile, repository and successful run URL to its exact workflow path.')
    origin = artifact.get('workflow_run')
    if not isinstance(origin,dict) or origin.get('id') != run['id'] or origin.get('head_sha') != run['head_sha'] or artifact.get('name') != PROFILES[profile]['artifact'] or artifact.get('expired') is not False or type(artifact.get('id')) is not int or artifact['id'] <= 0:
        reject('CFD_GITHUB_ARTIFACT_MISMATCH', 'Download the nonexpired artifact from this exact run ID/head SHA/profile, then retain its sidecar checksum.')
    jobs = metadata.get('jobs')
    jobs = jobs.get('jobs') if isinstance(jobs,dict) else jobs
    if not isinstance(jobs,list) or not 1 <= len(jobs) <= 16:
        reject('CFD_GITHUB_JOB_EVIDENCE_REQUIRED', 'Include bounded REST job/step evidence for the numerical-development job.')
    matching = [job for job in jobs if isinstance(job,dict) and job.get('name') == 'numerical-development-benchmark']
    if len(matching) != 1 or matching[0].get('run_id') != run['id'] or matching[0].get('status') != 'completed' or matching[0].get('conclusion') != 'success':
        reject('CFD_GITHUB_JOB_EVIDENCE_REQUIRED', 'Match one completed successful numerical-development job to this run.')
    if matching[0].get('head_sha',run['head_sha']) != run['head_sha']:
        reject('CFD_GITHUB_JOB_EVIDENCE_REQUIRED', 'Match the actual numerical job to the same immutable workflow head SHA.')
    steps = matching[0].get('steps')
    required = ['Install checksum-pinned OpenFOAM Foundation 12',PROFILES[profile]['solverStep']]
    preparation_names={'Verify FREE storage again and prepare bounded output evidence',
                       'Verify public runner again and prepare bounded output evidence'}
    if not isinstance(steps,list) or len(steps) > 64 or any(not any(isinstance(s,dict) and s.get('name') == name and s.get('status') == 'completed' and s.get('conclusion') == 'success' for s in steps) for name in required):
        reject('CFD_GITHUB_SOLVER_STEP_REQUIRED', 'Verify successful installer, actual solver and artifact-preparation steps; passing contract tests alone is insufficient.')
    if not any(isinstance(s,dict) and s.get('name') in preparation_names and s.get('status')=='completed' and s.get('conclusion')=='success' for s in steps):
        reject('CFD_GITHUB_SOLVER_STEP_REQUIRED', 'Verify one completed successful preparation step for this private-allowance or public-runner workflow; a step label does not independently establish storage allowance.')
    outer = metadata.get('outerArchiveSha256')
    if outer is not None:
        if not isinstance(outer,str) or not re.fullmatch(r'[0-9a-f]{64}',outer) or artifact.get('digest') != 'sha256:'+outer:
            reject('CFD_GITHUB_OUTER_ARCHIVE_MISMATCH', 'Compare the GitHub digest with the downloaded OUTER ZIP SHA-256, separately from the native inner ZIP.')
    return {'repository': repo,'runId':run['id'],'headSha':run['head_sha'],'runUrl':run['html_url'],
            'artifactId':artifact['id'],'artifactName':artifact['name'],'outerArchiveSha256':outer,
            'outerDigestMatched':outer is not None,'metadataAssociationMatched':True,
            'sourceApiUrls':{'run':f'https://api.github.com/repos/{repo}/actions/runs/{run["id"]}',
                'jobs':f'https://api.github.com/repos/{repo}/actions/runs/{run["id"]}/jobs',
                'artifact':f'https://api.github.com/repos/{repo}/actions/artifacts/{artifact["id"]}'},
            'accountStorageAllowanceVerified':False,
            'authenticityVerified':False,'source': 'Operator-supplied GitHub REST API metadata; offline authenticity not independently established.'}


def foam_body(raw):
    try:
        text = raw.decode('utf-8')
    except UnicodeError:
        reject('CFD_EDUCATIONAL_NATIVE_FORMAT_INVALID','Use bounded uncompressed ASCII OpenFOAM fields and mesh in this educational export.')
    text = re.sub(r'/\*.*?\*/','',text,flags=re.S)
    text = re.sub(r'//[^\n]*','',text)
    if re.search(r'\b(?:nan|inf|infinity)\b',text,re.I):
        reject('CFD_EDUCATIONAL_NONFINITE_FIELD','Inspect nonfinite native mesh/field values before presenting the numerical run.')
    if not re.search(r'\bformat\s+ascii\s*;',text):
        reject('CFD_EDUCATIONAL_NATIVE_FORMAT_INVALID','Expected declared ASCII native solver files; binary decoding is outside this verifier.')
    return re.sub(r'\bFoamFile\s*\{[^}]*\}','',text,count=1,flags=re.S)


def native_list(raw):
    body = foam_body(raw)
    match = re.fullmatch(r'\s*(\d+)\s*\((.*)\)\s*;?\s*',body,flags=re.S)
    if not match or int(match[1]) > 1000000:
        reject('CFD_EDUCATIONAL_NATIVE_LIST_INVALID','Supply an exact bounded native mesh list with its declared element count.')
    return int(match[1]),match[2]


def mesh_evidence(files, prefix, *, points_path=None):
    def raw(name):
        path = points_path if name=='points' and points_path is not None else prefix+'/constant/polyMesh/'+name
        if path not in files: reject('CFD_EDUCATIONAL_MESH_REQUIRED','Include points/faces/owner/neighbour from the same generated native mesh.')
        return files[path]
    points_count,points_text = native_list(raw('points'))
    triples = re.findall(r'\(([^()]+)\)',points_text)
    points = [[float(v) for v in re.findall(NUM,t)] for t in triples]
    if not points_count or len(points) != points_count or re.sub(r'\([^()]+\)','',points_text).strip() or any(len(p)!=3 or not all(math.isfinite(v) for v in p) or re.sub(NUM,'',triples[i]).strip() for i,p in enumerate(points)):
        reject('CFD_EDUCATIONAL_MESH_COUNT_MISMATCH','Match each declared mesh point to three finite SI coordinates.')
    owner_count,owner_text = native_list(raw('owner'))
    neighbor_count,neighbor_text = native_list(raw('neighbour'))
    owner,neighbor = ([int(v) for v in re.findall(r'\d+',text)] for text in (owner_text,neighbor_text))
    if len(owner)!=owner_count or len(neighbor)!=neighbor_count or not owner or neighbor_count>owner_count or re.search(r'[^\d\s]',owner_text+neighbor_text):
        reject('CFD_EDUCATIONAL_MESH_COUNT_MISMATCH','Match owner/neighbour label counts to the native face topology.')
    cells=max(owner+neighbor)+1
    if cells>100000 or set(owner+neighbor)!=set(range(cells)):
        reject('CFD_EDUCATIONAL_MESH_COUNT_MISMATCH','Use a contiguous bounded mesh cell index set from the actual mesher output.')
    face_count,face_text=native_list(raw('faces'))
    faces=re.findall(r'(\d+)\s*\(([^()]+)\)',face_text)
    if face_count!=owner_count or len(faces)!=face_count or re.sub(r'\d+\s*\([^()]+\)','',face_text).strip():
        reject('CFD_EDUCATIONAL_MESH_COUNT_MISMATCH','Pair each native face with one owner entry.')
    for count,indices in faces:
        ids=[int(v) for v in re.findall(r'\d+',indices)]
        if len(ids)!=int(count) or len(ids)<3 or len(ids)>256 or any(v>=points_count for v in ids) or re.search(r'[^\d\s]',indices):
            reject('CFD_EDUCATIONAL_MESH_TOPOLOGY_INVALID','Use finite bounded mesh faces referencing existing native points.')
    return {'cellCount':cells,'pointCount':points_count,'faceCount':face_count,'internalFaceCount':neighbor_count,
            'boundsM':{'minimum':[min(p[i] for p in points) for i in range(3)],'maximum':[max(p[i] for p in points) for i in range(3)]}}


def field_evidence(raw, *, cells, components, dimensions):
    body=foam_body(raw)
    if field_dimensions(body)!=dimensions:
        reject('CFD_EDUCATIONAL_FIELD_UNIT_MISMATCH','Keep one explicit native dimension set; [] means dimensionless only, while kinematic p, absolute pressure and p_rgh remain distinct quantities.')
    match=re.search(r'internalField\s+(?:nonuniform\s+List<(scalar|vector)>\s+(\d+)\s*\((.*?)\)\s*;|uniform\s+([^;]+);)',body,re.S)
    if not match: reject('CFD_EDUCATIONAL_FIELD_FORMAT_INVALID','Expected an explicit native uniform or nonuniform internal field.')
    values=[float(v) for v in re.findall(NUM,match[3] or match[4])]
    uniform=match[4] is not None
    expected=components if uniform else components*cells
    value_text=match[3] or match[4]
    tokens=re.sub(NUM,'',value_text)
    if re.sub(r'[()\s]','',tokens) or not uniform and (int(match[2])!=cells or match[1]!=('vector' if components==3 else 'scalar')) or len(values)!=expected or not all(math.isfinite(v) for v in values):
        reject('CFD_EDUCATIONAL_FIELD_COUNT_MISMATCH','Match scalar/vector native field cardinality to the actual mesh cell count; truncated lists are not valid results.')
    if components==3 and (len(re.findall(r'\([^()]+\)',value_text))!=(1 if uniform else cells) or any(len(re.findall(NUM,v))!=3 for v in re.findall(r'\([^()]+\)',value_text))):
        reject('CFD_EDUCATIONAL_FIELD_COUNT_MISMATCH','Each native vector must contain exactly three finite components for one cell.')
    if components==1 and ('(' in value_text or ')' in value_text):
        reject('CFD_EDUCATIONAL_FIELD_COUNT_MISMATCH','Native scalar values must not be vector tuples.')
    return {'minimum':min(values),'maximum':max(values),'cellCount':cells,'storedScalarComponentCount':len(values),'uniform':uniform}


def _entry(raw,key):
    text=re.sub(r'/\*.*?\*/|//[^\n]*','',raw.decode('utf-8'),flags=re.S)
    match=re.findall(r'(?:^|[;{}\n])\s*'+re.escape(key)+r'\s+('+NUM+r')(?:\s+\[[^\[\];]+\])?\s*;',text)
    if len(match)!=1: reject('CFD_EDUCATIONAL_SETTINGS_INVALID','Require one explicit finite '+key+' native setting.')
    value=float(match[0])
    if not math.isfinite(value): reject('CFD_EDUCATIONAL_SETTINGS_INVALID','Use finite SI settings.')
    return value


class InertNativeView:
    """Read-only native byte view used by the adapter's exact topology gate.

    The inspected archive is already bounded. No archive member is executed,
    extracted to disk, or substituted by a regenerated mesh.
    """
    def __init__(self,files,path):
        self.files,self.path=files,path

    def __truediv__(self,name):
        return InertNativeView(self.files,self.path+'/'+name)

    def read_text(self,encoding='utf-8'):
        return self.files[self.path].decode(encoding)

    def open(self,mode):
        if mode!='rb': reject('CFD_EDUCATIONAL_READ_ONLY_REQUIRED','Native evidence views allow binary reads only.')
        return BytesIO(self.files[self.path])


def impeller_source_evidence(files,prefix,profile):
    fixture=ROOT/'tools/cfd_worker/fixtures/openfoam12-impeller'
    source=impeller._verified_fixture(fixture)
    physical=profile=='impeller-transient-v1'
    expected={}
    source_rows={item['path']:item for item in source['files']}
    for relative in ('system/blockMeshDict','system/controlDict','system/createNonConformalCouplesDict',
                     'system/decomposeParDict','system/fvSchemes','system/fvSolution',
                     'constant/physicalProperties','constant/momentumTransport'):
        source_path=('mrf-reference/'+relative if not physical and relative in ('system/fvSchemes','system/fvSolution') else 'native/'+relative)
        expected[relative]=((fixture/source_path).read_text(encoding='utf-8'),source_path)
    motion='constant/dynamicMeshDict' if physical else 'constant/MRFProperties'
    source_path=('native/' if physical else 'mrf-reference/')+motion
    expected[motion]=((fixture/source_path).read_text(encoding='utf-8'),source_path)
    changes={'writeFormat':'ascii','writePrecision':'12','runTimeModifiable':'false','adjustTimeStep':'no',
             'writeControl':'timeStep','purgeWrite':'0',
             'endTime':'0.01' if physical else '50','deltaT':'0.001' if physical else '1','writeInterval':'10' if physical else '50'}
    control,control_source=expected['system/controlDict']
    for key,value in changes.items():
        control,count=re.subn(r'(?m)^(\s*'+re.escape(key)+r'\s+)[^;]+;',lambda match:match[1]+value+';',control)
        if count!=1: reject('CFD_EDUCATIONAL_SOURCE_PIN_MISMATCH','Require one exact explicit control setting per reviewed fixed impeller override.')
    expected['system/controlDict']=(control,control_source)
    if not physical:
        mrf,mrf_source=expected[motion]
        mrf=mrf.replace('cellZone    rotor;','cellZone    rotating;').replace('axis        (0 0 1);','axis        (1 0 0);')
        expected[motion]=(mrf,mrf_source)
    prohibited=['system/functions']+(['constant/MRFProperties'] if physical else ['constant/dynamicMeshDict'])
    if any(prefix+'/'+path in files for path in prohibited):
        reject('CFD_EDUCATIONAL_PROFILE_SETTINGS_MISMATCH','Keep the exact reviewed derived fixed profile; auxiliary function objects and the other rotation model are not part of this smoke.')
    checked=[]
    for relative,(text,source_path) in expected.items():
        path=prefix+'/'+relative
        if path not in files or files[path].decode('utf-8').replace('\r\n','\n')!=text.replace('\r\n','\n'):
            reject('CFD_EDUCATIONAL_PROFILE_SETTINGS_MISMATCH','Match every package-pinned impeller/MRF reference dictionary with only its explicit documented derivatives.')
        checked.append({'path':path,'tutorialSourcePath':source_path,'tutorialSourceSha256':source_rows[source_path]['sha256'],
                        'nativeSha256':sha256(files[path]).hexdigest()})
    return {'sourcePackage':source['sourcePackage'],'sourceCase':source['sourceCase'],'checkedSettings':checked,
        'geometryIsEducationalTutorial':True,'meshOverride':None,
        'geometryLimitations':['Official idealized30-degree radial impeller passage; no full volute or manufactured Pump identity.',
                               '9371 native cells and a short fixed run do not establish mesh independence or converged Pump performance.'],
        'mesherExecutionAuthenticityVerified':False,'quantitativeGeometryAccuracyValidated':False}


def impeller_ncc_evidence(files,prefix):
    boundary=foam_body(files[prefix+'/constant/polyMesh/boundary'])
    expected=[]
    for side in ('In','Out'):
        patches=['nonCouple'+side+'1','nonCouple'+side+'2']
        for angle in range(0,360,30):
            base='nonConformalCouple'+side+str(angle)
            for index,patch in enumerate(patches):
                name=base+'_on_'+patch
                blocks=re.findall(r'\b'+re.escape(name)+r'\s*\{([^{}]*)\}',boundary,re.S)
                neighbour=base+'_on_'+patches[1-index]
                if (len(blocks)!=1 or not re.search(r'\btype\s+nonConformalCyclic\s*;',blocks[0])
                        or not re.search(r'\boriginalPatch\s+'+patch+r'\s*;',blocks[0])
                        or not re.search(r'\bneighbourPatch\s+'+neighbour+r'\s*;',blocks[0])):
                    reject('CFD_EDUCATIONAL_NCC_TOPOLOGY_MISMATCH','Preserve all48 native nonConformalCyclic patches for the24 exact source In/Out couples, with original and reciprocal neighbour patch bindings; NCC is not AMI.')
                expected.append(name)
    return {'type':'NCC','configuredCouples':24,'nativeCyclicPatchCount':len(expected),'nativePatchNames':expected,
            'nativeBoundarySha256':sha256(files[prefix+'/constant/polyMesh/boundary']).hexdigest(),
            'sourceConfigurationSha256':sha256(files[prefix+'/system/createNonConformalCouplesDict']).hexdigest()}


def impeller_logged_iterations(log):
    values=[value for line in log.splitlines() if (value:=logged_seconds(line,'Time')) is not None]
    if values!=list(range(1,51)):
        reject('CFD_EDUCATIONAL_STEADY_ITERATION_MISMATCH','Require fifty actual consecutive steady solver iterations1..50; native Time labels are not physical seconds for MRF.')
    return {'interpretation':'steady-iteration','iteration':50,'loggedIterationCount':len(values),
            'physicalTimeSeconds':None,'timeStepSeconds':None,'step':None}


def impeller_student_equations(profile,*,states):
    mn=lambda value:'<mn>'+escape(str(value))+'</mn>'
    rows=[equation('CFD_EDUCATIONAL_IMPELLER_CELLS','Structured native-cell count',
        '<mi>N</mi><mo>=</mo><mo>∑</mo><msub><mi>N</mi><mtext>x,b</mtext></msub><msub><mi>N</mi><mtext>y,b</mtext></msub><msub><mi>N</mi><mtext>z,b</mtext></msub>',
        {'rotorBlocks[1]':3,'rotorNx[1]':11,'rotorNy[1]':11,'rotorNz[1]':17,'statorBlocks[1]':2,'statorNx[1]':10,'statorNy[1]':10,'statorNz[1]':16},
        '<mn>3</mn><mo>×</mo><mn>11</mn><mo>×</mo><mn>11</mn><mo>×</mo><mn>17</mn><mo>+</mo><mn>2</mn><mo>×</mo><mn>10</mn><mo>×</mo><mn>10</mn><mo>×</mo><mn>16</mn>',9371,'cells'),
        equation('CFD_EDUCATIONAL_IMPELLER_OMEGA','Angular speed from source RPM',
        '<mi>ω</mi><mo>=</mo><mfrac><mrow><mn>2</mn><mi>π</mi><mi>n</mi></mrow><mn>60</mn></mfrac>',
        {'n[rpm]':60,'secondsPerMinute[s/min]':60},'<mfrac><mrow><mn>2</mn><mi>π</mi><mo>×</mo><mn>60</mn></mrow><mn>60</mn></mfrac>',impeller.OMEGA,'rad/s')]
    if profile=='impeller-transient-v1':
        elapsed=states[-1]['physicalTimeSeconds']; dt=states[-1]['timeStepSeconds']
        rows += [equation('CFD_EDUCATIONAL_ELAPSED_TIME','Elapsed physical time','<mi>T</mi><mo>=</mo><msub><mi>t</mi><mi>end</mi></msub><mo>−</mo><msub><mi>t</mi><mn>0</mn></msub>',
                    {'t0[s]':0,'tend[s]':elapsed},mn(elapsed)+'<mo>−</mo><mn>0</mn>',elapsed,'s'),
                 equation('CFD_EDUCATIONAL_PHYSICAL_STEPS','Fixed physical steps','<mi>N</mi><mo>=</mo><mfrac><mi>T</mi><mi>Δt</mi></mfrac>',
                    {'T[s]':elapsed,'dt[s]':dt},'<mfrac>'+mn(elapsed)+mn(dt)+'</mfrac>',len(states),'1'),
                 equation('CFD_EDUCATIONAL_IMPELLER_ANGLE','Rotor angle at the actual saved physical time','<mi>θ</mi><mo>=</mo><mi>ω</mi><mi>T</mi>',
                    {'omega[rad/s]':impeller.OMEGA,'T[s]':elapsed},mn(impeller.OMEGA)+'<mo>×</mo>'+mn(elapsed),impeller.OMEGA*elapsed,'rad')]
    else:
        rows.append(equation('CFD_EDUCATIONAL_STEADY_ITERATIONS','Actual steady iterations (not physical time)',
            '<msub><mi>N</mi><mi>iterations</mi></msub><mo>=</mo><msub><mi>i</mi><mi>last</mi></msub>',
            {'firstIteration[1]':1,'lastIteration[1]':50},'<mn>50</mn>',50,'iterations'))
    for row in rows:
        row['source']={'title':'Checksum-bound OpenFOAM Foundation12 package20250206 native impeller and MRF reference',
            'url':'https://downloads.sourceforge.net/project/foam/foam/ubuntu/dists/noble/main/binary-amd64/openfoam12_20250206_amd64.deb',
            'reference':'tutorials/incompressibleFluid/impeller; declared MRF derivatives from mixerVessel2DMRF; source package SHA-256 '+impeller.OPENFOAM_PACKAGE_SHA256}
    return rows


def verify_impeller_native(files,report,inspected,profile,mesh):
    from untirta_cfd_worker.openfoam import verify_mesh_log
    prefix='output/native-case'; physical=profile=='impeller-transient-v1'; folder='0.01' if physical else '50'
    if mesh['cellCount']!=9371:
        reject('CFD_EDUCATIONAL_MESH_PROFILE_MISMATCH','The unchanged official impeller blocks must produce9371 cells, including6171 rotating cells.')
    if report.get('restart') or any(path.startswith('output/native-restart-case/') for path in files):
        reject('CFD_EDUCATIONAL_RESTART_EVIDENCE_MISMATCH','The reviewed impeller profiles have no restart execution acceptance; retain their first-run checkpoint only.')
    meshlog=files['output/logs/checkMesh.log'].decode('utf-8')
    verify_mesh_log(meshlog); validate_mesh_log(meshlog,mesh)
    for name in ('blockMesh','createNonConformalCouples','foamRun'):
        if not re.search(r'(?m)^\s*End\s*$',files['output/logs/'+name+'.log'].decode('utf-8')):
            reject('CFD_EDUCATIONAL_RUN_LOG_INCOMPLETE','Require complete actual blockMesh/NCC/foamRun logs for this exact fixed impeller run.')
    log=files['output/logs/foamRun.log'].decode('utf-8')
    if logged_mesh_time(log)!=0:
        reject('CFD_EDUCATIONAL_LOG_TIME_MISMATCH','The fixed impeller must load its initial native mesh at index/time zero.')
    if len(inspected['checkpoints'])!=1:
        reject('CFD_EDUCATIONAL_CHECKPOINT_TIME_MISMATCH','Retain exactly one final paired native impeller checkpoint, without extra or mixed-profile solution states.')
    cp=strict_json(files[inspected['checkpoints'][0]['path']])
    expected_state={'interpretation':'physical','physicalTimeSeconds':0.01,'timeStepSeconds':0.001,'step':10,'iteration':None} if physical else {
        'interpretation':'steady-iteration','physicalTimeSeconds':None,'timeStepSeconds':None,'step':None,'iteration':50}
    if (not {'timeInterpretation','physicalTimeSeconds','iteration'} <= set(report)
            or cp['timeState']!=expected_state or report.get('timeInterpretation')!=expected_state['interpretation']
            or report.get('physicalTimeSeconds')!=expected_state['physicalTimeSeconds'] or report.get('iteration')!=expected_state['iteration']):
        reject('CFD_EDUCATIONAL_TIME_INTERPRETATION_MISMATCH','Pair transient10×0.001s→0.01s or MRF50steady iterations with exact null inapplicable time fields; never relabel iterations as seconds.')
    if physical:
        states=logged_time_states(log,profile,files[prefix+'/system/controlDict'])
        if len(states)!=10 or any(not math.isclose(state['physicalTimeSeconds'],(index+1)*0.001,abs_tol=1e-12) or state['timeStepSeconds']!=0.001 for index,state in enumerate(states)):
            reject('CFD_EDUCATIONAL_TIME_STEP_MISMATCH','Complete all ten consecutive actual fixed0.001s integration steps through0.01s.')
    else:
        impeller_logged_iterations(log); states=[]
    definitions={'U':(3,'0 1 -1 0 0 0 0'),'p':(1,'0 2 -2 0 0 0 0'),'k':(1,'0 2 -2 0 0 0 0'),
                 'epsilon':(1,'0 2 -3 0 0 0 0'),'nut':(1,'0 2 -1 0 0 0 0')}
    initial_fields={}
    for name,(components,dimensions) in definitions.items():
        path='output/native-initial-state/0/'+name
        # Supplied initial dictionaries legitimately use $internalField in
        # boundary values. Count declarations, not variable references. Final
        # solved fields below use the adapter's stricter written-field gate.
        if len(re.findall(r'(?<![\w$])internalField\s+',foam_body(files[path])))!=1:
            reject('CFD_NATIVE_FIELD_INVALID','Retain exactly one initial internalField declaration; boundary $internalField references are distinct native dictionary substitutions.')
        initial_fields[name]=field_evidence(files[path],cells=9371,components=components,dimensions=dimensions)
    fields={}
    for name,(components,dimensions) in definitions.items():
        selected=[item for item in cp['nativeFiles'] if Path(item['path']).name==name]
        if len(selected)!=1 or selected[0]['path']!=prefix+'/'+folder+'/'+name:
            reject('CFD_EDUCATIONAL_NATIVE_FIELD_REQUIRED','Pair each finite U/p/k/epsilon/nut field at the exact final native time/iteration directory.')
        impeller.checked_field(InertNativeView(files,selected[0]['path']),dimensions,components,9371)
        fields[name]=field_evidence(files[selected[0]['path']],cells=9371,components=components,dimensions=dimensions)
        fields[name].update(dimensions=dimensions,unit={'U':'m/s','p':'m2/s2','k':'m2/s2','epsilon':'m2/s3','nut':'m2/s'}[name])
    uniform_path=prefix+'/'+folder+'/uniform/time'
    if uniform_path not in {item['path'] for item in cp['settingsFiles']}:
        reject('CFD_EDUCATIONAL_CHECKPOINT_TIME_MISMATCH','Include the actual saved uniform/time index/value/deltaT paired with this final native state.')
    for key,value in [('value',0.01 if physical else 50),('deltaT',0.001 if physical else 1),('index',10 if physical else 50)]:
        if not math.isclose(_entry(files[uniform_path],key),value,rel_tol=1e-8,abs_tol=1e-12):
            reject('CFD_EDUCATIONAL_CHECKPOINT_TIME_MISMATCH','Match native uniform/time to the exact physical state or steady iteration; MRF native value/deltaT are not seconds.')
    initial='output/native-initial-state/constant/polyMesh/points'
    original_case_points=files.get(prefix+'/constant/polyMesh/points')
    if ((original_case_points is not None and files[initial]!=original_case_points)
            or initial not in {item['path'] for item in cp['setupState']['initialStateFiles']}):
        reject('CFD_EDUCATIONAL_INITIAL_MESH_MISMATCH','Preserve the checksum-bound original mesh position before solving; final geometry must not replace the native initial snapshot.')
    count=len(impeller.points(InertNativeView(files,initial)))
    rotor=impeller.rotating_point_labels(InertNativeView(files,prefix+'/constant/polyMesh'),count)
    ncc=impeller_ncc_evidence(files,prefix)
    mesh_paths={item['path'] for item in cp['meshFiles']}
    ncc_paths=[path for path in mesh_paths if path.startswith(prefix+'/') and path.endswith('/fvMesh/polyFaces')]
    if not ncc_paths or physical and prefix+'/0.01/fvMesh/polyFaces' not in ncc_paths:
        reject('CFD_EDUCATIONAL_NCC_TOPOLOGY_MISMATCH','Include actual finite-volume NCC polyFaces topology in the paired native checkpoint; moving NCC requires its final0.01s topology.')
    for path in ncc_paths:
        foam_body(files[path])
    law=prefix+'/constant/'+('dynamicMeshDict' if physical else 'MRFProperties')
    expected_motion={'mode':'sliding-mesh' if physical else 'mrf','rotorAngleRad':impeller.OMEGA*0.01 if physical else None,
        'angularSpeedRadS':impeller.OMEGA,'axis':[1,0,0],'origin':[0,0,0],'motionLawSha256':sha256(files[law]).hexdigest()}
    if cp['motion']!=expected_motion or cp['interface']!={'type':'NCC','identifiers':impeller.INTERFACE_NAMES,'configurationSha256':ncc['sourceConfigurationSha256']}:
        reject('CFD_EDUCATIONAL_ROTOR_FRAME_MISMATCH','Bind the exact60rpm,+x-axis native motion law or fixed MRF frame with NCC, source configuration and saved rotor angle.')
    if physical:
        saved=prefix+'/0.01/polyMesh/points'
        motion=impeller.verify_motion(InertNativeView(files,initial),InertNativeView(files,saved),0.01,rotor_points=rotor)
        checkpoint_fields(cp,files,mesh,definitions,states[-1])
    else:
        if any(path.startswith(prefix+'/50/polyMesh/') for path in files):
            reject('CFD_EDUCATIONAL_ROTOR_FRAME_MISMATCH','MRF uses a fixed mesh; moved final points cannot substitute for steady-frame geometry.')
        motion={'mode':'mrf','fixedMeshPositionMatched':True,'rotatingCellCount':6171,'rotatingZonePointCount':len(rotor),
                'physicalTimeSeconds':None,'rotorAngleRad':None}
    return {'timeInterpretation':expected_state['interpretation'],'physicalEndTimeSeconds':0.01 if physical else None,
        'physicalStepCount':10 if physical else None,'steadyIterationCount':None if physical else 50,
        'initialFields':initial_fields,'fields':fields,'nccInterface':ncc,'meshMotionEvidence':motion,
        'initialMeshPosition':{'path':initial,'sha256':sha256(files[initial]).hexdigest(),
            'initialSnapshotChecksumMatched':True,'originalCasePointsSeparatelyPresent':original_case_points is not None,
            'claim':'The checkpoint retains original points in its explicit initial-state snapshot; no missing native-case path is regenerated or aliased.'},
        'equations':impeller_student_equations(profile,states=states),
        'physicalRotorRestartVerified':False,'restartExecutionVerified':False,'absolutePressureBasisAvailable':False,
        'densityKgM3':None,'vaporPressurePa':None,'headM':None,'npsh3M':None}


def profile_source_evidence(files,prefix,profile):
    """Check immutable tutorial settings and explicitly documented smoke overrides.

    Generated mesh/initialized fields remain derived evidence; a settings match
    does not authenticate mesher execution or establish geometry independence.
    """
    if profile in IMPELLER_PROFILES:
        return impeller_source_evidence(files,prefix,profile)
    fixture=ROOT/'tools/cfd_worker/fixtures/openfoam12'
    provenance=strict_json((fixture/'provenance.json').read_bytes())
    if provenance.get('commit')!=PIN:
        reject('CFD_EDUCATIONAL_SOURCE_PIN_MISMATCH','Restore the exact pinned tutorial provenance used by this verifier.')
    checked=[]
    for item in provenance['files']:
        source_path=item['path']
        if not source_path.startswith(profile+'/'): continue
        relative=source_path[len(profile)+1:]
        if not relative.startswith(('system/','constant/')) or '/geometry/' in '/'+relative: continue
        raw=(fixture/source_path).read_bytes()
        if sha256(raw).hexdigest()!=item['sha256'] or len(raw)!=item['bytes']:
            reject('CFD_EDUCATIONAL_SOURCE_PIN_MISMATCH','Restore immutable local tutorial source bytes/checksums before interpreting imported results.')
        expected=raw.decode('utf-8').replace('\r\n','\n')
        if relative=='system/controlDict':
            changes={'writeFormat':'ascii','runTimeModifiable':'false','purgeWrite':'0'}
            changes.update({'endTime':'0.05','deltaT':'0.005','writeInterval':'5'} if profile=='transient-cavity-v1' else {'endTime':'0.00001','writeInterval':'0.000005','maxCo':'1','maxAlphaCo':'0.5'})
            for key,value in changes.items():
                expected,count=re.subn(r'(?m)^(\s*'+re.escape(key)+r'\s+)[^;]+;',lambda m:m[1]+value+';',expected)
                if count!=1: reject('CFD_EDUCATIONAL_SOURCE_PIN_MISMATCH','Retain one exact documented smoke override per pinned control setting.')
        elif relative=='system/snappyHexMeshDict':
            for key in ('maxGlobalCells','maxLocalCells'):
                expected,count=re.subn(r'(?m)^(\s*'+key+r'\s+)[^;]+;',lambda m:m[1]+'100000;',expected)
                if count!=1: reject('CFD_EDUCATIONAL_SOURCE_PIN_MISMATCH','Retain exact bounded tutorial meshing overrides.')
            expected=expected.replace('level (4 4);','level (0 0);').replace('levels ((0.0 3));','levels ((0.0 0));')
            for key in ('snap','addLayers'):
                expected,count=re.subn(r'(?m)^(\s*'+key+r'\s+)[^;]+;',lambda m:m[1]+'false;',expected)
                if count!=1: reject('CFD_EDUCATIONAL_SOURCE_PIN_MISMATCH','Retain one explicit uniform-v3 snap=false/addLayers=false override without altering the official STL source.')
        path=prefix+'/'+relative
        if path not in files or files[path].decode('utf-8').replace('\r\n','\n')!=expected:
            reject('CFD_EDUCATIONAL_PROFILE_SETTINGS_MISMATCH','Match pinned physics/mesh/numerical dictionaries with only the documented versioned smoke overrides; arbitrary altered source is a different study.')
        checked.append({'path':path,'tutorialSourceSha256':item['sha256'],'nativeSha256':sha256(files[path]).hexdigest()})
    if profile=='transient-cavity-v1':
        for name in ('U','p','k','epsilon','nut'):
            path='output/native-initial-state/0/'+name
            source_path=profile+'/0/'+name
            item=next(i for i in provenance['files'] if i['path']==source_path)
            if path not in files or sha256(files[path]).hexdigest()!=item['sha256'] or len(files[path])!=item['bytes']:
                reject('CFD_EDUCATIONAL_PROFILE_INITIAL_STATE_MISMATCH','Retain the exact supplied cavity initial fields copied before its first physical solver step.')
    return {'sourceCommit':PIN,'checkedSettings':checked,'geometryIsEducationalTutorial':True,
            'meshOverride':dict(BULLET_MESH_V3) if profile=='cavitation-bullet-v1' else None,
            'geometryLimitations':['Unchanged 15×15×50 background, no local refinement; approximately five cells across the source 0.01 m bullet diameter.',
                'Castellated staircase surface is a coarse educational geometry approximation; no quantitative cavitation/bullet/Pump accuracy acceptance.'] if profile=='cavitation-bullet-v1' else [],
            'mesherExecutionAuthenticityVerified':False,'quantitativeGeometryAccuracyValidated':False}


def validate_mesh_log(log,mesh):
    if not re.search(r'(?m)^\s*Mesh OK\.\s*$',log) or re.search(r'(?im)\bFailed\s+[1-9]\d*\s+mesh checks?\b',log):
        reject('CFD_EDUCATIONAL_MESH_ACCEPTANCE_REQUIRED','Require all strict native checkMesh geometry/topology checks to pass; concave or failed checks remain unacceptable for this educational run.')
    for label,key in [('cells','cellCount'),('points','pointCount'),('faces','faceCount'),('internal faces','internalFaceCount')]:
        counts=re.findall(r'(?m)^\s*'+label+r'\s*:\s*(\d+)\s*$',log)
        if len(counts)!=1 or int(counts[0])!=mesh[key]:
            reject('CFD_EDUCATIONAL_MESH_LOG_MISMATCH','Match each strict checkMesh count to the exact imported generated native mesh, without reusing another mesh-check log.')


def validate_solver_profile(solver,profile):
    if profile in IMPELLER_PROFILES:
        source=impeller._verified_fixture(ROOT/'tools/cfd_worker/fixtures/openfoam12-impeller')
        physical=profile=='impeller-transient-v1'
        expected={'distribution':'OpenFOAM-Foundation','version':'12','profile':profile,
            'sourcePackage':source['sourcePackage'],'sourceCase':source['sourceCase'],
            'rotationModel':'sliding-mesh' if physical else 'mrf',
            'timeInterpretation':'physical' if physical else 'steady-iteration',
            'interfaceModel':'Foundation-12-NCC',
            'physicalModel':'single-phase-incompressible-radial-impeller-educational-smoke'}
        if not isinstance(solver,dict) or any(solver.get(key)!=value for key,value in expected.items()):
            reject('CFD_EDUCATIONAL_SOLVER_METHOD_MISMATCH','Bind the exact package-derived Foundation12 impeller profile, NCC interface and physical-time or steady-iteration model.')
        fluid=solver.get('fluid')
        geometry=solver.get('geometry')
        if (not isinstance(fluid,dict) or fluid.get('kinematicViscosityM2S')!=1e-5 or fluid.get('densityKgM3') is not None or fluid.get('vaporPressurePa') is not None
                or not isinstance(geometry,dict) or geometry.get('nativeCellCount')!=9371 or geometry.get('rotatingCellZone')!='rotating'
                or geometry.get('axis')!=[1,0,0] or geometry.get('originM')!=[0,0,0] or geometry.get('lengthUnit')!='m'):
            reject('CFD_EDUCATIONAL_SOLVER_METHOD_MISMATCH','Retain the native9371-cell +x rotor/nu-only fluid basis; do not invent density, vapour pressure or absolute pressure.')
        return
    if not isinstance(solver,dict) or any(solver.get(k)!=v for k,v in {'distribution':'OpenFOAM-Foundation','version':'12','sourceCommit':PIN,'profile':profile}.items()):
        reject('CFD_EDUCATIONAL_SOLVER_METHOD_MISMATCH','Associate the worker report with the exact pinned educational solver/profile.')
    expected=BULLET_MESH_V3 if profile=='cavitation-bullet-v1' else None
    if solver.get('meshOverride')!=expected:
        reject('CFD_EDUCATIONAL_MESH_PROFILE_MISMATCH','Declare the exact versioned educational mesh override; bullet uniform-v3 requires castellated staircase, snap=false, addLayers=false, no local refinement0/0 and max100000 cells.')


def initial_state_archive_evidence(files,solver,profile):
    """Verify archive-only placement of potential Phi and physical flux phi.

    Foundation potentialFoam reads the original Phi and writes lowercase phi.
    Their snapshot destinations must be distinct on case-insensitive systems.
    This checks preserved source bytes and declared mapping; the archive does
    not independently expose the original live case's time-zero filenames.
    """
    mapping=solver.get('initialStateArchiveMapping',{})
    expected=BULLET_INITIAL_ARCHIVE_MAPPING if profile=='cavitation-bullet-v1' else {}
    if mapping!=expected:
        reject('CFD_EDUCATIONAL_INITIAL_ARCHIVE_MAPPING_MISMATCH','Retain the exact archive-only mapping of original potential Phi and physical flux phi; do not rename native solver fields or relax case-collision checks.')
    evidence={'archiveMapping':dict(mapping),'archiveMappingMatched':True,
              'mappingScope':'Independent initial-state archive only; native case paths remain declared original.',
              'nativeCasePathPreservationIndependentlyVerified':False}
    if profile!='cavitation-bullet-v1': return evidence
    auxiliary='output/native-initial-state/potential-initializer/Phi'
    flux='output/native-initial-state/0/phi'
    source_path=profile+'/0/Phi'
    original=(ROOT/'tools/cfd_worker/fixtures/openfoam12'/source_path).read_bytes()
    provenance=strict_json((ROOT/'tools/cfd_worker/fixtures/openfoam12/provenance.json').read_bytes())
    entry=next(item for item in provenance['files'] if item['path']==source_path)
    if sha256(original).hexdigest()!=entry['sha256'] or len(original)!=entry['bytes']:
        reject('CFD_EDUCATIONAL_SOURCE_PIN_MISMATCH','Restore the immutable pinned potential initializer before interpreting its archived snapshot.')
    if files.get(auxiliary)!=original or flux not in files or 'output/native-initial-state/0/Phi' in files:
        reject('CFD_EDUCATIONAL_INITIAL_ARCHIVE_MAPPING_MISMATCH','Preserve exact original Phi bytes in the separate potential-initializer folder, retain physical phi at time zero, and remove ambiguous snapshot aliases.')
    if field_dimensions(foam_body(files[flux]))!='0 3 -1 0 0 0 0':
        reject('CFD_EDUCATIONAL_FIELD_UNIT_MISMATCH','The initial physical phi is volumetric flux in m3/s; it must not be replaced by the uppercase velocity potential Phi.')
    evidence.update(potentialInitializerSourceBytesMatched=True,physicalFluxSnapshotPresent=True,
                    potentialInitializerSha256=sha256(original).hexdigest(),physicalFluxSnapshotSha256=sha256(files[flux]).hexdigest())
    return evidence


def validate_stages(stages,profile):
    required=(['blockMesh','createNonConformalCouples','checkMesh','foamRun'] if profile in IMPELLER_PROFILES else
              ['blockMesh','snappyHexMesh','checkMesh','potentialFoam','foamRun'] if profile=='cavitation-bullet-v1' else ['blockMesh','checkMesh','foamRun'])
    if not isinstance(stages,list): reject('CFD_EDUCATIONAL_STAGE_EVIDENCE_REQUIRED','Include bounded actual mesher/mesh-check/initialization/solver stage evidence.')
    positions=[]
    for name in required:
        matched=[(index,stage) for index,stage in enumerate(stages) if isinstance(stage,dict) and stage.get('name')==name]
        if len(matched)!=1 or type(matched[0][1].get('exitCode')) is not int or matched[0][1]['exitCode']!=0:
            reject('CFD_EDUCATIONAL_STAGE_EVIDENCE_REQUIRED','Require exactly one successful actual stage per required mesher/mesh-check/initialization/solver operation.')
        positions.append(matched[0][0])
    if positions!=sorted(positions):
        reject('CFD_EDUCATIONAL_STAGE_ORDER_MISMATCH','Complete strict checkMesh acceptance before potentialFoam initialization and physical foamRun; a failed mesh must not reach the solver.')


def logged_seconds(line,key):
    """Foundation 12 prints physical Time as e.g. `Time = 0.005s`.

    Bare numbers are seconds in these pinned profiles. Only the explicit SI
    seconds suffix is accepted; unknown units must never be silently ignored
    or parsed as a numeric prefix (0.005ms is a different physical time).
    """
    if not re.match(r'^\s*'+re.escape(key)+r'\s*=',line): return None
    match=re.fullmatch(r'\s*'+re.escape(key)+r'\s*=\s*('+NUM+r')\s*(?:s\s*)?',line)
    if not match:
        reject('CFD_EDUCATIONAL_LOG_TIME_UNIT_INVALID','Accept only a complete finite physical time/dt in SI seconds, with optional Foundation 12 s suffix; ms or unknown/malformed units require explicit conversion upstream.')
    value=float(match[1])
    if not math.isfinite(value): reject('CFD_EDUCATIONAL_LOG_TIME_UNIT_INVALID','Physical log time/dt must be finite SI seconds.')
    return value


def logged_mesh_time(log):
    values=[value for line in log.splitlines() if (value:=logged_seconds(line,'Create mesh for time')) is not None]
    if len(values)!=1:
        reject('CFD_EDUCATIONAL_LOG_TIME_MISMATCH','Require exactly one anchored solver mesh-loading time in SI seconds for each separate solve log.')
    return values[0]


def logged_time_states(log,profile,control,*,initial_time=0,step_offset=0):
    if profile=='impeller-mrf-v1':
        reject('CFD_EDUCATIONAL_TIME_INTERPRETATION_MISMATCH','MRF iterations are steady iteration indices, never physical seconds.')
    dt=_entry(control,'deltaT') if profile in ('transient-cavity-v1','impeller-transient-v1') else None
    previous=initial_time; states=[]
    for line in log.splitlines():
        delta=logged_seconds(line,'deltaT')
        value=logged_seconds(line,'Time')
        if delta is not None: dt=delta
        if value is None: continue
        if dt is None or not math.isfinite(dt) or dt<=0 or not math.isfinite(value) or value<=previous or not math.isclose(value-previous,dt,rel_tol=1e-4,abs_tol=1e-12):
            reject('CFD_EDUCATIONAL_TIME_STEP_MISMATCH','Associate every physical step with an actual positive logged adaptive dt or exact fixed control dt and advancing time; allow only log-printing precision tolerance 1e-4.')
        states.append({'physicalTimeSeconds':value,'timeStepSeconds':dt,'step':step_offset+len(states)+1})
        previous=value
    return states


def checkpoint_fields(cp,files,mesh,definitions,state):
    if cp['timeState']['step']!=state['step'] or not math.isclose(cp['timeState']['physicalTimeSeconds'],state['physicalTimeSeconds'],rel_tol=1e-6,abs_tol=1e-12) or not math.isclose(cp['timeState']['timeStepSeconds'],state['timeStepSeconds'],rel_tol=1e-6,abs_tol=1e-12):
        reject('CFD_EDUCATIONAL_CHECKPOINT_TIME_MISMATCH','Pair each native checkpoint with its exact logged physical time, global step and dt; output interval is not dt.')
    fields={}
    for name,(components,dimensions) in definitions.items():
        paths=[item['path'] for item in cp['nativeFiles'] if Path(item['path']).name==name]
        if len(paths)!=1: reject('CFD_EDUCATIONAL_NATIVE_FIELD_REQUIRED','Include each required native scalar/vector field exactly once at each checkpoint.')
        fields[name]=field_evidence(files[paths[0]],cells=mesh['cellCount'],components=components,dimensions=dimensions)
        fields[name]['dimensions']=dimensions
    if 'alpha.water' in fields and (fields['alpha.water']['minimum'] < -1e-6 or fields['alpha.water']['maximum'] > 1+1e-6):
        reject('CFD_EDUCATIONAL_PHASE_FRACTION_INVALID','Retain liquid fraction inside [0,1] within the declared 1e-6 numerical tolerance; no clipping is applied.')
    for item in cp['settingsFiles']:
        if item['path'].endswith('/uniform/time'):
            native=files[item['path']]
            for key,expected in [('value',state['physicalTimeSeconds']),('deltaT',state['timeStepSeconds']),('index',state['step'])]:
                if not math.isclose(_entry(native,key),expected,rel_tol=1e-4 if key=='deltaT' else 1e-6,abs_tol=1e-12):
                    reject('CFD_EDUCATIONAL_CHECKPOINT_TIME_MISMATCH','The saved native uniform/time value, index and deltaT must agree with the exact checkpoint and solver log.')
    return fields


def equation(identifier,title,formula,inputs,substitution,value,unit):
    m=lambda content:'<math xmlns="http://www.w3.org/1998/Math/MathML" display="block">'+content+'</math>'
    return {'equationId':identifier,'title':title,'formulaMathMl':m(formula),'input':inputs,
            'inputMathMl':m('<mtable columnalign="left">'+''.join('<mtr><mtd><mtext>'+escape(k)+'</mtext><mo>=</mo><mn>'+escape(str(v))+'</mn></mtd></mtr>' for k,v in inputs.items())+'</mtable>'),
            'substitutionMathMl':m(substitution),'result':{'value':value,'unit':unit},
            'resultMathMl':m('<mn>'+escape(str(value))+'</mn><mtext> '+escape(unit)+'</mtext>'),
            'authority':'Python educational native-evidence verifier','domain':'Derived from declared pinned profile/native files; no Pump performance acceptance.'}


def student_equations(profile, *, elapsed, steps, final_dt, files, prefix, fields):
    mn=lambda v:'<mn>'+escape(str(v))+'</mn>'
    rows=[equation('CFD_EDUCATIONAL_ELAPSED_TIME','Elapsed physical time','<mi>T</mi><mo>=</mo><msub><mi>t</mi><mi>end</mi></msub><mo>−</mo><msub><mi>t</mi><mn>0</mn></msub>',
                   {'t0[s]':0,'tend[s]':elapsed},mn(elapsed)+'<mo>−</mo>'+mn(0),elapsed,'s')]
    if profile=='transient-cavity-v1':
        nu=_entry(files[prefix+'/constant/physicalProperties'],'nu')
        block=files[prefix+'/system/blockMeshDict'].decode('utf-8')
        scale=_entry(files[prefix+'/system/blockMeshDict'],'convertToMeters')
        u=files['output/native-initial-state/0/U'].decode('utf-8')
        lid=re.search(r'movingWall\s*\{[^}]*value\s+uniform\s*\(\s*('+NUM+r')\s+0\s+0\s*\)',u,re.S)
        if not lid or float(lid[1])!=1 or not re.search(r'\(20\s+20\s+1\)',block) or scale!=0.1 or nu!=1e-5:
            reject('CFD_EDUCATIONAL_PROFILE_SETTINGS_MISMATCH','Retain the exact educational cavity geometry, wall velocity and viscosity; altered cases need their own versioned profile.')
        velocity=float(lid[1]); length=scale
        reynolds=velocity*length/nu
        rows.append(equation('CFD_EDUCATIONAL_REYNOLDS','Cavity Reynolds number','<mi>Re</mi><mo>=</mo><mfrac><mrow><mi>U</mi><mi>L</mi></mrow><mi>ν</mi></mfrac>',
            {'U[m/s]':velocity,'L[m]':length,'nu[m²/s]':nu},'<mfrac><mrow>'+mn(velocity)+'<mo>×</mo>'+mn(length)+'</mrow>'+mn(nu)+'</mfrac>',reynolds,'1'))
        if not math.isclose(elapsed/final_dt,steps,rel_tol=1e-8):
            reject('CFD_EDUCATIONAL_TIME_STEP_MISMATCH','Match the fixed cavity time step to the actual logged physical step count.')
        rows.append(equation('CFD_EDUCATIONAL_PHYSICAL_STEPS','Fixed physical steps','<msub><mi>N</mi><mi>steps</mi></msub><mo>=</mo><mfrac><mi>T</mi><mrow><mi>Δ</mi><mi>t</mi></mrow></mfrac>',
            {'T[s]':elapsed,'dt[s]':final_dt},'<mfrac>'+mn(elapsed)+mn(final_dt)+'</mfrac>',steps,'1'))
    else:
        u=files['output/native-initial-state/0/U'].decode('utf-8')
        inlet=re.search(r'inlet\s*\{[^}]*value\s+uniform\s*\(\s*0\s+0\s+('+NUM+r')\s*\)',u,re.S)
        # potentialFoam may retain a $internalField boundary declaration.
        if not inlet: inlet=re.search(r'internalField\s+uniform\s*\(\s*0\s+0\s+('+NUM+r')\s*\)',u)
        if not inlet:
            reject('CFD_EDUCATIONAL_INITIAL_VELOCITY_REQUIRED','The saved initialized U file must explicitly retain the imposed inlet velocity; an unresolved or missing inlet cannot be replaced by a profile default.')
        velocity=float(inlet[1])
        # 0.1 m is the pinned fvModels tutorial characteristic-length reference,
        # not a measured pump/impeller size or a reconstructed STL dimension.
        fv=files[prefix+'/constant/fvModels'].decode('utf-8')
        if not re.search(r'L\s*=\s*0\.1\s*m',fv) or velocity!=20.0:
            reject('CFD_EDUCATIONAL_PROFILE_SETTINGS_MISMATCH','Retain the pinned tutorial reference L=0.1 m and imposed 20 m/s inlet velocity explicitly.')
        characteristic=0.1/velocity; ratio=elapsed/characteristic
        rows.append(equation('CFD_EDUCATIONAL_CHARACTERISTIC_TIME','Tutorial characteristic advection time','<msub><mi>t</mi><mi>char</mi></msub><mo>=</mo><mfrac><mi>L</mi><mi>U</mi></mfrac>',
            {'L_ref[m]':0.1,'U[m/s]':velocity},'<mfrac>'+mn(0.1)+mn(velocity)+'</mfrac>',characteristic,'s'))
        rows.append(equation('CFD_EDUCATIONAL_DURATION_RATIO','Simulated duration / characteristic time','<mi>τ</mi><mo>=</mo><mfrac><mi>T</mi><msub><mi>t</mi><mi>char</mi></msub></mfrac>',
            {'T[s]':elapsed,'tchar[s]':characteristic},'<mfrac>'+mn(elapsed)+mn(characteristic)+'</mfrac>',ratio,'1'))
        water=fields['alpha.water']
        for suffix,value in [('minimum',1-water['maximum']),('maximum',1-water['minimum'])]:
            operand=water['maximum'] if suffix=='minimum' else water['minimum']
            rows.append(equation('CFD_EDUCATIONAL_VAPOUR_'+suffix.upper(),'Vapour fraction '+suffix,'<msub><mi>α</mi><mi>v</mi></msub><mo>=</mo><mn>1</mn><mo>−</mo><msub><mi>α</mi><mi>l</mi></msub>',
                {'alpha_water[1]':operand},mn(1)+'<mo>−</mo>'+mn(operand),value,'1'))
    rows.append(equation('CFD_EDUCATIONAL_FINAL_DT','Actual final physical step','<msub><mi>Δt</mi><mi>final</mi></msub><mo>=</mo><msub><mi>Δt</mi><mi>native/log</mi></msub>',
        {'actualLoggedSteps[1]':steps,'finalNativeDt[s]':final_dt},mn(final_dt),final_dt,'s'))
    for row in rows:
        row['source']={'title':'Pinned OpenFOAM Foundation 12 tutorial and dimensional identities','pages':'Native dictionaries; no printed page numbering',
                       'url':'https://github.com/OpenFOAM/OpenFOAM-12/tree/'+PIN+'/tutorials','reference':'controlDict physical-time definition; Re=UL/ν, tchar=L/U, αv=1−αl are dimensional/phase-fraction identities.'}
    return rows


def verify_educational_run(raw, sidecar, metadata, *, sidecar_bytes=None):
    result={'schemaVersion':'untirta.educational-cfd-verification.v1','valid':False,'status':'InputRequired','diagnostics':[],
            'actualCfdRunVerified':False,'provenanceAuthenticityVerified':False,'engineeringValidated':False,
            'calculationFreshness':'Imported Evidence','pumpStatusWrites':False,'mainApplicationWrites':False,'npshr':None,'equations':[]}
    try:
        inspected=inspect_artifact(raw)
        if not inspected['valid']:
            reject(inspected['reasonCode'],inspected['diagnostics'][0]['correctiveAction'])
        if not isinstance(sidecar,dict) or sidecar.get('schemaVersion')!='untirta.cfd-development-artifact.v1' or sidecar.get('artifactSha256')!=sha256(raw).hexdigest() or type(sidecar.get('bytes')) is not int or sidecar['bytes']!=len(raw) or sidecar.get('includesNativeRestartFiles') is not True or sidecar.get('nativeImportSchema')!='untirta.cfd-artifact.v1' or sidecar.get('actualCfdRunVerified') is not False:
            reject('CFD_EDUCATIONAL_SIDECAR_MISMATCH','Use the exact native ZIP and sidecar with matching SHA-256, byte count and strict native schema.')
        if any(sidecar.get(key)!=inspected['binding'][key] for key in ('inputSha256','referenceSha256')):
            reject('CFD_EDUCATIONAL_SIDECAR_MISMATCH','The sidecar must preserve the exact immutable input/reference SHA-256 binding of this native ZIP.')
        sidecar_bytes=sidecar_bytes if sidecar_bytes is not None else json.dumps(sidecar,allow_nan=False).encode('utf-8')
        if not isinstance(sidecar_bytes,bytes) or len(sidecar_bytes)>64*1024 or strict_json(sidecar_bytes)!=sidecar:
            reject('CFD_EDUCATIONAL_SIDECAR_MISMATCH','Supply the exact bounded sidecar JSON bytes used for the combined archive upload byte budget.')
        if len(raw)+len(sidecar_bytes)>25*1024**2:
            reject('CFD_EDUCATIONAL_ARTIFACT_BUDGET_EXCEEDED','Keep the native ZIP plus sidecar within the workflow combined 25 MiB upload budget.')
        if inspected['evidenceRole']!='actual-solver' or inspected['provenance']['kind']!='github-actions-worker' or not inspected['solverExecutionClaimed']:
            reject('CFD_EDUCATIONAL_REAL_SOLVER_EVIDENCE_REQUIRED','Contract-test ZIPs cannot establish a real CFD execution; obtain the native output from the successful actual solver step.')
        with zipfile.ZipFile(BytesIO(raw)) as z:
            files={item.filename:z.read(item) for item in z.infolist()}
        job=strict_json(files.get('input/job.json',b'')); reference=strict_json(files.get('reference/snapshot.json',b''))
        profile=job.get('caseProfile')
        reference_matched=(reference==benchmark_reference(profile) if profile in IMPELLER_PROFILES else
                           reference.get('sourceCommit')==PIN and reference.get('caseProfile')==profile)
        if profile not in PROFILES or not reference_matched or job.get('parameters')!={}:
            reject('CFD_EDUCATIONAL_PROFILE_MISMATCH','Use one unchanged approved fixed educational profile with the exact tutorial source commit.')
        if job.get('inputRevision')!=('fixture-v3' if profile=='cavitation-bullet-v1' else 'fixture-v1'):
            reject('CFD_EDUCATIONAL_INPUT_REVISION_MISMATCH','Match the exact versioned fixture input: cavity fixture-v1 or bullet uniform-mesh fixture-v3. Older mesh studies retain their historical failure state.')
        association=github_association(metadata,profile)
        result['githubAssociation']=association
        rotation=('sliding-mesh' if profile=='impeller-transient-v1' else 'mrf') if profile in IMPELLER_PROFILES else 'stationary'
        physical_model='single-phase-incompressible' if profile in ('transient-cavity-v1',*IMPELLER_PROFILES) else 'liquid-vapour-cavitation'
        if inspected['solver']!={'adapter':'openfoam','distribution':'OpenFOAM-Foundation','version':'12'} or inspected['method']!={'id':profile,'rotationModel':rotation,'physicalModel':physical_model}:
            reject('CFD_EDUCATIONAL_SOLVER_METHOD_MISMATCH','Retain the exact OpenFOAM Foundation 12 educational physical model and stationary mesh method.')
        report=strict_json(files.get('output/manifest.json',b''))
        if report.get('status')!='DevelopmentBenchmarkComplete' or report.get('actualCfdRun') is not True or report.get('numericalSmokePassed') is not True or report.get('mainApplicationWrites') is not False or report.get('sourceInputUnchanged') is not True or report.get('referenceUnchanged') is not True or report.get('caseProfile')!=profile:
            reject('CFD_EDUCATIONAL_REPORT_REQUIRED','Match the completed numerical-smoke report, unchanged input/reference and exact educational profile.')
        for key in inspected['binding']:
            if report.get(key)!=inspected['binding'][key]: reject('CFD_EDUCATIONAL_REPORT_BINDING_MISMATCH','Match every worker report input/reference binding to the native archive.')
        solver=report.get('solver',{})
        validate_solver_profile(solver,profile)
        if report.get('engineeringValidated') is not False:
            reject('CFD_EDUCATIONAL_SOLVER_METHOD_MISMATCH','Associate the worker report with the exact pinned educational solver/profile and retain engineering validation as a separate unfinished study.')
        stages=report.get('stages',[])
        validate_stages(stages,profile)
        prefix='output/native-case'
        source_settings=profile_source_evidence(files,prefix,profile)
        initial_archive=initial_state_archive_evidence(files,solver,profile)
        mesh=mesh_evidence(files,prefix,points_path='output/native-initial-state/constant/polyMesh/points' if profile in IMPELLER_PROFILES else None)
        if profile in IMPELLER_PROFILES:
            evidence=verify_impeller_native(files,report,inspected,profile,mesh)
            result.update(valid=True,status='EducationalRunEvidenceMatched',reasonCode='CFD_EDUCATIONAL_RUN_EVIDENCE_MATCHED',
                profile=profile,binding=inspected['binding'],artifactSha256=inspected['artifactSha256'],archiveBytes=len(raw),
                sidecarBytes=len(sidecar_bytes),combinedUploadBytes=len(raw)+len(sidecar_bytes),
                sourceSettings=source_settings,initialStateArchive=initial_archive,mesh=mesh,**evidence,
                sourceAndNativeIntegrityVerified=True,runEvidenceAssociationMatched=True,nativeNumericalSmokeChecksPassed=True,
                temporalConvergenceVerified=False,meshIndependenceVerified=False,conservationAcceptanceVerified=False,
                claim='Associated successful GitHub impeller run, exact native NCC/fields and physical motion or steady iterations only; offline authenticity and engineering acceptance remain separate.')
            return result
        if profile=='transient-cavity-v1' and mesh['cellCount']!=400:
            reject('CFD_EDUCATIONAL_MESH_PROFILE_MISMATCH','The unchanged 20×20×1 cavity must produce 400 native cells.')
        if profile=='cavitation-bullet-v1' and mesh['cellCount']>15*15*50:
            reject('CFD_EDUCATIONAL_MESH_PROFILE_MISMATCH','Without local refinement or layers, the accepted uniform bullet mesh cannot exceed its unchanged 15×15×50 background-cell budget.')
        log=files.get('output/logs/foamRun.log',b'').decode('utf-8',errors='replace')
        meshlog=files.get('output/logs/checkMesh.log',b'').decode('utf-8',errors='replace')
        validate_mesh_log(meshlog,mesh)
        created=logged_mesh_time(log)
        time_states=logged_time_states(log,profile,files[prefix+'/system/controlDict'])
        times=[state['physicalTimeSeconds'] for state in time_states]
        if not re.search(r'(?m)^End\s*$',log) or created!=0 or not times or not math.isclose(times[-1],PROFILES[profile]['time'],rel_tol=1e-6,abs_tol=1e-12):
            reject('CFD_EDUCATIONAL_LOG_TIME_MISMATCH','Verify the actual solver loads physical time zero and advances strictly to the intended profile end, with Mesh OK and End logs.')
        original=[cp for cp in inspected['checkpoints'] if all(item['path'].startswith(prefix+'/') for item in strict_json(files[cp['path']])['nativeFiles'])]
        final=max(original,key=lambda cp:cp['timeState']['physicalTimeSeconds']) if original else None
        if final is None or final['timeState']['step']!=len(times) or not math.isclose(final['timeState']['physicalTimeSeconds'],times[-1],rel_tol=1e-6,abs_tol=1e-12):
            reject('CFD_EDUCATIONAL_CHECKPOINT_TIME_MISMATCH','Pair the final native checkpoint with the exact logged physical step and end time.')
        definitions={'U':(3,'0 1 -1 0 0 0 0'),'p':(1,'0 2 -2 0 0 0 0')} if profile=='transient-cavity-v1' else {'U':(3,'0 1 -1 0 0 0 0'),'p_rgh':(1,'1 -1 -2 0 0 0 0'),'alpha.water':(1,'0 0 0 0 0 0 0')}
        initial_fields={name:field_evidence(files['output/native-initial-state/0/'+name],cells=mesh['cellCount'],components=components,dimensions=dimensions) for name,(components,dimensions) in definitions.items()}
        if 'alpha.water' in initial_fields and (initial_fields['alpha.water']['minimum']<-1e-6 or initial_fields['alpha.water']['maximum']>1+1e-6):
            reject('CFD_EDUCATIONAL_PHASE_FRACTION_INVALID','Initial liquid fraction must remain finite and inside its declared domain before physical time integration.')
        fields={}
        for checkpoint in sorted(original,key=lambda c:c['timeState']['physicalTimeSeconds']):
            state=next((s for s in time_states if math.isclose(s['physicalTimeSeconds'],checkpoint['timeState']['physicalTimeSeconds'],rel_tol=1e-6,abs_tol=1e-12)),None)
            if state is None: reject('CFD_EDUCATIONAL_CHECKPOINT_TIME_MISMATCH','Every saved native checkpoint must correspond to one logged physical solver step.')
            fields=checkpoint_fields(strict_json(files[checkpoint['path']]),files,mesh,definitions,state)
        result['equations']=student_equations(profile,elapsed=times[-1],steps=len(times),final_dt=final['timeState']['timeStepSeconds'],files=files,prefix=prefix,fields=fields)
        restart=report.get('restart')
        if restart:
            source=[item for item in inspected['checkpoints'] if item['timeState']['physicalTimeSeconds']==0.05]
            target=[item for item in inspected['checkpoints'] if item['timeState']['physicalTimeSeconds']==0.055]
            restartlog=files.get('output/logs/restart-foamRun.log',b'').decode(errors='replace')
            start=logged_mesh_time(restartlog)
            restart_states=logged_time_states(restartlog,profile,files['output/native-restart-case/system/controlDict'],initial_time=0.05,step_offset=10)
            if not isinstance(restart,dict) or profile!='transient-cavity-v1' or len(source)!=1 or len(target)!=1 or start!=0.05 or len(restart_states)!=1 or not math.isclose(restart_states[-1]['physicalTimeSeconds'],0.055,abs_tol=1e-12) or not re.search(r'(?m)^End\s*$',restartlog) or restart.get('actualRestartExecutionCompleted') is not True or not any(s.get('name')=='restart-foamRun' and s.get('exitCode')==0 for s in stages if isinstance(s,dict)):
                reject('CFD_EDUCATIONAL_RESTART_EVIDENCE_MISMATCH','Pair the exact 0.05 s source with the continued 0.055 s native restart and actual loading/advance log.')
            source_native=strict_json(files[source[0]['path']]); target_native=strict_json(files[target[0]['path']])
            mesh_identity=lambda c:{i['path'].split('/constant/polyMesh/',1)[1]:(i['sha256'],i['bytes']) for i in c['meshFiles']}
            if mesh_identity(source_native)!=mesh_identity(target_native):
                reject('CFD_EDUCATIONAL_RESTART_EVIDENCE_MISMATCH','Restart must reuse the exact fixed mesh; a changed mesh belongs to a new numerical study.')
            # Descriptor digests include their distinct original/restart paths;
            # compare immutable content identities without treating paths as physics.
            settings_identity=lambda c:{i['path'].split('/system/',1)[1] if '/system/' in i['path'] else 'constant/'+i['path'].split('/constant/',1)[1]:(i['sha256'],i['bytes']) for i in c['settingsFiles'] if '/uniform/' not in i['path'] and not i['path'].endswith('/system/controlDict')}
            if settings_identity(source_native)!=settings_identity(target_native):
                reject('CFD_EDUCATIONAL_RESTART_EVIDENCE_MISMATCH','Reuse exact original solver/physics settings; only the documented restart time/write controls may change.')
            expected_control=files[prefix+'/system/controlDict'].decode('utf-8')
            for key,value in {'startFrom':'startTime','startTime':'0.05','endTime':'0.055','writeInterval':'1'}.items():
                expected_control,count=re.subn(r'(?m)^(\s*'+key+r'\s+)[^;]+;',lambda m:m[1]+value+';',expected_control)
                if count!=1: reject('CFD_EDUCATIONAL_RESTART_EVIDENCE_MISMATCH','Retain one exact restart time/write override.')
            restart_control=files['output/native-restart-case/system/controlDict']
            if restart_control.decode('utf-8').replace('\r\n','\n')!=expected_control.replace('\r\n','\n') or restart.get('originalControlSha256')!=sha256(files[prefix+'/system/controlDict']).hexdigest() or restart.get('restartControlSha256')!=sha256(restart_control).hexdigest():
                reject('CFD_EDUCATIONAL_RESTART_EVIDENCE_MISMATCH','Bind original and documented restart control settings to their exact checksums.')
            for state,key in [(source[0],'sourceSettingsSha256'),(target[0],'restartSettingsSha256')]:
                native=strict_json(files[state['path']])
                if restart.get(key)!=native['settingsSha256']: reject('CFD_EDUCATIONAL_RESTART_EVIDENCE_MISMATCH','Associate source/restart settings digests with their exact native checkpoints.')
            for name in definitions:
                source_path=next(i['path'] for i in source_native['nativeFiles'] if Path(i['path']).name==name)
                descriptor=next((i for i in restart.get('sourceNativeFiles',[]) if isinstance(i,dict) and i.get('path')==source_path),None)
                if descriptor is None or descriptor.get('sha256')!=sha256(files[source_path]).hexdigest() or descriptor.get('bytes')!=len(files[source_path]):
                    reject('CFD_EDUCATIONAL_RESTART_EVIDENCE_MISMATCH','Bind restart source fields to the exact original saved native pair.')
            checkpoint_fields(target_native,files,mesh,definitions,restart_states[0])
            for state,key in [(source[0],'sourceCheckpointChecksum'),(target[0],'restartCheckpointChecksum')]:
                native=strict_json(files[state['path']])
                if restart.get(key)!=native['checkpointChecksum']: reject('CFD_EDUCATIONAL_RESTART_EVIDENCE_MISMATCH','Keep exact parent/child native checkpoint checksums in the restart lineage.')
            result['restartEvidence']={'sourceTimeSeconds':0.05,'continuedTimeSeconds':0.055,'metadataAndNativeLineageMatched':True,
                'loadedSourceFieldPairIndependentlyVerified':False,'continuousRunEquivalenceVerified':False,'physicalRotorRestartVerified':False}
        result.update(valid=True,status='EducationalRunEvidenceMatched',reasonCode='CFD_EDUCATIONAL_RUN_EVIDENCE_MATCHED',
            profile=profile,binding=inspected['binding'],artifactSha256=inspected['artifactSha256'],archiveBytes=len(raw),
            sidecarBytes=len(sidecar_bytes),combinedUploadBytes=len(raw)+len(sidecar_bytes),
            sourceSettings=source_settings,initialStateArchive=initial_archive,mesh=mesh,initialFields=initial_fields,fields=fields,physicalEndTimeSeconds=times[-1],physicalStepCount=len(times),
            sourceAndNativeIntegrityVerified=True,runEvidenceAssociationMatched=True,nativeNumericalSmokeChecksPassed=True,
            temporalConvergenceVerified=False,meshIndependenceVerified=False,conservationAcceptanceVerified=False,
            claim='Associated successful GitHub run/native numerical-smoke evidence only; offline authenticity and engineering acceptance remain separate.')
    except WorkerDiagnostic as error:
        result['reasonCode']=error.code
        result['diagnostics'].append({'reasonCode':error.code,'correctiveAction':error.action})
    except (ValueError,TypeError,KeyError,OSError,UnicodeError,OverflowError,zipfile.BadZipFile):
        result['reasonCode']='CFD_EDUCATIONAL_EVIDENCE_INVALID'
        result['diagnostics'].append({'reasonCode':result['reasonCode'],'correctiveAction':'Supply complete bounded finite GitHub metadata, native ZIP, sidecar and native scalar/vector files.'})
    return result


def markdown_report(result):
    lines=['# Pemeriksaan bukti CFD pendidikan','',f"Status: **{result['status']}**",'',
           'Integritas file dan kecocokan metadata run diperiksa secara terpisah dari autentisitas provenance dan validasi engineering.',
           'Hasil tetap **Imported Evidence**. NPSHr, status Pump, dan Main tidak ditulis atau diubah.','']
    if result['valid']:
        gh=result['githubAssociation']; mesh=result['mesh']
        time_summary=(f"Iterasi steady MRF: {result['steadyIterationCount']}; waktu fisik tidak berlaku." if result['profile']=='impeller-mrf-v1' else
                      f"Waktu fisik: {result['physicalEndTimeSeconds']} s; {result['physicalStepCount']} langkah tercatat.")
        lines += [f"Profil: `{result['profile']}`. Run: [{gh['runId']}]({gh['runUrl']}); workflow commit `{gh['headSha']}`.",'',
                  f"Mesh native: {mesh['cellCount']} cells, {mesh['pointCount']} points, {mesh['faceCount']} faces.",
                  time_summary,'','## Input → Persamaan → Substitusi → Hasil','']
        for e in result['equations']:
            lines += ['### '+e['title'],'','Persamaan:',e['formulaMathMl'],'','Input:',e['inputMathMl'],'',
                      'Substitusi:',e['substitutionMathMl'],'','Hasil:',e['resultMathMl'],'',
                      'Otoritas: '+e['authority']+'. Sumber: ['+e['source']['title']+']('+e['source']['url']+').','']
        lines += ['## Batas penggunaan','',
                  '- Bukti metadata GitHub berasal dari file API yang disediakan operator; tool offline tidak mengautentikasi ulang GitHub.',
                  '- Pemeriksaan numerik mencakup mesh/field finite, satuan, jumlah elemen, waktu fisik dan lineage checkpoint.',
                  '- Independensi mesh, konvergensi temporal, conservation acceptance dan validasi eksperimen masih perlu studi tersendiri.',
                  '- Geometry merupakan tutorial pendidikan. Pompa nyata memerlukan wetted geometry dan konfigurasi produk yang tepat.',
                  '- NPSHr/NPSH3 membutuhkan studi pompa dan pressure sweep/head-drop criterion yang terpisah.']
        if result['profile']=='transient-cavity-v1':
            lines += ['- Cavity memakai RAS kEpsilon, Re 10000, mesh 20×20×1 dan tahap perkembangan awal 0.05 s.',
                      '- Field p cavity mempunyai satuan m²/s² (kinematic pressure); interpretasi sebagai tekanan absolut memerlukan density dan pressure reference yang sesuai.',
                      '- Restart yang tercatat hanya mesh tetap; kesetaraan terhadap uninterrupted run dan physical sliding rotor belum diverifikasi.']
        elif result['profile']=='cavitation-bullet-v1':
            lines += ['- Bullet memakai model liquid–vapour SchnerrSauer. Durasi 10 µs adalah 0.002 kali karakteristik 0.005 s.',
                      '- Revisi mesh castellated-staircase-uniform-v3 memakai snap=false dan addLayers=false, refinement 0/0, serta batas 100000 cells. STL resmi tetap sumber immutable, tetapi permukaan mesh merupakan pendekatan staircase.',
                      '- Background asli 15×15×50 dipertahankan, dengan sekitar lima cells melintasi diameter sumber bullet 0.01 m. Resolusi ini sangat kasar untuk prediksi kuantitatif cavitation.',
                      '- Pendekatan staircase dipakai untuk latihan alur meshing dan solver; akurasi geometri serta prediksi kuantitatif cavitation bullet atau Pump belum divalidasi.',
                      '- Fraksi uap dihitung dari 1−alpha.water tanpa clipping. Run dapat tetap valid sebagai smoke ketika fraksi uap belum berkembang.',
                      '- Snapshot Phi dari initializer potential disimpan terpisah dari phi physical flux agar nama tetap tidak ambigu di Windows. Byte Phi dibandingkan dengan sumber immutable; archive tidak memverifikasi ulang nama pada live native case.',
                      '- p_rgh merupakan hydrostatic-reduced pressure; penilaian vapor pressure membutuhkan rekonstruksi tekanan absolut/datum yang benar.']
        else:
            lines += ['- Impeller merupakan passage radial ideal30° dari paket resmi Foundation12,9371 cells dan6171 cells pada zona rotating; belum menjadi exact manufactured Pump atau model volute lengkap.',
                      '- NCC dipertahankan sebagai24 couples/48 native cyclic patches. NCC tidak disamakan dengan AMI.',
                      '- Transient memakai60rpm,+x,10×0.001s hingga0.01s dan memeriksa titik rotor terhadap topology aktual. MRF memakai geometri tetap dan50iterasi steady; iterasi bukan detik.',
                      '- Field p adalah kinematic pressure[m²/s²]. Sumber hanya menetapkan nu=1e-5m²/s; density, vapor pressure dan datum tekanan absolut belum tersedia.',
                      '- Tidak ada claim head, NPSHr/NPSH3, convergence, pressure sweep atau restart impeller yang diterima dari benchmark singkat ini.']
    else:
        lines += ['## Tindakan yang diperlukan','']+[f"- `{d['reasonCode']}`: {d['correctiveAction']}" for d in result['diagnostics']]
    return '\n'.join(lines)+'\n'


def _load(path,maximum):
    _regular_source(path)
    if path.stat().st_size>maximum: reject('CFD_EDUCATIONAL_INPUT_BUDGET_EXCEEDED','Reduce metadata/sidecar/native ZIP to the declared bounded verification budgets.')
    return path.read_bytes()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact',required=True,type=Path); parser.add_argument('--sidecar',required=True,type=Path)
    parser.add_argument('--run-metadata',required=True,type=Path); parser.add_argument('--output-dir',type=Path)
    args=parser.parse_args(argv)
    try:
        raw=_load(args.artifact,ArtifactLimits().archive_bytes)
        sidecar_bytes=_load(args.sidecar,64*1024)
        sidecar=strict_json(sidecar_bytes); metadata=strict_json(_load(args.run_metadata,2*1024**2))
        result=verify_educational_run(raw,sidecar,metadata,sidecar_bytes=sidecar_bytes)
        if args.output_dir:
            out=_workspace(args.output_dir); out.mkdir(parents=True,exist_ok=True)
            for name in ('educational-verification.json','educational-verification.md'):
                if (out/name).exists(): reject('CFD_EDUCATIONAL_REPORT_EXISTS','Choose a new independent report directory; existing evidence is immutable.')
            (out/'educational-verification.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
            (out/'educational-verification.md').write_text(markdown_report(result),encoding='utf-8')
    except WorkerDiagnostic as error:
        result={'schemaVersion':'untirta.educational-cfd-verification.v1','valid':False,'status':'InputRequired','reasonCode':error.code,
                'diagnostics':[{'reasonCode':error.code,'correctiveAction':error.action}],'actualCfdRunVerified':False,'engineeringValidated':False,'mainApplicationWrites':False}
    except (OSError,ValueError,UnicodeError,TypeError):
        result={'schemaVersion':'untirta.educational-cfd-verification.v1','valid':False,'status':'InputRequired','reasonCode':'CFD_EDUCATIONAL_REPORT_UNAVAILABLE',
                'diagnostics':[{'reasonCode':'CFD_EDUCATIONAL_REPORT_UNAVAILABLE','correctiveAction':'Use complete bounded readable evidence and a new writable independent output directory.'}],'actualCfdRunVerified':False,'engineeringValidated':False,'mainApplicationWrites':False}
    print(json.dumps(result,allow_nan=False,indent=2))
    return 0 if result['valid'] else 2


if __name__=='__main__':
    raise SystemExit(main())
