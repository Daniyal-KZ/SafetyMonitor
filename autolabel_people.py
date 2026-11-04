"""Local person proposals for the cleaned annotation workspace. Never promotes training data."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parent


def overlap(a,b):
    ax,ay,aw,ah=a; bx,by,bw,bh=b
    inter=max(0,min(ax+aw,bx+bw)-max(ax,bx))*max(0,min(ay+ah,by+bh)-max(ay,by))
    union=aw*ah+bw*bh-inter
    return inter/union if union>0 else 0


def add_proposals(data, predictions):
    person=next(c['id'] for c in data['categories'] if c['name']=='PERSON')
    existing={i['id']:[] for i in data['images']}
    for a in data['annotations']:
        if a['category_id']==person:
            existing[a['image_id']].append(a['bbox'])
    next_id=max((a['id'] for a in data['annotations']),default=-1)+1
    added=0
    for info in data['images']:
        boxes=existing[info['id']]
        for p in predictions[info['file_name']]:
            x,y,w,h=p['bbox']
            x1,y1=max(0,x),max(0,y)
            x2,y2=min(info['width'],x+w),min(info['height'],y+h)
            box=[x1,y1,x2-x1,y2-y1]
            if box[2]<2 or box[3]<2 or any(overlap(box,b)>=.5 for b in boxes):
                continue
            data['annotations'].append(dict(id=next_id,image_id=info['id'],category_id=person,
                bbox=box,area=box[2]*box[3],iscrowd=0,score=p['confidence'],
                annotation_source='yolo26n_person_proposal',review_status='unreviewed'))
            next_id+=1;added+=1;boxes.append(box)
        info['annotation_review_status']='needs_all_classes_review'
    data['info']['description']='DRAFT with unreviewed person proposals; all PPE classes still require review'
    return added


def write_atomic(path,data):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    temporary.replace(path)


def preview(dataset, output):
    data=json.loads((dataset/'train/_annotations.coco.json').read_text(encoding='utf-8'))
    by_id={i['id']:[] for i in data['images']}
    for a in data['annotations']:
        by_id[a['image_id']].append(a)
    names={c['id']:c['name'] for c in data['categories']}
    candidates=[i for i in data['images'] if any(a.get('annotation_source') for a in by_id[i['id']])]
    import random
    chosen=random.Random(42).sample(candidates,min(12,len(candidates)))
    canvas=Image.new('RGB',(1440,1200),'#20232a')
    font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',14)
    for n,info in enumerate(chosen):
        with Image.open(dataset/'train'/info['file_name']) as im:
            tile=im.convert('RGB').resize((360,360))
        draw=ImageDraw.Draw(tile)
        sx,sy=360/info['width'],360/info['height']
        for a in by_id[info['id']]:
            x,y,w,h=a['bbox'];auto=bool(a.get('annotation_source'))
            color='cyan' if auto else 'orange'
            draw.rectangle((x*sx,y*sy,(x+w)*sx,(y+h)*sy),outline=color,width=2)
            draw.text((max(0,x*sx),max(0,y*sy-16)),('AUTO ' if auto else '')+names[a['category_id']],
                      font=font,fill='white',stroke_width=1,stroke_fill='black')
        canvas.paste(tile,((n%4)*360,(n//4)*400))
        ImageDraw.Draw(canvas).text(((n%4)*360+3,(n//4)*400+365),f"{n+1}: image_id={info['id']} | AUTO = cyan",font=font,fill='white')
    canvas.save(output,quality=92)


def run(dataset, batch_dataset, batch_size=32):
    from ultralytics import YOLO
    import torch
    import cv2
    torch.set_num_threads(4)
    cv2.setNumThreads(1)
    for folder in (dataset,batch_dataset):
        status=json.loads((folder/'dataset_status.json').read_text(encoding='utf-8'))
        if status.get('training_ready') is not False:
            raise ValueError('Only an explicitly unapproved annotation workspace can be changed.')
    weights=ROOT/'weights/yolo26n.pt'
    settings={'model_sha256':hashlib.sha256(weights.read_bytes()).hexdigest(),
              'confidence':.35,'imgsz':640,'class':'person','iou':.5}
    jobs=[];datasets={}
    for split in ('train','valid','test'):
        path=dataset/split/'_annotations.coco.json'
        backup=path.with_name('_annotations.before_person.coco.json')
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        data=json.loads(backup.read_text(encoding='utf-8'))
        datasets[split]=data
        settings[split+'_source_sha256']=hashlib.sha256(backup.read_bytes()).hexdigest()
        jobs.extend((split,i['file_name']) for i in data['images'])
    connection=sqlite3.connect(dataset/'person_prediction_cache.sqlite3')
    try:
        connection.execute('CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY, value TEXT)')
        connection.execute('CREATE TABLE IF NOT EXISTS predictions (path TEXT PRIMARY KEY, value TEXT)')
        previous=connection.execute('SELECT value FROM metadata WHERE id=1').fetchone()
        if previous and json.loads(previous[0])!=settings:
            raise ValueError('Prediction cache does not match model/dataset/settings.')
        connection.execute('INSERT OR IGNORE INTO metadata VALUES (1,?)',(json.dumps(settings),))
        connection.commit()
        done={r[0] for r in connection.execute('SELECT path FROM predictions')}
        pending=[j for j in jobs if '/'.join(j) not in done]
        print(f'Person proposals: {len(pending)} pending / {len(jobs)} total; cache resumes interrupted work.',flush=True)
        model=YOLO(str(weights))
        if model.names.get(0)!='person':
            raise ValueError('Helper model class 0 is not person')
        device=0 if torch.cuda.is_available() else 'cpu'
        started=time.monotonic()
        def load_frame(job):
            split,name=job
            frame=cv2.imdecode(np.fromfile(dataset/split/name,dtype=np.uint8),cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError(f'Image became unreadable: {split}/{name}')
            return frame
        pool=ThreadPoolExecutor(max_workers=8)
        futures=[pool.submit(load_frame,j) for j in pending[:batch_size]]
        for start in range(0,len(pending),batch_size):
            batch=pending[start:start+batch_size]
            frames=[f.result() for f in futures]
            # Only prefetch one batch, keeping RAM bounded independently of dataset size.
            futures=[pool.submit(load_frame,j) for j in pending[start+batch_size:start+2*batch_size]]
            results=model.predict(source=frames,classes=[0],conf=.35,iou=.5,imgsz=640,
                                  device=device,quantize=16 if device!='cpu' else 32,
                                  verbose=False,save=False,batch=batch_size)
            for (split,name),result in zip(batch,results):
                values=[]
                for xyxy,confidence in zip(result.boxes.xyxy.cpu().tolist(),result.boxes.conf.cpu().tolist()):
                    x1,y1,x2,y2=xyxy
                    values.append({'bbox':[x1,y1,x2-x1,y2-y1],'confidence':confidence})
                connection.execute('INSERT INTO predictions VALUES (?,?)',('/'.join((split,name)),json.dumps(values)))
            connection.commit()
            completed=start+len(batch)
            if completed%256==0 or completed==len(pending):
                seconds=time.monotonic()-started
                eta=(len(pending)-completed)*seconds/completed
                print(f'{completed}/{len(pending)} | {completed/max(seconds,.001):.1f} images/s | remaining ~{eta/60:.1f} min',flush=True)
        pool.shutdown(wait=True)
        report={'settings':settings,'seconds':time.monotonic()-started,'training_ready':False,'splits':{}}
        for split,data in datasets.items():
            predictions={i['file_name']:json.loads(connection.execute('SELECT value FROM predictions WHERE path=?',
                         (split+'/'+i['file_name'],)).fetchone()[0]) for i in data['images']}
            added=add_proposals(data,predictions)
            person_id=next(c['id'] for c in data['categories'] if c['name']=='PERSON')
            person_images={a['image_id'] for a in data['annotations'] if a['category_id']==person_id}
            report['splits'][split]={'images':len(data['images']),'added_person_boxes':added,
                                    'images_still_without_person':len(data['images'])-len(person_images)}
            write_atomic(dataset/split/'_annotations.coco.json',data)
            batch_path=batch_dataset/split/'_annotations.coco.json'
            batch_backup=batch_path.with_name('_annotations.before_person.coco.json')
            if not batch_backup.exists():
                batch_backup.write_bytes(batch_path.read_bytes())
            batch_data=json.loads(batch_backup.read_text(encoding='utf-8'))
            batch_added=add_proposals(batch_data,predictions)
            report['splits'][split]['batch_added_person_boxes']=batch_added
            write_atomic(batch_path,batch_data)
        for folder in (dataset,batch_dataset):
            status_path=folder/'dataset_status.json'
            status=json.loads(status_path.read_text(encoding='utf-8'))
            status['person_proposals']='complete_unreviewed'
            status['training_ready']=False
            queue=[]
            for split in ('train','valid','test'):
                d=json.loads((folder/split/'_annotations.coco.json').read_text(encoding='utf-8'))
                names={c['id']:c['name'] for c in d['categories']}
                by_image={i['id']:[] for i in d['images']}
                for a in d['annotations']:
                    by_image[a['image_id']].append(a)
                for info in d['images']:
                    anns=by_image[info['id']]
                    people=[a for a in anns if names[a['category_id']]=='PERSON']
                    automatic=[a for a in people if a.get('annotation_source')]
                    queue.append([split,info['file_name'],len(people),len(automatic),
                                  sum(a['score']<.6 for a in automatic),not people,'needs_all_classes_review'])
                status['splits'][split]['annotations']=len(d['annotations'])
                status['splits'][split]['boxes']=dict(Counter(names[a['category_id']] for a in d['annotations']))
            with (folder/'person_review_queue.csv').open('w',encoding='utf-8-sig',newline='') as handle:
                writer=csv.writer(handle)
                writer.writerow(['split','filename','person_boxes','auto_person_boxes','auto_below_0.6',
                                 'no_person_detected','status'])
                writer.writerows(queue)
            write_atomic(status_path,status)
            write_atomic(folder/'person_proposals_report.json',report)
        preview(batch_dataset,batch_dataset/'person_proposals_preview.jpg')
        print(json.dumps(report,indent=2,ensure_ascii=False),flush=True)
    finally:
        connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,default=ROOT/'data/ppe_clean_review')
    parser.add_argument('--batch-dataset',type=Path,default=ROOT/'data/ppe_annotation_batch')
    parser.add_argument('--batch-size',type=int,default=32)
    args=parser.parse_args()
    if args.batch_size<1:
        parser.error('--batch-size must be positive')
    run(args.dataset,args.batch_dataset,args.batch_size)
