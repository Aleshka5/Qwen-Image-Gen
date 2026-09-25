# Qwen-Image Service

Flask-сервис генерации изображений на базе [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1),
собранный под **Tesla V100 32 GB**: до 10 референсных фото, выбор разрешения и промпт на английском.

---

## Что учтено про V100

V100 — это архитектура Volta (SM 7.0), и она накладывает три жёстких ограничения:

| Ограничение | Как обходим |
|---|---|
| Нет аппаратного **bfloat16** | Веса грузятся и кастуются в `float16`; текстовый энкодер считает в `float32` на CPU, где точность не стоит ничего |
| Нет **FlashAttention-2** (требует SM 8.0+) | `ATTENTION_BACKEND=sdpa` — PyTorch SDPA с ядрами `mem_efficient`/`math`, которые на Volta работают |
| 32 GB VRAM < ~40 GB весов DiT в fp16 | `MEMORY_MODE=int8` — weight-only int8 через `optimum-quanto` (~20 GB), вычисления остаются в fp16 |

Дополнительно текстовый энкодер (Qwen2.5-VL, ~8B) **принудительно живёт в CPU RAM** — это и требование стенда,
и ещё ~16 GB свободной VRAM под трансформер.

Diffusers исходит из того, что все модули пайплайна на одном устройстве, поэтому `text_encoder.to("cpu")`
сломал бы `__call__`. Вместо этого используется прокси [`app/offload.py`](app/offload.py): снаружи он
отвечает `device=cuda`, внутри переносит входы на CPU, считает там и возвращает эмбеддинги обратно на GPU.

### Режимы памяти (`MEMORY_MODE`)

| Режим | VRAM | Скорость | Когда брать |
|---|---|---|---|
| `int8` *(по умолчанию)* | ~22–26 GB | базовая | штатный режим для V100 32 GB |
| `fp16` | ~40 GB+ | быстрее | только если GPU больше 40 GB; на V100 получите OOM |
| `offload` | ~8 GB | в 3–10 раз медленнее | аварийный вариант, если int8 всё равно не влезает |

---

## Требования

* Podman 4.4+ с работающим GPU-проходом (`nvidia-container-toolkit` + CDI)
* NVIDIA-драйвер 525+ (проверено на 580.178.04)
* **~60 GB** свободного диска под веса модели
* **~48 GB** свободной RAM: текстовый энкодер в fp32 плюс буферы загрузки весов

---

## Быстрый старт

### 1. Настроить CDI для GPU (один раз на хосте)

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

Проверка, что Podman видит карту:

```bash
podman run --rm --device nvidia.com/gpu=all docker.io/nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

Для rootless Podman дополнительно нужно `sudo nvidia-ctk config --set nvidia-container-cli.no-cgroups --in-place`.

### 2. Подготовить окружение

```bash
cp .env.example .env
```

В `.env` правится как минимум `MODEL_ID`/`PIPELINE_CLASS` (если нужна другая ревизия) и, для gated-репозиториев, `HF_TOKEN`.

### 3. Собрать образ

```bash
podman build -t qwen-image-service:latest -f Containerfile .
```

### 4. Тома под кеш весов и результаты

Веса скачиваются один раз и должны пережить пересоздание контейнера:

```bash
podman volume create qwen-hf-cache && podman volume create qwen-outputs
```

### 5. Запустить

```bash
podman run -d --name qwen-image --device nvidia.com/gpu=all --security-opt=label=disable --env-file .env -p 8000:8000 -v qwen-hf-cache:/data/huggingface -v qwen-outputs:/data/outputs --shm-size=8g --memory=64g qwen-image-service:latest
```

Первый старт скачивает ~60 GB весов и квантует DiT — это десятки минут. Прогресс видно в логах:

```bash
podman logs -f qwen-image
```

Готовность:

```bash
curl -s localhost:8000/healthz
```

`{"loaded": true}` означает, что модель в памяти и сервис принимает запросы. UI — на `http://localhost:8000/`.

### Systemd-юнит (автозапуск)

```bash
podman generate systemd --name qwen-image --new --files --restart-policy=always
```

```bash
mkdir -p ~/.config/systemd/user && mv container-qwen-image.service ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now container-qwen-image
```

