/*
 * bl_modbus.c -- see bl_modbus.h.
 */
#include "bl_modbus.h"
#include "modbus_window.h"

#define FC_READ_HOLDING     3u
#define FC_WRITE_SINGLE     6u
#define FC_WRITE_MULTIPLE   16u
#define EXC_ILLEGAL_FUNCTION 1u
#define EXC_ILLEGAL_ADDRESS  2u
#define EXC_ILLEGAL_VALUE    3u

uint16_t blModbusCrc16(const uint8_t *data, uint32_t len)
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
  uint16_t crc = blModbusCrc16(resp, len);
  resp[len]     = (uint8_t)(crc & 0xFFu);
  resp[len + 1] = (uint8_t)(crc >> 8);
  return len + 2u;
}

static uint32_t exception(uint8_t *resp, uint8_t fc, uint8_t code)
{
  resp[0] = BL_MODBUS_ADDRESS;
  resp[1] = (uint8_t)(fc | 0x80u);
  resp[2] = code;
  return finish(resp, 3u);
}

uint32_t blModbusHandle(blCore_t *core, const uint16_t *identity,
                        const uint8_t *req, uint32_t reqLen, uint8_t *resp)
{
  mbWindow_t wins[2];
  uint16_t *p;
  uint8_t fc;

  if (reqLen < 4u || reqLen > BL_MODBUS_MAX_FRAME) return 0u;
  if (req[0] != BL_MODBUS_ADDRESS) return 0u;
  {
    uint16_t crc = blModbusCrc16(req, reqLen - 2u);
    if (req[reqLen - 2u] != (uint8_t)(crc & 0xFFu) || req[reqLen - 1u] != (uint8_t)(crc >> 8))
      return 0u;
  }

  wins[0].base = ELS_ID_BASE; wins[0].size = ELS_ID_SIZE;
  wins[0].regs = (uint16_t *)identity; wins[0].readOnly = 1;
  wins[1].base = ELS_BL_BASE; wins[1].size = ELS_BL_SIZE;
  wins[1].regs = core->regs;  wins[1].readOnly = 0;

  fc = req[1];
  switch (fc) {
    case FC_READ_HOLDING: {
      uint16_t addr, count;
      if (reqLen != 8u) return 0u;
      addr  = (uint16_t)((req[2] << 8) | req[3]);
      count = (uint16_t)((req[4] << 8) | req[5]);
      if (count < 1u || count > 125u) return exception(resp, fc, EXC_ILLEGAL_VALUE);
      if (mbResolveRange((uint16_t *)0, 0u, wins, 2u, addr, count, 0, &p) != 0u)
        return exception(resp, fc, EXC_ILLEGAL_ADDRESS);
      blCorePublish(core);
      resp[0] = BL_MODBUS_ADDRESS;
      resp[1] = fc;
      resp[2] = (uint8_t)(count * 2u);
      /* Ascending, one register at a time -- the ordering invariant. */
      for (uint16_t i = 0; i < count; i++) {
        resp[3u + 2u * i]     = (uint8_t)(p[i] >> 8);
        resp[3u + 2u * i + 1] = (uint8_t)(p[i] & 0xFFu);
      }
      return finish(resp, 3u + 2u * count);
    }

    case FC_WRITE_SINGLE: {
      uint16_t addr, value;
      if (reqLen != 8u) return 0u;
      addr  = (uint16_t)((req[2] << 8) | req[3]);
      value = (uint16_t)((req[4] << 8) | req[5]);
      if (mbResolveRange((uint16_t *)0, 0u, wins, 2u, addr, 1u, 1, &p) != 0u)
        return exception(resp, fc, EXC_ILLEGAL_ADDRESS);
      *p = value;
      blCoreService(core);          /* runs to completion before the reply */
      for (uint32_t i = 0; i < 6u; i++) resp[i] = req[i];
      return finish(resp, 6u);
    }

    case FC_WRITE_MULTIPLE: {
      uint16_t addr, count;
      uint8_t bytes;
      if (reqLen < 9u) return 0u;
      addr  = (uint16_t)((req[2] << 8) | req[3]);
      count = (uint16_t)((req[4] << 8) | req[5]);
      bytes = req[6];
      if (count < 1u || count > 123u || bytes != count * 2u || reqLen != 9u + (uint32_t)bytes)
        return exception(resp, fc, EXC_ILLEGAL_VALUE);
      if (mbResolveRange((uint16_t *)0, 0u, wins, 2u, addr, count, 1, &p) != 0u)
        return exception(resp, fc, EXC_ILLEGAL_ADDRESS);
      for (uint16_t i = 0; i < count; i++)
        p[i] = (uint16_t)((req[7u + 2u * i] << 8) | req[7u + 2u * i + 1u]);
      blCoreService(core);          /* runs to completion before the reply */
      for (uint32_t i = 0; i < 6u; i++) resp[i] = req[i];
      return finish(resp, 6u);
    }

    default:
      return exception(resp, fc, EXC_ILLEGAL_FUNCTION);
  }
}
