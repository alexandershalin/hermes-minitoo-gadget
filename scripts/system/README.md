# Системные файлы (ставит владелец, нужен root)

Эти файлы кладутся в `/etc`, поэтому нужен `sudo`. Ставить их **сам владелец сервера**
в своём терминале — **никогда через бота** (пароль sudo не должен проходить через чат).
Ни один из них не шлёт ничего колонке: они меняют только поведение USB-адаптера и права
на устройство ввода на этом компьютере.

| Файл | Куда | Что решает |
|---|---|---|
| `modprobe.d/btusb-no-autosuspend.conf` | `/etc/modprobe.d/` | autosuspend адаптера после перезагрузки снова включён (HANDOFF §5.7) |
| `udev/91-bt-intel-0a2a-no-autosuspend.rules` | `/etc/udev/rules.d/` | то же, страховка именно для Intel `8087:0a2a` |
| `udev/70-minitoo-avrcp-acl.rules` | `/etc/udev/rules.d/` | необязательно: читать кнопки колонки без группы `input` |

Подробности, проверка и откат — в шапке каждого файла; ниже то же коротко.

## 1. USB autosuspend выключен навсегда

Почему ручное `echo on > /sys/.../power/control` не держится: драйвер `btusb` при каждом
probe (загрузка, переподключение адаптера, сброс USB) снова включает autosuspend, если
параметр модуля `enable_autosuspend` равен `Y` (так собрано ядро Ubuntu). Поэтому нужны
оба файла: параметр модуля (основное) и udev-правило, которое срабатывает уже **после**
probe `btusb` (страховка).

Установка:

```bash
cd ~/hermes-minitoo-gadget/scripts/system
sudo install -m 644 modprobe.d/btusb-no-autosuspend.conf /etc/modprobe.d/
sudo install -m 644 udev/91-bt-intel-0a2a-no-autosuspend.rules /etc/udev/rules.d/
sudo update-initramfs -u
sudo udevadm control --reload-rules
sudo udevadm trigger --action=change --subsystem-match=usb --attr-match=idVendor=8087
# затем перезагрузка (или, когда колонка не нужна: sudo modprobe -r btusb && sudo modprobe btusb)
```

Проверка (без root):

```bash
cat /sys/module/btusb/parameters/enable_autosuspend      # N
bash ~/hermes-minitoo-gadget/scripts/diag/bt-health.sh    # раздел "autosuspend": control=on
```

Если стоит TLP: в `/etc/tlp.conf` задать `USB_EXCLUDE_BTUSB=1`. Не запускать
`powertop --auto-tune` — он снова включит autosuspend.

Откат:

```bash
sudo rm /etc/modprobe.d/btusb-no-autosuspend.conf /etc/udev/rules.d/91-bt-intel-0a2a-no-autosuspend.rules
sudo update-initramfs -u && sudo udevadm control --reload-rules
# перезагрузка
```

Если адаптер заменён на другой, поправьте `idVendor`/`idProduct` в правиле 91 (их
показывает `lsusb`); файл modprobe.d действует на любой `btusb`-адаптер.

## 2. Кнопки колонки без группы `input` (необязательно)

`minitoo-talk-key` читает Play/Pause из `/dev/input/eventN` устройства
`Divoom MiniToo-... (AVRCP)`. Обычно для этого пользователя добавляют в группу `input`,
но она даёт чтение всех клавиатур и мышей. Правило 70 выдаёт ACL `r--` только на
устройство колонки и только одному пользователю. В файле стоит заглушка `USERNAME` —
команда ниже подставляет имя текущего пользователя:

```bash
sed "s/USERNAME/$USER/g" ~/hermes-minitoo-gadget/scripts/system/udev/70-minitoo-avrcp-acl.rules \
  | sudo tee /etc/udev/rules.d/70-minitoo-avrcp-acl.rules >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger --action=change --subsystem-match=input   # или переподключить колонку
```

Проверка: `grep -B1 -A9 'Vendor=05d6' /proc/bus/input/devices` (номер `eventN`), затем
`getfacl /dev/input/eventN` — должна быть строка `user:<имя>:r--`. Нужен пакет `acl`
(`command -v setfacl`).

Откат: `sudo rm /etc/udev/rules.d/70-minitoo-avrcp-acl.rules && sudo udevadm control --reload-rules`;
ACL пропадёт при следующем подключении колонки (сразу: `sudo setfacl -x u:$USER /dev/input/eventN`).
Если из группы `input` пользователя убирали — вернуть: `sudo usermod -aG input "$USER"`.
