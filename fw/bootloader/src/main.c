/*
 * main.c -- the reflex ELS Modbus field bootloader, sector 0.
 *
 * WHAT IT DOES, in order:
 *   1. bring up the UART (same pins, baud and slave address as the app), the
 *      CRC unit and the backup-domain access;
 *   2. arm the IWDG -- it stays armed through the jump, so an app that hangs
 *      before Modbus is live gets reset and counted;
 *   3. blCoreBoot(): finish any interrupted copy, honor a stay-resident
 *      request, count strikes, decide;
 *   4. either count an attempt and jump into the RUN slot, or sit in the
 *      resident loop answering Modbus on the identity window (idStage = 1)
 *      and the control window until a JUMP is accepted.
 *
 * There are no interrupts in this program. Receive is DMA2 in circular mode
 * filling a ring in SRAM, which keeps running while this loop is stalled --
 * inside a blocking send, and through the seconds a flash erase holds up
 * instruction fetch. Frame boundaries come from polling the USART's LATCHED
 * IDLE flag, so a frame that completed during a stall is still there
 * afterwards; the length is the movement of the DMA's remaining count
 * (src/bl_hw.c, core/bl_rxring.c). Flash operations block, and the reply to
 * the command that started one is sent after it finishes, so the host is
 * never talking into a stalled flash interface.
 *
 * Bare-metal, no FreeRTOS, no HAL. Budget: sector 0, 16 KB.
 */
#include <stdint.h>
#include "stm32f4xx.h"
#include "els_identity.h"
#include "bl_core.h"
#include "bl_modbus.h"
#include "bl_hw.h"
#include "bl_port.h"

/* The identity window, stage = bootloader, app protocol 0. In flash. */
static const uint16_t blIdentityWindow[ELS_ID_SIZE] =
    ELS_ID_WINDOW_INIT(ELS_ID_STAGE_BOOTLOADER, 0u);

static blCore_t core;
static uint8_t  reqBuf[BL_HW_FRAME_MAX];
static uint8_t  respBuf[BL_MODBUS_MAX_FRAME];

/* Called by the CMSIS startup before main. Nothing to do: HSI, no PLL, VTOR
 * at 0x08000000 where this table already is. */
void SystemInit(void)
{
}

static void jumpNow(void)
{
  blCoreCountAttempt(&core);
  blHwJump(ELS_RUN_SLOT_BASE);
}

int main(void)
{
  blHwInit();
  blHwArmWatchdog();
  blCoreInit(&core);

  if (blCoreBoot(&core) == BL_BOOT_JUMP) {
    jumpNow();
  }

  /* Resident. */
  for (;;) {
    uint32_t n = blHwUartPoll(reqBuf);
    blPortWatchdogKick();
    if (n == 0u) continue;
    uint32_t m = blModbusHandle(&core, blIdentityWindow, reqBuf, n, respBuf);
    if (m != 0u) blHwUartSend(respBuf, m);
    if (core.jumpPending) {
      jumpNow();
    }
  }
}
