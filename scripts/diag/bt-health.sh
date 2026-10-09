#!/usr/bin/env bash
# bt-health.sh — здоровье Bluetooth-адаптера и ядра (HANDOFF §5.7; D6/D7/D8/D9 из
# исследования display-link).
#
# Что делает: только читает. Версии; адаптеры и их USB-устройства; autosuspend (параметр
# btusb, Kconfig, power/control каждого адаптера, установлены ли файлы scripts/system);
# журнал ядра: загрузка патча прошивки Intel, «corrupted SCO packet» (ошибка сборки
# USB-isoc на стороне компьютера, не радио), tx timeout, «Unable to disable scanning»;
# журнал bluetoothd; соседство с Wi-Fi той же комбо-карты и USB 3; совпадение ошибок
# сканирования с поисками minitoo-autoaddr. Колонке ничего не шлёт, root не нужен.
# Разделы с пометкой [нужна группа adm или systemd-journal] без этих групп будут пустыми.
#
# Запуск:  bash ~/hermes-minitoo-gadget/scripts/diag/bt-health.sh 2>&1 | tee ~/minitoo-bt-health.txt
#          (--boot -1 — смотреть журнал прошлой загрузки)
set -u
# shellcheck source=_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

BOOT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --boot) BOOT="${2:?нужно значение}"; shift 2 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
done

section "1. Версии"
cmd "uname -r"
cmd "bluetoothctl --version; pipewire --version 2>/dev/null | head -3; wireplumber --version 2>/dev/null | head -3"

section "2. Адаптеры и их USB-устройства"
for h in /sys/class/bluetooth/hci*; do
    [ -e "$h" ] || { note "адаптеров нет (/sys/class/bluetooth пуст)"; break; }
    dev="$(readlink -f "$h/device" 2>/dev/null)"
    usb="$dev"
    while [ -n "$usb" ] && [ "$usb" != "/" ] && [ ! -f "$usb/idVendor" ]; do usb="$(dirname "$usb")"; done
    if [ -f "$usb/idVendor" ]; then
        printf '%s: USB %s:%s %s  control=%s status=%s autosuspend_delay_ms=%s\n' "$(basename "$h")" \
            "$(cat "$usb/idVendor")" "$(cat "$usb/idProduct")" "$usb" \
            "$(cat "$usb/power/control" 2>/dev/null)" "$(cat "$usb/power/runtime_status" 2>/dev/null)" \
            "$(cat "$usb/power/autosuspend_delay_ms" 2>/dev/null)"
    else
        printf '%s: не USB (%s)\n' "$(basename "$h")" "$dev"
    fi
done
have lsusb && cmd "lsusb -d 8087:0a2a"
have hciconfig && cmd "hciconfig -a"
note "8087:0a2a — Bluetooth-часть комбо-карты Intel 7265/3165 (Wi-Fi на том же чипе и антеннах)"

section "3. Autosuspend (D7): ожидается control=on и параметр N"
cmd "cat /sys/module/btusb/parameters/enable_autosuspend"
cmd "grep BT_HCIBTUSB_AUTOSUSPEND /boot/config-\$(uname -r)"
cmd "ls -l /etc/modprobe.d/btusb-no-autosuspend.conf /etc/udev/rules.d/91-bt-intel-0a2a-no-autosuspend.rules"
cmd "grep -hE '^[[:space:]]*USB_(EXCLUDE_BTUSB|AUTOSUSPEND)' /etc/tlp.conf /etc/tlp.d/*.conf 2>/dev/null || echo '(TLP не настроен)'"
note "control=auto или параметр Y — поставить файлы из scripts/system (README там)"

section "4. Журнал ядра, загрузка $BOOT (D6)  [нужна группа adm или systemd-journal]"
journal_note
cmd "journalctl -k -b $BOOT --no-pager | grep -iE 'Bluetooth: hci|btusb|btintel|Intel.*(firmware|fw|patch)|ibt-' | head -40"
note "'completed and deactivated' в строках про патч Intel означает, что патч прошивки НЕ применён"
cmd "journalctl -k -b $BOOT --no-pager | grep -c 'corrupted SCO packet'"
cmd "journalctl -k -b $BOOT --no-pager | grep -iE 'corrupted SCO|tx timeout|command .* tx timeout|disable scanning|hci0: .*(fail|error|timeout)' | tail -40"

section "5. bluetoothd, загрузка $BOOT  [нужна группа adm или systemd-journal]"
cmd "journalctl -u bluetooth -b $BOOT --no-pager | grep -iE 'error|fail|getpeername|lost|refused|timeout' | tail -40"
note "'getpeername: Transport endpoint is not connected' — SDP-запрос по каналу, который другая сторона уже закрыла"

section "6. Wi-Fi той же карты и источники помех (D8)"
if have iw; then
    cmd "iw dev"
    for i in /sys/class/net/wl*; do
        [ -e "$i" ] && cmd "iw dev $(basename "$i") link"
    done
else
    note "нет iw"
fi
cmd "cat /sys/module/iwlwifi/parameters/bt_coex_active 2>/dev/null || echo '(iwlwifi не загружен)'"
cmd "rfkill list"
have lsusb && cmd "lsusb -t | grep -E '5000M|10000M|20000M' || echo '(USB 3 устройств нет)'"
note "если Wi-Fi этой карты не нужен (сервер по кабелю) — его отключение освобождает антенну для BT;"
note "Wi-Fi на 2,4 ГГц делит эфир с BT; bt_coex_active=0 НЕ ставить; USB 3 диски/кабели — подальше от антенн"

section "7. Ошибки сканирования рядом с поисками minitoo-autoaddr (D9)  [ядро: нужна группа]"
cmd "journalctl -k -b $BOOT -o short-iso --no-pager | grep 'disable scanning' | tail -20"
cmd "journalctl --user -u minitoo-autoaddr -b $BOOT -o short-iso --no-pager | grep -E 'в эфире|пробую|сопряжено|подключено|не удалось|config:|поиск' | tail -20"
note "'Unable to disable scanning: -16' бывает и без autoaddr (фоновое LE-сканирование, подключения)"

section "8. HFP-события WirePlumber, загрузка $BOOT (журнал своего пользователя)"
cmd "journalctl --user -u wireplumber -b $BOOT --no-pager | grep -ciE 'lost RFCOMM'"
cmd "journalctl --user -u wireplumber -b $BOOT --no-pager | grep -iE 'lost RFCOMM|SCO socket|msbc|cvsd|alt6' | tail -20"
note "часть этих строк уровня INFO: видны только с фрагментом 60-minitoo-hfp-at-log.conf или во время hfp-keys.sh"
note "кодек HFP во время записи: pw-dump | grep -oE '\"api.bluez5.(codec|profile)\": \"[^\"]+\"' | sort | uniq -c"

section "Прислать"
note "весь вывод (адресов колонки и токенов в нём нет, кроме строк autoaddr)."
