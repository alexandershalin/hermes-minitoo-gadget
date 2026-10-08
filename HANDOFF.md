# HANDOFF: Hermes + Divoom MiniToo — полный контекст проекта для другой модели

Снимок: **2026-10-08** (конец рабочей сессии). Автор: Падаван (инфраструктурный бот Саши), со слов и наблюдений за сессию.
Цель документа: дать модели-ревьюеру **всё**, что известно о проекте, чтобы она могла сделать аудит и правки без повторных расспросов.

Правила чтения:
- **[ПРОВЕРЕНО]** — видел в логах/выводе команд на реальном железе.
- **[ГИПОТЕЗА]** — предположение, не подтверждённое.
- **[ЧУЖОЕ]** — взято из сторонних репозиториев/документов, у нас не воспроизведено.
- Цитаты пользователя — дословные (по-русски).

---

## 1. Что это за проект

Колонка **Divoom MiniToo** (Bluetooth-колонка с экраном 160×128) используется как «голосовой гаджет» для агента **Hermes** (self-hosted AI-агент Nous Research, у Саши он управляет много чем).

- Экран колонки = экран Hermes Gadget (состояния Ready / Hold Talk / Listening / Thinking / Speaking).
- Динамик колонки = вывод речи агента (A2DP).
- Микрофон колонки = ввод речи пользователя (через HFP-профиль).
- Единственная физическая кнопка, которую мы можем читать на Linux, — **Play/Pause** (приходит как AVRCP-клавиша). Кнопки «talk» на колонке нет.

Репозиторий: <https://github.com/alexandershalin/hermes-minitoo-gadget> (ветка `main`, публичный).

Архитектура «ядра» (экран, ws-протокол с Hermes) описана в `docs/ARCHITECTURE.md`, исследования — в `research/`. **Этот файл** — дополнение: что сделали *вокруг* ядра (звук, кнопка, запись, автоадрес) и что **не работает**.

### Пожелание пользователя (важно для решений)
Саша — продвинутый пользователь, устал от повторяющихся ручных проверок. Он просил:
- не заставлять его повторять одни и те же действия;
- сообщения в Telegram/чате — коротко;
- красный/зелёный полноэкранный индикатор ему **не нужен** (был сделан и убран);
- бип после записи ему **не нужен** (пробовали, отменён);
- сохранить как есть: Listening не показывается надёжно — «оставь пока как есть».

---

## 2. Окружение

