/*
 * bl_crc32_sw.c -- software implementation of the STM32 CRC unit's CRC-32,
 * as the blPortCrc* port functions. Linked by the NATIVE tests only; on the
 * chip src/bl_hw.c serves the same three functions from the CRC peripheral.
 *
 * Poly 0x04C11DB7, init 0xFFFFFFFF, each 32-bit word XORed into the
 * register and shifted out MSB first, no reflection, no final XOR. The
 * bit-serial form is used on purpose: it is the textbook definition and is
 * checked against scripts/reflex_image.py's table-driven version, which is
 * checked against the published CRC-unit result for a single zero word
 * (0xC704DD7B). Three implementations, one answer.
 */
#include "bl_port.h"

static uint32_t crcState = 0xFFFFFFFFu;

void blPortCrcReset(void)
{
  crcState = 0xFFFFFFFFu;
}

void blPortCrcFeed(uint32_t word)
{
  uint32_t crc = crcState ^ word;
  for (int i = 0; i < 32; i++) {
    if (crc & 0x80000000u) crc = (crc << 1) ^ 0x04C11DB7u;
    else                   crc = (crc << 1);
  }
  crcState = crc;
}

uint32_t blPortCrcValue(void)
{
  return crcState;
}
