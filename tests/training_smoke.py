"""Opt-in real CUDA forward/backward/validation; never deploys test weights."""
from pathlib import Path
import json
import time

from models import ROOT, training_options
from safety_monitor.classes import CLASS_NAMES


def main():
    import torch
    from ultralytics import YOLO
    import yaml

    directory = ROOT / ".artifacts/training-smoke"
    directory.mkdir(parents=True, exist_ok=True)
    dataset = ROOT / "data/ppe5"
    config = {"path": str(dataset), "names": dict(enumerate(CLASS_NAMES))}
    for split in ("train", "val"):
        lines = (dataset / f"{split}.txt").read_text().splitlines()[:4]
        manifest = directory / f"{split}.txt"
        manifest.write_text("\n".join((dataset / line).resolve().as_posix() for line in lines), encoding="utf-8")
        config[split] = str(manifest)
    data = directory / "data.yaml"
    data.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    options = training_options("rtx3050", epochs=1, close_mosaic=0, plots=False)
    options.pop("model")
    start = time.monotonic()
    torch.cuda.reset_peak_memory_stats()
    detector = YOLO(str(ROOT / "weights/yolo26n.pt"))
    detector.train(data=str(data), device="0", project=str(directory), name="check", exist_ok=True, **options)
    best = Path(detector.trainer.best)
    loaded = YOLO(str(best))
    assert tuple(loaded.names.values()) == CLASS_NAMES
    image = (dataset / (dataset / "val.txt").read_text().splitlines()[0]).resolve()
    loaded.predict(str(image), imgsz=800, device="0", quantize=16, verbose=False)
    result = {"status": "passed", "images_train": 4, "images_val": 4, "epochs": 1,
              "device": torch.cuda.get_device_name(), "imgsz": 800, "batch": options["batch"], "workers": options["workers"],
              "peak_allocated_mb": round(torch.cuda.max_memory_allocated()/1024**2),
              "seconds": round(time.monotonic()-start, 1), "classes": CLASS_NAMES,
              "note": "Infrastructure check only; these weights are NOT deployed or accuracy evaluated."}
    (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