| Что | Значение |
|---|---|
| Сервер | домашний, Pentium N3700 (без AVX, без GPU), Ubuntu 26.04 LTS, ядро 7.0.0-34-generic |
| Python | 3.14.7 (системный) + venv проекта `.venv` |
| BlueZ | 5.85 |
| PipeWire | 1.6.2 (+ WirePlumber) |
| BT-адаптер | USB Intel `8087:0a2a` (`hci0`), на сервере единственный |
| Колонка | Divoom MiniToo, SDP Modalias `v05D6 p000A d0240` (fw ≈ 2.4.0 по чужим источникам) |
| MAC колонки (сейчас) | `B1:21:81:A0:78:53`; 8 окт. около 20:20 МСК-сессии адрес сменился (раньше оканчивался на `CB:09`), см. §5.8 |
| Hermes Gadget SDK | `v0.2.0`, коммит `323e3303ab68981f810fc3208119cd8a22e64af0`, лежит в `~/hermes-gadget-sdk` |
| STT | OpenRouter STT (command-провайдер в конфиге Hermes), язык ru |
| TTS | Piper (демон `piper-tts.service` на 127.0.0.1:8770), клиент `~/.local/bin/piper-tts-client.py`, голос `dmitri` |
| Сеть | прямой доступ в GitHub нужно делать **без** `HTTP(S)_PROXY` (прокси-IP забанен GitHub'ом) |

Пользовательские systemd-юниты (`systemctl --user`):

| Юнит | Что делает |
|---|---|
| `hermes-minitoo` | основной: Hermes Gadget (Linux client) + наш `MiniTooDisplay` по RFCOMM; `Restart=always` |
| `minitoo-talk-key` | слушает `/dev/input/eventN` AVRCP-устройства колонки, по Play/Pause запускает запись (`scripts/minitoo-talk-key.py`) |
| `minitoo-autoaddr` | следит за сменой BT-адреса колонки, перепарит и правит `config.json` (`scripts/minitoo-autoaddr.py`) |
| `pipewire`, `wireplumber` | звук |

Юниты `minitoo-talk-key` и `minitoo-autoaddr` лежат в `scripts/systemd/`, `hermes-minitoo.service` тоже лежит в `scripts/systemd/`.

---

## 3. Поток данных (как должно работать)

```
Play/Pause на колонке
   → AVRCP → ядро Linux → /dev/input/event11 (имя "Divoom MiniToo-App (AVRCP)")
   → minitoo-talk-key.py: on_press()
        1. hermes-minitoo button talk press        (Hermes Gadget: экран → Listening)
        2. pw-play SPEAK_WAV на bluez_output       («Говорите!» голосом Piper)
        3. pw-record с bluez_input (16 кГц mono)   → VAD (RMS), стоп по тишине 1,5 с
        4. hermes-minitoo button talk release      (Gadget: экран → Thinking, отправка аудио)
   → Hermes: STT → LLM → TTS → ответ в колонку (A2DP), экраны Thinking → Speaking → Ready
```

Параметры VAD в `minitoo-talk-key.py`: `silence_s=1.5`, `nospeech_s=12.0`, `MAX_REC_S=30`, порог = max(250, 3×медиана шума первых 0,8 с).

---

## 4. Что реально сделано в этой сессии [ПРОВЕРЕНО, всё в git]

Коммиты (старые → новые): `03b76d2` (индикатор + скрипты), `49acaa3` (запись в потоке), `8cccf10` (индикатор отключён), `d70f77a` (press до «Говорите»), `a29cf2c` (backoff-фикс), `f11ae6a` + `d101cdc` (ожидание ACK кадра — добавлено и откачено).

1. `scripts/minitoo-talk-key.py` — новый сервис: Play/Pause → запись с VAD.
2. `scripts/minitoo-autoaddr.py` — перепаривание при смене MAC.
3. `src/hermes_minitoo/display.py` — повтор неудачного кадра (`retry_window_s`, по умолчанию 60 с), логирование, **остаток индикатора** (см. §8 — мёртвый код).
4. `src/hermes_minitoo/transport.py` — баг «backoff продлевает сам себя» (см. §7.3).
5. Тесты: `tests/test_indicator.py`, `test_talk_key.py`, `test_backoff.py`; всего 21 проходят (`pytest -q`).
6. `config.json` (вне git, в `.gitignore`): адрес, `audio.output`/`audio.input` как `bluez_output.<MAC>` / `bluez_input.<MAC>`.

---

## 5. Нерешённые проблемы (главное для ревьюера)

### 5.1 Колонка не отдаёт нажатия кнопки во время записи [НЕ РЕШЕНО, самая странная]

Пользователь: «колонка не принимает нажатия кнопок во время записи (а это максимально странно, что запись нельзя остановить кнопкой)».

Факты [ПРОВЕРЕНО]:
- В обычном режиме (профиль `a2dp-sink`) Play/Pause приходит как `KEY_PLAYCD` 200 / `KEY_PAUSECD` 201 (колонка чередует!), джойстик влево/вправо = 165 (`PREVIOUSSONG`) / 163 (`NEXTSONG`). Устройство `event11`.
- Во время записи профиль карточки BlueZ переключается на `headset-head-unit` (`wpctl`/`pw-dump` показывают). В этот интервал в `evdev` **нет ни одного события** от кнопки, хотя читающий цикл теперь работает в отдельном потоке и читает постоянно (раньше был баг у нас — читающий цикл блокировался, исправлен, см. `49acaa3`). Лог `key code=… phase=… lag=…` показывает: ни разу не пришло событие в фазе `recording`.
- После записи, когда профиль возвращается в `a2dp-sink`, кнопка снова работает.
- Устройство `event11` при смене профиля **не пропадает** (минимум в те периоды, когда мы смотрели).

Гипотезы [ГИПОТЕЗА]:
1. В режиме гарнитуры колонка шлёт кнопку не по AVRCP, а **по HSP/HFP по RFCOMM**: `AT+CKPD=200` (HSP) или `AT+BVRA` (HFP voice recognition; в `BRSF=671` колонки есть бит VOICE_RECOG). Строка `AT+CKPD=200` присутствует в `libspa-bluez5.so` PipeWire, но, вероятно, только отвечает `OK`, не создавая input-события. Мы **не видели** этих AT-команд: отладочный лог WirePlumber (`btdebug.conf`) сняли только для рукопожатия, а не для момента нажатия.
2. AVRCP-канал (L2CAP PSM 0x17) в режиме SCO/eSCO не обслуживается прошивкой.
3. Радиоканал с слабым сигналом: RSSI −85, `hci0: corrupted SCO packet` — нажатия просто теряются. (Но обычный A2DP-режим при том же RSSI кнопку отдавал.)

Что нужно сделать ревьюеру:
- снять **`btmon -w`** (нужен root; Саша не хочет, чтобы sudo-пароль проходил через бота) во время нажатия в записи, посмотреть: приходит ли AVRCP PASSTHROUGH, приходит ли `AT+CKPD`/`AT+BVRA` на RFCOMM-канале HFP;
- либо включить в BlueZ/PipeWire debug RFCOMM-AT-трассу на момент записи (`SPA_DEBUG`/`WIREPLUMBER_DEBUG=bluez5:4`);
- если это AT+BVRA/CKPD — написать небольшую утилиту (или патч), читающую `/dev/rfcommN`/сокет HFP и поднимающую событие;
- проверить, не отключает ли AVRCP сам WirePlumber при переключении профиля (`bluez5.hw-volume`/`bluez5.roles`).

### 5.2 Кадры на экран «отправляются как ни попадя» [ЧАСТИЧНО, ПРОБЛЕМА ОСТАЁТСЯ]

Пользователь: «картинки отправляются как ни попадя».

Наблюдения [ПРОВЕРЕНО]:
- **Каждое** нажатие, переключающее профиль в `headset-head-unit`, рвёт RFCOMM-канал экрана (`did not send the 0x8B ready ACK` → `reconnect backoff is active`). Канал возвращается через 2–7 с (`recovered after N retries`).
- Кадр, посланный в момент разрыва (например, **Listening** сразу после `button talk press`), теряется. Повторять устаревший кадр бессмысленно — к моменту восстановления экран уже должен быть другим. Реализован «latest-frame-wins»: после восстановления уходит только актуальный.
- Раньше наблюдалось «залипание» старого кадра (экран оставался красным от тестового индикатора; Listening оставался после записи): кадр Thinking не доходил, потому что `reconnect backoff` продлевал сам себя (исправлено, §7.3).
- Реальная последовательность на экране в последнем успешном прогоне пользователя: «Hold Talk» (Listening **не появился**) → Thinking → Speaking → Ready.
- Попытка «подождать подтверждения доставки Listening перед `Говорите`» (коммит `f11ae6a`) не дала эффекта: за 8,7 с кадр не доставился (колонка недоступна по RFCOMM, пока открывается SCO). Откачено, пользователь сказал «оставь как есть».

Кандидатные направления:
- Не слать Listening вообще, а сохранять экранный статус «Hold Talk», либо заранее подготовить **персистентное изображение** на колонке (custom face / photo album, §9.5) и переключаться *командой* (она короче 0x8B-передачи и может пройти);
- слать Listening **до** открытия SCO, т. е. нажать `talk` позже: сначала отправить кадр, дождаться ACK, только потом `press`. Пользователь про это сказал «вариант 1», но потом отказался («оставь пока как есть»).
- Вынести проверку профиля: пока профиль `headset-head-unit`, не пытаться слать кадры, а ждать возврата в `a2dp-sink` (иначе «шторм» из бесполезных 8-секундных таймаутов).
- Подумать о другом канале экрана (BLE `AF30`/`fe010000`) — см. §9.4.

### 5.3 Запись не останавливается кнопкой (следствие 5.1)
Реализован досрочный стоп второй нажатой Play/Pause (`on_press` → `stop_evt.set()`), протестирован юнит-тестом, **на железе не сработал** из-за 5.1. Сейчас остановка только по тишине (VAD).

### 5.4 Звуковой индикатор «запись закончилась» [ОТМЕНЁН]
Пользователь хотел сигнал окончания записи. Бип воспроизводился на `bluez_output`, но *после* записи колонка ~6,3 с возвращается из `headset-head-unit` в `a2dp-sink`, и бип приходил с задержкой 9 с либо терялся. Пользователь: «забей на бип». В `scripts/minitoo-talk-key.py` бипа уже нет (функция удалена).
Экранный индикатор тоже был сделан (полноэкранный красный/зелёный) и отключён по просьбе («появляются не в тот момент, остаются, путают»).

### 5.5 «Первое нажатие ничего не делает» [ЧАСТО НАБЛЮДАЕТСЯ, НЕ ИССЛЕДОВАНО ДО КОНЦА]
Несколько раз пользователь сообщал: нажал — тишина; повторное нажатие запускает. В логах это либо нажатие в `phase=starting`/`debounce` (игнорируется), либо событие не пришло (колонка отключилась/профиль переключается). Нужен аккуратный разбор: логи `key code=… phase=… lag=…`.

### 5.6 Хвост «Говорите» в записи / обрезка начала подсказки
Начало звука на колонке обрезается при смене профиля, поэтому в `SPEAK_WAV` добавлено 0,9 с тишины перед словом (`ensure_wav()`, ffmpeg `adelay=900`). После `press` теперь запись идёт сразу после проигрывания; возможно попадание эха подсказки в запись (VAD это не учитывает).

### 5.7 Нестабильность Bluetooth-адаптера и канала
[ПРОВЕРЕНО]: `hci0: Unable to disable scanning: -16`, `lost RFCOMM connection`, `bluetoothd: getpeername: Transport endpoint is not connected`, `corrupted SCO packet`; несколько раз колонка пропадала полностью (`Connected: no`), помогал `bluetoothctl disconnect/connect` + рестарт `wireplumber`. USB-autosuspend адаптера отключён вручную (`power/control=on`) — **этот параметр не сохраняется после перезагрузки** (нужно udev-правило/TLP). RSSI гуляет от −58 до −85.

### 5.8 MAC-адрес колонки меняется
[ПРОВЕРЕНО] 8 окт. адрес колонки сменился (до этого был `…:CB:09`, в конфиге остался старый хвост). Колонка с префиксом `B1:21:81:` каждый раз — другой адрес (random static / privacy?). `minitoo-autoaddr` борется с этим (сканирует, выбирает самый «частый» адрес в эфире, пэйрит/trust/connect, правит `config.json`, перезапускает `hermes-minitoo`). Не отлажено до конца: пэйр делается через `bluetoothctl` с задержками, а не через D-Bus агент.

---

## 6. Журнал расследования (что уже пробовали, чтобы не повторять)

1. «Режим рации» / интерком: пользователь утверждал, что в документации колонки написано про использование как рации. Документацию (manuals.plus блокирует ботов; device.report дал неполный PDF) найти не удалось; **в исследованных репозиториях нет ни слова про walkie-talkie/intercom/PTT**. Максимум — `BRSF=671` включает `VOICE_RECOG` (бит HFP). Вывод: «рации» как отдельного режима, доступного через открытый протокол, не нашли [ГИПОТЕЗА, что он есть только в приложении/на самой колонке].
2. Попытка сделать «Говорите → слушаю → отпустить» в цикле: пользователь был раздражён повторами. Решено использовать Play/Pause как триггер + VAD.
3. HFP-рукопожатие: `AT+BRSF=671` (HF-фичи: EC/NR, 3-way, CLI, remote volume, enhanced call status/control, codec negotiation, HF indicators, eSCO S4, VOICE_RECOG), `AT+BVRA` не видели.
4. SDP-сервисы колонки (`bluetoothctl info`): SPP, A2DP sink, AVRCP target + controller, Handsfree, Message Notification, PnP, GAP, `0xAF30` (Unknown), vendor `49535343-fe7d-4ae5-8fa9-9fafd205e455` (ISSC/Microchip BLE transparent UART) и `fe010000-1234-5678-abcd-00805f9b34fb`.
5. Попытка включить debug WirePlumber (`btdebug.conf`) — снял, оставлять нельзя (флуд + нестабильность).
6. Проверено, что `pactl` на сервере нет — используем `wpctl`/`pw-dump`.
7. `AT+CKPD=200` в `libspa-bluez5.so` — найдено строкой, поведение не исследовано.

---

## 7. Исправленные баги (для аудита)

### 7.1 Хардкод адреса и устройства
`SINK` в `minitoo-talk-key.py` был жёстко `…CB:09`; теперь читается из `config.json → audio.output`. Аналогично `audio.input`.

### 7.2 Фильтр устройства ввода слишком строгий
Раньше искал имя `Divoom MiniToo-Audio (AVRCP)`; после смены профиля/имени устройства стало `Divoom MiniToo-App (AVRCP)`. Теперь: подстрока `MiniToo` **и** `(AVRCP)`.

### 7.3 Backoff транспорта продлевал сам себя
`send_rgb888()` ловил любое исключение, включая «reconnect backoff is active», и ставил `last_failure = now`. Дисплей повторяет раз в 1 с → пауза 2 с никогда не кончалась (сотни строк `backoff` подряд). Исправлено: `connect()` вызывается *вне* `try`, `last_failure` ставит только настоящий сбой. Тест: `tests/test_backoff.py`.

### 7.4 Потеря кадра после окна повторов
После `retry_window_s` кадр отбрасывался без перепостановки; теперь `self.dirty=True` → `present()` возьмёт актуальный кадр заново. Окно 30 → 60 с.

### 7.5 Главный цикл блокировался записью
`minitoo-talk-key.py` писал в основном потоке, пропускал нажатия, а в конце «сбрасывал» накопленные события. Теперь запись в `threading.Thread`; цикл читает события постоянно; вторая кнопка → `stop_evt`.

### 7.6 Прочее
- `pw-record` процесс корректно `kill()`+`wait()`.
- `release` и очистка индикатора — в `finally`.
- При старте сервиса сбрасывается «зажатая» кнопка talk (`button release`).
- `autoaddr`: запись `config.json` атомарная; мёртвый код удалён.

---

## 8. Известные недочёты кода (первоочередные цели аудита)

1. **Мёртвый код индикатора** в `display.py`: `INDICATOR_FILE`, `REC_COLOR`, `DONE_COLOR`, `_indicator()`, ветка в `present()`; `set_ind()` в `minitoo-talk-key.py` теперь только удаляет файл. Тесты `test_indicator.py` проверяют мёртвый код. Либо удалить, либо оставить как опцию с флагом конфигурации.
2. Хардкод абсолютных путей в `scripts/*.py` (`/home/bishop/...`, путь к Python Hermes с хешами `20715197cc5be820/…/738223755d2649faa3439a3b8f7036ae`). Нужны переменные окружения/конфиг.
3. `VAD`: простой RMS-порог; при шуме 2594 (профиль гарнитуры сначала выдаёт мусор) запись 14 секунд завершилась «речи нет» при том, что пользователь говорил. Нужен прогрев/отбрасывание первых 0,5–1 с после включения SCO, адаптивный порог.
4. Нет нормальной блокировки между `worker`/`on_press` (используется `dict`-состояние без `Lock`; гонка теоретически возможна между проверкой `phase` и запуском потока).
5. `minitoo-autoaddr`: `bluetoothctl` через stdin с `sleep` — хрупко; лучше `dbus`/`bluezero`. Выбор адреса «по частоте в эфире» — эвристика.
6. `config.json` бэкапы `*.bak-*` в каталоге (в `.gitignore`).
7. CI (`.github/workflows/ci.yml`) гоняет Python 3.12, локально 3.14.
8. (исправлено) `hermes-minitoo.service` добавлен в `scripts/systemd/`.
9. Два способа получить имя sink/source (`config.json` и `wpctl`); не согласованы при смене MAC (`autoaddr` меняет только `minitoo.address` через `str.replace`, что **заменяет все вхождения старого адреса**, включая `audio.*` — фактически работает благодаря этому, но не по замыслу).
10. Использование `hermes-minitoo button talk press/release` через `subprocess` (≈0,5–1 с на вызов).

---

## 9. Источники про хаки колонки, прошивки и железо

(Расширенная версия `research/SOURCES.md`; там же подробнее `HACKS.md` и `FIRMWARE.md`.)

### 9.1 Протокол по Bluetooth Classic (RFCOMM/SPP)
- **bugzmanov/divoom-minitoo** — <https://github.com/bugzmanov/divoom-minitoo>. Главный источник. `FINDINGS.md` (≈105 КБ): реверс Android-приложения Divoom и пробы на реальной колонке (fw **2.4.0**). SoC-семейство **Jieli (JL)**, сервисы `JL_SPP/JL_HFP/JL_HID/JL_A2DP`, BT 5.3 Classic. Один RFCOMM-клиент одновременно (приложение Divoom не увидит колонку, пока канал занят). Опкод `0x72` — «tool views» (секундомер, табло, **измеритель шума**, таймер); приложение **никогда не шлёт «выход из tool view»**, возврат только кнопкой. Много «тихих» команд (JSON `Tools/Set*` молча игнорируется, нужен бинарный `0x72`). **Опасно**: семейство «sleep» команд — «more probing would brick the device again». Нерешённо у них: управление галереей фото («the gallery control problem (UNSOLVED)»).
- **alvinunreal/divoom-minitoo-osx** — <https://github.com/alvinunreal/divoom-minitoo-osx>. `PROTOCOL.md`: путь `0x8B`, 128×128 RGB888 + Zstandard (окно 128 КиБ), чанки по 256 байт, persistent RFCOMM-демон, выбор custom ClockId.
- **sirnugget11/divoom-minitoo-dotnet** — <https://github.com/sirnugget11/divoom-minitoo-dotnet>. Нативный 160×128 путь `0x23` + MiniLZO, ожидание ACK `8B 55 00 01`, лимиты безопасности. **На нём основан наш транспорт.**
- **ruvnet/minitoo-control** — <https://github.com/ruvnet/minitoo-control>. Постоянный транспорт, анимированные статусы, измеренные тайминги, защитная валидация (JS).
- **alphafornow gist** — <https://gist.github.com/alphafornow/8d38848adf9be12d0f9dc7700dff5e21>. Независимые заметки по SPP/картинкам (Python).
- **lewilou22/divoom-minitoo-tools** — <https://github.com/lewilou22/divoom-minitoo-tools>. Windows-эксперименты, заявлены более высокие частоты кадров и одновременное аудио/видео.
- **jsniel/home-assistant-minitoo** — <https://github.com/jsniel/home-assistant-minitoo>. Интеграция с Home Assistant (+ ESP32-прокси).
- **AFrayde01/divoom-minitoo-codex** — <https://github.com/AFrayde01/divoom-minitoo-codex>; **giperfast/codex-minitoo** — <https://github.com/giperfast/codex-minitoo>. MiniToo как статус-экран для Codex/агентов.
- В клонах локально: `~/.hermes/profiles/padawan/cache/scratch/re/{bugzmanov,divoom-minitoo-osx,divoom-minitoo-dotnet,minitoo-control}` (не в git; доступны по ссылкам выше).

### 9.2 BLE-канал (нашли у себя в SDP, не использовали)
В SDP колонки есть сервис `0xAF30`, vendor UUID `49535343-fe7d-4ae5-8fa9-9fafd205e455` (ISSC/Microchip «transparent UART») и `fe010000-1234-5678-abcd-00805f9b34fb`. По `FINDINGS.md` bugzmanov'а «BLE не используется для команд»; по другим источникам `AF30`+`49535343…` связаны с передачей данных экрана. **Не проверено**, возможный запасной канал для экрана, который не рвётся при смене BT-профиля [ГИПОТЕЗА].

### 9.3 Прошивки и кастомный код
- **antiali.as/minitoo-forth** (Tangled) — <https://tangled.org/antiali.as/minitoo-forth/tree/public>. README: <https://tangled.org/antiali.as/minitoo-forth/blob/public/README.md>; доставка: <https://tangled.org/antiali.as/minitoo-forth/blob/public/docs/flash-delivery.md>; доказательства: <https://tangled.org/antiali.as/minitoo-forth/tree/public/docs>.
  Основное: прошивка приложения `flag41007.bin`, 1 183 237 байт, SHA-1 `c950735f817bd8a22b0d4d616f04e9dd058d93d5`; SoC приложения **Actions ATS2831** (даташит под NDA), вспомогательный/USB-чип **JieLi AC690N**. OTA-образ защищён **аддитивной контрольной суммой, не подписью**. Проект — крошечное FORTH-ядро в «code cave» ≈6,5 КиБ, просыпается на BT-команду `0x0407`, исполняет одну строку на пакет. **Доказано только в симуляции; на физической колонке запущено не было.** Маршруты доставки: SPP-OTA (заблокирован «catch-22» с bootloader state), SD-карта (закрыт), USB DFU/кнопочная комбинация (под вопросом, топология неясна: ATS2831 или JieLi), boot-straps/GPIO и JTAG (нужно вскрывать корпус), JieLi как мост (неизвестно).
  Прошивку получать через приложение Divoom (`GetUpdateFileV3` → `FileId` → CDN `f.divoom-gz.com`); бинарники в репозиторий не класть.
- **REvoom** — <https://divoom.2a03.party/> — индекс прошивок Divoom (не все билды MiniToo).
- Открытые вопросы (из `research/FIRMWARE.md`): подтверждён ли ATS2831 по кремнию; что делает AC690N; воспроизводима ли DFU-комбинация; разрешит ли HCI-снимок официального обновления «bootloader-state»; есть ли безопасный RAM-execution; соответствует ли ревизия FCC-фото; можно ли откатиться после неудачной прошивки.

### 9.4 Железо и регуляторка
- **FCC ID A8I-MINITOO** — <https://fccid.io/A8I-MINITOO>; внутренние фото: <https://fccid.io/A8I-MINITOO/Internal-Photos/Internal-Photos-8647766>.
- Экран 160×128 IPS; Bluetooth 5.3 Classic. Расхождение между источниками: у bugzmanov — семейство Jieli, у minitoo-forth — Actions ATS2831 + JieLi AC690N. **Не сверено.** Кремний на нашей колонке не вскрывали.

### 9.5 Что умеет колонка по чужим данным [ЧУЖОЕ]
Яркость (JSON `Channel/SetBrightness` или бинарный `0x32`), экран on/off (`0xBD/0x2F`), нативные tool views (`0x72`), игры (`0xA0`), фотоальбомы (`Photo/NewAlbum`, `LocalAddToAlbum`…), custom faces с переключением по ClockId (`Channel/SetClockSelectId`, быстрее, чем слать кадр), ANCS-подобные уведомления (**кастомная иконка `0x3C` роняет колонку**), `Device/GetStorageStatus`, `WhiteNoise/Get`. Подробнее — `research/HACKS.md`.

---

## 10. Чего мы НЕ знаем (чтобы не принимать за факт)

- Куда именно уходит нажатие во время записи (§5.1).
- Есть ли в колонке «режим рации» — нет подтверждения.
- Работает ли одновременный RFCOMM + SCO стабильно на другом BT-адаптере (у нас Intel 8087:0a2a, самый вероятный источник проблем).
- Постоянны ли причины смены MAC.
- Почему RSSI скачет от −58 до −85 при неподвижной колонке (помехи? антенна? расстояние?).
- Стабильна ли доставка кадров при **не**-переключающемся профиле (кроме сбоев после записи мы большой статистики не набирали).

---

## 11. Рекомендуемый план для ревьюера

1. Аудит `scripts/minitoo-talk-key.py` (потоки, гонки, VAD, пути).
2. Аудит `display.py`/`transport.py` (очередь кадров, повтор, backoff); убрать мёртвый индикатор.
3. Диагностика §5.1: `btmon`/RFCOMM-трасса во время нажатия в записи.
4. Продумать, как показывать «Listening» без доставки кадра в окно разрыва (персистентное изображение/команда/BLE).
5. Сделать конфигурируемыми пути и вынести параметры VAD в `config.json`.
6. Добавить `hermes-minitoo.service` и udev-правило автосна USB-адаптера в репозиторий.
7. Подумать о USB-адаптере другой модели (проверка гипотезы «адаптер — слабое звено»).

---

## 12. Как запускать/проверять

```bash
cd ~/hermes-minitoo-gadget
.venv/bin/python -m pytest -q                # 21 тест
systemctl --user status hermes-minitoo minitoo-talk-key minitoo-autoaddr
.venv/bin/hermes-minitoo status              # JSON: phase, screen, audio, ...
journalctl --user -u minitoo-talk-key -f     # события кнопок: key code=… phase=… lag=…
journalctl --user -u hermes-minitoo -f       # экран: retry/recovered/ready ACK
bluetoothctl info <MAC> | grep -E "Connected|RSSI"
wpctl status | grep -i minitoo               # профиль/узлы
```

Секреты: токен pairing и конфиг — только в `config.json` (в `.gitignore`); **не коммитить**.
