/*
 * bl_image.c -- see bl_image.h. The checks and their ORDER are mirrored by
 * scripts/reflex_image.py validate(); keep them in step.
 */
#include <string.h>
#include "bl_image.h"
#include "bl_port.h"

#define SRAM_BASE_ADDR 0x20000000u
#define SRAM_SIZE      0x20000u          /* 128 KB on the F411CE */

static uint32_t readWord(uint32_t addr)
{
  uint32_t w;
  memcpy(&w, blPortMap(addr), sizeof w);
  return w;
}

void blImageReadHeader(uint32_t base, elsImageHeader_t *out)
{
  memcpy(out, blPortMap(base + ELS_IMAGE_HEADER_OFFSET), sizeof *out);
}

uint32_t blImageCrc(uint32_t base, uint32_t length)
{
  const uint32_t crcWord = (ELS_IMAGE_HEADER_OFFSET + 12u) / 4u;
  uint32_t nwords = length / 4u;
  blPortCrcReset();
  for (uint32_t i = 0; i < nwords; i++) {
    uint32_t w = (i == crcWord) ? 0u : readWord(base + i * 4u);
    blPortCrcFeed(w);
  }
  return blPortCrcValue();
}

uint16_t blImageValidate(uint32_t base, elsImageHeader_t *hdr)
{
  elsImageHeader_t h;
  blImageReadHeader(base, &h);
  if (hdr) *hdr = h;
  if (h.magic != ELS_IMAGE_MAGIC)                  return ELS_BL_ERR_HDR_MAGIC;
  if (h.headerVersion != ELS_IMAGE_HEADER_VERSION) return ELS_BL_ERR_HDR_VERSION;
  if (h.imageLength < ELS_IMAGE_MIN_LENGTH ||
      h.imageLength > ELS_IMAGE_MAX_LENGTH ||
      (h.imageLength & 3u) != 0u)                  return ELS_BL_ERR_HDR_LENGTH;
  if (blImageCrc(base, h.imageLength) != h.crc32)  return ELS_BL_ERR_HDR_CRC;
  return ELS_BL_OK;
}

uint16_t blImageVectorsPlausible(uint32_t base)
{
  uint32_t msp   = readWord(base);
  uint32_t reset = readWord(base + 4u);
  if (msp < SRAM_BASE_ADDR || msp > SRAM_BASE_ADDR + SRAM_SIZE) return ELS_BL_ERR_VECTORS;
  if ((msp & 3u) != 0u)                                          return ELS_BL_ERR_VECTORS;
  if ((reset & 1u) == 0u)                                        return ELS_BL_ERR_VECTORS;
  if (reset < base || reset >= base + ELS_SLOT_SIZE)             return ELS_BL_ERR_VECTORS;
  return ELS_BL_OK;
}
