# Qwen-Image Service

Flask-сервис генерации изображений на базе [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1),
собранный под **Tesla V100 32 GB**: до 10 референсных фото, выбор разрешения и промпт на английском.

---

## Сеть и доступ

Браузеры открывают сервис по адресу **https://image.filenkov.store** — это Auth Gateway, а не этот процесс.
Контейнер `qwen-image` доступен только в podman-сети `qwen_image_gen_network`: gateway проксирует на upstream `http://qwen-image:8000`.
Сеть создаёт этот репозиторий; Auth-Service подключается к ней из своего compose как `external: true`.

Пускать или нет — решает gateway: роль `image` (`FAMILY` или `ADMIN` в User-Service).
Входящие от клиента `X-Auth-*` gateway вырезает. В самом сервисе нет логина, сессий, проверки ролей и проверки JWT.
Исключение — `POST /api/generate`: `X-Auth-User-Id` и `X-Auth-Email` читаются только чтобы подписать сохранение в WebStorage.
Маршруты `GET` эти заголовки не используют. Запрос, дошедший сюда, уже разрешён.
Поэтому граница доверия — сама сеть: в `qwen_image_gen_network` должны быть только gateway и `qwen-image`.
WebStorage в `qwen_image_gen_network` не входит. Любой другой контейнер в этой сети обходит проверку роли.

Чтобы после удачной генерации вызвать WebStorage, этот контейнер дополнительно подключается к сети Auth.
`AUTH_DOCKER_NETWORK` — сеть стека Auth (`deploy_auth_network`, если Auth запущен из `Auth-Service/deploy`).
Её этот репозиторий не создаёт: стек Auth уже должен быть поднят.
На этой сети контейнер остаётся с именем `qwen-image` и ходит на DNS-имя `app` — так там называется WebStorage.
Переименовывать этот контейнер в `app` нельзя.

После успешной генерации процесс делает `POST` на `WEBSTORAGE_URL` (по умолчанию `http://app:8000`) по пути `/api/generated`.
Он передаёт `X-Auth-User-Id` и, если заголовок был во входящем запросе, `X-Auth-Email`.
`Cookie`, `Authorization` и `X-Auth-Role` не отправляются. JWT этот сервис не проверяет.
В `.env` для адреса архива задаётся `WEBSTORAGE_URL=http://app:8000`.

`GET /healthz` в приложении анонимный. Gateway отдаёт этот путь без роли — для проб оператора, всё остальное требует роль `image`.

> **Никогда не добавляйте `-p 8000:8000`.** Это выставит GPU-UI в сеть хоста рядом с gateway и позволит обойти его.

---

## Что учтено про V100

V100 — это архитектура Volta (SM 7.0), и она накладывает три жёстких ограничения:

| Ограничение | Как обходим |
|---|---|
| Нет аппаратного **bfloat16** | Веса грузятся в `float16`; хвост текстового энкодера в RAM тоже `float16` |
| Нет **FlashAttention-2** (требует SM 8.0+) | `ATTENTION_BACKEND=native` — PyTorch SDPA (`mem_efficient` / `math`). Имя `sdpa` принимается как алиас |
| Весь пайплайн в fp16 ≈ **33 GB** (DiT 7B ~14 GB, Qwen3-VL 8B ~17.5 GB, VAE ~1.4 GB) | Энкодер целиком на карту не ставится. `int8` сжимает DiT до ~7 GB; vision-башня и 24 слоя декодера остаются в VRAM, хвост энкодера — в RAM в `float16` |

Qwen-Image-2.1 — это 7B single-stream DiT и текстовый энкодер **Qwen3-VL 8B**.
Энкодер **целиком на карту не ставится**: вместе с DiT и VAE он занимает около 33 GB. Vision-башня и первые слои декодера живут в VRAM (референсы идут через них), хвост — в RAM.

Diffusers исходит из того, что все модули пайплайна на одном устройстве, поэтому `text_encoder.to("cpu")`
сломал бы `__call__`. Вместо этого используется прокси [`app/offload.py`](app/offload.py): снаружи он
отвечает `device=cuda`, внутри считает на CPU (кроме vision и GPU-слоёв) и возвращает эмбеддинги на GPU.

