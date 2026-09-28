import base64,io,json,tempfile,unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from manage import prepare
from server import create_app,comparison
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=')
def fixture(root):
 project,backup=root/'project',root/'backup';model,split='llama3_2_11b','split_01';patch_root=project/'output/new/grounded_sam_patches/eval'/model;patch_root.mkdir(parents=True)
 def write(p,obj):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj))
 batch,tf,verify,desc=[],[],[],[]
 for i in range(2):
  key=f'country/image{i}.png';original=project/f'original{i}.png';original.write_bytes(PNG);patches=[]
  for j in range(3):
   p=patch_root/f'image{i}_p{j}.png';p.write_bytes(PNG);patches.append(p);batch.append({'source_key':key,'box_patch':str(p),'source_image':str(original),'box_xyxy':[0,0,1,1]})
  tf.append({'source_key':key,'source_image':str(original)})
  verify.append({'source_key':key,'original_pred_set':['Korea','Japan'],'refined_pred_set':['Korea'],'supporting_patches':{'Korea':[{'box_patch':str(patches[0]),'c':.9}]}})
  desc.append({'source_key':key,'split':split,'status':'ok','description':f'Exact description {i}.','verification_model':model,'description_model':model,'quantile':.75,'threshold':.8,'aps_pred_set':['Korea','Japan'],'refined_pred_set':['Korea']})
 write(patch_root/'batch_meta_eval.json',batch);write(backup/'06_patch_tf_scores'/model/split/'tf_scored_APS.json',tf)
 write(backup/'07_verification'/model/split/'quantile_0.75/image_level_results.json',verify)
 write(backup/'07_verification/best_quantile_selection_cov90_full_eval.json',{'models':{model:{'splits':{split:{'quantile':.75,'threshold':.8}}}}})
 f=backup/'descriptions.json';write(f,desc)
 return SimpleNamespace(state=root/'state',description_file=[f],project_root=project,backup_root=backup,model_key=model,limit=0,seed=42,name='Test study',allow_repeated_images=False,selection_file=None,patch_root=None,batch_meta=None,image_resize_root=None)
class EvaluationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.args=fixture(self.root)
  with redirect_stdout(io.StringIO()):prepare(self.args)
  self.app=create_app(self.args.state);self.client=self.app.test_client();self.codes=dict(line.split(': ',1) for line in (self.args.state/'access_codes.txt').read_text().splitlines() if ': ' in line)
  self.csrf=self.login(self.client,'01');self.study=self.client.get('/api/study').json;self.first,self.second=self.study['items']
 def tearDown(self):self.temp.cleanup()
 def login(self,c,account):
  r=c.post('/api/login',json={'code':self.codes[account]});self.assertEqual(r.status_code,200);c.environ_base['HTTP_X_STUDY_ID']=c.get('/api/study').json['studyId'];return r.json['csrf']
 def post(self,image,action,answers=None,**extra):
  return self.client.post('/api/evaluation',json={'evaluator':'01','imageId':image['image_id'],'action':action,'answers':answers,**extra},headers={'X-CSRF-Token':self.csrf})
 def answer(self,image,country='KR',mode='patches'):
  return {'countries':[country],'countryMode':'selected','evidence':{country:{'mode':mode,'patches':[image['patches'][0]['patch_id']] if mode=='patches' else []}}}
 def test_no_copy_no_hidden_fields_media(self):
  self.assertEqual(len(self.first['patches']),3);self.assertFalse(any(p.suffix in ('.png','.jpg') for p in self.args.state.rglob('*')))
  blob=json.dumps(self.study)
  for hidden in ('description','threshold','source_key','/project','refined_countries'):self.assertNotIn(hidden,blob)
  r=self.client.get('/'+self.first['original_image']);self.assertEqual(r.data,PNG);self.assertNotIn('original0',r.headers.get('Content-Disposition',''));r.close()
  self.assertEqual(self.app.test_client().get('/'+self.first['original_image']).status_code,401)
  self.assertEqual(self.client.get('/media/../../storage.py').status_code,404)
 def test_lock_resume_isolation_exports(self):
  self.assertEqual(self.post(self.first,'rate',rating=4).status_code,409)
  a=self.answer(self.first);self.assertEqual(self.post(self.first,'countries',a).status_code,200)
  state=self.client.get('/api/evaluation?evaluator=01').json;self.assertEqual(state['saved'][0]['stage'],'evidence');self.assertEqual(state['descriptions'],{})
  self.assertEqual(self.post(self.first,'lock',{**a,'evidence':{}}).status_code,400)
  locked=self.post(self.first,'lock',a);self.assertEqual(locked.status_code,200);self.assertEqual(self.post(self.first,'lock',a).json,locked.json)
  self.assertEqual(self.post(self.first,'countries',a).status_code,409)
  for value in (0,6,True):self.assertEqual(self.post(self.first,'rate',rating=value).status_code,400)
  self.assertEqual(self.post(self.first,'rate',rating=4).status_code,200);self.assertEqual(self.post(self.first,'rate',rating=4).status_code,200)
  self.assertEqual(self.post(self.first,'rate',rating=3).status_code,409);self.assertEqual(self.client.get('/api/export?evaluator=01').status_code,409)
  self.assertEqual(self.post(self.second,'lock',self.answer(self.second,'CN','none')).status_code,200);self.assertEqual(self.post(self.second,'rate',ratingUnsure=True).status_code,200)
  records={r['image_id']:r for r in self.client.get('/api/export?format=json').json['items']};m=records[self.first['image_id']]['comparisons'][0];self.assertEqual((m['precision'],m['recall'],m['jaccard']),(1,1,1))
  n=records[self.second['image_id']]['comparisons'][0];self.assertIsNone(n['model_patch_ids']);self.assertFalse(n['comparable']);self.assertEqual(self.client.get('/api/export?format=csv').status_code,200)
  other=self.app.test_client();self.login(other,'02');self.assertEqual(other.get('/api/evaluation?evaluator=02').json['saved'],[]);self.assertEqual(other.get('/api/evaluation?evaluator=01').status_code,403);self.assertEqual(other.get('/api/admin').status_code,403)
  reopened=create_app(self.args.state).test_client();self.login(reopened,'01');self.assertEqual(len(reopened.get('/api/evaluation').json['saved']),2)
  researcher=self.app.test_client();self.login(researcher,'researcher');self.assertEqual(researcher.get('/api/admin').json['progress'][0]['complete'],2);self.assertEqual(len(researcher.get('/api/export?evaluator=all').json['items']),2)
 def test_auth_csrf(self):
  anon=self.app.test_client();self.assertEqual(anon.get('/api/study',headers={'oai-authenticated-user-id':'spoof'}).status_code,401)
  self.assertEqual(anon.post('/api/login',json={'code':'wrong'}).status_code,401);self.assertEqual(anon.post('/api/login',json=[]).status_code,401)
  self.assertEqual(self.client.post('/api/logout').status_code,403);self.assertEqual(self.client.post('/api/logout',headers={'X-CSRF-Token':self.csrf,'Origin':'https://other.example'}).status_code,403)
  self.assertEqual(self.client.post('/api/logout',headers={'X-CSRF-Token':self.csrf}).status_code,200);self.assertEqual(self.client.get('/api/study').status_code,401)
 def test_changed_asset_and_failed_prepare(self):
  active=(self.args.state/'active_study.json').read_bytes();next((self.args.project_root/'output').rglob('*p2.png')).unlink()
  with self.assertRaises(FileNotFoundError),redirect_stdout(io.StringIO()):prepare(self.args)
  self.assertEqual((self.args.state/'active_study.json').read_bytes(),active)
  (self.args.project_root/'original0.png').write_bytes(PNG+b'changed');self.assertEqual(self.client.get('/'+self.first['original_image']).status_code,409)
 def test_stale_study_rejected(self):
  self.assertEqual(self.client.get('/api/evaluation',headers={'X-Study-ID':'old-study'}).status_code,409)
  self.assertEqual(self.client.get('/api/export',headers={'X-Study-ID':'old-study'}).status_code,409)
  self.assertEqual(self.client.get('/'+self.first['original_image'].split('?')[0]+'?study_id=old').status_code,409)
 def test_metrics_empty_unsure(self):
  ref={'country_comparison':{'KR':{'tf_evaluated':True,'retained_patch_ids':[]}}};a={'countries':['KR'],'evidence':{'KR':{'mode':'none','patches':[]}}};r=comparison(a,ref)[0]
  self.assertTrue(r['both_empty']);self.assertIsNone(r['jaccard']);a['evidence']['KR']['mode']='unsure';self.assertFalse(comparison(a,ref)[0]['comparable'])
 def test_navigation_drafts_and_completed_review(self):
  a=self.answer(self.second);a['countries'].append('JP')
  r=self.post(self.second,'draft',a,step=2);self.assertEqual(r.status_code,200);self.assertEqual(r.json['answers']['draftStep'],2)
  self.assertEqual(self.post(self.second,'lock',a).status_code,400)
  empty={'countries':[],'countryMode':'selected','evidence':{}}
  self.assertEqual(self.post(self.first,'draft',empty,step=1).status_code,200)
  rows=self.client.get('/api/evaluation').json
  self.assertEqual(rows['descriptions'],{});self.assertEqual(len(rows['saved']),2)
  restored=next(r for r in rows['saved'] if r['image_id']==self.second['image_id'])
  self.assertEqual(restored['answers']['evidence'],a['evidence'])
  locked=self.post(self.second,'lock',self.answer(self.second));self.assertEqual(locked.status_code,200)
  rating=self.post(self.second,'draft',empty,step=3,rating=4)
  self.assertEqual(rating.json['stage'],'locked');self.assertEqual(rating.json['answers']['countries'],['KR'])
  self.assertNotIn('submittedAt',rating.json['answers'])
  self.assertEqual(self.post(self.second,'draft',empty,step=1).status_code,409)
  self.assertEqual(self.post(self.second,'rate',rating=4).status_code,200)
  self.assertEqual(self.client.get('/api/export').status_code,409)
  reviewed=self.client.get('/api/evaluation').json
  self.assertIn(self.second['image_id'],reviewed['descriptions'])
  self.assertEqual(next(r for r in reviewed['saved'] if r['image_id']==self.second['image_id'])['answers']['rating'],4)
  self.assertEqual(self.post(self.second,'draft',step=3,rating=2).status_code,409)
  self.post(self.first,'lock',self.answer(self.first));self.post(self.first,'rate',ratingUnsure=True)
  self.assertEqual(len(self.client.get('/api/export').json['items']),2)
if __name__=='__main__':unittest.main()
