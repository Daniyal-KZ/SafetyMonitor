import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
import torch

from train_weights import COCO_CLASSES, validate_coco_dataset, transfer_weights


class CurrentTrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for n, split in enumerate(('train','valid','test')):
            folder = self.root / split
            folder.mkdir()
            name = split+'.jpg'
            Image.new('RGB',(40,40),(n*60,0,0)).save(folder/name)
            data = {'images':[{'id':0,'file_name':name,'width':40,'height':40}],
                    'categories':[{'id':i,'name':v} for i,v in enumerate(COCO_CLASSES)],
                    'annotations':[{'id':i,'image_id':0,'category_id':i,'bbox':[1,1,10,10]}
                                   for i in range(9)]}
            (folder/'_annotations.coco.json').write_text(json.dumps(data))

    def test_nine_classes_validated_without_promoting_draft(self):
        report = validate_coco_dataset(self.root)
        self.assertFalse(report['training_ready'])
        self.assertEqual(report['classes'][-1],'NO VEST')
        self.assertEqual(report['splits']['valid']['boxes']['NO VEST'],1)

    def test_class_order_and_missing_files_rejected(self):
        path=self.root/'train/_annotations.coco.json'
        data=json.loads(path.read_text())
        data['categories'][0]['name']='PERSON'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError): validate_coco_dataset(self.root)
        data['categories'][0]['name']='GLOVES'
        path.write_text(json.dumps(data))
        (self.root/'valid/valid.jpg').unlink()
        with self.assertRaises(ValueError): validate_coco_dataset(self.root)

    def test_nine_class_head_retains_new_no_vest_and_background_initialization(self):
        original={'class_embed.bias':torch.arange(6)}
        target={'class_embed.bias':torch.full((10,),-1)}
        labels={0:None,1:'GLOVES',2:'HELMET',3:'PERSON',4:'PPE GLASSES',5:'VEST'}
        result=transfer_weights(target,original,labels,COCO_CLASSES)['class_embed.bias']
        self.assertTrue(torch.equal(result[:5],torch.arange(1,6)))
        self.assertTrue((result[5:]==-1).all())
