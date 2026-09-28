import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
import zipfile
from contextlib import redirect_stdout
from PIL import Image

from bundle_loader import load_bundle
from dataset_loader import COUNTRIES
from manage import activate, snapshot, restore
from server import create_app


def bundle(path, count=65, unsafe=False, invalid_tf=False, prefix='', countries=None, two_patches=False):
    image=io.BytesIO();Image.new('RGB',(16,12),'blue').save(image,format='PNG')
    items=[];descriptions={};refs=[]
    for n in range(count):
        image_id=f'img_{prefix}{n:03d}';patch_id=f'p_{prefix}{n:03d}'
        items.append({'image_id':image_id,'original_image':'assets/original.png',
                      'patches':[{'patch_id':patch_id,'image':'assets/patch.png','box_xyxy':[0,0,8,8]}]})
        descriptions[image_id]={'description':f'Unmodified English description {n}.'}
        if two_patches:
            items[-1]['patches'].append({'patch_id':patch_id+'_extra','image':'assets/patch.png','box_xyxy':[1,1,9,9]})
        country = countries[n % len(countries)] if countries else 'country'
        refs.append({'image_id':image_id,'source_key':f'{country}/image{prefix}{n}.png','country_comparison':{
            c:{'tf_evaluated':c=='KR','retained_patch_ids':(['foreign_patch'] if invalid_tf else [patch_id]) if c=='KR' else None} for c,_ in COUNTRIES}})
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('study.json',json.dumps({'schema_version':'celve_human_eval_handoff_v1','countries':[{'id':c,'name':n} for c,n in COUNTRIES],'items':items}))
        archive.writestr('researcher/descriptions.json',json.dumps(descriptions))
        archive.writestr('researcher/analysis.json',json.dumps({'provenance':{'model_key':'test','split':'split_01','eligible_count':count,'sample_count':count},'items':refs}))
        archive.writestr('assets/original.png',image.getvalue());archive.writestr('assets/patch.png',image.getvalue())
        if unsafe:archive.writestr('../outside.txt','must never be written')


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.args=SimpleNamespace(state=self.root/'state',zip=self.root/'data.zip',limit=60,seed=42,max_mib=10,name='Test')
        bundle(self.args.zip)
    def tearDown(self):self.temp.cleanup()
    def activate(self):
        manifest=load_bundle(self.args)
        with redirect_stdout(io.StringIO()):activate(manifest,self.args.state)
        return manifest
    def login(self,app):
        codes=dict(line.split(': ',1) for line in (self.args.state/'access_codes.txt').read_text().splitlines() if ': ' in line)
        client=app.test_client();result=client.post('/api/login',json={'code':codes['01']})
        study=client.get('/api/study').json;client.environ_base['HTTP_X_STUDY_ID']=study['studyId']
        return client,study,result.json['csrf']
    def test_sixty_images_reimport_and_snapshot_restore(self):
        manifest=self.activate();self.assertEqual(len(manifest['study']['items']),60)
        client,study,csrf=self.login(create_app(self.args.state))
        last=study['items'][-1]
        body={'evaluator':'01','imageId':last['image_id'],'action':'lock','answers':{'countries':[],'countryMode':'none','evidence':{}}}
        self.assertEqual(client.post('/api/evaluation',json=body,headers={'X-CSRF-Token':csrf}).status_code,200)
        self.assertEqual(client.post('/api/evaluation',json={**body,'action':'rate','rating':5},headers={'X-CSRF-Token':csrf}).status_code,200)
        self.assertEqual(client.get('/api/export').status_code,409)
        self.assertEqual(manifest['study_id'],self.activate()['study_id'])
        self.assertEqual(len(client.get('/api/evaluation').json['saved']),1)
        output=self.root/'snapshot.tar.gz'
        with redirect_stdout(io.StringIO()):snapshot(self.args.state,output)
        with self.assertRaisesRegex(ValueError,'empty state'):restore(self.args.state,output)
        self.args.state=self.root/'restore'/'state'
        with redirect_stdout(io.StringIO()):restore(self.args.state,output)
        restored,new_study,_=self.login(create_app(self.args.state))
        self.assertEqual(study['studyId'],new_study['studyId'])
        self.assertEqual(restored.get('/api/evaluation').json['saved'][0]['answers']['rating'],5)
        image=restored.get('/'+new_study['items'][0]['original_image'])
        self.assertEqual(image.status_code,200);image.close()
    def test_invalid_bundles_preserve_active_study(self):
        self.activate();before=(self.args.state/'active_study.json').read_bytes()
        for flags in ({'unsafe':True},{'invalid_tf':True}):
            bundle(self.args.zip,**flags)
            with self.assertRaises(ValueError):self.activate()
            self.assertEqual(before,(self.args.state/'active_study.json').read_bytes())
        self.assertFalse((self.root/'outside.txt').exists())
        bundle(self.args.zip,count=2)
        with self.assertRaisesRegex(ValueError,'Requested 60'):load_bundle(self.args)
    def test_extraction_size_limit_and_empty_selection(self):
        self.args.max_mib=.001
        with self.assertRaisesRegex(ValueError,'uncompressed'):load_bundle(self.args)
        self.args.max_mib=10;self.args.limit=0
        self.assertEqual(len(load_bundle(self.args)['study']['items']),65)


if __name__=='__main__':unittest.main()
