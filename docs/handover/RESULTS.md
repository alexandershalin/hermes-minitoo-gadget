# Результаты проверок на железе

## 2026-10-09, сервер (Pentium N3700, Ubuntu 26.04, hci0 Intel 8087:0a2a)

### Вопрос 1: куда уходит Play/Pause во время записи — ПОДТВЕРЖДЕНО
Команда: `scripts/diag/hfp-keys.sh --debug` (240 с), в записи три нажатия.
Результат: колонка шлёт по HFP `AT+CHUP`, WirePlumber отвечает `+CME ERROR: 1`
(`RFCOMM receive command but modem not available`). Других AT-команд (`AT+BVRA`, `ATA`,
`AT+CKPD`) и событий джойстика/громкости в логе не было. `+CIEV: 2,1` на старте записи
и `+CIEV: 2,0` после закрытия SCO.
Вывод: гипотеза §5.1 подтверждена; AVRCP в профиле headset-head-unit не приходит.

### hfp_keys на реальной записи — РАБОТАЕТ
Конфигурация: юнит из репозитория, drop-in `MINITOO_HFP_KEYS=1`, остальные опции выключены.
Строка `RFCOMM receive command but modem not available` сохраняется при уровне
`spa.bluez5.native:I` (talk-key сам выставляет его через `wpctl set-log-level`).
Запись 15:17: старт по Play/Pause, `AT+CHUP` в 15:17:07 -> `action=stop`, запись
остановлена кнопкой, колонка ответила. Владелец: «все сработало».
Вывод: опция `hfp_keys` рабочая; можно оставить включённой.

### Состояние (прочее)
- Тесты: 254 passed. ruff на сервере не установлен.
- bt-health: 1039 `corrupted SCO packet`, TX errors hci0 = 114653; autosuspend
  закреплён только вручную (`control=on`), файлы из scripts/system не стоят (root, владелец).
- display-ack за сутки: 9340 повторов кадра, из них 9319 reconnect backoff,
  20 нет 0x8B ACK, 1 errno 104; 12 восстановлений, медиана 4,2 с.
- Не запускались: steady-hfp, address, usb-route; опции listen_preroll, hfp_gate,
  preroll_wait_sco не включались.

### Откат
`mv ~/.config/systemd/user/minitoo-talk-key.service.bak-main` обратно, удалить
`minitoo-talk-key.service.d`, `systemctl --user daemon-reload && restart minitoo-talk-key`.
