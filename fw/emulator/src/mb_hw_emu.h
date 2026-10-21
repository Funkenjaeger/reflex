/*
 * mb_hw_emu.h -- the transport's side of the emulated Modbus receiver
 * (mb_hw_emu.cpp): what the chip's USART and receive DMA do on their own.
 */
#ifndef MB_HW_EMU_H
#define MB_HW_EMU_H

#include <cstdint>

/* One received byte, landed in the ring by the "DMA". */
void mbEmuRxByte(uint8_t byte);

/* The line has gone quiet after at least one byte: the USART's IDLE flag. */
void mbEmuRxIdle(void);

#endif /* MB_HW_EMU_H */
