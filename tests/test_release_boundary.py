"""Contract tests only; these tests never claim a Fluent/OpenFOAM solver run."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'services/cfd_worker/src'))
from untirta_cfd_worker.contracts import Job,WorkerDiagnostic,digest
from untirta_cfd_worker.capabilities import capability_report
class ReleaseBoundary(unittest.TestCase):
    @unittest.skipUnless(sys.platform=='linux','Actual /usr/bin/env transport requires the Linux runner; no OpenFOAM execution claim.')
    def test_explicit_loader_assignment_survives_sanitized_environment(self):
        environment=dict(os.environ);environment.pop('LD_LIBRARY_PATH',None)
        library_path='/reviewed/openfoam12/lib:/reviewed/openfoam12/lib/dummy'
        run=subprocess.run(['/usr/bin/env','LD_LIBRARY_PATH='+library_path,sys.executable,'-B','-c',"import os; print(os.environ['LD_LIBRARY_PATH'])"],env=environment,capture_output=True,text=True,timeout=5,check=False)
        self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(run.stdout.strip(),library_path)
    def test_private_reference_extension_rejected(self):
        ref={'schemaVersion':'untirta.cfd-benchmark-reference.v1','kind':'development-benchmark','caseProfile':'transient-cavity-v1','sourceCommit':'0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd','snapshotId':'private-student-data'}
        source={'schemaVersion':'untirta.cfd-worker-job.v1','studyId':'transient-cavity-v1','inputRevision':'fixture-v1','adapter':'openfoam','caseProfile':'transient-cavity-v1','referenceSha256':digest(ref),'limits':{},'parameters':{}}
        with self.assertRaises(WorkerDiagnostic) as stopped: Job.parse(source,ref)
        self.assertEqual(stopped.exception.code,'CFD_PUBLIC_REFERENCE_SCHEMA_INVALID')
    def test_native_checkpoint_contract_integrity_is_separate_from_actual_solve(self):
        from untirta_cfd_worker.checkpoint import build_checkpoint,validate_checkpoint_descriptor
        from hashlib import sha256
        raw={'output/native-case/0.05/U':b'contract-U','output/native-case/0.05/p':b'contract-p','output/native-case/constant/polyMesh/points':b'contract-mesh','output/native-case/system/controlDict':b'contract-settings','output/native-initial-state/0/U':b'contract-initial-U','output/native-initial-state/0/p':b'contract-initial-p'}
        def item(path,role=None):
            out={'path':path,'sha256':sha256(raw[path]).hexdigest(),'bytes':len(raw[path])}
            if role: out['role']=role
            return out
        binding={'studyId':'transient-cavity-v1','inputRevision':'fixture-v1','inputSha256':'a'*64,'referenceSha256':'b'*64}
        cp=build_checkpoint(binding=binding,solver={'adapter':'openfoam','distribution':'OpenFOAM-Foundation','version':'12'},method={'id':'transient-cavity-v1','rotationModel':'stationary','physicalModel':'single-phase-incompressible'},time_state={'interpretation':'physical','physicalTimeSeconds':0.05,'timeStepSeconds':0.005,'step':10,'iteration':None},motion={'mode':'stationary','rotorAngleRad':None,'angularSpeedRadS':None,'axis':None,'origin':None,'motionLawSha256':None},interface={'type':'none','identifiers':[],'configurationSha256':None},native_files=[item('output/native-case/0.05/U','field'),item('output/native-case/0.05/p','field')],mesh_files=[item('output/native-case/constant/polyMesh/points')],settings_files=[item('output/native-case/system/controlDict')],setup_state={'initialStateSavedBeforePreview':True,'initialStateFiles':[item('output/native-initial-state/0/U'),item('output/native-initial-state/0/p')],'sequence':['setup-saved','initialized','initial-state-saved','solved','checkpoint-saved']},evidence_role='contract-test')
        checked=validate_checkpoint_descriptor(cp,binding=binding,files=raw)
        self.assertTrue(checked['valid']);self.assertFalse(checked['actualCfdRunVerified'])
        tampered=dict(raw);tampered['output/native-case/0.05/U']=b'mixed-run-U'
        rejected=validate_checkpoint_descriptor(cp,binding=binding,files=tampered)
        self.assertFalse(rejected['valid']);self.assertEqual(rejected['reasonCode'],'CFD_CHECKPOINT_CHECKSUM_MISMATCH')
    def test_prepare_fixed_case_preserves_originals_and_does_not_claim_solver_execution(self):
        from untirta_cfd_worker import openfoam
        fixture=ROOT/'tools/cfd_worker/fixtures/openfoam12'
        ref={'schemaVersion':'untirta.cfd-benchmark-reference.v1','kind':'development-benchmark','caseProfile':'transient-cavity-v1','sourceCommit':'0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd'}
        source={'schemaVersion':'untirta.cfd-worker-job.v1','studyId':'transient-cavity-v1','inputRevision':'fixture-v1','adapter':'openfoam','caseProfile':'transient-cavity-v1','referenceSha256':digest(ref),'limits':{},'parameters':{}}
        job=Job.parse(source,ref);original=(fixture/'transient-cavity-v1/system/controlDict').read_bytes()
        test_outputs=ROOT/'outputs';test_outputs.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_outputs) as temporary:
            workspace=Path(temporary);self.assertTrue(workspace.resolve().is_relative_to(test_outputs.resolve()));(workspace/'output').mkdir()
            case,stages,metadata=openfoam.prepare(job,workspace,fixture)
            self.assertEqual([s[0] for s in stages],['blockMesh','checkMesh','foamRun'])
            self.assertRegex((case/'system/controlDict').read_text(),r'endTime\s+0\.05;')
            self.assertIn('development-only',metadata['quantityClaim'])
            self.assertNotIn('actualCfdRun',metadata)
            self.assertEqual((fixture/'transient-cavity-v1/system/controlDict').read_bytes(),original)
    def test_fixed_profiles_only(self):
        for profile in ('transient-cavity-v1','cavitation-bullet-v1'):
            ref={'schemaVersion':'untirta.cfd-benchmark-reference.v1','kind':'development-benchmark','caseProfile':profile,'sourceCommit':'0b487fc98b88d3e8dc3c3ead4662e7632dd5e7dd'}
            source={'schemaVersion':'untirta.cfd-worker-job.v1','studyId':profile,'inputRevision':'fixture-v1','adapter':'openfoam','caseProfile':profile,'referenceSha256':digest(ref),'limits':{},'parameters':{}}
            self.assertEqual(Job.parse(source,ref).profile,profile)
            source['parameters']={'rpm':2900}
            with self.assertRaises(WorkerDiagnostic): Job.parse(source,ref)
    def test_fluent_not_supported(self):
        source={'schemaVersion':'untirta.cfd-worker-job.v1','studyId':'x','inputRevision':'v1','adapter':'pyfluent','caseProfile':'frozen-rotor-v1','referenceSha256':digest({}),'limits':{},'parameters':{}}
        with self.assertRaises(WorkerDiagnostic): Job.parse(source,{})
        self.assertEqual(set(capability_report()['adapters']),{'openfoam'})
    def test_cli_rejects_private_input(self):
        for flag in ('--input','--reference','--native-case','--command'):
            run=subprocess.run([sys.executable,'-B',str(ROOT/'tools/cfd_worker/run.py'),flag,'secret-input.json'],capture_output=True,text=True)
            self.assertEqual(run.returncode,2)
            self.assertIn('unrecognized arguments',run.stderr)
    def test_fixture_exact_provenance(self):
        from untirta_cfd_worker.contracts import file_digest
        folder=ROOT/'tools/cfd_worker/fixtures/openfoam12'
        provenance=json.loads((folder/'provenance.json').read_text())
        self.assertEqual(len(provenance['files']),29)
        for item in provenance['files']:
            self.assertEqual(file_digest(folder/item['path']),item['sha256'])
            self.assertEqual((folder/item['path']).stat().st_size,item['bytes'])
    def test_public_guard_fail_closed(self):
        spec=importlib.util.spec_from_file_location('public_guard',ROOT/'tools/cfd_worker/public_runner_guard.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
        for value in ({},{'repository':{'full_name':'simcalc/OpenFoam','private':True}}, {'repository':{'full_name':'wrong/repo','private':False}}):
            with self.assertRaises(ValueError): m.validate_event(value,'simcalc/OpenFoam')
        m.validate_event({'repository':{'full_name':'simcalc/OpenFoam','private':False}},'simcalc/OpenFoam')
    def test_no_private_application_modules(self):
        modules={p.name for p in (ROOT/'services/cfd_worker/src/untirta_cfd_worker').glob('*.py')}
        self.assertFalse(modules & {'lab_api.py','study.py','transient_study.py','pyfluent_driver.py','fluent_setup.py','fluent_reports.py'})
        self.assertFalse((ROOT/'tests/cfd_worker/fixtures').exists())
    def test_manual_public_standard_only(self):
        for path in (ROOT/'.github/workflows').glob('*.yml'):
            text=path.read_text()
            self.assertIn("github.event.repository.private == false",text)
            self.assertIn("vars.PUBLIC_EDUCATIONAL_RUNS_ENABLED == 'true'",text)
            self.assertIn('runs-on: ubuntu-24.04',text)
            self.assertIn('workflow_dispatch:',text)
            self.assertNotIn('pull_request:',text);self.assertNotIn('push:',text)
            self.assertNotIn('secrets.',text);self.assertNotIn('CFD_FREE_ALLOWANCE_ATTESTATION',text)
if __name__=='__main__': unittest.main()
