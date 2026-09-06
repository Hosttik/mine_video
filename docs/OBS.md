# Однократная настройка OBS

1. Создай **Profile → New → MineVideo** и **Scene Collection → New → MineVideo**. В ней создай сцену `MineVideo`. Не используй этот профиль для других записей.
2. Запусти Prism-instance `MineVideo`. В OBS добавь один источник окна игры с именем `Minecraft`. Windows: Game Capture → Capture specific window. macOS: macOS Screen Capture → окно Minecraft. Linux: Window Capture/Xcomposite в X11 либо разрешённый системой захват окна в Wayland. Названия источников зависят от ОС/сборки OBS; выбрать окно нужно один раз в интерфейсе OBS.
3. Transform → Fit to Screen. Убедись, что захватывается содержимое игры без панели launcher и рамки рабочего стола. Не минимизируй Minecraft: некоторые способы захвата перестают получать кадры.
4. Settings → Video: Base 1920×1080, Output 1920×1080, 30 или 60 fps. При изменении разрешения клиента снова проверь Fit to Screen. В итоговом экспорте по умолчанию 30 fps.
5. Settings → Output → Recording: MKV, качество High Quality или эквивалентное. Recording Path: полный путь к `recordings` рядом с `mine-video.toml`. Этот путь должен совпадать с разрешённым `recording_root`. Worker и OBS должны видеть одни и те же локальные файлы.
6. Настрой **игровой звук** и проверь индикатор уровня. В Windows возможен Application Audio Capture, в macOS — поддерживаемый OBS захват звука приложения, в Linux — отдельный audio monitor. Отключи микрофон и лишний desktop audio в этой студии. Одна аудиодорожка должна содержать игру. Наличие аудиодорожки проверяется программно; её содержимое и громкость нужно прослушать при первой настройке.
7. Tools → WebSocket Server Settings: Enable WebSocket Server, порт 4455, Authentication. Пароль — значение `MV_OBS_PASSWORD` из локального `.env`.
8. `mine-video doctor --launch` проверяет подключения и делает пробу источника. При необходимости разреши OBS захват экрана/аудио в настройках ОС; разрешение выдаётся пользователем на записывающей машине.

OBS WebSocket передаёт команды и путь записи, а не само видео. В этой версии удалённый OBS без общего файлового пути не поддерживается.

Нельзя направлять `source` на картинку или старый media-файл: guard проверяет Minecraft-клиент, а анализ снимков — источник OBS, но не доказывает семантическую идентичность этих двух потоков. Выбор реального окна игры — часть первоначальной настройки.

## Если не записывает

| Ошибка | Что проверить |
| --- | --- |
| `Capture guard not ready: world` | Datapack установлен до первого запуска Paper; клиент перешёл в `minevideo:studio` |
| `Capture guard not ready: render...` | Игра не свёрнута, не заморожена; компьютер не заблокирован |
| `Capture guard not ready: server/account` | Имя аккаунта и `server_address` совпадают в TOML и сгенерированном клиентском пакете |
| `black or blank` | В OBS выбран Minecraft, источник виден, у OBS есть разрешения |
| `stayed frozen` | Источник не замер при потере фокуса; в него не попал статичный launcher |
| `no audio stream` | В OBS включена записываемая игровая аудиодорожка |
| `outside recording_root` | Recording Path OBS и настройка `recording_root` указывают на одну папку |
| `New datapack did not load` | `server-data/world/datapacks` соответствует миру контейнера; версия 1.21.4 |
| `Readiness timed out` при запуске Paper | Docker работает, EULA принята, порт свободен; `docker compose logs mc` |

Если worker остановился с `recording-owner.json`, сначала восстанови доступ к dedicated OBS. Затем запусти worker ещё раз: он выполнит восстановление. Если запись вручную завершена и профиль сменился, верни профиль `MineVideo`; маркер не следует удалять вслепую.
