Enable Wifi
===========

Turns the WiFi radio on or off on the Raspberry Pi.

## Behavior

- **ON:** The WiFi adapter is on and can connect to networks. The
  Network Interface dropdown and the connection fields become active.
- **OFF:** The WiFi adapter is off. This saves power. In secure
  environments, it also prevents unintended network connections.

## Notes

- You need a compatible WiFi adapter (built in on Raspberry Pi 3/4/5)
- Changes take effect immediately through nmcli
- If WiFi was connected before and you turn it off and on again, it
  tries to reconnect to the last known network
