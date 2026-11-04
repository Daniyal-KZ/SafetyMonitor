"""Apply recorded visual crop review of all 357 newly proposed helmet boxes.

This is a category review of these crops, not certification of all objects in
the 1,830 full images. The dataset must retain training_ready=False.
"""
import json
from pathlib import Path
from collections import Counter

from autolabel_people import write_atomic
from autolabel_ppe import containment, overlap

ROOT=Path(__file__).resolve().parent
FOLDER=ROOT/'data/ppe_full_annotation_review'

# Explicitly visible hair, bald heads, fabric caps/wraps, hoods or non-hardhat hats.
NO_HELMET={
    8,9,10,11,17,20,22,24,27,30,38,39,40,
    51,52,59,60,61,62,67,70,75,76,79,
    86,87,88,92,93,95,98,99,100,101,105,106,107,110,112,114,115,123,
    128,129,133,134,135,139,140,142,144,145,146,149,150,154,155,165,168,
    221,224,245,251,263,264,266,267,274,275,276,277,278,280,282,286,288,289,291,292,294,
    302,304,306,327,330,331,332,334,336,337,339,344,345,346,
}
# Unclear crops, non-head objects, wrong extent (face/mask), or ambiguous headgear.
WITHHOLD={
    6,7,13,21,23,43,45,46,48,49,50,56,57,58,71,81,89,90,104,113,121,
    127,137,141,148,152,153,192,230,259,262,268,269,270,271,272,279,293,333,335,338,340,
}


def main():
    items=json.loads((ROOT/'.artifacts/annotation-review/helmets.json').read_text(encoding='utf-8'))
    assert len(items)==357 and not NO_HELMET & WITHHOLD
    actions={}
    for number,item in enumerate(items,1):
        key=(item['split'],item['annotation']['id'])
        assert key not in actions
        actions[key]=(number,item,'correct_to_no_helmet' if number in NO_HELMET else
                      'withhold_uncertain_or_invalid' if number in WITHHOLD else 'helmet_category_confirmed')
    log=[];summary={}
    for split in ('train','valid','test'):
        path=FOLDER/split/'_annotations.coco.json'
        backup=path.with_name('_annotations.before_visual.coco.json')
        if not backup.exists(): backup.write_bytes(path.read_bytes())
        data=json.loads(backup.read_text(encoding='utf-8'))
        image_by_id={i['id']:i for i in data['images']}
        kept=[]
        for a in data['annotations']:
            match=actions.get((split,a['id']))
            if match is None:
                kept.append(a);continue
            number,item,action=match
            assert a==item['annotation'] and image_by_id[a['image_id']]['file_name']==item['image']['file_name']
            log.append({'crop_number':number,'split':split,'filename':item['image']['file_name'],
                        'original_annotation':a.copy(),'action':action})
            if action=='withhold_uncertain_or_invalid': continue
            a['category_review']='assistant_visual_crop_review'
            a['review_status']='needs_full_image_review'
            if action=='correct_to_no_helmet':
                a['category_id']=5
                a['annotation_source']='assistant_visual_correction'
                a['original_model_score']=a.pop('score',None)
            kept.append(a)
        data['annotations']=kept
        data['info']['description']='Partially visually reviewed annotation draft; full-image class completeness not verified'
        write_atomic(path,data)
        names={c['id']:c['name'] for c in data['categories']}
        summary[split]={'images':len(data['images']),'annotations':len(kept),
                        'boxes':dict(Counter(names[a['category_id']] for a in kept))}
    assert len(log)==357
    write_atomic(FOLDER/'visual_review_log.json',log)
    status_path=FOLDER/'dataset_status.json'
    status=json.loads(status_path.read_text(encoding='utf-8'))
    status['training_ready']=False
    status['current_splits']=summary
    status['visual_review']={'new_helmet_crops_checked':357,'actions':dict(Counter(i['action'] for i in log)),
                            'full_images_completely_reviewed':0}
    write_atomic(status_path,status)
    print(json.dumps(status['visual_review'],ensure_ascii=False,indent=2))


if __name__=='__main__': main()
