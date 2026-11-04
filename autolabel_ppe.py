"""Add direct PPE predictions; never infer a NO class from a missing positive detection."""
import csv
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import time
from unittest.mock import patch

import cv2
import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

from autolabel_people import overlap, write_atomic
from prepare_ppe_review import ALIASES, CLASSES, link_image

ROOT=Path(__file__).resolve().parent
MODEL=ROOT/'.artifacts/annotation-helper/best.pt'
SOURCE=ROOT/'data/ppe_annotation_batch'
OUTPUT=ROOT/'data/ppe_all_classes_draft'
MODEL_URL='https://huggingface.co/Hexmon/vyra-yolo-ppe-detection/tree/0c77b4be598e9069f50db2acfecd4bf0acd72fc9'
MODEL_CARD=ROOT/'.artifacts/annotation-helper/README.md'
SOURCE_NAME='vyra_ppe_direct_prediction'
OPPOSITE={1:5,5:1,3:6,6:3,0:7,7:0,4:8,8:4}


def containment(a,b):
    ax,ay,aw,ah=a;bx,by,bw,bh=b
    intersection=max(0,min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))
    area=min(aw*ah,bw*bh)
    return intersection/area if area>0 else 0


def load_helper(path=None, aliases=None):
    path=MODEL if path is None else path
    aliases=ALIASES if aliases is None else aliases
    from ultralytics import YOLO
    # Allow only the installed standard neural-network module classes required
    # by this checkpoint; never enable arbitrary pickle deserialization.
    allowed=[]
    for name in torch.serialization.get_unsafe_globals_in_checkpoint(path):
        if not name.startswith(('torch.nn.modules.','ultralytics.nn.modules.','ultralytics.nn.tasks.')):
            raise ValueError(f'Unexpected checkpoint global: {name}')
        module,attr=name.rsplit('.',1)
        cls=getattr(importlib.import_module(module),attr)
        if not isinstance(cls,type) or not issubclass(cls,torch.nn.Module):
            raise ValueError(f'Not an allowed network module: {name}')
        allowed.append(cls)
    original_load=torch.load
    def safe_load(*args,**kwargs):
        kwargs['weights_only']=True
        return original_load(*args,**kwargs)
    with torch.serialization.safe_globals(allowed),patch('torch.load',safe_load):
        model=YOLO(str(path))
    required={name for name,canonical in aliases.items() if canonical!='PERSON'}
    if not required <= set(model.names.values()):
        raise ValueError(f'Missing required helper classes: {model.names}')
    return model


def merge_proposals(data,predictions):
    by_image={i['id']:[] for i in data['images']}
    for a in data['annotations']:
        by_image[a['image_id']].append(a)
    next_id=max((a['id'] for a in data['annotations']),default=-1)+1
    added={name:0 for name in CLASSES};conflicts=[]
    for info in data['images']:
        existing=by_image[info['id']]
        for p in sorted(predictions[info['file_name']],key=lambda p:-p['score']):
            cls=p['category_id']; box=p['bbox']
            if any(a['category_id']==cls and (overlap(a['bbox'],box)>=.35 or containment(a['bbox'],box)>=.8) for a in existing):
                continue
            contrary=[a for a in existing if a['category_id']==OPPOSITE.get(cls) and
                      (overlap(a['bbox'],box)>=.25 or containment(a['bbox'],box)>=.5)]
            if contrary:
                conflicts.append({'image':info['file_name'],'proposal':p,'existing_ids':[a['id'] for a in contrary]})
                continue
            annotation=dict(id=next_id,image_id=info['id'],category_id=cls,bbox=box,
                            area=box[2]*box[3],iscrowd=0,score=p['score'],
                            annotation_source=SOURCE_NAME,review_status='unreviewed')
            data['annotations'].append(annotation);existing.append(annotation)
            next_id+=1;added[CLASSES[cls]]+=1
        info['annotation_review_status']='needs_all_classes_review'
    data['info']['description']='DRAFT: source annotations + person and direct PPE proposals; not verified ground truth'
    return added,conflicts