---

## API

### `POST /api/generate` — `multipart/form-data`

| Поле | Тип | По умолчанию | Описание |
|---|---|---|---|
| `prompt` | string | — | Обязательный, только на английском (кириллица отклоняется) |
| `negative_prompt` | string | `" "` | Тоже на английском |
| `images` | file[] | — | До 10 файлов: JPEG/PNG/WEBP/BMP, суммарно до `MAX_UPLOAD_MB` |
| `resolution` | string | `1328x1328` | Ключ пресета или произвольное `ШxВ` (стороны выравниваются до кратных 16) |
| `steps` | int | 30 | 1…`MAX_STEPS` |
| `true_cfg_scale` | float | 4.0 | 1.0…10.0 |
| `seed` | int | `-1` | `-1` — случайный |

```bash
curl -X POST localhost:8000/api/generate -F "prompt=A cinematic portrait of the person, soft rim light, 85mm lens" -F "resolution=1664x928" -F "steps=30" -F "images=@face1.jpg" -F "images=@face2.jpg" -o result.json
```

Ответ:

```json
{
  "seed": 1823486689,
  "duration": 96.4,
  "width": 1664,
  "height": 928,
  "images": [{"name": "20260922-124332-3b47ec64.png", "url": "/outputs/20260922-124332-3b47ec64.png"}]
}
```

Ошибки валидации — `400`, недоступность или OOM пайплайна — `503`, превышение размера загрузки — `413`.

### Остальные эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/` | Веб-форма |
| `GET` | `/healthz` | Статус, режим памяти, ошибка загрузки модели |
| `GET` | `/api/config` | Лимиты и список пресетов разрешения |
| `GET` | `/outputs/<name>` | Готовое изображение (хранятся последние `KEEP_OUTPUTS` штук) |

---

## Структура

```
app/
  config.py      снимок настроек из окружения
  offload.py     прокси текстового энкодера (веса в RAM, интерфейс «как на GPU»)
  generator.py   загрузка пайплайна, режимы памяти, генерация под GPU-локом
  imaging.py     пресеты разрешений, валидация загрузок, сохранение результатов
  routes.py      веб-форма и JSON API
  __init__.py    фабрика приложения, фоновая предзагрузка модели
wsgi.py          точка входа gunicorn
```

Генерация сериализована `threading.Lock`: одна карта — одна задача за раз, параллельные HTTP-запросы ждут
очереди, а не дерутся за VRAM. Поэтому gunicorn запускается с `--workers 1 --threads 4 --timeout 0`.

---

## Диагностика

**`CUDA out of memory`** — уменьшите разрешение или число входных фото; затем `MEMORY_MODE=offload`.
Проверьте, что VRAM не занята посторонним процессом: `nvidia-smi`.

**`Cannot find class QwenImageEditPlusPipeline` / `KeyError` при загрузке** — установленный diffusers старше модели.
Пересоберите образ, заменив в `requirements.txt` строку diffusers на
`git+https://github.com/huggingface/diffusers.git@main`, либо задайте `PIPELINE_CLASS` явно.

**`не принимает изображения на вход`** — выбранный `MODEL_ID` указывает на text-to-image-вариант без
image-условия. Возьмите edit-ревизию модели или уберите загрузку фото.

**Процесс убит OOM-killer'ом при старте** — не хватило RAM под fp32-энкодер. Поднимите `--memory`
или поставьте `TEXT_ENCODER_DTYPE=float16` (чуть меньше точность, вдвое меньше RAM).

**`RuntimeError: "addmm_impl_cpu_" not implemented for 'Half'`** — старый CPU-бэкенд PyTorch не умеет fp16
на CPU. Вернитесь к `TEXT_ENCODER_DTYPE=float32`.

**Медленно, ~2–4 мин на кадр** — это ожидаемо: V100 без bf16 и FlashAttention примерно втрое медленнее
современных карт. Снижайте `steps` (20–25 обычно достаточно) и разрешение.

**Веса качаются каждый запуск** — не подключён том `qwen-hf-cache:/data/huggingface` или `HF_HOME` в `.env`
указывает не туда.
