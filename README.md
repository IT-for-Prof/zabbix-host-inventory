# zabbix-host-inventory

Инвентарь установленного ПО, ОС и сети для Zabbix 7.0: два шаблона, которые пишут
данные в поля инвентаря хоста. Только встроенные ключи Zabbix agent 7.0+ (agent и
agent2) — ни UserParameter, ни system.run, ни скриптов на хостах.

| Файл | Шаблон | templateid (mon.itforprof.com) |
|---|---|---|
| `template/linux_inventory.yaml` | Linux inventory by Zabbix agent active | 14010 |
| `template/windows_inventory.yaml` | Windows inventory by Zabbix agent active | 14011 |

YAML — выгрузка `configuration.export` с production. Этот репозиторий —
источник правды; на сервере правки делаются импортом отсюда.

## Linux (14010)

Мастер-итем `system.sw.packages.get[.*]` (active, 1h) отдаёт JSON-манифест
пакетов dpkg/rpm, зависимые итемы раскладывают его:

- `swpkg.readable` → `software_full` — отсортированный список «name version (arch)»;
- `swpkg.summary` → `software` — «N packages (dpkg)»;
- `swpkg.arch` → `hw_arch` — преобладающая архитектура пакетов;
- `system.sw.os[name]` → `os_full` — PRETTY_NAME дистрибутива;
- `swpkg.version[nginx|openssl|openssh-server|docker-ce]` — версии для триггеров;
- LLD `swpkg.lld` → `swpkg.ver[{#PKG.NAME}]` по allowlist-макросу `{$SWINV.APPS}`:
  добавил пакет в макрос — итем появился на всём парке.

Сеть:

- `vfs.file.contents[/proc/net/fib_trie]` → `swinv.net.ip4` — JSON-список
  локальных IPv4 (строки `/32 host LOCAL`, без дублей, без `{$SWINV.IP.NOT_MATCHES}`,
  по умолчанию loopback и шлюзы мостов Docker `172.17–31.X.0.1`) → `swinv.net.list` →
  `host_networks`. Имён интерфейсов в fib_trie нет, поэтому мосты отсекаются эвристикой
  по адресу; мост с нестандартной подсетью дописывается в макрос хоста. Точка в макросе —
  `[.]`: он подставляется в JS-строку, где `\.` теряет слеш;
- `system.hw.macaddr` (1h) → `swinv.net.mac` — JSON `[{ifname, mac}]` без нулевых MAC и
  интерфейсов из `{$SWINV.IF.NOT_MATCHES}` → `swinv.net.macs` → `macaddress_a`.

MAC не через прототип в `net.if.discovery`: это правило живёт в стоковом «Linux by
Zabbix agent active» (правка пропадёт при обновлении), своё с тем же ключом на хост не
привяжется, а прототипы не пишут в инвентарь.

Требует агент **7.0+**: на 5.4/6.0 `system.sw.packages.get` — «Unknown metric».
ПО внутри контейнеров в манифест не попадает. `system.sw.os[short]` намеренно
не используется: читает `/proc/version_signature`, который есть только в Ubuntu.

## Windows (14011)

ПО — `registry.get[<Uninstall>,values,"^(DisplayName|DisplayVersion|Publisher|SystemComponent|ParentKeyName|ReleaseType)$"]`
по двум веткам (64-бит и WOW6432Node). Ключ обходит все подключи, включая записи MSI
под GUID и вложенные (`Uninstall\1C\8.3.25.1374`). Цепочка на ветку:

- мастер, history 0 — ответ на терминальном сервере 250–350 КБ: история режет текст
  на 64 КБ, предобработка зависимых получает его целиком;
- `swinv.apps[x64|x86]` — JSON `[{name, version, publisher}]` без системных компонентов
  (`SystemComponent=1`), обновлений (`ParentKeyName`, `ReleaseType`) и
  `{$SW.EXCLUDE.BUILTIN}` / `{$SW.EXCLUDE.MATCHES}`; обрезанный ответ или невалидный
  regex — ошибка итема, а не пустой список;
- `swinv.apps.list[x64]` → `software_full`, `swinv.apps.list[x86]` → `notes` — строки «Имя Версия»;
- `swinv.apps.summary[x64]` → `software` — «N apps (64-bit)», якорь триггера живости.

Сеть — `wmi.getall[root\cimv2,"select Description,MACAddress,IPAddress,IPSubnet from Win32_NetworkAdapterConfiguration where IPEnabled=True"]`,
сырой JSON хранится как текст (14d) → `swinv.net.list` («адаптер MAC: IP/префикс»,
без link-local) → `host_networks`; `swinv.net.macs` → `macaddress_a`.

WMI: производитель → `vendor`, модель → `model`, серийник BIOS → `serialno_a`.
Требует агент 7.0+ (`registry.get`).

Ключи `swinv.net.list` и `swinv.net.macs` одинаковы в обоих шаблонах — потребителю API
не нужно различать ОС. `macaddress_a` — 64 символа, влезает три адреса; полный список
в `swinv.net.list` (Windows) и `swinv.net.mac` (Linux).

Оба шаблона: `{$SWINV.NODATA}` (30h) — порог триггера «данные устарели».

