"""
download_models.py — предзагрузка локальных моделей в кэш HuggingFace.

Качает (с докачкой .incomplete):
  - intfloat/multilingual-e5-small      (~471 MB, локальные embeddings)
  - cross-encoder/ms-marco-MiniLM-L-6-v2 (~90 MB, реранкинг)

Запуск (фоновый, Windows/git-bash):
    nohup .venv/Scripts/python.exe scripts/download_models.py > data/models_download.log 2>&1 &
    echo $!
"""

from __future__ import annotations

import time

MODELS = [
    "intfloat/multilingual-e5-small",
    "cross-encoder/ms-marco-MiniLM-L-6-v2",
]


def main() -> int:
    from huggingface_hub import snapshot_download

    print(f"Старт скачивания: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    for model in MODELS:
        print(f"\n>>> {model} ...", flush=True)
        path = snapshot_download(model)
        print(f"    готово: {path}", flush=True)
    print(f"\nВсё скачано: {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
