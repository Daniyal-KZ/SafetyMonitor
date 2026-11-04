import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image
import torch
import yaml

from safety_monitor.classes import canonical_name
from safety_monitor.ppe import BoxDetection as Detection, draw_detections
from train_weights import TRAIN_CLASSES, SOURCE_MAP, SOURCE_NAMES, prepare_dataset, transfer_weights


class TransferChecks(unittest.TestCase):
    def test_one_based_original_rows_are_transferred_by_name(self):
        labels = {0: None, 1: 'GLOVES', 2: 'HELMET', 3: 'PERSON', 4: 'PPE GLASSES', 5: 'VEST'}
        source = {'class_embed.weight': torch.arange(12).reshape(6, 2),
                  'transformer.enc_out_class_embed.0.bias': torch.arange(6),
                  'backbone.weight': torch.tensor([123])}
        target = {'class_embed.weight': torch.full((9, 2), -1),
                  'transformer.enc_out_class_embed.0.bias': torch.full((9,), -2),
                  'backbone.weight': torch.tensor([0])}
        result = transfer_weights(target, source, labels)
        self.assertTrue(torch.equal(result['class_embed.weight'][:5], source['class_embed.weight'][1:]))
        self.assertTrue(torch.equal(result['transformer.enc_out_class_embed.0.bias'][:5], torch.arange(1, 6)))
        self.assertTrue((result['class_embed.weight'][5:] == -1).all())
        self.assertEqual(result['backbone.weight'].item(), 123)
        self.assertTrue((target['class_embed.weight'] == -1).all())

    def test_retraining_keeps_already_learned_negative_classes(self):
        source = {'class_embed.bias': torch.arange(9)}
        target = {'class_embed.bias': torch.full((9,), -1)}
        labels = dict(enumerate(TRAIN_CLASSES)) | {8: None}
        result = transfer_weights(target, source, labels)['class_embed.bias']
        self.assertTrue(torch.equal(result[:8], torch.arange(8)))
        self.assertEqual(result[8].item(), -1)

    def test_incompatible_architecture_is_rejected(self):
        with self.assertRaises(ValueError):
            transfer_weights({'backbone': torch.zeros(3)}, {'backbone': torch.zeros(2)}, {})


class DatasetChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.output = self.root / 'source', self.root / 'output'
        for i, split in enumerate(('train', 'val', 'test')):
            (self.source / 'images' / split).mkdir(parents=True)
            (self.source / 'labels' / split).mkdir(parents=True)
            Image.new('RGB', (100, 100), (50*i, 10, 20)).save(self.source / 'images' / split / 'one.png')
            (self.source / 'labels' / split / 'one.txt').write_text(
                '\n'.join(f'{label} .5 .5 .2 .4' for label in range(11)))
        (self.source / 'data.yaml').write_text(yaml.safe_dump({'names': SOURCE_NAMES}))

    def test_coco_negative_labels_boxes_and_split_integrity(self):
        report = prepare_dataset(self.source, self.output)
        self.assertEqual(report['splits']['train']['counts']['NO HELMET'], 1)
        payload = json.loads((self.output / 'train/_annotations.coco.json').read_text())
        self.assertEqual([c['name'] for c in payload['categories']], list(TRAIN_CLASSES))
        self.assertEqual([a['category_id'] for a in payload['annotations']],
                         [SOURCE_MAP[i] for i in range(11) if i in SOURCE_MAP])
        self.assertEqual(payload['annotations'][0]['bbox'], [40, 30, 20, 40])
        self.assertEqual(len(payload['annotations']), 8)

    def test_cross_split_duplicate_cannot_be_validation(self):
        (self.source / 'images/val/one.png').write_bytes((self.source / 'images/train/one.png').read_bytes())
        with self.assertRaises(ValueError):
            prepare_dataset(self.source, self.output)

    def test_wrong_source_names_are_rejected(self):
        (self.source / 'data.yaml').write_text(yaml.safe_dump({'names': list(reversed(SOURCE_NAMES))}))
        with self.assertRaises(ValueError):
            prepare_dataset(self.source, self.output)


class DisplayChecks(unittest.TestCase):
    def test_negative_names_and_red_outline(self):
        for raw, name in [('no_helmet', 'NO HELMET'), ('no_goggle', 'NO PPE GLASSES'), ('no-gloves', 'NO GLOVES')]:
            self.assertEqual(canonical_name(raw), name)
            image = draw_detections(np.zeros((120, 220, 3), dtype=np.uint8),
                                    [Detection(name, .9, (20, 40, 100, 100))])
            self.assertEqual(image[100, 50].tolist(), [30, 30, 240])
