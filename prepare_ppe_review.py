"""Build a reversible, deduplicated PPE annotation workspace, NOT approved ground truth."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import shutil

from PIL import Image

ROOT = Path(__file__).resolve().parent
CLASSES = ('GLOVES', 'HELMET', 'PERSON', 'PPE GLASSES', 'VEST',
           'NO HELMET', 'NO PPE GLASSES', 'NO GLOVES', 'NO VEST')
ALIASES = dict(zip(('Gloves', 'Hardhat', 'Person', 'Goggles', 'Safety Vest',
                   'NO-Hardhat', 'NO-Goggles', 'NO-Gloves', 'NO-Safety Vest'), CLASSES))
SPLITS = ('test', 'valid', 'train')  # keep exact duplicates in evaluation, never also in training


def inspect_image(record):
    try:
        raw = record['path'].read_bytes()
        with Image.open(io.BytesIO(raw)) as im:
            im.load()  # reject truncated/corrupt files, not merely bad headers
            rgb = im.convert('RGB')
            width, height = rgb.size
            pixels = hashlib.sha256(f'{width},{height}:'.encode()+rgb.tobytes()).hexdigest()
            tiny = list(rgb.convert('L').resize((9, 8)).getdata())
            bits = ''.join('1' if tiny[y*9+x] > tiny[y*9+x+1] else '0' for y in range(8) for x in range(8))
            return {'width':width, 'height':height, 'pixels':pixels,
                    'sha256':hashlib.sha256(raw).hexdigest(), 'dhash':f'{int(bits,2):016x}'}
    except (OSError, ValueError) as error:
        return {'error':type(error).__name__, 'message':str(error)}


def normalize_box(box, width, height):
    if len(box) != 4 or not all(isinstance(v, (int,float)) and math.isfinite(v) for v in box):
        raise ValueError('invalid_bbox')
    x,y,w,h = box
    if w <= 0 or h <= 0:
        raise ValueError('nonpositive_bbox')
    x1,y1,x2,y2 = max(0,x),max(0,y),min(width,x+w),min(height,y+h)
    if x2 <= x1 or y2 <= y1:
        raise ValueError('bbox_outside_image')
    return [x1,y1,x2-x1,y2-y1]


def write_json(path, data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def link_image(source, destination):
    try:
        os.link(source,destination)
    except OSError:
        shutil.copy2(source,destination)


def export_dataset(records, output):
    output.mkdir(parents=True,exist_ok=False)
    summaries = {}
    for split in SPLITS:
        folder = output/split
        folder.mkdir()
        images, annotations, counts = [], [], Counter()
        for r in records:
            if r['split'] != split:
                continue
            image_id = len(images)
            filename = r['pixels']+'.jpg'
            link_image(r['path'],folder/filename)
            images.append(dict(id=image_id,file_name=filename,width=r['width'],height=r['height']))
            for a in r['annotations']:
                box=a['bbox']
                annotations.append(dict(id=len(annotations),image_id=image_id,category_id=a['category_id'],
                                        bbox=box,area=box[2]*box[3],iscrowd=a['iscrowd']))
                counts[CLASSES[a['category_id']]]+=1
        write_json(folder/'_annotations.coco.json',dict(
            info={'description':'DRAFT: incomplete annotations; manual review required'},licenses=[],
            images=images,annotations=annotations,
            categories=[dict(id=i,name=n,supercategory='PPE') for i,n in enumerate(CLASSES)]))
        summaries[split]={'images':len(images),'annotations':len(annotations),'boxes':dict(counts)}
    write_json(output/'dataset_status.json',{
        'status':'needs_annotation_review','training_ready':False,'classes':list(CLASSES),
        'reason':'Missing people and PPE annotations in source. Cleaning is not relabeling.',
        'image_storage':'Hard links when supported. Do not edit image pixels; edit annotation files only.',
        'splits':summaries})
    return summaries


def select_batch(records):
    selected=[]
    for split,quota in [('train',200),('valid',50),('test',30)]:
        pool=[r for r in records if r['split']==split]
        random.Random(42).shuffle(pool)
        chosen=set()
        # At most one exact dHash per split in the small batch. It is a diversity
        # heuristic only: hash collisions are NOT grounds to delete source photos.
        used_hashes=set()
        for cls in sorted(range(len(CLASSES)),key=lambda c:sum(c in r['labels'] for r in pool)):
            have=sum(cls in r['labels'] for r in pool if r['pixels'] in chosen)
            for r in pool:
                if have>=quota:
                    break
                if cls not in r['labels'] or r['pixels'] in chosen or r['dhash'] in used_hashes:
                    continue
                chosen.add(r['pixels']);used_hashes.add(r['dhash']);have+=1
        selected.extend(r for r in pool if r['pixels'] in chosen)
    return selected


def prepare(source, output, batch_output):
    if output.exists() or batch_output.exists():
        raise FileExistsError('Output already exists; choose new --output and --batch-output paths.')
    records=[]
    exclusions=[]
    source_hashes={}
    for split in SPLITS:
        path=source/split/'_annotations.coco.json'
        source_hashes[split]=hashlib.sha256(path.read_bytes()).hexdigest()
        data=json.loads(path.read_text(encoding='utf-8'))
        cats={c['id']:c['name'] for c in data['categories']}
        by_image=defaultdict(list)
        image_ids={i['id'] for i in data['images']}
        if len(image_ids)!=len(data['images']):
            raise ValueError('Duplicate image IDs')
        for a in data['annotations']:
            if a['image_id'] not in image_ids or a['category_id'] not in cats:
                raise ValueError('Annotation references unknown image/category')
            by_image[a['image_id']].append(a)
        for info in data['images']:
            image_path=(source/split/info['file_name']).resolve()
            if not image_path.is_relative_to((source/split).resolve()):
                raise ValueError('Image path escapes dataset split')
            annotations=[dict(a,category_id=CLASSES.index(ALIASES[cats[a['category_id']]]))
                         for a in by_image[info['id']] if cats[a['category_id']] in ALIASES]
            records.append(dict(split=split,source_id=info['id'],path=image_path,
                                source_file=info['file_name'],annotations=annotations,
                                declared_size=(info['width'],info['height'])))
    print(f'Decoding and checking {len(records)} images...',flush=True)
    with ThreadPoolExecutor(max_workers=4) as executor:
        for i,(record,inspection) in enumerate(zip(records,executor.map(inspect_image,records)),1):
            record.update(inspection)
            if i%2000==0:
                print(f'Checked {i}/{len(records)}',flush=True)
    accepted={}
    counters=Counter()
    for r in records:
        reason=None
        if 'error' in r:
            reason='missing_or_corrupt_image'
        elif (r['width'],r['height']) != r['declared_size']:
            reason='image_size_mismatch'
        elif not r['annotations']:
            reason='no_target_annotations_not_verified_background'
        else:
            normalized=[]
            try:
                for a in r['annotations']:
                    box=normalize_box(a['bbox'],r['width'],r['height'])
                    counters['clipped_boxes']+=box!=a['bbox']
                    normalized.append(dict(category_id=a['category_id'],bbox=box,iscrowd=int(a.get('iscrowd',0))))
            except ValueError as error:
                reason=str(error)
            if reason is None:
                r['annotations']=normalized
                if r['pixels'] in accepted:
                    existing=accepted[r['pixels']]
                    # Same decoded pixels: merge labels from both copies, keeping evaluation precedence.
                    existing['annotations'].extend(normalized)
                    reason='exact_pixel_duplicate'
                    if existing['split']!=r['split']:
                        counters['cross_split_duplicates_removed']+=1
                else:
                    accepted[r['pixels']]=r
        if reason:
            exclusions.append(dict(split=r['split'],filename=r['source_file'],reason=reason,
                                   detail=r.get('message','')))
    cleaned=list(accepted.values())
    for r in cleaned:
        unique={}
        for a in r['annotations']:
            key=(a['category_id'],*a['bbox'],a['iscrowd'])
            unique[key]=a
        counters['duplicate_boxes_removed']+=len(r['annotations'])-len(unique)
        r['annotations']=list(unique.values())
        r['labels']={a['category_id'] for a in r['annotations']}
    print(f'Exporting {len(cleaned)} cleaned images as annotation drafts...',flush=True)
    summary=export_dataset(cleaned,output)
    batch=select_batch(cleaned)
    batch_summary=export_dataset(batch,batch_output)
    chosen={r['pixels'] for r in batch}
    with (output/'review_queue.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.writer(file)
        writer.writerow(['split','file_name','source_file','classes','person_missing','selected_for_batch','status'])
        for r in cleaned:
            writer.writerow([r['split'],r['pixels']+'.jpg',r['source_file'],
                             '|'.join(CLASSES[i] for i in sorted(r['labels'])),
                             2 not in r['labels'],r['pixels'] in chosen,'needs_full_class_review'])
    write_json(output/'excluded_files.json',exclusions)
    # Same dHash across splits signals possible near duplicates; do not call it proof.
    hashes=defaultdict(list)
    for r in cleaned:
        hashes[r['dhash']].append({'split':r['split'],'filename':r['pixels']+'.jpg'})
    suspicious=[v for v in hashes.values() if len({r['split'] for r in v})>1]
    write_json(output/'possible_cross_split_near_duplicates.json',suspicious)
    report=dict(source=str(source),source_annotation_sha256=source_hashes,
                original_records=len(records),retained=len(cleaned),
                exclusions=dict(Counter(e['reason'] for e in exclusions)),repairs=dict(counters),
                possible_cross_split_near_duplicate_groups=len(suspicious),
                clean_splits=summary,batch_splits=batch_summary,training_ready=False)
    write_json(output/'preparation_report.json',report)
    for dest in (output,batch_output):
        (dest/'READ_ME_FIRST.txt').write_text(
            'ЧЕРНОВИК РАЗМЕТКИ — НЕ ГОТОВЫЙ ОБУЧАЮЩИЙ ДАТАСЕТ.\n'
            'Удаление дублей не исправляет пропущенные Person и СИЗ.\n'
            'Проверьте все 9 классов на каждом кадре перед обучением.\n'
            'Исходные фотографии сохранены в data/train, data/valid, data/test.\n'
            'Фото подключены жёсткими ссылками, где это возможно: не изменяйте пиксели файлов.\n'
            'JSON-разметка независима; редактировать её можно.\n'
            'Возможные похожие кадры между split перечислены в отчёте полной очищенной копии.\n',encoding='utf-8')
        for name in ('README.dataset.txt','README.roboflow.txt'):
            if (source/name).is_file():
                shutil.copy2(source/name,dest/name)
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=ROOT/'data')
    parser.add_argument('--output',type=Path,default=ROOT/'data/ppe_clean_review')
    parser.add_argument('--batch-output',type=Path,default=ROOT/'data/ppe_annotation_batch')
    args=parser.parse_args()
    prepare(args.source,args.output,args.batch_output)
