import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from prepare_ppe_review import normalize_box, prepare
from autolabel_people import add_proposals


class ReviewTests(unittest.TestCase):
    def test_clip_and_reject_invalid_boxes(self):
        self.assertEqual(normalize_box([-5,10,20,30],100,100),[0,10,15,30])
        for box in ([0,0,float('nan'),2],[0,0,-2,4],[101,0,4,4]):
            with self.assertRaises(ValueError):
                normalize_box(box,100,100)

    def test_proposals_preserve_ground_truth_and_mark_new_boxes(self):
        data={'info':{},'categories':[{'id':2,'name':'PERSON'}],
              'images':[{'id':3,'file_name':'one.jpg','width':100,'height':100}],
              'annotations':[{'id':7,'image_id':3,'category_id':2,'bbox':[0,0,30,90]}]}
        predictions={'one.jpg':[{'bbox':[1,1,30,89],'confidence':.9},
                                {'bbox':[60,20,50,60],'confidence':.8}]}
        self.assertEqual(add_proposals(data,predictions),1)
        self.assertEqual(data['annotations'][0]['bbox'],[0,0,30,90])
        new=data['annotations'][1]
        self.assertEqual(new['bbox'],[60,20,40,60])
        self.assertEqual(new['review_status'],'unreviewed')
        self.assertEqual(new['score'],.8)
        self.assertEqual(new['id'],8)

    def test_cleaning_deduplicates_across_splits_without_touching_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source'
            originals={}
            for split in ('train','valid','test'):
                folder=source/split;folder.mkdir(parents=True)
                # train and test deliberately share exact pixels.
                color=(12,34,56) if split!='valid' else (56,34,12)
                Image.new('RGB',(40,40),color).save(folder/'one.jpg')
                d={'categories':[{'id':1,'name':'Hardhat'},{'id':2,'name':'Person'}],
                   'images':[{'id':0,'file_name':'one.jpg','width':40,'height':40}],
                   'annotations':[{'id':0,'image_id':0,'category_id':1 if split=='test' else 2,
                                   'bbox':[5,5,20,20],'iscrowd':0}]}
                if split=='train':
                    d['images'].append({'id':1,'file_name':'missing.jpg','width':40,'height':40})
                path=folder/'_annotations.coco.json';path.write_text(json.dumps(d))
                originals[path]=path.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                report=prepare(source,root/'clean',root/'batch')
            self.assertEqual(report['retained'],2)
            self.assertEqual(report['repairs']['cross_split_duplicates_removed'],1)
            self.assertEqual(report['exclusions']['missing_or_corrupt_image'],1)
            test=json.loads((root/'clean/test/_annotations.coco.json').read_text())
            self.assertEqual({a['category_id'] for a in test['annotations']},{1,2})
            self.assertEqual(report['clean_splits']['train']['images'],0)
            self.assertFalse(json.loads((root/'clean/dataset_status.json').read_text())['training_ready'])
            for path,content in originals.items():
                self.assertEqual(path.read_bytes(),content)
