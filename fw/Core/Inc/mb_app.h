/*
 * mb_app.h -- the application's Modbus RTU slave: the shared core
 * (modbus_rtu/mb_rtu.c) bound to this image by Core/mb_port/mb_rtu_port.h,
 * over the transport in mb_app_hw.h.
 *
 * Address 17, FC 3 / 6 / 16. Registers 0..size-1 are rampsSharedData_t; the
 * identity window (els_identity.h) is served read-only at ELS_ID_BASE; a
 * write touching an `access: ro_firmware` register is refused with
 * exception 2 and writes nothing, except a 0 to one the schema marks
 * `host_reset: true` (Ramps_access_generated.h).
 *
 * Polled, one frame per call, from task context. The frame is handled to
 * completion -- registers copied or written -- before the reply starts, and
 * the next frame is not taken until that reply has left.
 */
#ifndef MB_APP_H
#define MB_APP_H

#include <stdint.h>

/* The slave address. Ramps.h MODBUS_ADDRESS, which Ramps.c checks against
 * this at compile time; the bootloader answers the same one. */
#define MB_APP_ADDRESS 17u

#ifdef __cplusplus
extern "C" {
#endif

/* Bind the map and the identity window and start the transport. `regs` is
 * rampsSharedData_t as `nregs` registers. */
void mbAppInit(uint16_t *regs, uint16_t nregs, const uint16_t *identity);

/* Service the link once: take at most one complete frame, handle it, start
 * its reply. Returns 1 if a reply was started, else 0. */
int mbAppService(void);

/* Replies sent since boot, wrapping. The user LED blinks on it and the
 * boot-attempt counter is cleared on it (els_boot.h): a reply proves a frame
 * reached this slave intact, which a burst of line noise does not. */
uint16_t mbAppReplies(void);

#ifdef __cplusplus
}
#endif

#endif /* MB_APP_H */
