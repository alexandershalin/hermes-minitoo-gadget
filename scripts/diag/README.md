# Диагностика MiniToo (запускает владелец или агент на железе)

Скрипты отвечают на открытые вопросы из `HANDOFF.md` (§5) и исследований. Общие правила:

- **Колонке ничего не пишется.** Исключения названы в имени скрипта и спрашивают
  подтверждение: `steady-hfp.sh` открывает микрофон колонки (то же переключение в HFP, что
  при обычной записи), `address.sh --radio` запускает поиск устройств (занимает радио).
- **Адрес колонки нигде не зашит** — всегда берётся из `config.json` (`minitoo.address`;
  другой путь: `MINITOO_CONFIG=/путь/config.json`). Токен из конфига не печатается.
- **Root не нужен.** Команды, которым он нужен, скрипты только печатают — владелец
  запускает их сам, не через бота. Строки журнала ядра и `bluetooth.service` требуют
  группы `adm` или `systemd-journal`; такие разделы помечены `[нужна группа ...]`.
- Скрипты, работающие до Ctrl-C (`hfp-keys.sh`, `steady-hfp.sh`), сами пишут лог в
  `~/minitoo-diag/` (другой каталог: `MINITOO_DIAG_DIR=...`). Остальные печатают в
  терминал — сохранить: `... 2>&1 | tee ~/minitoo-<имя>.txt`.
- `--help` у каждого скрипта печатает его шапку.

| Скрипт | Вопрос | Длительность |
|---|---|---|
| `links.py` | когда поднимается/падает SCO/eSCO к колонке, живо ли ACL при сбоях экрана | до Ctrl-C |
| `hfp-keys.sh` | приходит ли Play/Pause во время записи AT-командой по HFP (§5.1, §5.3) | до Ctrl-C |
| `display-ack.sh` | какие сбои у экрана: молчание колонки, разрыв, errno (§5.2) | секунды |
| `steady-hfp.sh` | молчит ли экран весь период HFP или только на переключениях A2DP/HFP (§5.2) | ~1,5 мин |
| `bt-health.sh` | здоровье адаптера: прошивка Intel, corrupted SCO, autosuspend, Wi-Fi (§5.7) | секунды |
| `address.sh` | почему меняется адрес колонки и одна ли это колонка (§5.8) | секунды (+1 мин с `--radio`) |
| `usb-route.sh` | можно ли вести звук и кнопки по USB-кабелю (обходит HFP целиком) | секунды |

## links.py

```bash
python3 -I ~/hermes-minitoo-gadget/scripts/diag/links.py            # таблица при изменениях
python3 -I ~/hermes-minitoo-gadget/scripts/diag/links.py --rssi     # + RSSI/LQ (если есть hcitool)
```

Раз в 0,5 с спрашивает у ядра список соединений адаптера (`HCIGETCONNLIST`) через один
сокет на всё время работы — в эфир ничего не уходит. Строка печатается, когда таблица
меняется. Пример:

```text
12:00:01.250  *B1:21:81:A0:78:53 ACL h=11 central enc
12:00:03.912  *B1:21:81:A0:78:53 ACL h=11 central enc | *B1:21:81:A0:78:53 eSCO h=257 central enc
```

`*` — колонка из `config.json`; `eSCO`/`SCO` — открыт голосовой канал (HFP). Если ACL
пропадает одновременно со сбоем экрана, это разрыв связи, а не «молчание» колонки.
`--rssi`: RSSI соединения считается от «золотого» диапазона (0 — норма, минус — слабее).
Удобно держать в отдельном терминале во время `hfp-keys.sh` и обычного нажатия.

## hfp-keys.sh

```bash
bash ~/hermes-minitoo-gadget/scripts/diag/hfp-keys.sh            # уровень I
bash ~/hermes-minitoo-gadget/scripts/diag/hfp-keys.sh --debug    # уровень D: весь обмен AT-командами
```

На время теста поднимает лог WirePlumber только для темы `spa.bluez5.native` (без
перезапуска WirePlumber), показывает журнал этой темы и печатает порядок нажатий (одно,
двойное, удержание, джойстик, громкость, нажатие сразу после записи). По Ctrl-C сам
возвращает уровень: `"N,spa.bluez5.native:I"`, если установлен
`scripts/wireplumber/60-minitoo-hfp-at-log.conf`, иначе `"N"` (уровень по умолчанию).
`wpctl set-log-level -` не используется: он стёр бы и шаблон из conf.d-фрагмента.

Ожидаемо: `RFCOMM receive command but modem not available: AT+CHUP` (или `AT+BVRA`, `ATA`,
`AT+CKPD`...) в момент нажатия — кнопка приходит по HFP, её можно ловить на хосте.
Ничего при нажатиях — колонка ничего не шлёт по HFP; дальше только `btmon` (root, сам
владелец). Если строк нет совсем даже при подключении колонки — запустить с `--grep`.
Прислать: файл `~/minitoo-diag/hfp-keys-*.log` и время нажатий.

