/*
 * mb_app.c -- see mb_app.h.
 *
 * Underneath the register map:
 *
 *   - receive is circular DMA with the IDLE flag marking frame end
 *     (mb_app_hw.h);
 *   - frames are checked and served by the same core as the bootloader's
 *     (modbus_rtu/mb_rtu.c), so the two stages cannot disagree about a frame;
 *   - a write touching a firmware-owned register is refused (exception 2).
 *
 * No interrupts here: the caller decides when to poll.
 */
#include "mb_app.h"
#include "mb_app_hw.h"
#include "mb_rtu.h"
#include "els_identity.h"
#include "Ramps_access_generated.h"

static const uint8_t      roBits[] = RAMPS_RO_FIRMWARE_BITS_INIT;
static const mbRtuAccess_t access  = { 0u, RAMPS_ACCESS_REGISTERS, roBits };

/* Receiver counters, blDiag layout (ELS_BL_DG_*). Not served over Modbus;
 * read them over SWD (symbol mbAppDiag). */
uint16_t mbAppDiag[ELS_BL_DIAG_REGS];

static mbAppPort_t     port;
static const uint16_t *identityWindow;
static uint8_t         reqBuf[MB_RTU_MAX_FRAME];
static uint8_t         respBuf[MB_RTU_MAX_FRAME];
static volatile uint16_t replies;

void mbAppInit(uint16_t *regs, uint16_t nregs, const uint16_t *identity)
{
  port.regs   = regs;
  port.size   = nregs;
  port.access = &access;
  port.diag   = mbAppDiag;
  identityWindow = identity;
  mbHwInit(mbAppDiag);
}

int mbAppService(void)
{
  uint32_t n, m;

  /* respBuf is the DMA's source until the reply has left, and a request
   * cannot legitimately arrive before then: the host is the only master and
   * waits for the reply. Anything that does arrive stays in the ring. */
  if (mbHwTxBusy()) return 0;

  n = mbHwPoll(reqBuf, sizeof reqBuf);
  if (n == 0u) return 0;

  m = mbRtuHandle(&port, identityWindow, reqBuf, n, respBuf);
  if (m == 0u) return 0;

  mbHwSend(respBuf, m);
  replies++;
  return 1;
}

uint16_t mbAppReplies(void)
{
  return replies;
}
