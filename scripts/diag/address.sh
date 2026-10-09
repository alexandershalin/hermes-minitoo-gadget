#!/usr/bin/env bash
# address.sh — почему у колонки меняется BT-адрес и одна ли это колонка (HANDOFF §5.8;
# исследование mac-change).
#
# Что делает по умолчанию: только чтение. Адрес берётся из config.json (minitoo.address),
# префикс (первые три байта) — из него же. Печатает записи BlueZ о колонках
# (Name/Alias/тип адреса/Class/Modalias/UUID/Paired/Connected), свойства D-Bus, устройство
# ввода AVRCP (Vendor/Product), имена узлов PipeWire, формы адреса в config.json и историю
# minitoo-autoaddr. Root не нужен; ничего не меняет и колонке ничего не шлёт.
#
#   --radio   дополнительно (с подтверждением): поиск BR/EDR и LE по 25 с, SDP-запрос к колонке
#             и запрос имени (hcitool name). В колонку ничего не пишется, но поиск занимает
#             радио: на время лучше остановить minitoo-autoaddr и hermes-minitoo (команды
#             печатаются; скрипт их сам не выполняет).
# Команды, которым нужен root, только ПЕЧАТАЮТСЯ — их владелец запускает сам.
#
# Запуск:  bash ~/hermes-minitoo-gadget/scripts/diag/address.sh [--radio] [--hci 0] 2>&1 | tee ~/minitoo-address.txt
set -u
# shellcheck source=_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

RADIO=0
HCI=0
while [ $# -gt 0 ]; do
    case "$1" in
        --radio) RADIO=1; shift ;;
        --hci) HCI="${2:?нужно значение}"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
done
need_mac

section "0. Адрес из $CONF"
note "minitoo.address = $MAC  (префикс $PFX)"
for key in input output; do
    value="$(conf_get audio "$key" 2>/dev/null)" || value="(нет)"
    note "audio.$key = $value"
done
note "форма с ':' — loopback-узлы WirePlumber; форма с '_' (и .N) — настоящие узлы BlueZ"

section "1. Записи BlueZ с MiniToo/Divoom/$PFX"
cmd "bluetoothctl devices | grep -iE 'minitoo|divoom|$PFX'"
CANDS="$(bluetoothctl devices 2>/dev/null | grep -iE "minitoo|divoom|$PFX" | awk '{print $2}' | sort -u)"
for a in $CANDS; do
    cmd "bluetoothctl info $a | grep -E 'Device|Name|Alias|Class|Icon|Paired|Bonded|Trusted|Connected|Modalias|UUID|AdvertisingFlags|ManufacturerData|PreferredBearer'"
    cmd "busctl --system introspect org.bluez /org/bluez/hci$HCI/dev_${a//:/_} org.bluez.Device1 2>&1 | grep -E '^\\.(Address|AddressType|Name|Alias|Class|Modalias|Paired|Bonded|Trusted|Connected|ServicesResolved|AdvertisingFlags|RSSI) '"
done
[ -n "$CANDS" ] || note "записей нет"
note "Modalias v05D6p000A = DID JieLi; d0240 — версия (может меняться с прошивкой)."
note "AddressType random бывает только у LE; у BR/EDR-колонки должен быть public."

section "2. Устройство ввода AVRCP (Vendor/Product не зависят от имени и адреса)"
cmd "grep -B1 -A9 'Bus=0005' /proc/bus/input/devices"

