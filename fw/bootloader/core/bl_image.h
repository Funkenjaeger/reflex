/*
 * bl_image.h -- image header validation, shared by every slot operation.
 * Portable; reads flash only through blPortMap and CRCs through blPortCrc*.
 */
#ifndef BL_IMAGE_H
#define BL_IMAGE_H

#include <stdint.h>
#include "els_identity.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Copy the header out of the slot at `base` (no validation). */
void blImageReadHeader(uint32_t base, elsImageHeader_t *out);

/* CRC32 of [base, base+length) with the header's crc32 word taken as 0.
 * `length` must be a multiple of 4 and >= ELS_IMAGE_MIN_LENGTH. */
uint32_t blImageCrc(uint32_t base, uint32_t length);

/* Validate the image in the slot at `base`: magic, header version, length
 * range and alignment, then the CRC. Returns ELS_BL_OK or the first
 * ELS_BL_ERR_HDR_* that applies, in that order, and fills *hdr if given. */
uint16_t blImageValidate(uint32_t base, elsImageHeader_t *hdr);

/* The two vectors the jump depends on: MSP inside SRAM, reset handler inside
 * this slot with the Thumb bit set. Returns ELS_BL_OK or ELS_BL_ERR_VECTORS. */
uint16_t blImageVectorsPlausible(uint32_t base);

#ifdef __cplusplus
}
#endif

#endif /* BL_IMAGE_H */
