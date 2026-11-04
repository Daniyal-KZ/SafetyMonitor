"""Fine-tune the existing RF-DETR weights with a wall-clock budget on Windows."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import time

from PIL import Image

from safety_monitor.inference import MODEL_PATH, LABELS_PATH, ROOT, read_labels

TRAIN_CLASSES = ('GLOVES', 'HELMET', 'PERSON', 'PPE GLASSES', 'VEST',
                 'NO HELMET', 'NO PPE GLASSES', 'NO GLOVES')
SOURCE_MAP = {1: 0, 0: 1, 6: 2, 4: 3, 2: 4, 7: 5, 8: 6, 9: 7}
SOURCE_NAMES = ['helmet', 'gloves', 'vest', 'boots', 'goggles', 'none',
                'Person', 'no_helmet', 'no_goggle', 'no_gloves', 'no_boots']
DATASET = ROOT / 'data/rfdetr_ppe8'
COCO_CLASSES = TRAIN_CLASSES + ('NO VEST',)
DEFAULT_COCO_DATASET = ROOT / 'data/ppe_full_annotation_review'


def validate_coco_dataset(folder: Path) -> dict:
    """Validate the explicit nine-class contract without treating draft labels as verified."""
    status_path = folder / 'dataset_status.json'
    status = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
    report = {'classes': list(COCO_CLASSES), 'dataset': str(folder.resolve()),
              'training_ready': status.get('training_ready') is True, 'splits': {}}
    filenames = set()
    for split in ('train', 'valid', 'test'):
        path = folder / split / '_annotations.coco.json'
        data = json.loads(path.read_text(encoding='utf-8'))
        categories = {c['id']: c['name'] for c in data['categories']}
        if categories != dict(enumerate(COCO_CLASSES)):
            raise ValueError(f'{split}: неверный порядок девяти классов COCO.')
        images = {i['id']: i for i in data['images']}
        if not images or len(images) != len(data['images']):
            raise ValueError(f'{split}: пустая выборка или повторные ID изображений.')
        for info in images.values():
            image_path = (folder / split / info['file_name']).resolve()
            if not image_path.is_relative_to((folder / split).resolve()) or not image_path.is_file():
                raise ValueError(f'{split}: отсутствует изображение или некорректен путь: {info["file_name"]}')
            if info['file_name'] in filenames:
                raise ValueError('Повтор изображения между разделами датасета.')
            filenames.add(info['file_name'])
        counts = Counter()
        ids = set()
        for a in data['annotations']:
            if a['id'] in ids or a['image_id'] not in images or a['category_id'] not in categories:
                raise ValueError(f'{split}: некорректные ссылки или ID аннотаций.')
            ids.add(a['id'])
            x, y, w, h = a['bbox']
            im = images[a['image_id']]
            if not all(math.isfinite(v) for v in (x,y,w,h)) or min(x,y) < 0 or min(w,h) <= 0 or x+w > im['width']+.01 or y+h > im['height']+.01:
                raise ValueError(f'{split}: некорректная рамка {a["id"]}.')
            counts[a['category_id']] += 1
        if set(counts) != set(categories):
            raise ValueError(f'{split}: отсутствуют примеры некоторых классов.')
        report['splits'][split] = {'images':len(images), 'annotations':len(ids),
                                   'annotation_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                                   'boxes':{categories[k]:counts[k] for k in categories}}
    return report


def prepare_dataset(source: Path, destination: Path) -> dict:
    import yaml
    if not (source / 'data.yaml').is_file():
        raise FileNotFoundError(
            'Нет исходного data/construction-ppe/data.yaml. Папки ppe_clean_review и '
            'ppe_annotation_batch содержат непроверенную разметку и пока не подключены к обучению.')
    names = yaml.safe_load((source / 'data.yaml').read_text(encoding='utf-8'))['names']
    names = [names[k] for k in sorted(names)] if isinstance(names, dict) else names
    if names != SOURCE_NAMES:
        raise ValueError('Ожидается исходный Construction-PPE с 11 классами.')
    destination.mkdir(parents=True, exist_ok=True)
    categories = [dict(id=i, name=name, supercategory='PPE') for i, name in enumerate(TRAIN_CLASSES)]
    seen = set()
    report = {'classes': list(TRAIN_CLASSES), 'splits': {}}
    for old_split, split in [('train', 'train'), ('val', 'valid'), ('test', 'test')]:
        folder = destination / split
        folder.mkdir(exist_ok=True)
        images, annotations, counts = [], [], Counter()
        duplicates = 0
        for path in sorted((source / 'images' / old_split).iterdir()):
            if path.suffix.lower() not in {'.jpg', '.jpeg', '.png'}:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest in seen:
                duplicates += 1
                continue
            seen.add(digest)
            with Image.open(path) as image:
                width, height = image.size
                image.verify()
            image_id = len(images)
            filename = digest + path.suffix.lower()
            label_file = source / 'labels' / old_split / (path.stem + '.txt')
            for line in label_file.read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                fields = line.split()
                if len(fields) != 5:
                    raise ValueError(f'Некорректная разметка: {label_file.name}')
                old = int(fields[0])
                cx, cy, w, h = map(float, fields[1:])
                if old not in range(11) or not all(math.isfinite(v) for v in (cx, cy, w, h)):
                    raise ValueError(f'Некорректный класс/координаты: {label_file.name}')
                if not (0 <= cx <= 1 and 0 <= cy <= 1 and 0 < w <= 1 and 0 < h <= 1):
                    raise ValueError(f'Координаты вне диапазона: {label_file.name}')
                if old not in SOURCE_MAP:
                    continue
                x1, y1 = max(0, (cx-w/2)*width), max(0, (cy-h/2)*height)
                x2, y2 = min(width, (cx+w/2)*width), min(height, (cy+h/2)*height)
                if x2 <= x1 or y2 <= y1:
                    continue
                label = SOURCE_MAP[old]
                annotations.append(dict(id=len(annotations), image_id=image_id, category_id=label,
                                        bbox=[x1, y1, x2-x1, y2-y1], area=(x2-x1)*(y2-y1), iscrowd=0))
                counts[label] += 1
            target = folder / filename
            if not target.exists():
                try:
                    os.link(path, target)
                except OSError:
                    shutil.copy2(path, target)
            images.append(dict(id=image_id, file_name=filename, width=width, height=height))
        if not images or (split in {'train', 'valid'} and set(counts) != set(range(len(TRAIN_CLASSES)))):
            raise ValueError(f'{split}: нет изображений или разметки всех восьми классов.')
        payload = dict(info={}, licenses=[], images=images, annotations=annotations, categories=categories)
        (folder / '_annotations.coco.json').write_text(json.dumps(payload), encoding='utf-8')
        report['splits'][split] = dict(images=len(images), duplicates_skipped=duplicates,
                                      counts={name: counts[i] for i, name in enumerate(TRAIN_CLASSES)})
    (destination / 'preparation_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def transfer_weights(target_state, source_state, source_labels, classes=TRAIN_CLASSES):
    """Copy the backbone/boxes and transfer class rows BY NAME, not by position."""
    by_name = {name: key for key, name in source_labels.items() if name}
    result = {}
    for key, target in target_state.items():
        if key == '_kp_active_mask':
            # RF-DETR 1.11 adds this deterministic keypoint schema buffer.
            # Detection checkpoints from older versions do not contain it.
            result[key] = target
            continue
        if key not in source_state:
            raise ValueError(f'Несовместимая архитектура: отсутствует {key}')
        source = source_state[key]
        if 'class_embed' in key:
            remapped = target.clone()
            for new_id, name in enumerate(classes):
                old_id = by_name.get(name)
                if old_id is not None:
                    remapped[new_id].copy_(source[old_id])
            # Newly added negative categories retain the model's random initialization.
            result[key] = remapped
        elif source.shape == target.shape:
            result[key] = source
        else:
            raise ValueError(f'Несовместимая форма весов: {key}: {source.shape} != {target.shape}')
    return result


def deploy(best: Path, output: Path, destination: Path | None = MODEL_PATH, classes=TRAIN_CLASSES) -> dict:
    """Validate the candidate before replacing the single deployed model; keep rollback files."""
    import torch
    import numpy as np
    from safety_monitor.inference import RFDETRDetector

    checkpoint = torch.load(best, map_location='cpu', weights_only=True)
    args = checkpoint.get('args', {})
    if args.get('class_names') != list(classes):
        raise ValueError('Веса не содержат ожидаемый порядок классов.')
    if checkpoint['model']['class_embed.weight'].shape[0] != len(classes)+1:
        raise ValueError('Неверное количество выходов модели.')
    # Test using the same inference entry point as the application, including metadata.
    candidate = output / 'weights.pt'
    shutil.copy2(best, candidate)
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    labels = {str(i): name for i, name in enumerate(classes)}
    labels[str(len(classes))] = None
    manifest = {'sha256': digest, 'source': 'Local RF-DETR training; zero-based COCO label order', 'labels': labels}
    candidate_labels = candidate.with_suffix('.labels.json')
    candidate_labels.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    detector = RFDETRDetector(candidate)
    result = detector.predict(np.zeros((512, 512, 3), dtype=np.uint8))
    assert detector.labels_verified and set(detector.supported_rules) == {'helmet', 'vest', 'gloves', 'glasses'}
    assert result.xyxy.shape[1] == 4
    del detector, checkpoint
    torch.cuda.empty_cache()
    if destination is None:
        return {'weights': str(candidate), 'labels': str(candidate_labels), 'sha256': digest}
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_destination = destination.with_suffix('.labels.json')
    backup = output / 'previous'
    backup.mkdir(exist_ok=True)
    for path in (destination, manifest_destination):
        if path.exists():
            shutil.copy2(path, backup / path.name)
    tmp_weights = destination.with_suffix('.pt.tmp')
    tmp_labels = manifest_destination.with_suffix('.json.tmp')
    try:
        shutil.copy2(candidate, tmp_weights)
        shutil.copy2(candidate_labels, tmp_labels)
        os.replace(tmp_weights, destination)
        os.replace(tmp_labels, manifest_destination)
    except Exception:
        # Restore the pair if a filesystem error happens between its two replaces.
        for path in (destination, manifest_destination):
            if (backup / path.name).exists():
                shutil.copy2(backup / path.name, path)
        raise
    finally:
        tmp_weights.unlink(missing_ok=True)
        tmp_labels.unlink(missing_ok=True)
    return {'weights': str(destination), 'sha256': digest, 'backup': str(backup)}


def train(minutes: float, *, smoke: bool = False, deploy_result: bool = False,
          dataset: Path = DEFAULT_COCO_DATASET) -> Path:
    import torch
    from pytorch_lightning import seed_everything
    from rfdetr.config import RFDETRSmallConfig, TrainConfig
    from rfdetr.training import RFDETRModelModule, RFDETRDataModule, build_trainer
    from filelock import FileLock

    if not torch.cuda.is_available():
        raise RuntimeError('Для этого профиля нужна NVIDIA CUDA и подходящая CUDA-сборка PyTorch. См. раздел обучения в README.md.')
    with FileLock(str(ROOT / 'weights/training.lock'), timeout=0):
        dataset = dataset.resolve()
        report = validate_coco_dataset(dataset)
        classes = tuple(report['classes'])
        if not report['training_ready']:
            print('ПРОБНОЕ ОБУЧЕНИЕ: частичная авторазметка. Метрики не являются независимой оценкой качества. Рабочие веса не заменяются.', flush=True)
            deploy_result = False
        output = ROOT / 'runs/rfdetr' / (datetime.now().strftime('%Y%m%d-%H%M%S') + ('-smoke' if smoke else f'-{minutes:g}min'))
        output.mkdir(parents=True, exist_ok=False)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        seed_everything(42, workers=True)
        (output / 'dataset_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        mc = RFDETRSmallConfig(num_classes=len(classes), pretrain_weights=None,
                               device='cuda', resolution=512, gradient_checkpointing=True)
        tc = TrainConfig(dataset_dir=str(dataset), dataset_file='roboflow', output_dir=str(output),
                         class_names=list(classes), epochs=1 if smoke else 1000,
                         batch_size=1, grad_accum_steps=8, eval_batch_size=1, num_workers=0,
                         persistent_workers=False, pin_memory=True,
                         lr=1e-4, lr_encoder=1e-5, warmup_epochs=0.5,
                         lr_scheduler='step', lr_scheduler_kwargs={'lr_drop': 30},
                         multi_scale=False, expanded_scales=False, amp_dtype='fp16',
                         use_ema=True, early_stopping=False, tensorboard=False, wandb=False,
                         run_test=False, checkpoint_interval=1000, eval_interval=1,
                         eval_max_dets=100, eval_backend='faster_coco_eval', log_per_class_metrics=True,
                         progress_bar='tqdm', seed=42)
        module = RFDETRModelModule(mc, tc)
        original = torch.load(MODEL_PATH, map_location='cpu', weights_only=True)
        digest = hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()
        labels = read_labels(LABELS_PATH, digest, original['model']['class_embed.weight'].shape[0])
        if not labels or 'PERSON' not in labels.values():
            raise ValueError('Нужна проверенная карта классов исходного weights.pt.')
        module.model.load_state_dict(transfer_weights(module.model.state_dict(), original['model'], labels, classes), strict=True)
        print('Исходный weights.pt загружен: backbone и классы перенесены по названиям.', flush=True)
        del original
        dm = RFDETRDataModule(mc, tc)
        kwargs = {'max_time': {'seconds': max(1, int(minutes*60))},
                  'accelerator': 'gpu', 'devices': 1, 'num_sanity_val_steps': 0,
                  'log_every_n_steps': 1 if smoke else 20, 'enable_model_summary': False}
        if smoke:
            kwargs.update(limit_train_batches=8, limit_val_batches=4)
        trainer = build_trainer(tc, mc, **kwargs)
        config = {'minutes': minutes, 'smoke': smoke, 'source_sha256': digest,
                  'classes': list(classes), 'model': mc.model_dump(mode='json'),
                  'training': tc.model_dump(mode='json')}
        (output / 'run_config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'Обучение: максимум {minutes:g} минут + завершение проверки/сохранения. Папка: {output}', flush=True)
        started = time.monotonic()
        trainer.fit(module, datamodule=dm)
        trainer.save_checkpoint(output / 'last.ckpt')
        metrics = {str(k): float(v) for k, v in trainer.callback_metrics.items() if getattr(v, 'numel', lambda: 0)() == 1}
        result = dict(seconds=time.monotonic()-started, optimizer_steps=trainer.global_step,
                      epoch_counter=trainer.current_epoch, metrics=metrics, deployed=False)
        best = output / 'checkpoint_best_total.pth'
        if not best.is_file():
            raise RuntimeError(f'Лучшие веса не получены. Исходная модель сохранена. См. {output}')
        del trainer, module, dm
        import gc
        gc.collect()
        torch.cuda.empty_cache()
        result['export'] = deploy(best, output, destination=None, classes=classes)
        if not smoke and deploy_result:
            score = metrics.get('val/mAP_50_95', float('nan'))
            if result['optimizer_steps'] == 0 or not math.isfinite(score) or score <= 0:
                print('Нет шагов обучения или положительного validation mAP. Исходные веса сохранены.', flush=True)
            else:
                result['deployment'] = deploy(best, output, classes=classes)
                result['deployed'] = True
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        print('Готово. ' + ('Запустите run.cmd: обновлён weights.pt.' if result['deployed'] else
                           f'Новая модель: {output / "weights.pt"}. Рабочий weights.pt не изменён.'), flush=True)
        return output


def main():
    parser = argparse.ArgumentParser(description='RF-DETR weights.pt: СИЗ и NO HELMET, обучение на 90 минут')
    parser.add_argument('--minutes', type=float, default=90)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--smoke', action='store_true', help='8 тренировочных батчей без замены рабочих весов')
    parser.add_argument('--no-deploy', action='store_true')
    parser.add_argument('--deploy', action='store_true', help='Автозамена разрешена только для проверенного датасета')
    parser.add_argument('--dataset', type=Path, default=DEFAULT_COCO_DATASET)
    args = parser.parse_args()
    if not math.isfinite(args.minutes) or args.minutes <= 0:
        parser.error('--minutes должен быть положительным числом')
    if args.prepare_only:
        print(json.dumps(validate_coco_dataset(args.dataset), indent=2))
    else:
        train(args.minutes, smoke=args.smoke, deploy_result=args.deploy and not args.no_deploy, dataset=args.dataset)


if __name__ == '__main__':
    main()
