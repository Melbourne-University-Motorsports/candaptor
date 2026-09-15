# CANdaptor as a “standard” USB CAN adapter — feasibility

**Verdict:** a software bridge that other CAN tools can use is **easy and worth doing**. A virtual USB device that shows up as Peak / Vector / Kvaser / candleLight hardware is **not practical**, especially on macOS.

CANdaptor talks to an Ewert Energy CANdapter, which is already a USB device. Hosts see an FTDI virtual serial port (`/dev/cu.usbserial-*`, `/dev/ttyUSB*`, `COMx`). Industry software does not fail to *detect USB*; it fails because it is looking for a **different protocol and/or a vendor USB identity**, not a generic COM port.

## What the hardware actually speaks

Ewert Energy’s CANdapter is an FTDI USB-serial dongle with an ASCII command set that *looks like* Lawicel SLCAN but is not compatible with it. Ewert documents it as a virtual serial port on Windows (VCP or D2XX) and Linux (VCP only). ([Ewert CANdapter product page](https://www.ewertenergy.com/products.php?item=candapter); [CANdapter manual](https://www.ewertenergy.com/products/candapter/downloads/candapter_manual.pdf))

This repo’s driver (`candaptor/__init__.py`) matches that manual:

| Function | CANdaptor / Ewert CANdapter | Lawicel / Linux SLCAN |
| --- | --- | --- |
| Standard 11-bit frame | `Tiiildd…` | `tiiildd…` |
| Extended 29-bit frame | `Xiiiiiiiildd…` | `Tiiiiiiiildd…` |
| Timestamp enable | `A0` / `A1` | `Z0` / `Z1` |
| Success on most commands | ACK byte `0x06` | CR `0x0D` (TX often `z\r` / `Z\r`) |
| Error | BEL `0x07` | BEL `0x07` |
| Open / close / bitrate / version / flags | `O`, `C`, `S0`–`S8`, `V`, `F` | same letters |

Sources: this repo’s `CANdaptor` class; [Lawicel CANUSB manual](https://www.canusb.com/files/canusb_manual.pdf); [Linux `slcan.c` frame letters](http://rtsrv.cs.unc.edu/cgit/cgit.cgi/litmus-rt.git/plain/drivers/net/can/slcan.c); python-can `slcan.py`.

The `T` collision is the important one. Lawicel uses case to distinguish standard vs extended. CANdaptor commands are **not case-sensitive**, and `T` means standard while `X` means extended. A Lawicel client that sends an extended frame as `T…` is asking CANdaptor to transmit a *standard* frame.

python-can already knows about this dongle on receive: it accepts `x` as an alternate extended-frame letter ([issue #1506](https://github.com/hardbyte/python-can/issues/1506), [PR #1528](https://github.com/hardbyte/python-can/commit/0de8ca82f1c18afff438a7c0efba5f512ea6581e)). It still *transmits* extended frames as Lawicel `T…`, so TX of 29-bit IDs through stock `interface=slcan` is still wrong for this dongle. ACK is also different: python-can treats `0x0D` / `0x07` as line endings, not `0x06`.

Bitrate tables `S0`–`S6`/`S8` match. `S7` is 800 kbit/s on both Lawicel and this driver. python-can maps `S7` to 750 kbit/s (CANable-style); that is a python-can quirk, not a CANdaptor one.

CANdaptor is CAN 2.0 only (8-byte frames). It cannot grow into CAN FD by bridging.

## What “industry software” actually binds to

“Detected as a USB CAN adapter” means different things to different apps:

| Software | How it finds hardware | Bridge that would work |
| --- | --- | --- |
| python-can, cantools, many scripts | `slcan`, `gs_usb`, `socketcan`, `pcan`, … | python-can plugin, or SLCAN PTY |
| Linux `candump` / Wireshark / can-utils | SocketCAN netdev (`can0`) | SLCAN translator + `slcand`, or a SocketCAN daemon |
| Cangaroo | gs_usb / candleLight USB | fake gs_usb device (hard) |
| SavvyCAN | GVRET serial, or Qt SerialBus (SocketCAN / PeakCAN / Vector) | GVRET PTY (macOS/Windows), or SocketCAN (Linux) |
| PCAN-View / PCAN-Explorer | Peak hardware + Peak driver only | not realistically |
| Vector CANalyzer / CANoe | Vector hardware + license | not realistically |
| Kvaser CanKing etc. | Kvaser hardware + CANlib | not realistically |

Peak’s own support states PCAN-Explorer only supports PEAK-System / official OEM hardware. ([Peak forum](https://forum.peak-system.com/viewtopic.php?t=2217)) Qt SerialBus PeakCAN/Vector plugins likewise talk to those vendors’ APIs, which enumerate vendor USB IDs. Spoofing Peak (`0x0C72`) or Vector VIDs and reimplementing their USB protocols would be a large reverse-engineering job, would fight driver signing, and would still not unlock Vector’s licensed toolchain.

The open protocol that *does* get you native Linux CAN (and Cangaroo) is **gs_usb** / candleLight: vendor-specific USB with bulk endpoints, implemented by [candleLight_fw](https://github.com/candle-usb/candleLight_fw) and [linux `gs_usb.c`](https://github.com/torvalds/linux/blob/master/drivers/net/can/usb/gs_usb.c). That is a real USB function, not a serial port.

SavvyCAN’s serial path is **GVRET**, a binary `0xF1`-prefixed protocol, not SLCAN. ([M2RET `CommProtocol.txt`](https://raw.githubusercontent.com/collin80/M2RET/master/CommProtocol.txt); [SavvyCAN connection docs](https://savvycan.com/docs/connectionwindow.html))

## Option 1 — Protocol bridge (easy, recommended)

Userspace daemon:

1. Open the real CANdaptor serial port with the existing driver.
2. Present a **pseudoterminal** (macOS/Linux) or a virtual COM pair (Windows `com0com`) that speaks Lawicel SLCAN.
3. Translate `t`/`T`/`r`/`R` ↔ `T`/`X`, `Z` ↔ `A`, ACK `0x06` ↔ CR, and swallow CANable FD letters (`d`/`D`/`b`/`B`) as errors.

Then:

- python-can: `Bus(interface="slcan", channel="/dev/pts/N", bitrate=500000)`
- Linux: `slcand -o -s6 /dev/pts/N slcan0` then `ip link set slcan0 up` ([CANable getting started](https://canable.io/getting-started.html); can-utils `slcand` / [`slcanpty.c`](https://raw.githubusercontent.com/linux-can/can-utils/master/slcanpty.c) is the inverse direction — SocketCAN → PTY — proving the pattern is first-class in can-utils)

**Effort:** a few days, including tests against the existing parser. Cross-platform. No kernel modules, no USB gadget hardware.

A dedicated **python-can `BusABC`** for CANdaptor is even smaller (1–2 days) and unlocks the Python ecosystem without a PTY. It does not help GUI tools that are not python-can.

A **GVRET PTY** (same daemon, different front protocol) is the macOS-friendly way to feed SavvyCAN. GVRET is documented and several third-party firmwares already implement a subset. Extra few days.

## Option 2 — SocketCAN netdev (Linux only, medium)

After option 1, `slcand` already gives `slcan0`. Alternatively, a small daemon can open `PF_CAN` / `SOCK_RAW` against a `vcan` or a custom candev — but `vcan` is a loopback, not a pass-through to hardware. The supported path is slcan.

This is the highest-leverage Linux UX: Wireshark, `candump`, SavvyCAN via Qt SerialBus SocketCAN, python-can `socketcan`.

macOS has no SocketCAN. [mac-can/SLCAN](https://github.com/mac-can/SLCAN) is a userspace Lawicel library, not a kernel CAN stack — it still wants option 1’s translated PTY.

## Option 3 — Virtual USB gadget that looks like gs_usb / PCAN (hard, mostly Linux)

This is the literal “virtual USB device” idea.

Linux can emulate USB devices in software via the gadget API, Dummy HCD/UDC, and [USB Raw Gadget](https://docs.kernel.org/5.10/usb/raw-gadget.html) ([xairy/raw-gadget](https://github.com/xairy/raw-gadget/)). Dummy HCD attaches the fake device to the *same* machine’s host controller, so `gs_usb` could in theory probe it and create `can0`. USB/IP `vudc` + `vhci-hcd` can export a gadget to another machine. ([usbip README](https://github.com/torvalds/linux/blob/master/tools/usb/usbip/README))

To look like candleLight you must implement the gs_usb control requests (`GS_USB_BREQ_*`) and bulk frame format, and use a VID/PID the host driver accepts (Geschwister Schneider `0x1d50:0x606f` is the usual open one). Klipper already has a compact gs_usb gadget implementation for real UDCs ([`usb_canbus.c`](https://github.com/majastanislawska/pick/blob/0d5269d9/src/generic/usb_canbus.c)).

Why this is a poor fit here:

- **macOS cannot do it.** Macs are USB hosts. There is no public USB gadget / dummy HCD stack for presenting a fake device to the same Mac (or to another host) from userspace. Apple silicon dual-role hardware is reserved for system features, not arbitrary gadgets.
- **Dummy HCD is Linux-only**, needs extra kernel modules, and Raw Gadget is documented as a debugging interface, not a product.
- **Performance and correctness.** gs_usb expects USB bulk timing. The CANdaptor behind it is 115200 baud ASCII. Under bus load the fake device will stall or drop frames while still advertising candleLight capabilities.
- **PCAN/Vector/Kvaser** add proprietary protocols, Windows driver signing, and license checks on top. Peak/Vector apps will not treat a cloned VID as supported hardware even if enumeration succeeds.

A Raspberry Pi Zero / STM32 sitting *between* CANdaptor and the PC, running candleLight-like firmware, is technically a USB gadget — but at that point you should just use a CANable.

## Effort vs payoff

```
easy, high payoff
  python-can CANdaptor interface
  SLCAN PTY translator  →  python-can, slcand, many serial tools
  GVRET PTY translator  →  SavvyCAN on macOS/Windows

medium, Linux-only
  slcand → can0 → Wireshark, can-utils, SavvyCAN SocketCAN

hard, Linux-only, low extra payoff
  dummy_hcd + gs_usb gadget  →  Cangaroo auto-detect, native can0 without slcand

not practical
  emulate PCAN-USB / Vector / Kvaser so official vendor software “detects” it
```

## Recommendation

Do **not** start with a virtual USB device. On this project’s usual host (macOS, default port `/dev/cu.usbserial-…`) that path is effectively closed.

Do build a **userspace translator** in this repo:

1. Reuse `candaptor.CANdaptor` for the real dongle.
2. Expose Lawicel SLCAN on a PTY (and later a Windows COM pair).
3. Optionally expose GVRET on a second PTY for SavvyCAN.
4. Document the Linux `slcand` one-liner so the same bridge becomes `can0`.

That is a small, testable module, matches how CANable/Lawicel already integrate with the ecosystem, and avoids kernel USB work that cannot run on the Macs this driver already targets.

CAN FD, PCAN-View, and CANalyzer stay out of scope unless real Peak/Vector hardware is added.