## JSON для сверки с IPAM

Два вычисляемых элемента с одинаковым форматом в обоих шаблонах, тег `component: inventory`,
без триггеров. Формула склеивает источники через `concat(last(...))`, JS-предобработка
собирает JSON через `JSON.stringify`.

`swinv.net.ifaces` — раз в час, неизменное значение повторяется раз в 12 часов, история 14 дней:

```json
{"schema": 1, "hostname": "ifp-vm02",
 "interfaces": [
   {"name": "ens33", "mac": "00:0c:29:dd:3b:f7", "up": null, "kind": "physical", "addresses": ["172.21.4.16/24"]},
   {"name": "wg0", "mac": "", "up": null, "kind": "tunnel", "addresses": ["192.168.255.12/24"]}],
 "unassigned": []}
```

- `addresses: []` — у интерфейса нет IPv4; `mac: ""` — нет MAC (туннель); `null` — неизвестно;
- `kind`: `physical`, `bridge`, `tunnel`, `container`, `virtual` (vlan, bond, dummy); `lo` не выводится;
- `unassigned` — адреса, интерфейс которых не определён;
- источник без данных дольше `{$SWINV.SRC.NODATA}` (14h) или неразборчивый ответ — элемент в
  not supported, а не пустой список.

| Поле | Linux | Windows |
|---|---|---|
| интерфейсы | `/proc/net/dev` | `Win32_NetworkAdapter` с `NetConnectionID` |
| адрес/префикс | fib_trie (LOCAL) + самый длинный подключённый маршрут из `/proc/net/route` | `MSFT_NetIPAddress`, по `InterfaceIndex` |
| mac | `system.hw.macaddr`; у интерфейсов без IPv4 — `null` | `MACAddress` |
| up | всегда `null` | `NetConnectionStatus`: 2 — true; 0, 7 или отключён — false |
| kind | маркеры `/sys/devices/virtual/net` (`bridge`, `tun_flags`), имя, нулевой MAC виртуального интерфейса | описание адаптера, `PhysicalAdapter` |

На Linux адрес попадает в `unassigned`, если к нему нет подключённого маршрута (записывается
как /32) или одна сеть подключена к двум интерфейсам. Маска туннеля — по маршруту: у WireGuard
с `Address=…/32` и `AllowedIPs=…/24` будет /24. `/proc/net/route` читается как little-endian. Адрес /32 без подключённого маршрута (WireGuard, VPS с
адресом /32 и шлюзом onlink) остаётся в `unassigned`: связь адреса с интерфейсом ядро отдаёт только
через netlink, встроенного ключа для неё нет.

`swinv.hw` — раз в 12 часов: `{"schema": 1, "vendor", "model", "serial", "machine_id"}`, пустая
строка — значение недоступно или это заглушка SMBIOS («Default string», «To be filled by O.E.M.»). Linux: `/sys/class/dmi/id/sys_vendor`, `product_name`,
`/etc/machine-id`; serial без root недоступен. Windows: WMI-итемы производителя, модели и
серийника BIOS; `machine_id` пуст.

## Привязка к хостам

Вручную — шаблон на хост поверх стокового «Linux/Windows by Zabbix agent active».
На mon.itforprof.com — действиями авторегистрации:

- **96 «AUTO: Linux inventory»** — метаданные содержат `:os=` и не
  `:os=windows:`, либо `Linux` → шаблон 14010, группа 215;
- **97 «AUTO: Windows inventory»** — метаданные содержат `:os=windows:`
  или `Windows` → шаблоны 14011, 10299, 14047, группа 216.

Инвентарь хоста должен быть в режиме Automatic (глобально `default_inventory_mode=1`),
иначе записи в поля отбрасываются.

## Проверка

```sh
python3 tests/check.py   # нужны PyYAML и node
```

Берёт JS-предобработку прямо из YAML, подставляет макросы шаблона и гоняет на
синтетических ответах агентов. Реальные ответы в репозиторий не кладутся — в них
внутренние адреса.

## Импорт

Frontend: *Data collection → Templates → Import*, или API `configuration.import`
с `templates.updateExisting`, `items.createMissing/updateExisting`,
`discoveryRules.createMissing/updateExisting`, `deleteMissing: false`.

Грабли: частичный импорт с `updateExisting` затирает описание и макросы шаблона,
если их нет в источнике, — импортируйте файл целиком. Поле инвентаря
называется `hw_arch`, не `hardware_arch`.

## Выгрузка с сервера

```sh
curl -s https://mon.itforprof.com/api_jsonrpc.php \
  -H 'Content-Type: application/json-rpc' -H "Authorization: Bearer $ZBX_TOKEN" \
  -d '{"jsonrpc":"2.0","method":"configuration.export","id":1,
       "params":{"format":"yaml","options":{"templates":["14010"]}}}' \
  | jq -r .result > template/linux_inventory.yaml
```

## Автор и лицензия

Константин Тютюнник / Konstantin Tyutyunnik, https://itforprof.com  
Репозиторий: https://github.com/IT-for-Prof/zabbix-host-inventory  
Лицензия: MIT, см. [LICENSE](LICENSE).