### Режимы памяти (`MEMORY_MODE`)

| Режим | VRAM | Скорость | Когда брать |
|---|---|---|---|
| `int8` *(по умолчанию)* | ~20 GB весов (DiT int8 + VAE + 24 слоя энкодера) + KV-кэш | базовая | штатный режим для референсов: DiT ~7 GB, остаток VRAM — энкодер и KV. Каждый референс — около 2 GB KV в fp16 |
| `fp16` | ~16 GB один DiT, энкодер почти весь в RAM | быстрее | text-to-image и 1–2 фото. Десять референсов в этот бюджет не входят; RAM при этом забивается раньше VRAM |
| `offload` | ~8 GB | в 3–10 раз медленнее | если и int8 упирается в VRAM |

Вычисления и в `int8` остаются fp16: у V100 тензорные ядра именно такие. Квантизация экономит память весов, а не ускоряет matmul.
`true_cfg_scale` выше 1 хранит второй KV-кэш и на десяти референсах переполняет 32 GB в любом режиме.

---

## Требования

* Podman 4.4+ с работающим GPU-проходом (`nvidia-container-toolkit` + CDI)
* NVIDIA-драйвер 560+ (проверено на 580.178.04)
* **~40 GB** свободного диска под веса (сами файлы около 33 GB)
* **~40 GB** свободной RAM: хвост Qwen3-VL в fp16 плюс буфер на время загрузки. 64 GB — запас, если вернётесь к `TEXT_ENCODER_DTYPE=float32`

---

## Быстрый старт

### 1. Настроить CDI для GPU (один раз на хосте)

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

Проверка, что Podman видит карту:

```bash
podman run --rm --device nvidia.com/gpu=all docker.io/nvidia/cuda:12.6.3-base-ubuntu22.04 nvidia-smi
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

Сборка многостадийная: `base` (CUDA + Python) → `ml` (torch 2.7.1+cu126, diffusers, transformers,
optimum-quanto — из `requirements-ml.txt`) → `app` (Flask/gunicorn из `requirements.txt` и код).
Правка кода или веб-зависимостей пересобирает только `app`; стадия `ml` берётся из кеша слоёв.
Не используйте `--no-cache` без нужды — он заставит заново ставить весь ML-стек (сами колёса
при этом возьмутся из pip-кеша сборки, не из сети).

Проверить только ML-стек, не собирая сервис:

```bash
podman build --target ml -t qwen-image-ml -f Containerfile .
```

### 4. Тома под кеш весов и результаты, сеть

Веса скачиваются один раз и должны пережить пересоздание контейнера:

```bash
podman volume create qwen-hf-cache && podman volume create qwen-outputs
```

Сеть, через которую к сервису ходит Auth Gateway (один раз):

```bash
podman network create qwen_image_gen_network
```

Сеть Auth (`AUTH_DOCKER_NETWORK`, при запуске из `Auth-Service/deploy` это `deploy_auth_network`) здесь не создаётся.
Перед запуском стек Auth уже должен быть поднят: на этой сети WebStorage отвечает как `app`.

### 5. Запустить

Порт 8000 на хост не публикуется — см. [«Сеть и доступ»](#сеть-и-доступ).
Контейнер подключается к обеим сетям: к `qwen_image_gen_network` и к сети Auth.

```bash
podman run -d --name qwen-image \
  --network qwen_image_gen_network \
  --network "${AUTH_DOCKER_NETWORK:-deploy_auth_network}" \
  --device nvidia.com/gpu=all \
  --security-opt=label=disable \
  --env-file .env \
  -v qwen-hf-cache:/data/huggingface \
  -v qwen-outputs:/data/outputs \
  --shm-size=8g --memory=64g \
  qwen-image-service:latest
```

Первый старт скачивает ~33 GB весов и квантует DiT — это десятки минут. Прогресс видно в логах:

```bash
podman logs -f qwen-image
```

Готовность — изнутри сети или изнутри контейнера (в образе есть `curl`):

```bash
podman run --rm --network qwen_image_gen_network docker.io/curlimages/curl -s http://qwen-image:8000/healthz
```

```bash
podman exec qwen-image curl -fsS http://127.0.0.1:8000/healthz
```

`{"loaded": true}` означает, что модель в памяти и сервис принимает запросы. UI — на `https://image.filenkov.store/` (через gateway).

