/*
 * bl_modbus.h -- the bootloader's Modbus RTU slave, bytes in, bytes out.
 *
 * Serves FC 3 / 6 / 16 on slave address 17 (the app's address, Ramps.h
 * MODBUS_ADDRESS), over exactly two register windows: the identity window
 * (read-only) and the bootloader control window (read/write), resolved by
 * the same modbus_window.h the app uses. A write that leaves blCommand
 * nonzero is executed to completion BEFORE the reply is sent, so the host
 * never has a request in flight while the flash interface is stalled by an
 * erase; the reply's arrival is "the operation finished", and blSeq /
 * blResult say how.
 *
 * No hardware in this file: the UART byte driver in src/bl_hw.c hands over
 * a complete frame and sends back whatever this returns.
 */
#ifndef BL_MODBUS_H
#define BL_MODBUS_H

#include <stdint.h>
#include "bl_core.h"

#ifdef __cplusplus
extern "C" {
#endif

#define BL_MODBUS_ADDRESS   17u
#define BL_MODBUS_MAX_FRAME 256u

/* Modbus RTU CRC-16 (poly 0xA001 reflected, init 0xFFFF), low byte first
 * on the wire. Exposed for the tests and the UART driver. */
uint16_t blModbusCrc16(const uint8_t *data, uint32_t len);

/* Handle one received frame (address .. CRC inclusive). Writes the reply
 * into resp (capacity BL_MODBUS_MAX_FRAME) and returns its length, or 0 when
 * no reply is due (not our address, bad CRC, runt). */
uint32_t blModbusHandle(blCore_t *core, const uint16_t *identity,
                        const uint8_t *req, uint32_t reqLen, uint8_t *resp);

#ifdef __cplusplus
}
#endif

#endif /* BL_MODBUS_H */
