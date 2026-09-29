# One Modbus register map for the bootloader and the application

The bootloader and the ELS application answer on the same Modbus address, so a
host has to know which one it is talking to before it does anything else.

Both expose an **identity window at a fixed address (2048), outside the
application's register struct**: stage (bootloader or application), build
revision, and the application's `protocolVersion`. The bootloader adds its own
control window at 2304; the application does not answer there. The contract is
one append-only header, `fw/Core/Inc/els_identity.h`.

The address is fixed because the bootloader is flashed once and must outlive
every change to the application's layout. It sits outside the struct so that
`protocolVersion` keeps meaning "the register layout changed" and not "the
firmware was rebuilt".
