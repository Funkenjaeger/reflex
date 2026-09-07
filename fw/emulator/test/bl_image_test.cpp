/*
 * bl_image_test.cpp -- image header validation (bootloader/core/bl_image.c)
 * over the mock flash. Every header field is mutated one at a time and must
 * produce ITS OWN distinct blResult code, in the documented check order, so
 * the host can name what is wrong rather than "bad image".
 *
 * MUTATIONS (seen red 2026-09-06, each reverted after):
 *   M1 blImageValidate: drop the CRC compare      -> "one flipped payload byte" and
 *                                                    "crc field +1" cases fail
 *   M2 blImageCrc: do not zero the crc word       -> "valid image validates" fails
 *   M3 blImageVectorsPlausible: drop Thumb check  -> "reset without Thumb bit" fails
 */
#include <cstdio>
#include "bl_mock_port.h"

extern "C" {
#include "bl_image.h"
}

static int failures = 0;
static void check(bool ok, const char *label) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", label);
    if (!ok) failures++;
}

static uint8_t *at(uint32_t addr) { return mock::flash + (addr - ELS_FLASH_BASE); }

int main() {
    const uint32_t base = ELS_STAGING_SLOT_BASE;
    const uint32_t len = 4096;

    mock::reset();
    uint32_t crc = mock::writeImage(base, len, 77);
    elsImageHeader_t h;
    check(blImageValidate(base, &h) == ELS_BL_OK, "valid image validates");
    check(h.crc32 == crc && h.imageLength == len && h.buildRev == 0x1234567u,
          "header fields read back (crc, length, buildRev)");
    check(blImageCrc(base, len) == crc, "blImageCrc recomputes the header CRC");
    check(blImageVectorsPlausible(base) == ELS_BL_OK, "plausible vectors accepted");

    /* Erased slot: the magic check comes first and is what a blank slot hits. */
    mock::reset();
    check(blImageValidate(base, nullptr) == ELS_BL_ERR_HDR_MAGIC, "erased slot -> HDR_MAGIC");

    /* Each field, one at a time. */
    mock::reset(); mock::writeImage(base, len, 77);
    at(base + ELS_IMAGE_HEADER_OFFSET)[0] ^= 0x01;
    check(blImageValidate(base, nullptr) == ELS_BL_ERR_HDR_MAGIC, "magic corrupted -> HDR_MAGIC");

    mock::reset(); mock::writeImage(base, len, 77);
    at(base + ELS_IMAGE_HEADER_OFFSET + 4)[0] = 2;
    check(blImageValidate(base, nullptr) == ELS_BL_ERR_HDR_VERSION, "header version 2 -> HDR_VERSION");

    mock::reset(); mock::writeImage(base, len, 77);
    { uint32_t bad = ELS_SLOT_SIZE + 4; memcpy(at(base + ELS_IMAGE_HEADER_OFFSET + 8), &bad, 4); }
    check(blImageValidate(base, nullptr) == ELS_BL_ERR_HDR_LENGTH, "length > slot -> HDR_LENGTH");

    mock::reset(); mock::writeImage(base, len, 77);
    { uint32_t bad = ELS_IMAGE_MIN_LENGTH - 4; memcpy(at(base + ELS_IMAGE_HEADER_OFFSET + 8), &bad, 4); }
    check(blImageValidate(base, nullptr) == ELS_BL_ERR_HDR_LENGTH, "length < header end -> HDR_LENGTH");

    mock::reset(); mock::writeImage(base, len, 77);
    { uint32_t bad = len + 2; memcpy(at(base + ELS_IMAGE_HEADER_OFFSET + 8), &bad, 4); }
    check(blImageValidate(base, nullptr) == ELS_BL_ERR_HDR_LENGTH, "length not x4 -> HDR_LENGTH");

    mock::reset(); mock::writeImage(base, len, 77);
    at(base + len - 1)[0] ^= 0x80;
    check(blImageValidate(base, nullptr) == ELS_BL_ERR_HDR_CRC, "one flipped payload byte (last) -> HDR_CRC");

    mock::reset(); mock::writeImage(base, len, 77);
    at(base + 0x100)[0] ^= 0x01;
    check(blImageValidate(base, nullptr) == ELS_BL_ERR_HDR_CRC, "one flipped byte inside the vector table -> HDR_CRC");

    mock::reset(); mock::writeImage(base, len, 77);
    at(base + ELS_IMAGE_HEADER_OFFSET + 12)[0] ^= 0x01;
    check(blImageValidate(base, nullptr) == ELS_BL_ERR_HDR_CRC, "crc field +-1 -> HDR_CRC");

    /* Bytes past imageLength are NOT covered: a longer slot with junk after
     * the image is still valid (the staging slot is erased to 0xFF anyway). */
    mock::reset(); mock::writeImage(base, len, 77);
    at(base + len)[0] = 0x00;
    check(blImageValidate(base, nullptr) == ELS_BL_OK, "bytes past imageLength are outside the CRC");

    /* Vectors. */
    mock::reset(); mock::writeImage(base, len, 77, 0x1234567u, false);
    check(blImageVectorsPlausible(base) == ELS_BL_ERR_VECTORS, "MSP outside SRAM / reset without Thumb bit -> VECTORS");
    mock::reset(); mock::writeImage(base, len, 77);
    { uint32_t rst = ELS_RUN_SLOT_BASE + 0x401u; memcpy(at(base + 4), &rst, 4); }
    check(blImageVectorsPlausible(base) == ELS_BL_ERR_VECTORS, "reset handler in another slot -> VECTORS");
    mock::reset(); mock::writeImage(base, len, 77);
    { uint32_t rst = base + 0x400u; memcpy(at(base + 4), &rst, 4); }
    check(blImageVectorsPlausible(base) == ELS_BL_ERR_VECTORS, "reset without Thumb bit -> VECTORS");
    mock::reset(); mock::writeImage(base, len, 77);
    { uint32_t msp = 0x20020004u; memcpy(at(base), &msp, 4); }
    check(blImageVectorsPlausible(base) == ELS_BL_ERR_VECTORS, "MSP past the top of SRAM -> VECTORS");

    /* Header struct is exactly the wire size the host assumes. */
    check(sizeof(elsImageHeader_t) == ELS_IMAGE_HEADER_SIZE, "elsImageHeader_t is 32 bytes");

    printf("%s\n", failures ? "FAILURES" : "all passed");
    return failures ? 1 : 0;
}