section "3. Версии и узлы PipeWire"
cmd "wireplumber --version 2>/dev/null | head -3; pipewire --version 2>/dev/null | head -3; bluetoothctl --version"
cmd "wpctl status | grep -iA3 minitoo"
cmd "pw-dump 2>/dev/null | python3 -I -c 'import json, sys
for o in json.load(sys.stdin):
    p = (o.get(\"info\") or {}).get(\"props\") or {}
    if o.get(\"type\") == \"PipeWire:Interface:Node\" and \"bluez\" in str(p.get(\"node.name\", \"\")):
        print(p.get(\"node.name\"), \"|\", p.get(\"media.class\"), \"|\", p.get(\"api.bluez5.address\"), \"|\", p.get(\"api.bluez5.internal\"))'"

section "4. История minitoo-autoaddr"
cmd "journalctl --user -u minitoo-autoaddr --no-pager | grep -E 'в эфире|config:|сопряжено|подключено|не удалось|не опознано|поиск' | tail -60"

if [ "$RADIO" -eq 1 ]; then
    section "5. Радио: поиск и SDP (по подтверждению)"
    cat <<EOF
Будет: bluetoothctl --timeout 25 scan bredr, затем scan le, sdptool browse $MAC, hcitool name $MAC.
Колонке ничего не пишется, но поиск занимает радио адаптера. Лучше сначала в другом терминале:
  systemctl --user stop minitoo-autoaddr hermes-minitoo
и после: systemctl --user start hermes-minitoo minitoo-autoaddr
Подключённая колонка обычно в поиске не видна; проверить, живы ли оба адреса, можно только
отключив её (bluetoothctl disconnect $MAC) — это владелец решает сам, скрипт этого не делает.
EOF
    if confirm "Запустить поиск сейчас?"; then
        cmd "bluetoothctl --timeout 25 scan bredr | grep -iE '$PFX|minitoo|divoom'"
        cmd "bluetoothctl info $MAC | grep -E 'Name|Alias'"
        cmd "bluetoothctl --timeout 25 scan le | grep -iE '$PFX|minitoo|divoom'"
        cmd "bluetoothctl info $MAC | grep -E 'Name|Alias|AdvertisingFlags'"
        if have sdptool; then
            cmd "timeout 30 sdptool browse $MAC | grep -iE 'Service Name|UUID|Channel|0xaf30|49535343|fe010000|ATT|GATT'"
        else
            note "нет sdptool"
        fi
        if have hcitool; then cmd "timeout 15 hcitool name $MAC"; else note "нет hcitool"; fi
        note "Если Name стал '-App' только после scan le — вероятно, это BLE-имя того же устройства, а не другая колонка."
    else
        note "поиск пропущен"
    fi
fi

section "Команды с root — владелец запускает сам, не через бота"
cat <<EOF
# Тип каждого найденного устройства (BR/EDR или LE Public/Random), имя и EIR:
sudo btmgmt --index $HCI find
# Захват эфира во время поиска (в другом терминале запустить address.sh --radio), Ctrl-C:
sudo btmon -w ~/minitoo-scan.btsnoop
btmon -r ~/minitoo-scan.btsnoop | grep -iE -A15 'Inquiry Result|Extended Inquiry|LE Advertising Report' | grep -iE 'Address|Name|Flags|Company|Data|Class'
# Записи BlueZ о колонках: двухрежимность, версия прошивки (Version), даты (%w — создание); ключи не выводятся:
sudo sh -c 'for d in /var/lib/bluetooth/*/$PFX*; do echo "== \$d"; stat -c "создан %w  изменён %y" "\$d"; grep -E "^\[|^(Name|Class|SupportedTechnologies|AddressType|Source|Vendor|Product|Version|Trusted)=" "\$d/info"; done'
# Откуда UUID 0xAF30 / 49535343-... / FE010000-...: SDP-записи и GATT в кэше BlueZ:
sudo sh -c 'for c in /var/lib/bluetooth/*/cache/$PFX*; do echo "== \$c"; grep -ioE "af30|49535343[0-9a-f-]{0,28}|fe010000[0-9a-f-]{0,28}" "\$c" | sort | uniq -c; done'
EOF

section "Проверка повторяемости (самый дешёвый решающий тест)"
cat <<EOF
Приложение Divoom на телефоне закрыть (лучше выключить на телефоне Bluetooth). Затем 3 раза:
  1) выключить колонку кнопкой питания, подождать 10 с, включить;
  2) подождать 1-2 минуты (сторож minitoo-autoaddr может сам перепарить), затем
       bluetoothctl devices | grep -iE 'minitoo|divoom|$PFX'
       grep -o '"address": *"[^"]*"' $CONF
  3) записать адрес, который колонка показала в этот раз (новая строка в devices или
     сообщение 'config: X -> Y' в journalctl --user -u minitoo-autoaddr).
Как читать:
  новый адрес после КАЖДОГО включения — прошивка не сохраняет адрес (сбой записи настроек
    или генерация при загрузке): сторож нужен постоянно, лучше с IDENTIFY;
  адрес не меняется — смена 8 октября была разовой (сброс, обновление прошивки, разряд).
Вопросы владельцу: 8 октября приложение Divoom обновляло прошивку? колонку сбрасывали
(долгое нажатие / комбинация кнопок)? батарея садилась полностью?
Прислать: весь вывод скрипта (в нём адреса колонок, но нет ключей и токена) и записанные адреса.
EOF
