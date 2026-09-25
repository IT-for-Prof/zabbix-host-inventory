#!/usr/bin/env python3
"""Run every JavaScript preprocessing step from the templates against synthetic agent output.

Usage: python3 tests/check.py   (needs PyYAML and node)
The JS is taken from the YAML as is; {$MACRO} values are substituted from the template's macros,
the way Zabbix does it, so a broken regex in a macro default is caught here too.
"""
import json
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load(name):
    t = yaml.safe_load((ROOT / 'template' / name).read_text())['zabbix_export']['templates'][0]
    macros = {m['macro']: m.get('value', '') for m in t.get('macros', [])}
    js = {}
    for i in t['items']:
        for p in i.get('preprocessing', []):
            if p['type'] == 'JAVASCRIPT':
                code = p['parameters'][0]
                for k, v in macros.items():
                    code = code.replace(k, v)
                js[i['key']] = code
    return js


def run(code, value):
    """Return ('ok', result) or ('throw', message), executing like Zabbix: function(value) {code}."""
    src = ('var f = new Function("value", %s);'
           'try { console.log(JSON.stringify(["ok", String(f(%s))])); }'
           'catch (e) { console.log(JSON.stringify(["throw", String(e)])); }') % (json.dumps(code), json.dumps(value))
    out = subprocess.run(['node', '-e', src], capture_output=True, text=True, check=True).stdout
    return tuple(json.loads(out))


fails = 0


def check(label, got, want):
    global fails
    if got != want:
        fails += 1
        print(f'FAIL {label}\n  got:  {got!r}\n  want: {want!r}')
    else:
        print(f'ok   {label}')


# ---------- Windows ----------
win = load('windows_inventory.yaml')
U = 'HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\'


def rv(sub, name, data):
    return {'fullkey': U + sub, 'lastsubkey': sub.split('\\')[-1], 'name': name, 'data': data, 'type': 'REG_SZ'}


reg = json.dumps([
    rv('{11111111-2222-3333-4444-555555555555}', 'DisplayName', 'MSI App'),         # MSI entry under GUID: kept
    rv('{11111111-2222-3333-4444-555555555555}', 'DisplayVersion', '2.0'),
    rv('{11111111-2222-3333-4444-555555555555}', 'Publisher', 'ACME'),
    rv('1C\\8.3.25.1374', 'DisplayName', '1C:Enterprise 8 (8.3.25.1374)'),           # nested subkey, version in name
    rv('1C\\8.3.25.1374', 'DisplayVersion', '8.3.25.1374'),
    rv('Connection Manager', 'SystemComponent', 1),                                 # system component, no name
    rv('Hidden', 'DisplayName', 'Hidden Component'),                                # system component with name
    rv('Hidden', 'SystemComponent', 1),
    rv('KB5000001', 'DisplayName', 'Security Update for X (KB5000001)'),            # update of a parent
    rv('KB5000001', 'ParentKeyName', 'X'),
    rv('KB123', 'DisplayName', 'KB123'),                                            # {$SW.EXCLUDE.BUILTIN}
    rv('7-Zip', 'DisplayName', '7-Zip 19.00 (x64)'),
    rv('7-Zip', 'DisplayVersion', '19.00'),
    rv('7-Zip-dup', 'DisplayName', '7-Zip 19.00 (x64)'),                            # duplicate name+version
    rv('7-Zip-dup', 'DisplayVersion', '19.00'),
    rv('NoVersion', 'DisplayName', 'zeta tool'),
])
st, apps = run(win['swinv.apps[x64]'], reg)
check('win apps: parses', st, 'ok')
check('win apps: names, order, filters', [a['name'] for a in json.loads(apps)],
      ['1C:Enterprise 8 (8.3.25.1374)', '7-Zip 19.00 (x64)', 'MSI App', 'zeta tool'])