С хоста на публичном `:8000` ничего не слушает. Обе команды должны вывести пустоту:

```bash
podman port qwen-image; ss -ltn | grep :8000
```

### Systemd-юнит (автозапуск)

Файл `deploy/qwen-image.container` — Quadlet. При каждом старте, в том числе после перезагрузки, он поднимает контейнер сразу в двух сетях: `qwen_image_gen_network` и `deploy_auth_network`. Обе сети должны существовать до старта юнита. Первую создаёт шаг 4. Вторую создаёт стек Auth; этот репозиторий её не создаёт.

`podman generate systemd --new` не используется: он повторяет исходный `podman run` и теряет вторую сеть, если контейнер когда-то создали только с `qwen_image_gen_network`.

```bash
mkdir -p ~/.config/containers/systemd
cp deploy/qwen-image.container ~/.config/containers/systemd/
systemctl --user daemon-reload
```

Quadlet сам попадает в `default.target` (`WantedBy`). Отдельный `systemctl enable` для такого юнита не нужен. `daemon-reload` не пересоздаёт уже запущенный контейнер. Следующий старт (загрузка машины или `systemctl --user start qwen-image.service`) поднимает его в обеих сетях. `start` сейчас остановит текущий процесс и заново загрузит модель.

---

## API

### `POST /api/generate` — `multipart/form-data`

| Поле | Тип | По умолчанию | Описание |
|---|---|---|---|
| `prompt` | string | — | Обязательный, только на английском (кириллица отклоняется) |
| `negative_prompt` | string | `" "` | Тоже на английском |
| `images` | file[] | — | До 10 файлов: JPEG/PNG/WEBP/BMP, суммарно до `MAX_UPLOAD_MB` |
| `size_mode` | string | — | `preset` или `custom`. Без поля работает старый `resolution` |
| `quality` | string | `high` | При `size_mode=preset`: `high` (~2K) или `medium` (те же пропорции, около 1024²) |
| `aspect` | string | `1:1` | При `size_mode=preset`: `1:1`, `4:3`, `3:4`, `3:2`, `2:3`, `16:9`, `9:16` |
| `width`, `height` | int | — | При `size_mode=custom`: каждая сторона 32…3000 px, затем вниз до кратной 32 |
| `resolution` | string | `2048x2048` | Старый вход: ключ пресета или `ШxВ` в тех же пределах 32…3000 |
| `steps` | int | 40 | 1…`MAX_STEPS` |
| `true_cfg_scale` | float | 1.0 | 1.0…10.0. `1.0` — без guidance, штатный режим 2.1 |
| `seed` | int | `-1` | `-1` — случайный |

Снаружи запросы идут на `https://image.filenkov.store/api/generate` и требуют сессию gateway с ролью `image`.
Для отладки оператором — изнутри сети, напрямую на `http://qwen-image:8000`:

```bash
podman run --rm --network qwen_image_gen_network --security-opt=label=disable -v "$PWD":/work:ro -w /work docker.io/curlimages/curl -X POST http://qwen-image:8000/api/generate -F "prompt=A cinematic portrait of the person, soft rim light, 85mm lens" -F "resolution=2048x2048" -F "steps=40" -F "true_cfg_scale=1" -F "images=@face1.jpg" -F "images=@face2.jpg" > result.json
```

Ответ после удачного сохранения в WebStorage:

```json
{
  "seed": 1823486689,
  "duration": 96.4,
  "width": 2048,
  "height": 2048,
  "images": [{"name": "20260922-124332-3b47ec64.png", "url": "/outputs/20260922-124332-3b47ec64.png"}],
  "saved": true,
  "storage_id": "20260927T115012Z-3b47ec64"
}
```

Если `X-Auth-User-Id` не UUID, или WebStorage ответил ошибкой, оборвался по таймауту или недоступен, генерация всё равно `200`: `saved` равен `false`, в `storage_error` текст ошибки, `storage_id` нет, а `images` по-прежнему указывают на локальный PNG.