## display-ack.sh

```bash
bash ~/hermes-minitoo-gadget/scripts/diag/display-ack.sh [--since '-2 days']
```

Гистограмма строк `MiniToo display retry N: ...` из журнала `hermes-minitoo`: нет `0x8B
ready ACK` (колонка молчит при открытом канале), connect timeout, EOF (колонка закрыла
канал), наш `reconnect backoff` (последствие первой ошибки), `errno N`. Плюс
распределение `recovered after ... (X s)`, отброшенные кадры и, если работает новый код
экрана с `--verbose`, задержка `ready ACK in N ms`. Прислать: вывод целиком.

## steady-hfp.sh

```bash
bash ~/hermes-minitoo-gadget/scripts/diag/steady-hfp.sh [-d 60]
```

Спрашивает подтверждение, затем держит микрофон колонки открытым 60 с (`pw-record` в
`/dev/null`; колонка переходит в HFP, как при записи), показывая журнал `hermes-minitoo`
и `links.py`, и ещё 20 с наблюдает возврат в A2DP. Отметки `T0`/`T1` — начало и конец.
Ответ: есть `ready ACK in N ms` в середине окна — колонка отвечает и в HFP, мешают только
переключения; одни `did not send the 0x8B ready ACK` до `T1` — колонка молчит весь HFP.
**Оговорка:** код экрана из origin/main удачные кадры не логирует, поэтому без нового кода
и `--verbose` тест не решающий (скрипт проверяет `--verbose` и подсказывает, как его
включить). Прислать: `~/minitoo-diag/steady-hfp-*.log`.

## bt-health.sh

```bash
bash ~/hermes-minitoo-gadget/scripts/diag/bt-health.sh [--boot -1] 2>&1 | tee ~/minitoo-bt-health.txt
```

Версии; адаптеры и `power/control` их USB-устройств; параметр `btusb enable_autosuspend` и
установлены ли файлы из `scripts/system`; журнал ядра: патч прошивки Intel (`completed and
deactivated` — патч не применён), число `corrupted SCO packet` (ошибка USB на стороне
компьютера), `tx timeout`, `Unable to disable scanning`; ошибки `bluetoothd`; Wi-Fi той же
комбо-карты, `bt_coex_active`, USB 3 рядом; ошибки сканирования рядом с поисками
`minitoo-autoaddr`. Ожидаемо после установки `scripts/system`: `control=on`, параметр `N`.

## address.sh

```bash
bash ~/hermes-minitoo-gadget/scripts/diag/address.sh 2>&1 | tee ~/minitoo-address.txt
bash ~/hermes-minitoo-gadget/scripts/diag/address.sh --radio     # + поиск BR/EDR и LE, SDP (с подтверждением)
```

Записи BlueZ о колонках (тип адреса, Modalias, UUID, Paired/Connected), свойства D-Bus,
устройство ввода AVRCP (Vendor 05d6 / Product 000a), имена узлов PipeWire и формы адреса в
`config.json`, история `minitoo-autoaddr`. Печатает (не выполняет) команды с root
(`btmgmt find`, `btmon`, файлы `/var/lib/bluetooth`) и процедуру проверки повторяемости:
3 раза выключить/включить колонку и записать адрес. Новый адрес при каждом включении —
прошивка не сохраняет адрес; тот же — смена была разовой. Перед `--radio` лучше
остановить `minitoo-autoaddr` и `hermes-minitoo` (команды печатаются). Прислать: вывод и
записанные адреса.

## usb-route.sh

```bash
bash ~/hermes-minitoo-gadget/scripts/diag/usb-route.sh 2>&1 | tee ~/minitoo-usb-route.txt
```

Сначала подключить колонку к серверу USB-C кабелем с данными. Только списки: `lsusb -d
4c4a:`, накопитель `BR28 UDISK` (не монтировать), `arecord -l`/`aplay -l`, `wpctl status`,
HID-кнопки в `/proc/bus/input/devices`, журнал ядра. В конце — ручные шаги (запись с
USB-микрофона, `evtest` кнопок, работает ли экран по Bluetooth во время звука по USB).
Ожидаемо, если путь рабочий: устройство `4c4a:4e55`, карта с микрофоном, HID-устройство
Divoom/JieLi. Откат — отключить кабель.

## Как прислать результаты

Файлы из `~/minitoo-diag/` и сохранённые `~/minitoo-*.txt` — целиком, плюс время нажатий
и что было на экране колонки. Токенов и ключей сопряжения в выводе нет; адреса колонки
есть (в `address.sh`, `links.py`, `hfp-keys.sh`) — это не секрет, но при публикации можно
заменить хвост адреса.
