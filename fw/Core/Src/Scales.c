/*
 * Scales.c -- encoder timer setup for the scale inputs (TIM1-TIM4).
 *
 * What the rest of the firmware relies on:
 *
 *  - Every edge of both channels counts (x4 decoding), up or down with the
 *    direction of rotation, so CNT is the scale's position in counts.
 *  - All four counters wrap at 16 bits. The ISR takes each tick's movement as
 *    (int16_t)(CNT - previous CNT), which is correct across a wrap as long as a
 *    scale moves fewer than 32768 counts per tick. TIM2 is a 32-bit timer and
 *    MX_TIM2_Init gives it a 32-bit period, but the 16-bit period set here is
 *    the one that runs, so all four behave alike.
 *  - Nothing else is attached: no prescaler, no interrupts, no trigger output.
 *
 * RampsStart() calls this for each scale AFTER the CubeMX MX_TIMx_Init()
 * functions, so these settings, not tim.c's, are what the hardware runs with.
 * A change to how the scales count belongs here.
 */
#include "Scales.h"

/* Input filter 0xF: with CKD = DIV1 the filter samples at fDTS/32 and needs
 * 8 agreeing samples, so an edge must hold for 8 x 320 ns = 2.56 us before it
 * counts. The fastest real signal, a 1024 PPR encoder at 4500 rpm, toggles
 * each channel at 76.8 kHz: a 6.5 us half-period, about 2.5x the filter time.
 * Compare against the per-channel rate, not the x4 count rate. The filter is
 * there to reject short glitches; how much electrical noise it removes on the
 * machine has not been measured. */
#define SCALE_INPUT_FILTER 0xFu

HAL_StatusTypeDef initScaleTimer(TIM_HandleTypeDef *timHandle)
{
  /* Time base: count the encoder's edges directly over the full 16-bit range. */
  timHandle->Init.Prescaler         = 0;
  timHandle->Init.CounterMode       = TIM_COUNTERMODE_UP;
  timHandle->Init.Period            = 0xFFFFu;
  timHandle->Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
  timHandle->Init.RepetitionCounter = 0;
  timHandle->Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;

  /* Both channels wired straight to their own pins, same edge sense, no
   * prescaling: TI12 mode then counts on all four edges per cycle. */
  TIM_Encoder_InitTypeDef quadrature = {
    .EncoderMode  = TIM_ENCODERMODE_TI12,
    .IC1Polarity  = TIM_ICPOLARITY_RISING,
    .IC1Selection = TIM_ICSELECTION_DIRECTTI,
    .IC1Prescaler = TIM_ICPSC_DIV1,
    .IC1Filter    = SCALE_INPUT_FILTER,
    .IC2Polarity  = TIM_ICPOLARITY_RISING,
    .IC2Selection = TIM_ICSELECTION_DIRECTTI,
    .IC2Prescaler = TIM_ICPSC_DIV1,
    .IC2Filter    = SCALE_INPUT_FILTER,
  };

  /* A scale timer drives nothing else. */
  TIM_MasterConfigTypeDef standalone = {
    .MasterOutputTrigger = TIM_TRGO_RESET,
    .MasterSlaveMode     = TIM_MASTERSLAVEMODE_DISABLE,
  };

  HAL_StatusTypeDef status = HAL_TIM_Encoder_Init(timHandle, &quadrature);
  if (status == HAL_OK) {
    status = HAL_TIMEx_MasterConfigSynchronization(timHandle, &standalone);
  }
  return status;
}
