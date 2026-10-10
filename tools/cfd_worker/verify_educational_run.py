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

PIN = '0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd'
PROFILES = {
    'transient-cavity-v1': {'workflow': '.github/workflows/transient-cfd-validation.yml',
                          'artifact': 'transient-cfd-development-evidence', 'time': 0.05,
                          'solverStep': 'Real bounded physical-transient development fixture'},
    'cavitation-bullet-v1': {'workflow': '.github/workflows/cavitation-cfd-validation.yml',
                           'artifact': 'cavitation-cfd-development-evidence', 'time': 0.00001,
                           'solverStep': 'Real bounded cavitation development fixture'}}
NUM = r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?'


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


def mesh_evidence(files, prefix):
    def raw(name):
        path = prefix+'/constant/polyMesh/'+name
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
    unit=re.search(r'dimensions\s*\[([^\]]+)\]\s*;',body)
    if not unit or ' '.join(unit[1].split())!=dimensions:
        reject('CFD_EDUCATIONAL_FIELD_UNIT_MISMATCH','Keep native field dimensions explicit; kinematic p, absolute pressure and p_rgh are distinct quantities.')
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


def profile_source_evidence(files,prefix,profile):
    """Check immutable tutorial settings and explicitly documented smoke overrides.

    Generated mesh/initialized fields remain derived evidence; a settings match
    does not authenticate mesher execution or establish geometry independence.
    """
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
            expected=expected.replace('level (4 4);','level (2 2);').replace('levels ((0.0 3));','levels ((0.0 1));')
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
    return {'sourceCommit':PIN,'checkedSettings':checked,'geometryIsEducationalTutorial':True,'mesherExecutionAuthenticityVerified':False}


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
    dt=_entry(control,'deltaT') if profile=='transient-cavity-v1' else None
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
        if profile not in PROFILES or reference.get('sourceCommit')!=PIN or reference.get('caseProfile')!=profile or job.get('parameters')!={}:
            reject('CFD_EDUCATIONAL_PROFILE_MISMATCH','Use one unchanged approved fixed educational profile with the exact tutorial source commit.')
        association=github_association(metadata,profile)
        result['githubAssociation']=association
        if inspected['solver']!={'adapter':'openfoam','distribution':'OpenFOAM-Foundation','version':'12'} or inspected['method']!={'id':profile,'rotationModel':'stationary','physicalModel':'single-phase-incompressible' if profile=='transient-cavity-v1' else 'liquid-vapour-cavitation'}:
            reject('CFD_EDUCATIONAL_SOLVER_METHOD_MISMATCH','Retain the exact OpenFOAM Foundation 12 educational physical model and stationary mesh method.')
        report=strict_json(files.get('output/manifest.json',b''))
        if report.get('status')!='DevelopmentBenchmarkComplete' or report.get('actualCfdRun') is not True or report.get('numericalSmokePassed') is not True or report.get('mainApplicationWrites') is not False or report.get('sourceInputUnchanged') is not True or report.get('referenceUnchanged') is not True or report.get('caseProfile')!=profile:
            reject('CFD_EDUCATIONAL_REPORT_REQUIRED','Match the completed numerical-smoke report, unchanged input/reference and exact educational profile.')
        for key in inspected['binding']:
            if report.get(key)!=inspected['binding'][key]: reject('CFD_EDUCATIONAL_REPORT_BINDING_MISMATCH','Match every worker report input/reference binding to the native archive.')
        solver=report.get('solver',{})
        if not isinstance(solver,dict) or any(solver.get(k)!=v for k,v in {'distribution':'OpenFOAM-Foundation','version':'12','sourceCommit':PIN,'profile':profile}.items()) or report.get('engineeringValidated') is not False:
            reject('CFD_EDUCATIONAL_SOLVER_METHOD_MISMATCH','Associate the worker report with the exact pinned educational solver/profile and retain engineering validation as a separate unfinished study.')
        required=['blockMesh','checkMesh','foamRun']+(['snappyHexMesh','potentialFoam'] if profile=='cavitation-bullet-v1' else [])
        stages=report.get('stages',[])
        if not isinstance(stages,list) or any(not any(s.get('name')==name and s.get('exitCode')==0 for s in stages if isinstance(s,dict)) for name in required):
            reject('CFD_EDUCATIONAL_STAGE_EVIDENCE_REQUIRED','Verify successful actual meshing, mesh-check, initialization and solver stages in the worker report.')
        prefix='output/native-case'
        source_settings=profile_source_evidence(files,prefix,profile)
        mesh=mesh_evidence(files,prefix)
        if profile=='transient-cavity-v1' and mesh['cellCount']!=400:
            reject('CFD_EDUCATIONAL_MESH_PROFILE_MISMATCH','The unchanged 20×20×1 cavity must produce 400 native cells.')
        log=files.get('output/logs/foamRun.log',b'').decode('utf-8',errors='replace')
        meshlog=files.get('output/logs/checkMesh.log',b'').decode('utf-8',errors='replace')
        created=logged_mesh_time(log)
        time_states=logged_time_states(log,profile,files[prefix+'/system/controlDict'])
        times=[state['physicalTimeSeconds'] for state in time_states]
        if 'Mesh OK.' not in meshlog or not re.search(r'(?m)^End\s*$',log) or created!=0 or not times or not math.isclose(times[-1],PROFILES[profile]['time'],rel_tol=1e-6,abs_tol=1e-12):
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
            sourceSettings=source_settings,mesh=mesh,initialFields=initial_fields,fields=fields,physicalEndTimeSeconds=times[-1],physicalStepCount=len(times),
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
        lines += [f"Profil: `{result['profile']}`. Run: [{gh['runId']}]({gh['runUrl']}); workflow commit `{gh['headSha']}`.",'',
                  f"Mesh native: {mesh['cellCount']} cells, {mesh['pointCount']} points, {mesh['faceCount']} faces.",
                  f"Waktu fisik: {result['physicalEndTimeSeconds']} s; {result['physicalStepCount']} langkah tercatat.",'','## Input → Substitusi → Hasil','']
        for e in result['equations']:
            lines += ['### '+e['title'],'','Persamaan:',e['formulaMathMl'],'','Input:',e['inputMathMl'],'',
                      'Substitusi:',e['substitutionMathMl'],'','Hasil:',e['resultMathMl'],'']
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
        else:
            lines += ['- Bullet memakai model liquid–vapour SchnerrSauer. Durasi 10 µs adalah 0.002 kali karakteristik 0.005 s.',
                      '- Fraksi uap dihitung dari 1−alpha.water tanpa clipping. Run dapat tetap valid sebagai smoke ketika fraksi uap belum berkembang.',
                      '- p_rgh merupakan hydrostatic-reduced pressure; penilaian vapor pressure membutuhkan rekonstruksi tekanan absolut/datum yang benar.']
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
