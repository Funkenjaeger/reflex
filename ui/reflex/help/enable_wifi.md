Enable Wifi
===========

Turns the Wifi radio on or off on the Raspberry Pi.

## Behavior

- **ON:** The Wifi adapter is on and can connect to networks. The
  Network interface dropdown and the connection fields become active.
- **OFF:** The Wifi adapter is off. This saves power. In secure
  environments, it also prevents unintended network connections.

## Notes

- You need a compatible Wifi adapter (built in on Raspberry Pi 3/4/5)
- Changes take effect immediately through nmcli
- If Wifi was connected before and you turn it off and on again, it
  tries to reconnect to the last known network
