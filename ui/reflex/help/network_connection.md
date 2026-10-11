Network Connection
==================

Configure the WiFi connection for the Raspberry Pi.

## Fields

### Network Interface
The WiFi hardware adapter that the system detected. If there is more
than one adapter, select the one to configure. The Hardware Address
and State fields below it are read-only.

### Network (SSID)
Press this field to open a popup. The popup lists the WiFi networks
that a scan found. Select the network to connect to.

### Wifi Password
Enter the password for the selected network. For an open network,
leave this field blank.

### Apply Configuration
This button saves the network and password, then tries to connect.
The status area at the bottom shows connection progress and any
errors.

## IP Properties

After the connection is made, these read-only fields show:
- **IP Address** — assigned by DHCP or static configuration
- **DNS Server** — the DNS resolver in use
- **Default Gateway** — the network gateway

## Notes

- Network operations can take several seconds
- If the connection fails, examine the password and try again
- This screen supports only WPA/WPA2/WPA3 personal networks