def preview(data,folder):
    by_image={i['id']:[] for i in data['images']}
    for a in data['annotations']: by_image[a['image_id']].append(a)
    candidates=[i for i in data['images'] if any(a.get('annotation_source')==SOURCE_NAME for a in by_image[i['id']])]
    import random
    selected=random.Random(12).sample(candidates,min(24,len(candidates)))
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',14)
    manifest=[]
    for page in range(0,len(selected),12):
        canvas=Image.new('RGB',(1440,1200),'#20232a')
        for n,info in enumerate(selected[page:page+12]):
            with Image.open(folder/'train'/info['file_name']) as im: tile=im.convert('RGB').resize((360,360))
            draw=ImageDraw.Draw(tile)
            sx,sy=360/info['width'],360/info['height']
            for a in by_image[info['id']]:
                # Focus this preview on PPE; PERSON was inspected separately.
                if a['category_id']==2: continue
                x,y,w,h=a['bbox'];auto=a.get('annotation_source') in ('vyra_ppe_direct_prediction','hazards_ppe_direct_prediction')
                edited=a.get('annotation_source')=='assistant_visual_correction'
                draw.rectangle((x*sx,y*sy,(x+w)*sx,(y+h)*sy),outline='lime' if edited else 'cyan' if auto else 'orange',width=2)
                draw.text((max(0,x*sx),max(0,y*sy-16)),('EDIT ' if edited else 'AUTO ' if auto else '')+CLASSES[a['category_id']],
                          font=font,fill='white',stroke_width=1,stroke_fill='black')
            canvas.paste(tile,((n%4)*360,(n//4)*400))
            ImageDraw.Draw(canvas).text(((n%4)*360+3,(n//4)*400+363),f"{page+n+1}: id={info['id']}",font=font,fill='white')
            manifest.append({'number':page+n+1,'image':info,'annotations':by_image[info['id']]})
        canvas.save(folder/f'ppe_preview_{page//12+1}.jpg',quality=92)
    write_atomic(folder/'preview_manifest.json',manifest)


def main():
    torch.set_num_threads(4);cv2.setNumThreads(1)
    model=load_helper()
    print('Helper classes:',model.names,flush=True)
    OUTPUT.mkdir(exist_ok=True)
    model_digest=hashlib.sha256(MODEL.read_bytes()).hexdigest()
    report={'training_ready':False,'helper_sha256':model_digest,
            'helper_url':MODEL_URL,'source_dataset':str(SOURCE),
            'note':'Model-generated annotations are not independent validation ground truth.',
            'splits':{}}
    started=time.monotonic()
    for split in ('train','valid','test'):
        src=SOURCE/split/'_annotations.coco.json'
        data=json.loads(src.read_text(encoding='utf-8'))
        folder=OUTPUT/split;folder.mkdir(exist_ok=True)
        cached=OUTPUT/(split+'_predictions.json')
        if cached.exists():
            saved=json.loads(cached.read_text(encoding='utf-8'))
            if saved['source_sha256']!=hashlib.sha256(src.read_bytes()).hexdigest() or saved['model_sha256']!=model_digest:
                raise ValueError('Cache differs from dataset or model')
            predictions=saved['predictions']
        else:
            predictions={}
            for start in range(0,len(data['images']),8):
                batch=data['images'][start:start+8]
                frames=[cv2.imdecode(np.fromfile(SOURCE/split/i['file_name'],dtype=np.uint8),cv2.IMREAD_COLOR) for i in batch]
                results=model.predict(frames,conf=.45,iou=.5,imgsz=640,device=0,quantize=16,verbose=False,save=False)
                for info,result in zip(batch,results):
                    proposals=[]
                    for xyxy,score,class_id in zip(result.boxes.xyxy.cpu().tolist(),result.boxes.conf.cpu().tolist(),result.boxes.cls.cpu().tolist()):
                        name=model.names[int(class_id)]
                        if name not in ALIASES: continue
                        canonical=ALIASES[name]
                        if canonical=='PERSON': continue
                        if score < (.6 if canonical.startswith('NO ') else .5): continue
                        x1,y1,x2,y2=xyxy
                        x1,y1=max(0,x1),max(0,y1);x2,y2=min(info['width'],x2),min(info['height'],y2)
                        if x2-x1<2 or y2-y1<2: continue
                        proposals.append({'category_id':CLASSES.index(canonical),'bbox':[x1,y1,x2-x1,y2-y1],'score':score})
                    predictions[info['file_name']]=proposals
                if start%128==0: print(f'{split}: {min(start+8,len(data["images"]))}/{len(data["images"])}',flush=True)
            write_atomic(cached,{'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),
                                 'model_sha256':model_digest,'predictions':predictions})
        for info in data['images']:
            target=folder/info['file_name']
            if not target.exists(): link_image(SOURCE/split/info['file_name'],target)
        shutil.copy2(src,folder/'_annotations.before_ppe.coco.json')
        added,conflicts=merge_proposals(data,predictions)
        write_atomic(folder/'_annotations.coco.json',data)
        write_atomic(OUTPUT/(split+'_conflicts.json'),conflicts)
        report['splits'][split]={'images':len(data['images']),'added':added,'conflicts_held_for_review':len(conflicts)}
        if split=='train': preview(data,OUTPUT)
    report['seconds']=time.monotonic()-started
    write_atomic(OUTPUT/'dataset_status.json',report)
    for name in ('README.dataset.txt','README.roboflow.txt'):
        shutil.copy2(SOURCE/name,OUTPUT/name)
    shutil.copy2(MODEL_CARD,OUTPUT/'annotation_helper_model_card.md')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--second-pass',action='store_true')
    args=parser.parse_args()
    if args.second_pass:
        MODEL=ROOT/'.artifacts/annotation-helper/second/buildvision-hazards-v0.3.pt'
        SOURCE=OUTPUT
        OUTPUT=ROOT/'data/ppe_full_annotation_review'
        MODEL_URL='https://huggingface.co/constructelligence/construction-site-safety-hazards/tree/79673cff5a8bb249fc8ec9cca3daa19e2ef95191'
        MODEL_CARD=ROOT/'.artifacts/annotation-helper/second/README.md'
        SOURCE_NAME='hazards_ppe_direct_prediction'
        ALIASES=dict(zip(('gloves','hardhat','person','goggles','safety vest',
                          'no-hardhat','no-goggles','no-gloves','no-safety vest'),CLASSES))
    main()
