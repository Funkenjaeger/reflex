/*
 * mb_app_hw.h -- the byte transport under the application's Modbus slave
 * (mb_app.h): complete frames in, a reply out.
 *
 * Two implementations. On the chip, Core/Src/mb_app_hw.c: USART1 receives
 * into a ring by circular DMA and the USART's latched IDLE flag marks the end
 * of a frame, as in the field bootloader (bootloader/src/bl_hw.c); the reply
 * goes out by DMA too. In the lathe-emulator, emulator/src/mb_hw_emu.cpp
 * feeds the same ring arithmetic (bootloader/core/bl_rxring.c) from the
 * PTY/TCP transport.
 *
 * No interrupts on either side: the caller polls. The transport never calls
 * back into the slave.
 */
#ifndef MB_APP_HW_H
#define MB_APP_HW_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Bring up receive and transmit. `diag` is the slave's receiver counters
 * (ELS_BL_DIAG_REGS of them, ELS_BL_DG_* layout), kept for every later call. */
void mbHwInit(uint16_t *diag);

/* One poll. Returns the length of a complete frame copied into `frame`
 * (capacity `frameMax`), or 0 when no frame has ended since the last call. */
uint32_t mbHwPoll(uint8_t *frame, uint32_t frameMax);

/* Nonzero while a reply is still leaving: the buffer handed to mbHwSend must
 * not be touched until this reads 0. */
int mbHwTxBusy(void);

/* Start sending `len` bytes from `data`, which must stay untouched until
 * mbHwTxBusy() reads 0. Returns at once. */
void mbHwSend(const uint8_t *data, uint32_t len);

#ifdef __cplusplus
}
#endif

#endif /* MB_APP_HW_H */
