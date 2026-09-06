# Mine Video

Сервис для пакетного производства роликов **из записи настоящего Minecraft Java**. Сервер ставит сцену, графический клиент её отрисовывает, OBS записывает окно игры, FFmpeg выпускает MP4 и обложку.

Это первая версия для одной записывающей машины. Встроены три параметрических шаблона; произвольный текстовый запрос пока не превращается в новый игровой сюжет. YouTube-публикация не подключена.

## Что реализовано

- REST API, CLI, SQLite-очередь, пакетная постановка заданий, история состояний.
- Автозапуск Paper через Docker Compose, клиента через Prism и OBS через командную строку после первоначальной настройки.
- Возврат запущенного клиента на сервер после отключения через защищённый endpoint Fabric-мода.
- Отдельное измерение `minevideo:studio`, построение арены и управление событиями по тикам сервера.
- Фиксированная камера с проверкой положения, имени аккаунта, сервера, измерения, отсутствия меню и свежего отрисованного кадра.
- OBS WebSocket 5: проверка источника, pre-roll, start/stop, сохранение исходника. Проверки чёрного/пустого и полностью застывшего источника.
- MP4 H.264/AAC, 16:9 и 9:16, субтитры EN/RU, JPEG-обложка из игры, JSON с названием, исходом и SHA-256 файлов.
- Локальный `agent`, который обслуживает очередь на машине с Minecraft/OBS, и настоящий `smoke-test` через Minecraft → OBS → FFmpeg.
- Диагностический bundle с состоянием Minecraft/Fabric/OBS, screenshot OBS и логами; секреты `.env` в bundle не попадают.
- Отмена, ограничение времени, последовательный доступ к записывающей машине, восстановление после прерывания.

| Шаблон | Содержание | Параметры |
| --- | --- | --- |
| `mob_arena` | Железный голем против кадавров, настоящий AI Minecraft | 2–24 моба, seed, лимит времени |
| `tnt_chain` | Цепная реакция TNT и второй залп | seed меняет материал построек; сцена 15–16 секунд |
| `tower_build` | Сборка башни по слоям и финальный показ | seed выбирает палитру; 15–180 секунд |

`seed` воспроизводит расстановку и параметры сцены. **Он не гарантирует идентичный бой**: физика и AI Minecraft зависят от состояния сервера. Победитель определяется по живым мобам; при истечении времени результат — «победитель не определён».

## Готовые компоненты

