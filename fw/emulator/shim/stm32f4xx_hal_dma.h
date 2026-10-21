/*
 * HAL DMA shim for emulator.
 * Minimal stubs. The firmware's DMA use is the Modbus transport's, which
 * the emulator replaces whole (src/mb_hw_emu.cpp).
 */
#ifndef __STM32F4xx_HAL_DMA_H
#define __STM32F4xx_HAL_DMA_H

#include "stm32f4xx.h"
#include "stm32f4xx_hal_def.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DMA_IT_HT 0x00000008U

typedef struct {
    DMA_Stream_TypeDef *Instance;
} DMA_HandleTypeDef;


#ifdef __cplusplus
}
#endif

#endif /* __STM32F4xx_HAL_DMA_H */
