//
// Created by stefano on 12/30/22.
//
#include "Scales.h"

HAL_StatusTypeDef initScaleTimer(TIM_HandleTypeDef * timHandle)
{
  TIM_Encoder_InitTypeDef sConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};

  timHandle->Init.Prescaler = 0;
  timHandle->Init.CounterMode = TIM_COUNTERMODE_UP;
  timHandle->Init.Period = 65535;
  timHandle->Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  timHandle->Init.RepetitionCounter = 0;
  timHandle->Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  sConfig.EncoderMode = TIM_ENCODERMODE_TI12;
  sConfig.IC1Polarity = TIM_ICPOLARITY_RISING;
  sConfig.IC1Selection = TIM_ICSELECTION_DIRECTTI;
  sConfig.IC1Prescaler = TIM_ICPSC_DIV1;
  /* Filter 0xF, NOT 0 -- and the 0 here is why d229fef did nothing for six days.
   *
   * MX_TIMx_Init() in tim.c sets these to 15 (d229fef, 2026-08-25, "encoder
   * input filters 0 -> 0xF"). But RampsStart() runs AFTER those inits and
   * re-configures every scale timer through THIS function, so the 0 below
   * overwrote 15 on every boot, before HAL_TIM_Encoder_Start(). The commit was
   * flashed 2026-08-25 and is an ancestor of the currently-flashed 8483d77, so
   * the machine has carried an inert fix ever since. Found 2026-09-04.
   *
   * SAFE AT MAX SPEED, checked rather than assumed: at CKD=DIV1 filter 0xF is
   * fDTS/32 with N=8 = 8 * 320 ns = 2.56 us of required stability. The fastest
   * legitimate signal is 1024 PPR at 4500 encoder RPM = 76.8 kHz per CHANNEL,
   * a 6.5 us half-period -- about 2.5x margin. (Do not use the x4-decoded count
   * rate, ~307 kcounts/s, for this comparison; the filter sees channel edges,
   * not decoded counts.)
   *
   * WHAT THIS DOES NOT CLAIM: that it fixes the VFD noise. That was cured by
   * bonding the encoder cable shield (2026-08-29, belt-off count -51,525 -> 0).
   * Whether filtering would ALSO have helped is unmeasured and depends on the
   * width of the injected glitches, which nobody captured -- a counts/s figure
   * does not tell you pulse width. This change only makes the running
   * configuration match the one the repo claims.
   */
  sConfig.IC1Filter = 15;
  sConfig.IC2Polarity = TIM_ICPOLARITY_RISING;
  sConfig.IC2Selection = TIM_ICSELECTION_DIRECTTI;
  sConfig.IC2Prescaler = TIM_ICPSC_DIV1;
  sConfig.IC2Filter = 15;

  sMasterConfig.MasterOutputTrigger = TIM_TRGO_ENABLE;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;

  HAL_StatusTypeDef result = HAL_TIM_Encoder_Init(timHandle, &sConfig);
  if (result != HAL_OK) {
    return result;
  }

  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;

  result = HAL_TIMEx_MasterConfigSynchronization(timHandle, &sMasterConfig);
  return result;
}
