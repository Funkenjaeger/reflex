/*
 * Scales.h -- the encoder ("scale") inputs.
 *
 * Each scale is a quadrature encoder decoded in hardware by one of TIM1-TIM4.
 * The motion ISR reads the counters directly; this module only puts a timer
 * into the counting mode the ISR expects. See Scales.c.
 */
#ifndef REFLEX_SCALES_H
#define REFLEX_SCALES_H

#include "stm32f4xx_hal.h"

/* Number of encoder inputs, and the length of every per-scale array in the
 * Modbus-mapped shared data. tools/genregs.py reads this line, so it stays a
 * bare integer with nothing after it. */
#define SCALES_COUNT 4

/* Configure one encoder timer for x4 quadrature counting over a 16-bit range.
 * The handle's Instance must already be set (MX_TIMx_Init does that). Does not
 * start the counter: follow with HAL_TIM_Encoder_Start(). Returns the first
 * HAL error, or HAL_OK. */
HAL_StatusTypeDef initScaleTimer(TIM_HandleTypeDef *timHandle);

#endif /* REFLEX_SCALES_H */
