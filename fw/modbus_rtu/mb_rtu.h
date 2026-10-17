/*
 * mb_rtu.h -- the Modbus RTU slave core, bytes in, bytes out.
 *
 * One frame handler for every image that serves the register map: it checks
 * the frame (length, slave address, CRC), serves FC 3 / 6 / 16 over the
 * windows modbus_window.h resolves, and composes the reply. Everything that
 * differs between images comes from that image's PORT HEADER, mb_rtu_port.h,
 * found on the image's own include path (bootloader/core/mb_rtu_port.h for
 * the bootloader, emulator/test/mb_rtu_port/ for the native test). It must
 * define:
 *
 *   mbRtuPort_t            the context the handler is called with
 *   MB_RTU_PORT_ADDRESS    the slave address this image answers
 *   MB_RTU_PORT_WINDOWS    how many windows mbRtuPortWindows() fills
 *   mbRtuPortMainRegs(port), mbRtuPortMainSize(port)
 *                          the main map registers 0..size-1 are served from,
 *                          or NULL and 0 for none
 *   mbRtuPortWindows(port, identity, wins)
 *                          fill wins[] for this frame; called once per frame
 *                          that passed its CRC, before the function code is
 *                          looked at. Main map and windows together are the
 *                          WINDOW RESOLVER's input (mbResolveRange)
 *   mbRtuPortAccess(port)  the access table (mbRtuAccess_t) writes are checked
 *                          against, or NULL to check none
 *   mbRtuPortBeforeRead(port)
 *                          called after an FC 3 range resolves, before the
 *                          registers are copied into the reply
 *   mbRtuPortCommand(port) the COMMAND HOOK: called after an FC 6 / 16 write
 *                          lands and BEFORE the reply is composed, so a
 *                          command it executes has finished by the time the
 *                          host sees the reply
 *   mbRtuPortDrop(port, why)
 *                          a frame was dropped without a reply; why is
 *                          MB_RTU_DROP_CRC or MB_RTU_DROP_FRAME
 *
 * The hooks are bound at compile time, not through function pointers: each
 * image compiles this core once against its own port, and the calls inline
 * to exactly the code a hand-written slave would have. The bootloader is
 * reflashed only over SWD, so its image is held to that.
 *
 * Frames that get an exception reply were received intact and are not
 * dropped: the host sees the exception.
 */
#ifndef MB_RTU_H
#define MB_RTU_H

#include <stdint.h>
#include "modbus_window.h"

#ifdef __cplusplus
extern "C" {
#endif

#define MB_RTU_MAX_FRAME 256u

#define MB_RTU_FC_READ_HOLDING   3u
#define MB_RTU_FC_WRITE_SINGLE   6u
#define MB_RTU_FC_WRITE_MULTIPLE 16u

#define MB_RTU_EXC_ILLEGAL_FUNCTION 1u
#define MB_RTU_EXC_ILLEGAL_ADDRESS  MB_EXC_ILLEGAL_ADDRESS
#define MB_RTU_EXC_ILLEGAL_VALUE    3u

/* Why mbRtuPortDrop() was called. */
#define MB_RTU_DROP_FRAME 0u   /* runt, oversized, not our address, bad length */
#define MB_RTU_DROP_CRC   1u

/* Which registers a host may not write. Bit (r - base) of roBits, LSB first
 * within each byte, is set for each register r the firmware owns
 * (`access: ro_firmware` in the registers/ schemas; the generated table is
 * Ramps_access_generated.h). A write, FC 6 or FC 16, that touches any such
 * register is answered with exception 2, ILLEGAL DATA ADDRESS, and writes
 * nothing: the same answer a write into a read-only window gets
 * (modbus_window.h), so a host has one rule for "not writable". Registers
 * outside base..base+size-1 are not checked. */
typedef struct {
  uint16_t       base;
  uint16_t       size;
  const uint8_t *roBits;
} mbRtuAccess_t;

#include "mb_rtu_port.h"

/* Modbus RTU CRC-16 (poly 0xA001 reflected, init 0xFFFF), low byte first
 * on the wire. */
uint16_t mbRtuCrc16(const uint8_t *data, uint32_t len);

/* Handle one received frame (address .. CRC inclusive). Writes the reply
 * into resp (capacity MB_RTU_MAX_FRAME) and returns its length, or 0 when
 * no reply is due (not our address, bad CRC, runt). `identity` is the
 * identity window (els_identity.h), passed to mbRtuPortWindows(). */
uint32_t mbRtuHandle(mbRtuPort_t *port, const uint16_t *identity,
                     const uint8_t *req, uint32_t reqLen, uint8_t *resp);

#ifdef __cplusplus
}
#endif

#endif /* MB_RTU_H */