Ошибки валидации — `400`, недоступность или OOM пайплайна — `503`, превышение размера загрузки — `413`. В этих случаях WebStorage не вызывается.

### `POST /api/customize` — `multipart/form-data`

Правка одного фото. В форме нет разрешения: размер берётся из файла. Ответ — тот же HxW.

| Поле | Тип | По умолчанию | Описание |
|---|---|---|---|
| `system_prompt` | string | — | Обязательный, только на английском. Что поправить на фото |
| `negative_prompt` | string | `""` | Тоже на английском |
| `image` | file | — | Ровно один файл: JPEG/PNG/WEBP/BMP |
| `steps` | int | 40 | 1…`MAX_STEPS` |
| `true_cfg_scale` | float | 1.0 | 1.0…10.0 |
| `seed` | int | `-1` | `-1` — случайный |

Подгонка под кратность 32 и лимит стороны, и возврат результата к исходному HxW, спрятаны в `PhotoFrame` (`app/imaging.py`). В JSON `width` и `height` — размер загруженного фото, не промежуточный кадр модели.

### Остальные эндпоинты

| Метод | Путь | Назначение |
|---|---|---|
| `GET` | `/` | Веб-форма генерации |
| `GET` | `/customize` | Веб-форма кастомизации одного фото |
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
tests/           pytest-набор, работает без GPU и ML-зависимостей
wsgi.py          точка входа gunicorn
requirements-dev.txt  зависимости для тестов
```

Генерация сериализована `threading.Lock`: одна карта — одна задача за раз, параллельные HTTP-запросы ждут
очереди, а не дерутся за VRAM. Поэтому gunicorn запускается с `--workers 1 --threads 4 --timeout 0`.

---

## Тесты

Тесты подменяют генератор заглушкой: torch, diffusers и GPU не нужны.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

```bash
.venv/bin/pytest
```

Смоук-тест сети podman запускается только явно. Нужен локально собранный образ `qwen-image-service:latest`, GPU не нужен:

```bash
RUN_PODMAN_TESTS=1 .venv/bin/pytest -m podman
```

---

## Диагностика

**`CUDA out of memory`** — уберите лишние референсы, опустите разрешение до `1024x1024` и оставьте `true_cfg_scale=1`.
Десять фото при guidance выше 1 держат два KV-кэша и не влезают в 32 GB. Затем `MEMORY_MODE=offload`.
Проверьте, что VRAM не занята посторонним процессом: `nvidia-smi`.

**`module diffusers has no attribute QwenImage…Pipeline` / `Cannot find class …` / `KeyError` при загрузке** — установленный diffusers старше модели.
В `requirements-ml.txt` закрепите diffusers на свежий коммит main:
`diffusers @ https://github.com/huggingface/diffusers/archive/<sha>.tar.gz` (git в образе не нужен)
и пересоберите образ. Так уже сделано для `QwenImage21Pipeline` (Qwen-Image-2.1).

**`не принимает изображения на вход`** — выбранный `MODEL_ID` указывает на text-to-image-вариант без
image-условия. Возьмите edit-ревизию модели или уберите загрузку фото.

**Процесс убит OOM-killer'ом при старте или на референсах** — энкодер ещё в RAM. Проверьте
`TEXT_ENCODER_DTYPE=float16` и `TEXT_ENCODER_GPU_LAYERS` (24 по умолчанию). `MEMORY_MODE=fp16`
оставляет DiT на 14 GB VRAM и почти весь энкодер в RAM — на референсах так и упираетесь в RAM.
Не хватило лимита контейнера — поднимите `--memory`.

**`RuntimeError: "addmm_impl_cpu_" not implemented for 'Half'`** — старый CPU-бэкенд PyTorch не умеет fp16
на CPU. Вернитесь к `TEXT_ENCODER_DTYPE=float32`.

**Медленно, несколько минут на кадр** — ожидаемо для 2048 и 40 шагов на V100 без FlashAttention.
`true_cfg_scale` выше 1 удваивает проход трансформера; для 2.1 оставляйте 1. Разрешение `1024x1024` заметно быстрее.

**Веса качаются каждый запуск** — не подключён том `qwen-hf-cache:/data/huggingface` или `HF_HOME` в `.env`
указывает не туда.
