/*
 * mb_rtu.c -- see mb_rtu.h.
 */
#include "mb_rtu.h"

uint16_t mbRtuCrc16(const uint8_t *data, uint32_t len)
{
  uint16_t crc = 0xFFFFu;
  for (uint32_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (int b = 0; b < 8; b++) {
      if (crc & 1u) crc = (uint16_t)((crc >> 1) ^ 0xA001u);
      else          crc = (uint16_t)(crc >> 1);
    }
  }
  return crc;
}

static uint32_t finish(uint8_t *resp, uint32_t len)
{
  uint16_t crc = mbRtuCrc16(resp, len);
  resp[len]     = (uint8_t)(crc & 0xFFu);
  resp[len + 1] = (uint8_t)(crc >> 8);
  return len + 2u;
}

static uint32_t exception(uint8_t *resp, uint8_t fc, uint8_t code)
{
  resp[0] = MB_RTU_PORT_ADDRESS;
  resp[1] = (uint8_t)(fc | 0x80u);
  resp[2] = code;
  return finish(resp, 3u);
}

static uint32_t dropped(mbRtuPort_t *port, unsigned why)
{
  mbRtuPortDrop(port, why);
  return 0u;
}

/* Nonzero when addr..addr+count-1 touches a register `acc` marks as
 * firmware-owned. */
static int touchesReadOnly(const mbRtuAccess_t *acc, uint16_t addr, uint16_t count)
{
  uint32_t end = (uint32_t)addr + (uint32_t)count;
  for (uint32_t r = addr; r < end; r++) {
    uint32_t i = r - acc->base;
    if (r >= acc->base && i < acc->size && (acc->roBits[i >> 3] & (1u << (i & 7u))))
      return 1;
  }
  return 0;
}

uint32_t mbRtuHandle(mbRtuPort_t *port, const uint16_t *identity,
                     const uint8_t *req, uint32_t reqLen, uint8_t *resp)
{
  mbWindow_t wins[MB_RTU_PORT_WINDOWS];
  const mbRtuAccess_t *acc;
  uint16_t *p;
  uint8_t fc;

  if (reqLen < 4u || reqLen > MB_RTU_MAX_FRAME) return dropped(port, MB_RTU_DROP_FRAME);
  if (req[0] != MB_RTU_PORT_ADDRESS) return dropped(port, MB_RTU_DROP_FRAME);
  {
    uint16_t crc = mbRtuCrc16(req, reqLen - 2u);
    if (req[reqLen - 2u] != (uint8_t)(crc & 0xFFu) || req[reqLen - 1u] != (uint8_t)(crc >> 8))
      return dropped(port, MB_RTU_DROP_CRC);
  }

  mbRtuPortWindows(port, identity, wins);
  acc = mbRtuPortAccess(port);

  fc = req[1];
  switch (fc) {
    case MB_RTU_FC_READ_HOLDING: {
      uint16_t addr, count;
      if (reqLen != 8u) return dropped(port, MB_RTU_DROP_FRAME);
      addr  = (uint16_t)((req[2] << 8) | req[3]);
      count = (uint16_t)((req[4] << 8) | req[5]);
      if (count < 1u || count > 125u) return exception(resp, fc, MB_RTU_EXC_ILLEGAL_VALUE);
      if (mbResolveRange(mbRtuPortMainRegs(port), mbRtuPortMainSize(port), wins, MB_RTU_PORT_WINDOWS, addr, count, 0, &p) != 0u)
        return exception(resp, fc, MB_RTU_EXC_ILLEGAL_ADDRESS);
      mbRtuPortBeforeRead(port);
      resp[0] = MB_RTU_PORT_ADDRESS;
      resp[1] = fc;
      resp[2] = (uint8_t)(count * 2u);
      /* Ascending, one register at a time -- the ordering invariant. */
      for (uint16_t i = 0; i < count; i++) {
        resp[3u + 2u * i]     = (uint8_t)(p[i] >> 8);
        resp[3u + 2u * i + 1] = (uint8_t)(p[i] & 0xFFu);
      }
      return finish(resp, 3u + 2u * count);
    }

    case MB_RTU_FC_WRITE_SINGLE: {
      uint16_t addr, value;
      if (reqLen != 8u) return dropped(port, MB_RTU_DROP_FRAME);
      addr  = (uint16_t)((req[2] << 8) | req[3]);
      value = (uint16_t)((req[4] << 8) | req[5]);
      if (mbResolveRange(mbRtuPortMainRegs(port), mbRtuPortMainSize(port), wins, MB_RTU_PORT_WINDOWS, addr, 1u, 1, &p) != 0u)
        return exception(resp, fc, MB_RTU_EXC_ILLEGAL_ADDRESS);
      if (acc != (const mbRtuAccess_t *)0 && touchesReadOnly(acc, addr, 1u))
        return exception(resp, fc, MB_RTU_EXC_ILLEGAL_ADDRESS);
      *p = value;
      mbRtuPortCommand(port);       /* runs to completion before the reply */
      for (uint32_t i = 0; i < 6u; i++) resp[i] = req[i];
      return finish(resp, 6u);
    }

    case MB_RTU_FC_WRITE_MULTIPLE: {
      uint16_t addr, count;
      uint8_t bytes;
      if (reqLen < 9u) return dropped(port, MB_RTU_DROP_FRAME);
      addr  = (uint16_t)((req[2] << 8) | req[3]);
      count = (uint16_t)((req[4] << 8) | req[5]);
      bytes = req[6];
      if (count < 1u || count > 123u || bytes != count * 2u || reqLen != 9u + (uint32_t)bytes)
        return exception(resp, fc, MB_RTU_EXC_ILLEGAL_VALUE);
      if (mbResolveRange(mbRtuPortMainRegs(port), mbRtuPortMainSize(port), wins, MB_RTU_PORT_WINDOWS, addr, count, 1, &p) != 0u)
        return exception(resp, fc, MB_RTU_EXC_ILLEGAL_ADDRESS);
      if (acc != (const mbRtuAccess_t *)0 && touchesReadOnly(acc, addr, count))
        return exception(resp, fc, MB_RTU_EXC_ILLEGAL_ADDRESS);
      for (uint16_t i = 0; i < count; i++)
        p[i] = (uint16_t)((req[7u + 2u * i] << 8) | req[7u + 2u * i + 1u]);
      mbRtuPortCommand(port);       /* runs to completion before the reply */
      for (uint32_t i = 0; i < 6u; i++) resp[i] = req[i];
      return finish(resp, 6u);
    }

    default:
      return exception(resp, fc, MB_RTU_EXC_ILLEGAL_FUNCTION);
  }
}