| Компонент | Роль |
| --- | --- |
| [Paper + itzg Docker image](https://docker-minecraft-server.readthedocs.io/en/latest/types-and-platforms/server-types/paper/) | Готовый Minecraft-сервер, загрузка Paper и RCON |
| [Prism Launcher](https://prismlauncher.org/wiki/getting-started/command-line-interface/) | Установка и запуск настоящего клиента, Microsoft-аккаунт, вход на сервер |
| [Fabric Loader](https://fabricmc.net/2024/12/02/1214.html) | Загрузка небольшого capture-guard; Fabric API не нужен |
| [OBS Studio](https://obsproject.com/kb/remote-control-guide) + [obsws-python](https://github.com/aatikturk/obsws-python) | Захват окна и дистанционное управление записью |
| [MCRcon](https://pypi.org/project/mcrcon/) | Готовый RCON-клиент |
| [FFmpeg](https://ffmpeg.org/ffmpeg.html) | Монтаж, кодирование, субтитры, обложка |
| FastAPI / Uvicorn / SQLite | API и локальная очередь |

Собственный код связывает готовые программы. Платные MythicMobs, WorldEdit, ReplayMod и BBS для встроенных сцен не требуются. `client-guard` не заменяет игровой движок и не синтезирует видео: он скрывает HUD, сообщает о реальной отрисовке и управляет переподключением.

Контракт версии: **Minecraft 1.21.4, Paper build 232, datapack format 61, Fabric Loader 0.16.10, Java 21**. Обновлять Minecraft отдельно от datapack и мода нельзя. Формат datapack закреплён по [релизу Mojang](https://www.minecraft.net/en-us/article/minecraft-java-edition-1-21-4).

## Установка на записывающей машине

Нужны Python 3.11+, Docker Compose, Minecraft Java с лицензией, Prism Launcher, Java 21, OBS 28+ и FFmpeg с libass/libx264. Клиент, OBS и Python-agent запускаются в одной графической пользовательской сессии; только Paper работает в Docker. Обычный сервер без графического клиента не может записывать экран Minecraft.

1. Склонировать проект и установить Python-пакет:

   ```bash
   git clone https://github.com/Hosttik/mine_video.git
   cd mine_video
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -e '.[dev]'
   mine-video init --player YOUR_MINECRAFT_NICKNAME
   ```

   В Windows: `py -3.12 -m venv .venv`, затем `.venv\Scripts\Activate.ps1`. В `mine-video.toml` указать полные пути к `prismlauncher.exe`, `obs64.exe`, `ffmpeg.exe` и `ffprobe.exe`, если они не в PATH. Для macOS `init` подставляет стандартные пути приложений.

2. Открыть локальный `.env`. Прочитать [Minecraft EULA](https://aka.ms/MinecraftEULA) и установить `EULA=TRUE`, если принимаешь условия. Пароли уже сгенерированы. Используется online-mode и whitelist с указанным аккаунтом. Не направлять этот сервис на обычный игровой сервер: студия рассчитана на выделенный сервер.

3. Получить capture-guard из артефакта `minevideo-capture` в [GitHub Actions](https://github.com/Hosttik/mine_video/actions/workflows/ci.yml) или собрать его с Java 21 и Gradle 8.12:

   ```bash
   gradle --no-daemon -p client-guard build
   mine-video client-pack --guard-jar client-guard/build/libs/minevideo-capture-0.1.0.jar
   ```

   Импортировать `data/MineVideo-client.zip` в Prism, сохранить имя instance **MineVideo**, войти в лицензированный Microsoft-аккаунт. Настроить Java 21 и один раз запустить instance, чтобы Prism скачал игру. Пакет содержит локальный guard-token — его нельзя публиковать.

4. Один раз настроить OBS по [инструкции](docs/OBS.md): профиль, коллекция и сцена `MineVideo`; источник окна игры `Minecraft`; запись MKV с игровым звуком; WebSocket-пароль из `MV_OBS_PASSWORD`. В Recording Path указать **абсолютный путь** к папке `recordings` этого проекта. В macOS разрешить захват экрана и звука. Обе программы должны оставаться в активной графической сессии.

5. Проверить подключения:

   ```bash
   mine-video doctor --launch
   ```

6. Для постоянной записывающей машины запустить локальный агент:

   ```bash
   mine-video agent
   ```

`agent` — понятное имя существующего локального worker-механизма; он последовательно забирает jobs из SQLite-очереди и управляет Paper, Prism/Minecraft и OBS. Старое имя `mine-video worker` оставлено как совместимый алиас.

Если нужен API и агент одним процессом управления, можно по-прежнему использовать:

```bash
mine-video start
```

`start` поднимает API и дочерний `agent`. Paper, Prism и OBS запускаются при первом задании, если ещё не работают. `doctor --launch` делает это заранее. В браузере API-документация доступна по `http://127.0.0.1:8000/docs`; в Authorize вставить `MV_API_TOKEN` из `.env`.

## Первый настоящий E2E-прогон

После первоначальной настройки выполнить:

```bash
mine-video smoke-test
```

Команда проверяет **не preview и не мок**. Она ставит короткий `tnt_chain` job и проводит его через настоящий конвейер:

```text
Paper → Prism/Minecraft → Fabric capture guard → OBS → raw recording → FFmpeg → landscape.mp4
```

Если `agent` уже запущен, smoke job заберёт он. Если агента нет, `smoke-test` временно сам запускает worker на один job. Студия перед smoke-test должна быть без других активных jobs.

При успехе команда печатает `job_id`, путь к `landscape.mp4` и путь к `diagnostics.zip`. При ошибке она всё равно старается сформировать `diagnostics.zip`. Его можно приложить к баг-репорту или передать для разбора; `.env`, RCON/OBS/API/guard токены туда намеренно не включаются.

Для первого прогона полезно видеть окно Minecraft и предпросмотр OBS: автоматические проверки выявляют технический сбой, но не оценивают привлекательность кадра, композицию камеры или качество звукового микса.

## Создание видео

Во втором терминале с активированным окружением:

```bash
mine-video submit --template mob_arena --mobs 12 --seed 42 --duration 40 --language ru
mine-video status
mine-video wait JOB_ID
```

Пакет из десяти сцен с последовательными seeds:

```bash
mine-video submit --template tower_build --seed 100 --duration 30 --count 10
mine-video submit --template tnt_chain --format short --seed 8
mine-video cancel JOB_ID
```

Посмотреть план без запуска игры и записи:

```bash
mine-video preview --template mob_arena --seed 42
```

REST API:

```http
POST /jobs
Authorization: Bearer YOUR_MV_API_TOKEN
Idempotency-Key: episode-001
Content-Type: application/json

{
  "template": "mob_arena",
  "seed": 42,
  "duration_seconds": 40,
  "mob_count": 12,
  "formats": ["landscape", "short"],
  "language": "ru"
}
```

| Метод | Endpoint | Назначение |
| --- | --- | --- |
| GET | `/health` | Доступность API и свежесть agent heartbeat |
| GET | `/templates` | Доступные сцены |
| POST / GET | `/jobs` | Создать задание / список |
| GET | `/jobs/{id}` | Статус и ссылки на результат |
| GET | `/jobs/{id}/events` | История выполнения |
| POST | `/jobs/{id}/cancel` | Отменить задание |
| POST | `/jobs/{id}/retry` | Новое задание с параметрами неудачного запуска |
| GET | `/jobs/{id}/artifacts/{name}` | Скачать готовый артефакт; diagnostics доступен и у failed jobs |

Все endpoints с заданиями требуют Bearer-токен. `Idempotency-Key` повторно возвращает то же задание; другой payload с тем же ключом получает 409. Клиент API не может передавать shell-команды, команды Minecraft или пути файлов.

## Результат

Каждый запуск хранится в `data/jobs/JOB_ID/`:

- `landscape.mp4` — 1920×1080, 30 fps по умолчанию;
- `short.mp4` — 1080×1920, весь игровой кадр на размытом фоне;
- `raw.mkv` или `raw.mp4` — исходник OBS;
- `thumbnail.jpg` — кадр действия или финальной постройки, 1280×720;
- `metadata.json` — название, описание, теги, реальный исход сцены;
- `plan.json`, `request.json`, `datapack.zip` — воспроизводимая конфигурация;
- `capture.json` — серверные тики, время записи и пробы кадров;
- `diagnostics/` — best-effort состояние машины и screenshot OBS;
- `diagnostics.zip` — переносимый bundle для разбора реального E2E-прогона;
- `manifest.json` — размер и SHA-256 опубликованных файлов;
- `failure.json` и журналы — если задание завершилось ошибкой.

`duration_seconds` — верхняя граница сценария в игровых секундах. Бой заканчивается через три секунды после определения результата, TNT-сцена — через 15–16 секунд. При лагах запись может быть длиннее: субтитры привязываются к наблюдаемому времени записи и тикам. Сервис не выдумывает отсутствующий исход.

## Эксплуатация и границы первой версии

Состояния: `queued → preparing → recording → rendering → succeeded`. Отдельные завершения: `failed`, `cancelled`, `interrupted`.

Одна очередь обслуживает одну записывающую машину. API можно запустить отдельно (`mine-video serve`), локальный исполнитель — отдельно (`mine-video agent`, совместимый алиас `mine-video worker`). Все исполнители этой студии должны использовать один `data_dir`; файловый lock предотвращает одновременный захват. Для нескольких машин нужны отдельные студии и распределитель заданий.

У dedicated OBS не должно быть других записей или стримов. Сервис отказывается начинать при уже активной записи. Перед собственной записью сохраняется маркер владения. После аварии worker закрывает принадлежащую студии запись; при недоступном OBS сохраняет маркер и останавливается. Прерванное видео автоматически не объявляется готовым и не переснимается под старым ID.

Raw-файлы сохраняются и в OBS-каталоге, и рядом с результатом; автоматической очистки пока нет. `minimum_free_gb` проверяется перед каждым заданием. При работе без присмотра выделить диск и настроить собственную политику хранения. Блокировка экрана, сон ОС и отсутствие разрешений на захват могут прервать запись.

Камера пока фиксированная: нет облётов, отслеживания бойцов или replay-рендеринга. В вертикальном формате сохраняется вся арена, а не делается потенциально неверный кроп. Нет LLM-сценариста, озвучки, музыки, аналитики удержания или загрузки на YouTube. Готовые шаблоны — основа для проверки формата, не гарантия интересного канала. Следующее расширение имеет смысл выбирать по качеству первых реальных записей.

## Проверки

```bash
python -m unittest discover -s tests -v
ruff check .
gradle --no-daemon -p client-guard build
```

Тесты проверяют конкурентные claims, idempotency, отмену, восстановление, ограничение сцен, защиту субтитров от ASS-вставок, diagnostics bundle и настоящий FFmpeg-экспорт со звуком в обоих форматах. Внешние игровые/OBS-адаптеры в тестах worker заменены управляемыми заглушками. Синтетический видеопаттерн используется только в тесте кодировщика; в рабочем конвейере такого режима нет.

CI устанавливает Python-зависимости, запускает тесты и собирает Fabric JAR. Это не заменяет сквозную запись с лицензированным графическим клиентом: для этого теперь есть `mine-video smoke-test` на локальной записывающей машине. Текущая фактическая проверка описана в [VALIDATION.md](docs/VALIDATION.md).
