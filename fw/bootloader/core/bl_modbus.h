/*
 * bl_modbus.h -- the bootloader's Modbus RTU slave, bytes in, bytes out.
 *
 * Serves FC 3 / 6 / 16 on slave address 17 (the app's address, Ramps.h
 * MODBUS_ADDRESS), over the identity window (read-only) and the bootloader
 * control window (read/write, with its blDiag tail served as a separate
 * read-only window), resolved by the same modbus_window.h the app uses.
 * Frames it drops without a reply are counted in blDiag (CRC errors apart
 * from every other reject; bl_diag.h). A write that leaves blCommand
 * nonzero is executed to completion BEFORE the reply is sent, so the host
 * never has a request in flight while the flash interface is stalled by an
 * erase; the reply's arrival is "the operation finished", and blSeq /
 * blResult say how.
 *
 * The frame handling is the shared core, modbus_rtu/mb_rtu.c, compiled
 * against this image's port, mb_rtu_port.h: call mbRtuHandle(&core,
 * identity, ...). No hardware in either file: the UART byte driver in
 * src/bl_hw.c hands over a complete frame and sends back whatever the
 * handler returns.
 */
#ifndef BL_MODBUS_H
#define BL_MODBUS_H

#include "mb_rtu.h"

#define BL_MODBUS_ADDRESS   MB_RTU_PORT_ADDRESS
#define BL_MODBUS_MAX_FRAME MB_RTU_MAX_FRAME

#endif /* BL_MODBUS_H */
