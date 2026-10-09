#!/usr/bin/env bash
# usb-route.sh — можно ли вести звук и кнопки колонки по USB-кабелю, а Bluetooth оставить
# только экрану (исследования firmware / display-link, D12).
#
# Зачем: по стороннему описанию (divoom-minitoo-tools, наблюдение на Windows) по USB-C
# MiniToo — составное устройство JieLi 4C4A:4E55: накопитель «BR28 UDISK», USB Audio
# (динамик + микрофон) и HID-кнопки (Play/Pause, громкость). Если это так и на Linux,
# профиль BT не переключается в HFP вовсе — нет SCO, нет разрывов экрана (§5.2) и кнопка
# работает во время записи (§5.1). На Linux это НЕ проверено.
#
# Что делает: только читает списки устройств (lsusb, lsblk, ALSA, PipeWire,
# /proc/bus/input/devices, журнал ядра). Ничего не монтирует, не пишет на накопитель,
# колонке ничего не отправляет. Root не нужен (журнал ядра — группа adm/systemd-journal).
#
# Перед запуском: подключить колонку к серверу USB-C кабелем С ДАННЫМИ (не только зарядным).
# Запуск:  bash ~/hermes-minitoo-gadget/scripts/diag/usb-route.sh 2>&1 | tee ~/minitoo-usb-route.txt
set -u
# shellcheck source=_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

section "1. USB-устройство JieLi (VID 4c4a)"
if have lsusb; then
    cmd "lsusb -d 4c4a: || echo 'НЕ НАЙДЕНО: кабель только зарядный, не подключён или колонка не в USB-режиме'"
    cmd "lsusb -v -d 4c4a: 2>/dev/null | grep -E 'idVendor|idProduct|iManufacturer|iProduct|bInterfaceClass|bInterfaceSubClass|iInterface|tSamFreq|bNrChannels'"
else
    note "нет lsusb (пакет usbutils)"
fi

section "2. Накопитель BR28 UDISK (только список; НЕ монтировать и НЕ писать на него)"
cmd "lsblk -S -o NAME,VENDOR,MODEL,REV,TRAN | grep -iE 'NAME|BR28|UDISK'"

section "3. ALSA: есть ли карта Divoom/JieLi для записи и воспроизведения"
cmd "arecord -l"
cmd "aplay -l"

section "4. PipeWire/WirePlumber: узлы USB-звука"
cmd "wpctl status | grep -iE -A3 'divoom|jieli|minitoo|usb'"

section "5. Устройства ввода: HID-кнопки колонки по USB (Bus=0003)"
cmd "grep -B1 -A9 -iE 'Vendor=4c4a|divoom|jieli' /proc/bus/input/devices"

section "6. Журнал ядра о подключении"
journal_note
cmd "journalctl -k -b --no-pager | grep -iE '4c4a|jieli|br28|divoom|usb-storage|snd-usb' | tail -40"

section "Что дальше (вручную, по желанию)"
cat <<'EOF'
Если в п.1 есть 4c4a и в п.3/п.4 есть карта с микрофоном:
  1. Запись 5 с с USB-микрофона (имя узла из `wpctl status`, раздел Sources):
       pw-record --target <имя_узла> --rate 16000 --channels 1 /tmp/usbmic.wav   # говорить, Ctrl-C
       pw-play /tmp/usbmic.wav
  2. Кнопки: в п.5 найти eventN колонки и нажать Play/Pause (нужна группа input):
       evtest /dev/input/eventN        # в том числе во время записи из шага 1
  3. Экран по Bluetooth во время звука по USB: ~/hermes-minitoo-gadget/.venv/bin/hermes-minitoo status
     и посмотреть, обновляется ли экран, пока играет звук по USB.
Ничего не настраивать до результатов: переключение audio.input/audio.output в config.json
и отключение BT-профиля колонки — отдельный шаг после проверки.
Откат: просто отключить кабель.
Прислать: весь вывод скрипта и результаты шагов 1-3.
EOF
