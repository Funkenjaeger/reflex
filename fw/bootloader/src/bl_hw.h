/*
 * bl_hw.h -- the hardware services main.c needs beyond bl_port.h.
 */
#ifndef BL_HW_H
#define BL_HW_H

#include <stdint.h>

#define BL_HW_FRAME_MAX 256u

void     blHwInit(void);
/* Frame length when one completed, else 0. `diag` is the blDiag counters
 * (bl_diag.h), bumped for frames taken, overflow drops, USART error flags and
 * DMA restarts; NULL counts nothing. */
uint32_t blHwUartPoll(uint8_t *frame, uint16_t *diag);
void     blHwUartSend(const uint8_t *data, uint32_t len);
void     blHwArmWatchdog(void);
void     blHwJump(uint32_t appBase) __attribute__((noreturn));

#endif /* BL_HW_H */
