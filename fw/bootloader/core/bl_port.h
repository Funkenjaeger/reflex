/*
 * bl_port.h -- everything the bootloader's portable core needs from the
 * chip, as plain functions. src/bl_hw.c implements them on the STM32F411;
 * the native tests (fw/emulator/test/bl_*_test.cpp) implement them over a
 * byte array with 1->0 flash semantics and a power-loss injector.
 *
 * The split is the whole reason the copy/swap state machine can be tested
 * for power loss at every operation boundary without a flash controller.
 */
#ifndef BL_PORT_H
#define BL_PORT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Flash. Addresses are absolute (0x0800xxxx). Erase takes the BASE address
 * of a sector and erases that whole sector. Program writes nwords 32-bit
 * words at addr (4-aligned), PSIZE x32. Both return 0 on success, nonzero
 * on a flash-controller error. */
int blPortErase(uint32_t sectorBase);
int blPortProgram(uint32_t addr, const uint32_t *words, uint32_t nwords);

/* Read view of flash at addr: on hardware the address itself, in tests a
 * pointer into the mock array. Valid for the rest of the containing sector. */
const uint8_t *blPortMap(uint32_t addr);

/* CRC32, STM32 CRC-unit variant (els_identity.h). Reset, feed words, read. */
void     blPortCrcReset(void);
void     blPortCrcFeed(uint32_t word);
uint32_t blPortCrcValue(void);

/* RTC backup registers, index ELS_BKP_*_IDX. */
uint32_t blPortBkpRead(uint32_t idx);
void     blPortBkpWrite(uint32_t idx, uint32_t value);

/* Refresh the IWDG if it is armed. Called around long flash operations. */
void blPortWatchdogKick(void);

#ifdef __cplusplus
}
#endif

#endif /* BL_PORT_H */