check('win apps: publisher kept', json.loads(apps)[2], {'name': 'MSI App', 'version': '2.0', 'publisher': 'ACME'})
check('win list: version appended only when absent from name', run(win['swinv.apps.list[x64]'], apps),
      ('ok', '1C:Enterprise 8 (8.3.25.1374)\n7-Zip 19.00 (x64)\nMSI App 2.0\nzeta tool'))
check('win summary', run(win['swinv.apps.summary[x64]'], apps), ('ok', '4 apps (64-bit)'))
check('win apps: empty hive is a legal empty list', run(win['swinv.apps[x64]'], '[]'), ('ok', '[]'))
check('win apps: truncated payload throws', run(win['swinv.apps[x64]'], reg[:len(reg) // 2])[0], 'throw')
check('win apps: invalid macro regex throws',
      run(win['swinv.apps[x64]'].replace("new RegExp('(?!)')", "new RegExp('(')"), reg)[0], 'throw')

nic = json.dumps([
    {'Description': 'Intel NIC', 'MACAddress': 'AA:BB:CC:DD:EE:FF',
     'IPAddress': ['10.1.2.3', 'fe80::1', '2001:db8::5', '169.254.1.1'],
     'IPSubnet': ['255.255.252.0', '64', '48', '255.255.0.0']},
    {'Description': 'VPN', 'MACAddress': None, 'IPAddress': None, 'IPSubnet': None},
])
check('win nic list', run(win['swinv.net.list'], nic),
      ('ok', 'Intel NIC aa:bb:cc:dd:ee:ff: 10.1.2.3/22, 2001:db8::5/48\nVPN : '))
check('win nic macs', run(win['swinv.net.macs'], nic), ('ok', 'aa:bb:cc:dd:ee:ff'))
check('win nic: non-JSON throws', run(win['swinv.net.list'], 'ERROR')[0], 'throw')

# ---------- Linux ----------
lin = load('linux_inventory.yaml')
fib = '''Main:
  +-- 0.0.0.0/0 3 0 5
     |-- 0.0.0.0
        /0 universe UNICAST
     |-- 203.0.113.10
        /32 host LOCAL
     +-- 127.0.0.0/8 2 0 2
        +-- 127.0.0.0/31 1 0 0
           |-- 127.0.0.0
              /8 host LOCAL
           |-- 127.0.0.1
              /32 host LOCAL
     +-- 192.0.2.0/24 2 0 2
           |-- 192.0.2.0
              /32 link BROADCAST
              /24 link UNICAST
           |-- 192.0.2.9
              /32 host LOCAL
        |-- 192.0.2.255
           /32 link BROADCAST
Local:
  +-- 0.0.0.0/0 3 0 5
           |-- 192.0.2.9
              /32 host LOCAL
     |-- 203.0.113.10
        /32 host LOCAL
'''
st, ip4 = run(lin['swinv.net.ip4'], fib)
check('linux ip4: LOCAL /32 only, deduped, no 127.*, numeric order', (st, ip4), ('ok', '["192.0.2.9","203.0.113.10"]'))
check('linux ip list', run(lin['swinv.net.list'], ip4), ('ok', '192.0.2.9, 203.0.113.10'))
docker = fib.replace('Local:', """     +-- 172.17.0.0/16 2 0 2
           |-- 172.17.0.1
              /32 host LOCAL
     +-- 172.21.0.0/16 2 0 2
           |-- 172.21.0.1
              /32 host LOCAL
           |-- 172.21.4.9
              /32 host LOCAL
     +-- 172.200.0.0/16 2 0 2
           |-- 172.200.0.1
              /32 host LOCAL
Local:""")
check('linux ip4: Docker bridge gateways (172.17-31.x.0.1) dropped, real 172.21.4.9 kept',
      run(lin['swinv.net.ip4'], docker), ('ok', '["172.21.4.9","172.200.0.1","192.0.2.9","203.0.113.10"]'))
check('linux ip4: loopback-only throws', run(lin['swinv.net.ip4'], 'Main:\n     |-- 127.0.0.1\n        /32 host LOCAL\n')[0], 'throw')

macs = '[docker0] 02:42:00:00:00:01, [eth0] 02:00:00:00:00:0A, [wg] 00:00:00:00:00:00, [veth12ab] 02:00:00:00:00:0c, [ens34] 02:00:00:00:00:0b'
st, mac = run(lin['swinv.net.mac'], macs)
check('linux mac json: zero MAC and container interfaces dropped', (st, json.loads(mac)),
      ('ok', [{'ifname': 'eth0', 'mac': '02:00:00:00:00:0a'}, {'ifname': 'ens34', 'mac': '02:00:00:00:00:0b'}]))
check('linux macs', run(lin['swinv.net.macs'], mac), ('ok', '02:00:00:00:00:0a, 02:00:00:00:00:0b'))
check('linux mac: unexpected shape throws', run(lin['swinv.net.mac'], 'garbage')[0], 'throw')

# ---------- swinv.net.ifaces / swinv.hw ----------
# WMI samples use the shape wmi.getall really returns: booleans as "True"/"False" strings, null properties absent.
# The formula of the calculated item is evaluated the way Zabbix does it: count() -> 0 for a stale source,
# a positive number otherwise; last() -> the source value; the result goes into the item's JS.
# nodata() is not used: it errors for its whole period after a server restart, which would make
# the items unsupported for 14h after every restart.


def split_args(s):
    out, depth, quote, cur = [], 0, False, ''
    for ch in s:
        if ch == '"':
            quote = not quote
        elif not quote and ch in '([':
            depth += 1
        elif not quote and ch in ')]':
            depth -= 1
        if ch == ',' and depth == 0 and not quote:
            out.append(cur)
            cur = ''
        else:
            cur += ch
    return out + [cur]


def template_items(name):
    t = yaml.safe_load((ROOT / 'template' / name).read_text())['zabbix_export']['templates'][0]
    return {i['key']: i for i in t['items']}


def calc(name, key, sources, stale=()):
    items = template_items(name)
    f = items[key]['params']
    out = ''
    for a in split_args(f[len('concat('):-1]):
        if a.startswith('"'):
            out += a[1:-1]
            continue
        fn, ref = a.split('(//', 1)
        k = split_args(ref[:-1])[0]
        if fn == 'count':
            check(f'{name}: {key} source exists and keeps history ({k[:40]})',
                  k in items and str(items[k].get('history')) != '0', True)
            out += '0' if k in stale else '2'
        else:
            out += sources[k]
    return out


def calc_run(js, name, key, sources, stale=()):
    return run(js[key], calc(name, key, sources, stale))


DEV = """Inter-|   Receive
 face |bytes    packets
    lo: 1 2 3
  eth0:123 4 5
  eth1: 0 0 0
  eth2: 0 0 0
  ens4: 0 0 0
   wg0: 1 1 1
docker0: 1 1 1
veth1a2b: 1 1 1
   br0: 1 1 1
 as0t0: 1 1 1
"""
SYSFS = json.dumps([{'pathname': '/sys/devices/virtual/net/' + p} for p in
                    ['lo/uevent', 'wg0/uevent', 'docker0/uevent', 'docker0/bridge', 'br0/uevent', 'br0/bridge', 'as0t0/uevent']])
FIB = fib.replace('Local:', """     +-- 10.9.0.0/24 2 0 2
           |-- 10.9.0.2
              /32 host LOCAL
     +-- 198.51.100.0/24 2 0 2
           |-- 198.51.100.5
              /32 host LOCAL
     +-- 172.17.0.0/16 2 0 2
           |-- 172.17.0.1
              /32 host LOCAL
Local:""")
ROUTE = """Iface	Destination	Gateway 	Flags	RefCnt	Use	Metric	Mask		MTU	Window	IRTT
eth0	00000000	010200C0	0003	0	0	100	00000000	0	0	0
eth0	000200C0	00000000	0001	0	0	100	00FFFFFF	0	0	0
eth0	010200C0	00000000	0005	0	0	100	FFFFFFFF	0	0	0
wg0	0000090A	00000000	0001	0	0	0	00FFFFFF	0	0	0
eth1	006433C6	00000000	0001	0	0	0	00FFFFFF	0	0	0
eth2	006433C6	00000000	0001	0	0	0	00FFFFFF	0	0	0
docker0	000011AC	00000000	0001	0	0	0	0000FFFF	0	0	0
"""
MACS = ('[eth0] 02:00:00:00:00:0A, [eth0:1] 02:00:00:00:00:0A, [wg0] 00:00:00:00:00:00, '
        '[docker0] 02:42:00:00:00:01, [eth1] 02:00:00:00:00:0B, [as0t0] 00:00:00:00:00:00')
VDIR = next(k for k in template_items('linux_inventory.yaml') if k.startswith('vfs.dir.get['))
check('linux: list-valued sources of swinv.net.ifaces are TEXT (CHAR cuts at 255: six Docker bridges overflow macaddr)',
      [k for k, i in template_items('linux_inventory.yaml').items()
       if k in ('system.hw.macaddr', 'vfs.file.contents[/proc/net/dev]', 'vfs.file.contents[/proc/net/route]',
                'vfs.file.contents[/proc/net/fib_trie]', VDIR) and i['value_type'] != 'TEXT'], [])
check('linux sysfs markers', run(lin[VDIR], SYSFS), ('ok', 'as0t0 uevent\nbr0 bridge uevent\ndocker0 bridge uevent\nlo uevent\nwg0 uevent'))
check('linux sysfs: output without lo throws', run(lin[VDIR], '[]')[0], 'throw')
lsrc = {
    'system.hostname[host]': 'ifp-vm02',
    'vfs.file.contents[/proc/net/dev]': run(lin['vfs.file.contents[/proc/net/dev]'], DEV)[1],
    VDIR: run(lin[VDIR], SYSFS)[1],
    'vfs.file.contents[/proc/net/fib_trie]': FIB,
    'vfs.file.contents[/proc/net/route]': ROUTE,
    'system.hw.macaddr': MACS,
}
st, v = calc_run(lin, 'linux_inventory.yaml', 'swinv.net.ifaces', lsrc)
check('linux ifaces: parses', st, 'ok')
v = json.loads(v) if st == 'ok' else {}
check('linux ifaces: header and unassigned (no route -> /32, one network on two interfaces)',
      {k: v.get(k) for k in ('schema', 'hostname', 'unassigned')},
      {'schema': 1, 'hostname': 'ifp-vm02', 'unassigned': ['198.51.100.5/24', '203.0.113.10/32']})
check('linux ifaces: per interface', {i['name']: (i['mac'], i['up'], i['kind'], i['addresses']) for i in v.get('interfaces', [])}, {
    'as0t0': ('', None, 'tunnel', []),                        # virtual, zero MAC, no tun_flags (OpenVPN AS)
    'br0': (None, None, 'bridge', []),
    'docker0': ('02:42:00:00:00:01', None, 'container', ['172.17.0.1/16']),
    'ens4': (None, None, 'physical', []),
    'eth0': ('02:00:00:00:00:0a', None, 'physical', ['192.0.2.9/24']),
    'eth1': ('02:00:00:00:00:0b', None, 'physical', []),
    'eth2': (None, None, 'physical', []),
    'veth1a2b': (None, None, 'container', []),
    'wg0': ('', None, 'tunnel', ['10.9.0.2/24']),
})
check('linux ifaces: stale source throws',
      calc_run(lin, 'linux_inventory.yaml', 'swinv.net.ifaces', lsrc, stale={'system.hw.macaddr'})[0], 'throw')
check('linux ifaces: fib_trie without LOCAL throws', calc_run(
    lin, 'linux_inventory.yaml', 'swinv.net.ifaces', {**lsrc, 'vfs.file.contents[/proc/net/fib_trie]': 'Main:\n'})[0], 'throw')
check('linux ifaces: unexpected route header throws', calc_run(
    lin, 'linux_inventory.yaml', 'swinv.net.ifaces', {**lsrc, 'vfs.file.contents[/proc/net/route]': 'x'})[0], 'throw')
check('linux hw: N/A becomes empty, serial always empty', calc_run(lin, 'linux_inventory.yaml', 'swinv.hw', {
    'vfs.file.contents[/sys/class/dmi/id/sys_vendor]': 'VMware, Inc.',
    'vfs.file.contents[/sys/class/dmi/id/product_name]': 'N/A',
    'vfs.file.contents[/etc/machine-id]': '0123456789abcdef0123456789abcdef'}),
    ('ok', '{"schema":1,"vendor":"VMware, Inc.","model":"","serial":"","machine_id":"0123456789abcdef0123456789abcdef"}'))

witems = template_items('windows_inventory.yaml')
NA_KEY = next(k for k in witems if 'Win32_NetworkAdapter ' in k)
IP_KEY = next(k for k in witems if 'MSFT_NetIPAddress' in k)
adapters = [
    {'InterfaceIndex': 12, 'NetConnectionID': 'Ethernet0', 'Description': 'Intel(R) PRO "Server" \\ NIC',
     'MACAddress': '00:0C:29:9A:CF:39', 'NetConnectionStatus': 2, 'NetEnabled': 'True', 'PhysicalAdapter': 'True'},
    {'InterfaceIndex': 15, 'NetConnectionID': 'wg0', 'Description': 'WireGuard Tunnel',   # no MACAddress property
     'NetConnectionStatus': 2, 'NetEnabled': 'True', 'PhysicalAdapter': 'False'},
    {'InterfaceIndex': 20, 'NetConnectionID': 'Ethernet1', 'Description': 'vmxnet3 Ethernet Adapter',
     'MACAddress': '00:0C:29:00:00:01', 'NetConnectionStatus': 7, 'NetEnabled': 'True', 'PhysicalAdapter': 'True'},
    {'InterfaceIndex': 25, 'NetConnectionID': 'Ethernet2', 'Description': 'Microsoft Loopback Adapter',  # disabled, virtual
     'MACAddress': '02:00:4C:4F:4F:50', 'NetConnectionStatus': 0, 'NetEnabled': 'False', 'PhysicalAdapter': 'False'},
]
addrs = [{'InterfaceIndex': 12, 'IPAddress': '172.21.4.16', 'PrefixLength': 24},
         {'InterfaceIndex': 15, 'IPAddress': '192.168.255.12', 'PrefixLength': 32},
         {'InterfaceIndex': 1, 'IPAddress': '127.0.0.1', 'PrefixLength': 8},
         {'InterfaceIndex': 30, 'IPAddress': '10.0.0.5', 'PrefixLength': 32}]
wsrc = {'system.hostname[host]': 'ET-TS02', NA_KEY: json.dumps(adapters), IP_KEY: json.dumps(addrs)}
st, v = calc_run(win, 'windows_inventory.yaml', 'swinv.net.ifaces', wsrc)
check('win ifaces: quote and backslash in adapter description keep JSON valid', st, 'ok')
check('win ifaces', json.loads(v) if st == 'ok' else v, {'schema': 1, 'hostname': 'ET-TS02', 'interfaces': [
    {'name': 'Ethernet0', 'mac': '00:0c:29:9a:cf:39', 'up': True, 'kind': 'physical', 'addresses': ['172.21.4.16/24']},
    {'name': 'Ethernet1', 'mac': '00:0c:29:00:00:01', 'up': False, 'kind': 'physical', 'addresses': []},
    {'name': 'Ethernet2', 'mac': '02:00:4c:4f:4f:50', 'up': False, 'kind': 'virtual', 'addresses': []},
    {'name': 'wg0', 'mac': '', 'up': True, 'kind': 'tunnel', 'addresses': ['192.168.255.12/32']},
], 'unassigned': ['10.0.0.5/32']})
check('win ifaces: address without PrefixLength throws (WMI dropped the property)', calc_run(
    win, 'windows_inventory.yaml', 'swinv.net.ifaces',
    {**wsrc, IP_KEY: json.dumps([{'InterfaceIndex': 12, 'IPAddress': '172.16.70.11'}])})[0], 'throw')
check('win ifaces: no IPv4 at all throws',
      calc_run(win, 'windows_inventory.yaml', 'swinv.net.ifaces', {**wsrc, IP_KEY: '[]'})[0], 'throw')
check('win ifaces: stale source throws', calc_run(
    win, 'windows_inventory.yaml', 'swinv.net.ifaces', wsrc, stale={'system.hostname[host]'})[0], 'throw')
check('win hw: N/A serial becomes empty', calc_run(win, 'windows_inventory.yaml', 'swinv.hw', {
    'wmi.get[root\\cimv2,"select Manufacturer from Win32_ComputerSystem"]': 'VMware, Inc.',
    'wmi.get[root\\cimv2,"select Model from Win32_ComputerSystem"]': 'VMware Virtual Platform',
    'wmi.get[root\\cimv2,"select SerialNumber from Win32_BIOS"]': 'N/A'}),
    ('ok', '{"schema":1,"vendor":"VMware, Inc.","model":"VMware Virtual Platform","serial":"","machine_id":""}'))
check('win hw: SMBIOS placeholder serial becomes empty', calc_run(win, 'windows_inventory.yaml', 'swinv.hw', {
    'wmi.get[root\\cimv2,"select Manufacturer from Win32_ComputerSystem"]': 'Micro-Star International Co., Ltd.',
    'wmi.get[root\\cimv2,"select Model from Win32_ComputerSystem"]': 'To be filled by O.E.M.',
    'wmi.get[root\\cimv2,"select SerialNumber from Win32_BIOS"]': 'Default string'}),
    ('ok', '{"schema":1,"vendor":"Micro-Star International Co., Ltd.","model":"","serial":"","machine_id":""}'))

# ---------- liveness chains ----------
# A nodata() trigger stays quiet only if its item gets a value at least once per window. On the path from
# the polled master to that item only the master may drop unchanged values: a second heartbeat downstream
# is not phase-locked to the master's and stretches the silence to master + downstream heartbeat (seen on
# 2026-09-25: 24h + 12h > 30h, three false "manifest stale" problems).
SEC = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}


def sec(v):
    return int(v[:-1]) * SEC[v[-1]] if v[-1] in SEC else int(v)


for name in ('linux_inventory.yaml', 'windows_inventory.yaml'):
    t = yaml.safe_load((ROOT / 'template' / name).read_text())['zabbix_export']['templates'][0]
    items = {i['key']: i for i in t['items']}
    window = {m['macro']: m['value'] for m in t['macros']}
    for i in t['items']:
        for tr in i.get('triggers', []):
            w = sec(window['{$SWINV.NODATA}'])
            chain, cur = [], i
            while cur.get('master_item'):
                chain.append(cur)
                cur = items[cur['master_item']['key']]
            dropped = [c['key'] for c in chain
                       if any(p['type'].startswith('DISCARD_UNCHANGED') for p in c.get('preprocessing', []))]
            check(f'{name}: nothing between master and nodata item drops values ({i["key"]})', dropped, [])
            hb = [sec(p['parameters'][0]) for p in cur.get('preprocessing', []) if p['type'] == 'DISCARD_UNCHANGED_HEARTBEAT']
            check(f'{name}: master delay + heartbeat fits the nodata window ({cur["key"][:40]})',
                  sec(cur['delay']) + (hb[0] if hb else 0) < w, True)

print('FAILED: %d' % fails if fails else 'all passed')
sys.exit(1 if fails else 0)
