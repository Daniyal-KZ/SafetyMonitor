import unittest
from autolabel_ppe import merge_proposals


class PPEProposalTests(unittest.TestCase):
    def data(self):
        return {'info':{},'images':[{'id':0,'file_name':'one.jpg'}],
                'annotations':[{'id':0,'image_id':0,'category_id':1,'bbox':[10,10,20,20]}]}

    def test_conflicting_negative_is_not_inserted_over_existing_helmet(self):
        data=self.data()
        added,conflicts=merge_proposals(data,{'one.jpg':[{'category_id':5,'bbox':[10,10,20,20],'score':.99}]})
        self.assertEqual(len(data['annotations']),1)
        self.assertEqual(len(conflicts),1)
        self.assertEqual(sum(added.values()),0)

    def test_new_equipment_is_marked_as_unreviewed_and_duplicates_are_skipped(self):
        data=self.data()
        predictions={'one.jpg':[{'category_id':1,'bbox':[11,10,20,20],'score':.9},
                                {'category_id':4,'bbox':[50,50,20,30],'score':.8}]}
        added,conflicts=merge_proposals(data,predictions)
        self.assertEqual(added['VEST'],1)
        self.assertEqual(added['HELMET'],0)
        self.assertEqual(data['annotations'][-1]['review_status'],'unreviewed')
        self.assertEqual(conflicts,[])

    def test_missing_predictions_do_not_create_negative_classes(self):
        data=self.data()
        added,conflicts=merge_proposals(data,{'one.jpg':[]})
        self.assertEqual(len(data['annotations']),1)
        self.assertEqual(sum(added.values()),0)
