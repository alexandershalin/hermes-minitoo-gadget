# Фрагменты WirePlumber (пользовательский уровень, root не нужен)

Фрагменты кладутся в `~/.config/wireplumber/wireplumber.conf.d/` и читаются при старте
WirePlumber. Читаются только файлы с окончанием `.conf`, поэтому экспериментальные
фрагменты лежат здесь с суффиксом `.disabled` и сами по себе ничего не делают.
Подробности, риски и откат — в шапке каждого файла.

| Файл | Статус | Что делает |
|---|---|---|
| `60-minitoo-hfp-at-log.conf` | можно ставить | журнал хранит INFO-строки темы `spa.bluez5.native`: AT-команды колонки во время записи (след Play/Pause, HANDOFF §5.1) |
| `61-minitoo-no-dummy-call.conf.disabled` | эксперимент | только для карты MiniToo убирает фиктивный «звонок» `+CIEV: 2,1` при записи |
| `62-minitoo-cvsd.conf.disabled` | эксперимент | HFP только CVSD (без mSBC) для всех BT-гарнитур компьютера |

## Общие правила

- Установка: `mkdir -p ~/.config/wireplumber/wireplumber.conf.d` и `cp` файла туда
  (для `.disabled` — под именем без суффикса).
- Применение: один раз `systemctl --user restart wireplumber`. Звук Bluetooth-колонки
  пропадёт на несколько секунд, профили переподключатся. Делать, когда колонка не нужна,
  не во время записи; при необходимости переподключить колонку
  (`bluetoothctl connect <MAC из config.json>`).
- Если у WirePlumber задана переменная `WIREPLUMBER_DEBUG`, настройка `log.level` из
  фрагментов не действует. Проверка:
  `systemctl --user show wireplumber -p Environment; systemctl --user show-environment | grep WIREPLUMBER_DEBUG`.
- Откат: удалить файл из `~/.config/wireplumber/wireplumber.conf.d/` и снова
  `systemctl --user restart wireplumber`.

## 60-minitoo-hfp-at-log.conf

Зачем: во время записи колонка, по-видимому, отправляет нажатие Play/Pause не по AVRCP,
а AT-командой по HFP (`AT+CHUP` и т. п.). PipeWire без модема отвечает ошибкой и пишет
об этом строкой уровня INFO, которую WirePlumber по умолчанию (уровень N) выбрасывает.
Фрагмент поднимает до INFO **только** тему `spa.bluez5.native`, без потока пакетов.

Без перезапуска (до следующего рестарта WirePlumber) то же самое делает
`wpctl set-log-level "N,spa.bluez5.native:I"`. Возвращать уровень после диагностики —
**только** этой же строкой или удалением файла. Не использовать `wpctl set-log-level -`:
он стирает и шаблон из фрагмента, и до перезапуска WirePlumber AT-строк в журнале не будет.
`scripts/diag/hfp-keys.sh` делает это правильно сам.

Проверка: нажать Play/Pause во время записи, затем
`journalctl --user -u wireplumber --since -10min -o short-precise TOPIC=spa.bluez5.native`.
Искать `modem not available: AT+...`.

## 61-minitoo-no-dummy-call.conf.disabled (эксперимент)

Гипотеза: из-за фиктивного звонка колонка во время записи превращает Play/Pause в
`AT+CHUP`, а громкость — в `AT+VGS`. Без звонка Play/Pause может снова приходить по AVRCP.
Риски: SCO может закрываться без звонка (обрыв записи); начало записи может обрезаться
сильнее; громкость во время записи перестанет приходить как `AT+VGS`.

Порядок: сначала попробовать без файла и без перезапуска — командой `pw-cli set-param`
из шапки файла (только пока колонка простаивает, id устройства меняется при каждом
переподключении), одна запись с нажатиями, затем вернуть `false`. Только если помогло —
поставить файл. Правило совпадает лишь с картой `bluez_card.B1_21_81_*`; лучше вписать
полный адрес своей колонки (с `_` вместо `:`) и поправить при смене адреса.

## 62-minitoo-cvsd.conf.disabled (эксперимент)

Ставить, только если во время записи
`pw-dump | grep -oE '"api.bluez5.(codec|profile)": "[^"]+"' | sort | uniq -c`
показывает `msbc`: адаптер Intel `8087:0a2a` официально не поддерживает wideband speech.
Сравнить число `corrupted SCO packet` (`scripts/diag/bt-health.sh`) и качество записи до/после.
Действует на все Bluetooth-гарнитуры этого компьютера; микрофон станет 8 кГц.
