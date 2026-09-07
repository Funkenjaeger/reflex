/*
 * bl_core_test.cpp -- the bootloader's decision logic, copy/swap state
 * machine, strike counter and Modbus command surface, driven through the
 * mock flash port with a power-loss injector.
 *
 * SECTIONS
 *   A. boot decisions on a fresh board: no image -> stay; valid RUN -> jump,
 *      attempt counted; STAY request honored and consumed.
 *   B. a full update through the Modbus byte layer (erase, write, verify,
 *      apply, jump): result codes, blSeq edge per operation, command cleared
 *      on consume, blStatus progression, TRIAL journaled, BACKUP = old RUN.
 *   C. confirmation and strikes: app writes (TAG,0) -> TRIAL promoted to
 *      IDLE; three unconfirmed attempts -> swap-back to BACKUP, REVERTED;
 *      a fourth strike-out with nothing to revert to -> STRUCK_OUT, stay.
 *   D. THE POWER-LOSS SWEEP: an apply is interrupted after N flash
 *      operations for EVERY N from the first erase to the last journal
 *      word, then "rebooted" on the resulting flash; at each N the invariant
 *      is that blCoreBoot never says JUMP unless RUN validates, and that the
 *      board converges (a second, uninterrupted boot ends in TRIAL with the
 *      new image or REVERTED/IDLE with the old one -- never torn).
 *   E. distinct failure codes: every ELS_BL_ERR_* the command path can
 *      produce is produced by exactly the situation it names.
 *   F. blSeq-before-payload: register offsets and the write order the host
 *      relies on, observed through a real FC3 frame.
 *
 * MUTATIONS (seen red 2026-09-06, each applied alone and reverted):
 *   M1 bl_core.c doApply: journal TRIAL BEFORE copySlot instead of after
 *      -> sweep D reports a JUMP into a torn RUN (the invariant this file
 *         exists for)
 *   M2 blCoreBoot: skip the in-flight resume switch
 *      -> sweep D "converges" fails (RUN torn after reboot, stays torn)
 *   M3 blCoreBoot: `attempts >= MAX` -> `attempts > MAX`
 *      -> C "third unconfirmed boot reverts" fails
 *   (blCoreService's result-before-seq write ORDER is not observable from a
 *    single-threaded test -- no reader can interleave the two stores -- so
 *    it is pinned by review and by the layout assertion in F, not by a
 *    mutation. The layout is what a torn FC3 read actually depends on.)
 *   M7 copySlot without the readback + re-validate -> E9 (stuck bit) fails;
 *      the power-loss sweep does NOT catch it, because a torn program never
 *      reports success -- which is why E9 exists
 *   M8 cmdJump without the RUN check -> A6 NO_RUN_IMAGE
 *   M5 cmdApply without stagedOk check -> E NOT_STAGED fails
 *   M6 backupIsDifferentAndValid always true -> C "REVERTED then strikes ->
 *      STRUCK_OUT, no ping-pong" fails
 */
#include <cstdio>
#include <vector>
#include "bl_mock_port.h"

extern "C" {
#include "bl_core.h"
#include "bl_image.h"
#include "bl_state.h"
#include "bl_modbus.h"
}

static int failures = 0;
static void check(bool ok, const char *label) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", label);
    if (!ok) failures++;
}

static const uint16_t identity[ELS_ID_SIZE] = ELS_ID_WINDOW_INIT(ELS_ID_STAGE_BOOTLOADER, 0u);

/* ---- a tiny Modbus master over blModbusHandle -------------------------- */

static std::vector<uint8_t> frame(std::vector<uint8_t> body) {
    uint16_t crc = blModbusCrc16(body.data(), (uint32_t)body.size());
    body.push_back((uint8_t)(crc & 0xFF));
    body.push_back((uint8_t)(crc >> 8));
    return body;
}

static std::vector<uint8_t> ask(blCore_t *c, const std::vector<uint8_t> &req) {
    uint8_t resp[BL_MODBUS_MAX_FRAME];
    uint32_t n = blModbusHandle(c, identity, req.data(), (uint32_t)req.size(), resp);
    return std::vector<uint8_t>(resp, resp + n);
}

static std::vector<uint16_t> readRegs(blCore_t *c, uint16_t addr, uint16_t count, uint8_t *exc = nullptr) {
    auto r = ask(c, frame({ 17, 3, (uint8_t)(addr >> 8), (uint8_t)addr, (uint8_t)(count >> 8), (uint8_t)count }));
    std::vector<uint16_t> out;
    if (exc) *exc = 0;
    if (r.size() >= 5 && (r[1] & 0x80)) { if (exc) *exc = r[2]; return out; }
    if (r.size() != 5u + 2u * count) return out;
    for (unsigned i = 0; i < count; i++) out.push_back((uint16_t)((r[3 + 2 * i] << 8) | r[4 + 2 * i]));
    return out;
}

static bool writeReg(blCore_t *c, uint16_t addr, uint16_t value) {
    auto req = frame({ 17, 6, (uint8_t)(addr >> 8), (uint8_t)addr, (uint8_t)(value >> 8), (uint8_t)value });
    auto r = ask(c, req);
    return r == req;
}

static bool writeRegs(blCore_t *c, uint16_t addr, const std::vector<uint16_t> &vals) {
    std::vector<uint8_t> body = { 17, 16, (uint8_t)(addr >> 8), (uint8_t)addr,
                                  (uint8_t)(vals.size() >> 8), (uint8_t)vals.size(), (uint8_t)(vals.size() * 2) };
    for (uint16_t v : vals) { body.push_back((uint8_t)(v >> 8)); body.push_back((uint8_t)v); }
    auto r = ask(c, frame(body));
    return r.size() == 8 && r[1] == 16;
}

/* Issue a command and return (seq moved?, result). */
struct Outcome { bool acked; uint16_t result; uint16_t status; };
static Outcome command(blCore_t *c, uint16_t cmd) {
    uint16_t seqBefore = readRegs(c, ELS_BL_BASE + ELS_BL_SEQ, 1)[0];
    writeReg(c, ELS_BL_BASE + ELS_BL_COMMAND, cmd);
    auto head = readRegs(c, ELS_BL_BASE, 4);
    Outcome o;
    o.acked = head[ELS_BL_SEQ] == (uint16_t)(seqBefore + 1);
    o.result = head[ELS_BL_RESULT];
    o.status = head[ELS_BL_STATUS];
    return o;
}

/* Stream `data` into STAGING the way modbus-flash.py does: one FC16 from
 * blCommand through blData per chunk. */
static bool streamImage(blCore_t *c, const uint8_t *data, uint32_t len, uint32_t crc) {
    for (uint32_t off = 0; off < len; off += 200) {
        uint32_t n = len - off; if (n > 200) n = 200;
        uint32_t addr = ELS_STAGING_SLOT_BASE + off;
        std::vector<uint16_t> regs = { ELS_BL_CMD_WRITE,
            (uint16_t)addr, (uint16_t)(addr >> 16), (uint16_t)len, (uint16_t)(len >> 16),
            (uint16_t)crc, (uint16_t)(crc >> 16), ELS_BL_SLOT_STAGING, 0, (uint16_t)n, 0, 0, 0 };
        for (uint32_t i = 0; i < n; i += 2) {
            uint16_t v = data[off + i] | (uint16_t)((i + 1 < n ? data[off + i + 1] : 0xFF) << 8);
            regs.push_back(v);
        }
        while (regs.size() < 13 + 100) regs.push_back(0xFFFF);
        uint16_t seqBefore = readRegs(c, ELS_BL_BASE + ELS_BL_SEQ, 1)[0];
        if (!writeRegs(c, ELS_BL_BASE + ELS_BL_COMMAND, regs)) return false;
        auto head = readRegs(c, ELS_BL_BASE, 4);
        if (head[ELS_BL_SEQ] != (uint16_t)(seqBefore + 1) || head[ELS_BL_RESULT] != ELS_BL_OK) return false;
        if (head[ELS_BL_COMMAND] != 0) return false;
    }
    return true;
}

/* Build an image into a host-side buffer (via the mock's builder in a
 * scratch slot, then copied out) so the "host" has bytes to stream. */
static std::vector<uint8_t> hostImage(uint32_t len, uint32_t seed, uint32_t rev, uint32_t *crc) {
    /* build in STAGING, read it out, erase again */
    *crc = mock::writeImage(ELS_STAGING_SLOT_BASE, len, seed, rev, true, ELS_RUN_SLOT_BASE);
    std::vector<uint8_t> v(blPortMap(ELS_STAGING_SLOT_BASE), blPortMap(ELS_STAGING_SLOT_BASE) + len);
    memset(mock::flash + (ELS_STAGING_SLOT_BASE - ELS_FLASH_BASE), 0xFF, ELS_SLOT_SIZE);
    return v;
}

static bool fullUpdate(blCore_t *c, const std::vector<uint8_t> &img, uint32_t crc, const char *tag) {
    Outcome o = command(c, ELS_BL_CMD_ERASE);
    if (!(o.acked && o.result == ELS_BL_OK)) { printf("  %s: erase failed %u\n", tag, o.result); return false; }
    if (!streamImage(c, img.data(), (uint32_t)img.size(), crc)) { printf("  %s: stream failed\n", tag); return false; }
    o = command(c, ELS_BL_CMD_VERIFY);
    if (!(o.acked && o.result == ELS_BL_OK)) { printf("  %s: verify failed %u\n", tag, o.result); return false; }
    o = command(c, ELS_BL_CMD_APPLY);
    if (!(o.acked && o.result == ELS_BL_OK)) { printf("  %s: apply failed %u\n", tag, o.result); return false; }
    return true;
}

int main() {
    const uint32_t LEN = 2048;   /* small images keep the sweep fast */

    /* ================= A. boot decisions ================= */
    {
        mock::reset();
        blCore_t c; blCoreInit(&c);
        check(blCoreBoot(&c) == BL_BOOT_STAY, "A1 blank flash: stay resident");
        check(c.regs[ELS_BL_RUN_VALID] == 0 && c.regs[ELS_BL_STATUS] == ELS_BL_STATUS_IDLE, "A1 ...runValid 0, status IDLE");
        check(mock::bkp[0] == 0, "A1 ...no attempt counted when staying");

        mock::reset();
        mock::writeImage(ELS_RUN_SLOT_BASE, LEN, 1);
        blCoreInit(&c);
        check(blCoreBoot(&c) == BL_BOOT_JUMP, "A2 valid RUN: jump");
        blCoreCountAttempt(&c);
        check(mock::bkp[0] == ELS_BOOT_ATTEMPTS_WORD(1), "A2 ...attempt counter TAG|1 written before the jump");

        /* second boot without app confirmation: count 2 */
        blCoreInit(&c);
        check(blCoreBoot(&c) == BL_BOOT_JUMP && c.attempts == 1, "A3 unconfirmed reboot reads count 1");
        blCoreCountAttempt(&c);
        check(mock::bkp[0] == ELS_BOOT_ATTEMPTS_WORD(2), "A3 ...and writes 2");

        /* a power cycle wipes the tag: back to 0, and NOT a confirmation */
        mock::bkp[0] = 0;
        blCoreInit(&c);
        check(blCoreBoot(&c) == BL_BOOT_JUMP && c.attempts == 0, "A4 untagged counter (power cycle) reads 0");

        /* STAY request */
        mock::bkp[1] = ELS_BOOT_REQ_STAY;
        blCoreInit(&c);
        check(blCoreBoot(&c) == BL_BOOT_STAY, "A5 STAY request: stay resident with a valid RUN");
        check(mock::bkp[1] == 0, "A5 ...request consumed");
        check(c.regs[ELS_BL_RUN_VALID] == 1, "A5 ...runValid still 1");
        blCoreInit(&c);
        check(blCoreBoot(&c) == BL_BOOT_JUMP, "A5 ...next boot jumps again");

        /* corrupt RUN: stay, and a JUMP command is refused with its own code */
        mock::flash[ELS_RUN_SLOT_BASE - ELS_FLASH_BASE + 0x300] ^= 1;
        blCoreInit(&c);
        check(blCoreBoot(&c) == BL_BOOT_STAY, "A6 one flipped bit in RUN: stay");
        Outcome o = command(&c, ELS_BL_CMD_JUMP);
        check(o.acked && o.result == ELS_BL_ERR_NO_RUN_IMAGE, "A6 ...JUMP command -> NO_RUN_IMAGE");
    }

    /* ================= B. a full update over Modbus ================= */
    {
        mock::reset();
        uint32_t oldCrc = mock::writeImage(ELS_RUN_SLOT_BASE, LEN, 1, 0x1111111u);
        mock::bkp[1] = ELS_BOOT_REQ_STAY;
        blCore_t c; blCoreInit(&c);
        check(blCoreBoot(&c) == BL_BOOT_STAY, "B0 resident by request");

        /* identity window via the byte layer */
        auto id = readRegs(&c, ELS_ID_BASE, ELS_ID_SIZE);
        check(id.size() == 8 && id[0] == ELS_ID_MAGIC && id[1] == ELS_ID_STAGE_BOOTLOADER && id[6] == 0,
              "B1 identity window over Modbus: magic, stage 1, app protocol 0");
        uint8_t exc = 0;
        readRegs(&c, 0, 4, &exc);
        check(exc == 2, "B1 register 0 is exception 2 in the bootloader");
        readRegs(&c, ELS_ID_BASE - 1, 2, &exc);
        check(exc == 2, "B1 straddling into the identity window is exception 2");
        check(!writeReg(&c, ELS_ID_BASE, 1), "B1 writing the identity window is refused");

        uint32_t crc;
        auto img = hostImage(LEN, 2, 0x2222222u, &crc);
        check(mock::slotCrc(ELS_RUN_SLOT_BASE) == oldCrc, "B2 host image built without disturbing RUN");

        Outcome o = command(&c, ELS_BL_CMD_ERASE);
        check(o.acked && o.result == ELS_BL_OK && o.status == ELS_BL_STATUS_IDLE, "B3 ERASE acks OK");
        check(readRegs(&c, ELS_BL_BASE + ELS_BL_COMMAND, 1)[0] == 0, "B3 ...blCommand cleared on consume");

        check(streamImage(&c, img.data(), (uint32_t)img.size(), crc), "B4 stream: every chunk acks OK with seq +1");
        check(memcmp(blPortMap(ELS_STAGING_SLOT_BASE), img.data(), img.size()) == 0, "B4 ...STAGING holds the image");

        o = command(&c, ELS_BL_CMD_VERIFY);
        check(o.acked && o.result == ELS_BL_OK && o.status == ELS_BL_STATUS_STAGED, "B5 VERIFY -> OK, status STAGED");

        o = command(&c, ELS_BL_CMD_APPLY);
        check(o.acked && o.result == ELS_BL_OK && o.status == ELS_BL_STATUS_READY_TO_JUMP, "B6 APPLY -> OK, READY_TO_JUMP");
        check(blStateRead() == ELS_BL_STATE_TRIAL, "B6 ...journal says TRIAL");
        check(readRegs(&c, ELS_BL_BASE + ELS_BL_COPY_STATE, 1)[0] == ELS_BL_STATE_TRIAL, "B6 ...blCopyState publishes TRIAL");
        check(mock::slotCrc(ELS_RUN_SLOT_BASE) == crc, "B6 ...RUN holds the new image");
        check(mock::slotCrc(ELS_BACKUP_SLOT_BASE) == oldCrc, "B6 ...BACKUP holds the old image");
        check(blImageValidate(ELS_BACKUP_SLOT_BASE, nullptr) == ELS_BL_OK, "B6 ...and it validates");
        check(readRegs(&c, ELS_BL_BASE + ELS_BL_RUN_VALID, 1)[0] == 1, "B6 ...blRunValid 1");

        o = command(&c, ELS_BL_CMD_JUMP);
        check(o.acked && o.result == ELS_BL_OK && c.jumpPending, "B7 JUMP -> OK, jump pending after the reply");
        blCoreCountAttempt(&c);
        check(mock::bkp[0] == ELS_BOOT_ATTEMPTS_WORD(1), "B7 ...attempt 1 counted");

        /* STAY is a no-op that acks */
        c.jumpPending = 0;
        o = command(&c, ELS_BL_CMD_STAY);
        check(o.acked && o.result == ELS_BL_OK, "B8 STAY acks OK");

        /* ---- C. confirmation and strikes, continuing from B ---- */
        /* the app came alive and cleared the counter */
        mock::bkp[0] = ELS_BOOT_ATTEMPTS_WORD(0);
        blCoreInit(&c);
        check(blCoreBoot(&c) == BL_BOOT_JUMP, "C1 confirmed TRIAL boots");
        check(blStateRead() == ELS_BL_STATE_IDLE, "C1 ...and is promoted to IDLE");

        /* Now a second update that never comes alive. */
        mock::bkp[1] = ELS_BOOT_REQ_STAY;
        blCoreInit(&c); blCoreBoot(&c);
        uint32_t crc3; auto img3 = hostImage(LEN, 3, 0x3333333u, &crc3);
        check(fullUpdate(&c, img3, crc3, "C2"), "C2 second update applied");
        check(mock::slotCrc(ELS_BACKUP_SLOT_BASE) == crc, "C2 ...BACKUP now holds image 2");
        command(&c, ELS_BL_CMD_JUMP); blCoreCountAttempt(&c);
        check(mock::bkp[0] == ELS_BOOT_ATTEMPTS_WORD(1), "C2 attempt 1");
        blCoreInit(&c); check(blCoreBoot(&c) == BL_BOOT_JUMP, "C2 boot 2 still jumps"); blCoreCountAttempt(&c);
        blCoreInit(&c); check(blCoreBoot(&c) == BL_BOOT_JUMP, "C2 boot 3 still jumps"); blCoreCountAttempt(&c);
        check(mock::bkp[0] == ELS_BOOT_ATTEMPTS_WORD(3), "C2 ...counter reads 3");
        blCoreInit(&c);
        blBootDecision_t d = blCoreBoot(&c);
        check(d == BL_BOOT_JUMP, "C3 third unconfirmed boot reverts and jumps");
        check(blStateRead() == ELS_BL_STATE_REVERTED, "C3 ...journal REVERTED");
        check(mock::slotCrc(ELS_RUN_SLOT_BASE) == crc, "C3 ...RUN holds image 2 again");
        check(c.attempts == 0, "C3 ...reverted image starts with a fresh counter");
        blCoreCountAttempt(&c);
        check(mock::bkp[0] == ELS_BOOT_ATTEMPTS_WORD(1), "C3 ...written as 1");

        /* The reverted image also fails to come alive: no ping-pong. */
        blCoreInit(&c); check(blCoreBoot(&c) == BL_BOOT_JUMP, "C4 reverted boot 2"); blCoreCountAttempt(&c);
        blCoreInit(&c); check(blCoreBoot(&c) == BL_BOOT_JUMP, "C4 reverted boot 3"); blCoreCountAttempt(&c);
        blCoreInit(&c);
        check(blCoreBoot(&c) == BL_BOOT_STAY, "C4 REVERTED then strikes -> STRUCK_OUT, no ping-pong");
        check(c.regs[ELS_BL_STATUS] == ELS_BL_STATUS_STRUCK_OUT, "C4 ...status STRUCK_OUT");
        check(mock::slotCrc(ELS_RUN_SLOT_BASE) == crc, "C4 ...RUN untouched");
        /* the host can still force it */
        o = command(&c, ELS_BL_CMD_JUMP);
        check(o.acked && o.result == ELS_BL_OK && c.jumpPending, "C4 ...host JUMP overrides the strike-out");

        /* Confirmation of a REVERTED image promotes to IDLE too. */
        mock::bkp[0] = ELS_BOOT_ATTEMPTS_WORD(0);
        blCoreInit(&c); blCoreBoot(&c);
        check(blStateRead() == ELS_BL_STATE_IDLE, "C5 confirmed REVERTED -> IDLE");

        /* Strike-out with BACKUP == RUN (nothing different to revert to). */
        mock::bkp[0] = ELS_BOOT_ATTEMPTS_WORD(3);
        memcpy(mock::flash + (ELS_BACKUP_SLOT_BASE - ELS_FLASH_BASE), mock::flash + (ELS_RUN_SLOT_BASE - ELS_FLASH_BASE), LEN);
        blCoreInit(&c);
        check(blCoreBoot(&c) == BL_BOOT_STAY && c.regs[ELS_BL_STATUS] == ELS_BL_STATUS_STRUCK_OUT,
              "C6 strikes with BACKUP identical to RUN -> STRUCK_OUT, no pointless copy");
        check(blStateRead() == ELS_BL_STATE_IDLE, "C6 ...journal untouched");
    }

    /* ================= D. the power-loss sweep ================= */
    {
        mock::reset();
        uint32_t oldCrc = mock::writeImage(ELS_RUN_SLOT_BASE, LEN, 10, 0xAAAAAAAu);
        uint32_t newCrc; auto img = hostImage(LEN, 11, 0xBBBBBBBu, &newCrc);
        /* Stage it once, uninterrupted, and count the ops of an apply. */
        blCore_t c; mock::bkp[1] = ELS_BOOT_REQ_STAY; blCoreInit(&c); blCoreBoot(&c);
        command(&c, ELS_BL_CMD_ERASE);
        streamImage(&c, img.data(), (uint32_t)img.size(), newCrc);
        command(&c, ELS_BL_CMD_VERIFY);
        std::vector<uint8_t> stagedFlash(mock::flash, mock::flash + sizeof mock::flash);
        mock::opsCount = 0;
        Outcome o = command(&c, ELS_BL_CMD_APPLY);
        unsigned applyOps = mock::opsCount;
        check(o.result == ELS_BL_OK && applyOps > 2 * LEN / 4, "D0 uninterrupted apply succeeds; op count recorded");
        printf("  apply = %u flash operations\n", applyOps);

        unsigned jumpsIntoTorn = 0, nonConverged = 0, tornAtInterrupt = 0, endedNew = 0, endedOld = 0;
        for (unsigned n = 0; n < applyOps; n++) {
            /* restore the staged-but-not-applied flash and the bkp regs */
            memcpy(mock::flash, stagedFlash.data(), sizeof mock::flash);
            memset(mock::bkp, 0, sizeof mock::bkp);
            blCoreInit(&c);
            c.stagedOk = 1;                     /* VERIFY passed before the apply */
            mock::powerLossIn = (long)n;
            if (setjmp(mock::powerLossJmp) == 0) {
                c.regs[ELS_BL_COMMAND] = ELS_BL_CMD_APPLY;
                blCoreService(&c);
                printf("  n=%u: apply completed without power loss?!\n", n);
                failures++;
                continue;
            }
            /* lights out after n ops. Was RUN torn at this instant? */
            if (blImageValidate(ELS_RUN_SLOT_BASE, nullptr) != ELS_BL_OK) tornAtInterrupt++;
            mock::powerLossIn = -1;
            /* reboot 1 */
            blCoreInit(&c);
            blBootDecision_t d = blCoreBoot(&c);
            bool runOk = blImageValidate(ELS_RUN_SLOT_BASE, nullptr) == ELS_BL_OK &&
                         blImageVectorsPlausible(ELS_RUN_SLOT_BASE) == ELS_BL_OK;
            if (d == BL_BOOT_JUMP && !runOk) { jumpsIntoTorn++; printf("  n=%u: JUMP into an invalid RUN\n", n); }
            /* reboot 2, no interruption: must be settled */
            blCoreInit(&c);
            d = blCoreBoot(&c);
            uint8_t st = blStateRead();
            uint32_t runCrc = mock::slotCrc(ELS_RUN_SLOT_BASE);
            bool settled = runOk && d == BL_BOOT_JUMP &&
                           ((st == ELS_BL_STATE_TRIAL && runCrc == newCrc) ||
                            (st == ELS_BL_STATE_REVERTED && runCrc == oldCrc) ||
                            (st == ELS_BL_STATE_IDLE && runCrc == oldCrc));
            if (!settled) { nonConverged++; printf("  n=%u: not converged: state %u run ok %d crc %08x\n", n, st, runOk, runCrc); }
            else if (runCrc == newCrc) endedNew++; else endedOld++;
        }
        printf("  sweep: %u interruptions, RUN torn at %u of them, ended new %u / old %u\n",
               applyOps, tornAtInterrupt, endedNew, endedOld);
        check(jumpsIntoTorn == 0, "D1 no interruption point yields a JUMP into an invalid RUN");
        check(nonConverged == 0, "D2 every interruption point converges to a valid RUN (new on TRIAL, old on REVERTED/IDLE)");
        check(tornAtInterrupt > 0, "D3 the sweep actually produced torn RUN slots (the injector is live)");
        check(endedNew > 0 && endedOld > 0, "D4 both outcomes occur: resumed copies and reverts");
    }

    /* ================= E. distinct failure codes ================= */
    {
        mock::reset();
        mock::writeImage(ELS_RUN_SLOT_BASE, LEN, 20);
        mock::bkp[1] = ELS_BOOT_REQ_STAY;
        blCore_t c; blCoreInit(&c); blCoreBoot(&c);
        Outcome o;

        o = command(&c, 99);
        check(o.acked && o.result == ELS_BL_ERR_BAD_COMMAND, "E bad command -> BAD_COMMAND");
        o = command(&c, ELS_BL_CMD_APPLY);
        check(o.acked && o.result == ELS_BL_ERR_NOT_STAGED, "E APPLY before VERIFY -> NOT_STAGED");
        o = command(&c, ELS_BL_CMD_VERIFY);
        check(o.acked && o.result == ELS_BL_ERR_HDR_MAGIC && o.status == ELS_BL_STATUS_BAD_IMAGE,
              "E VERIFY of an erased STAGING -> HDR_MAGIC, status BAD_IMAGE");

        writeReg(&c, ELS_BL_BASE + ELS_BL_SLOT, ELS_BL_SLOT_RUN);
        o = command(&c, ELS_BL_CMD_ERASE);
        check(o.acked && o.result == ELS_BL_ERR_SLOT, "E ERASE with blSlot = RUN -> SLOT (RUN is never a target)");
        o = command(&c, ELS_BL_CMD_WRITE);
        check(o.acked && o.result == ELS_BL_ERR_SLOT, "E WRITE with blSlot = RUN -> SLOT");
        writeReg(&c, ELS_BL_BASE + ELS_BL_SLOT, ELS_BL_SLOT_STAGING);

        writeReg(&c, ELS_BL_BASE + ELS_BL_WRITE_LEN, 0);
        o = command(&c, ELS_BL_CMD_WRITE);
        check(o.acked && o.result == ELS_BL_ERR_WRITE_LEN, "E WRITE len 0 -> WRITE_LEN");
        writeReg(&c, ELS_BL_BASE + ELS_BL_WRITE_LEN, 202);
        o = command(&c, ELS_BL_CMD_WRITE);
        check(o.acked && o.result == ELS_BL_ERR_WRITE_LEN, "E WRITE len 202 -> WRITE_LEN");
        writeReg(&c, ELS_BL_BASE + ELS_BL_WRITE_LEN, 100);
        blCoreSetReg32(&c, ELS_BL_TARGET_ADDR_LO, ELS_RUN_SLOT_BASE);
        o = command(&c, ELS_BL_CMD_WRITE);
        check(o.acked && o.result == ELS_BL_ERR_ADDR_RANGE, "E WRITE into RUN -> ADDR_RANGE");
        blCoreSetReg32(&c, ELS_BL_TARGET_ADDR_LO, ELS_STAGING_SLOT_BASE + ELS_SLOT_SIZE - 96);
        o = command(&c, ELS_BL_CMD_WRITE);
        check(o.acked && o.result == ELS_BL_ERR_ADDR_RANGE, "E WRITE running off the end of STAGING -> ADDR_RANGE");
        blCoreSetReg32(&c, ELS_BL_TARGET_ADDR_LO, ELS_STAGING_SLOT_BASE + 2);
        o = command(&c, ELS_BL_CMD_WRITE);
        check(o.acked && o.result == ELS_BL_ERR_ADDR_RANGE, "E WRITE unaligned -> ADDR_RANGE");

        /* Host cross-checks: image good, host numbers wrong. */
        uint32_t crc; auto img = hostImage(LEN, 21, 0x4444444u, &crc);
        command(&c, ELS_BL_CMD_ERASE);
        streamImage(&c, img.data(), (uint32_t)img.size(), crc);
        blCoreSetReg32(&c, ELS_BL_IMAGE_LEN_LO, LEN + 4);
        o = command(&c, ELS_BL_CMD_VERIFY);
        check(o.acked && o.result == ELS_BL_ERR_HOST_LEN, "E VERIFY with the host length wrong -> HOST_LEN");
        blCoreSetReg32(&c, ELS_BL_IMAGE_LEN_LO, LEN);
        blCoreSetReg32(&c, ELS_BL_IMAGE_CRC_LO, crc ^ 1);
        o = command(&c, ELS_BL_CMD_VERIFY);
        check(o.acked && o.result == ELS_BL_ERR_HOST_CRC, "E VERIFY with the host CRC wrong -> HOST_CRC");
        blCoreSetReg32(&c, ELS_BL_IMAGE_CRC_LO, crc);
        o = command(&c, ELS_BL_CMD_VERIFY);
        check(o.acked && o.result == ELS_BL_OK, "E VERIFY with matching host numbers -> OK");

        /* A WRITE over already-programmed flash that cannot clear the bits it
         * needs: the readback mismatch is its own code. */
        blCoreSetReg32(&c, ELS_BL_TARGET_ADDR_LO, ELS_STAGING_SLOT_BASE);
        writeReg(&c, ELS_BL_BASE + ELS_BL_WRITE_LEN, 4);
        writeReg(&c, ELS_BL_BASE + ELS_BL_DATA, 0xFFFF);
        writeReg(&c, ELS_BL_BASE + ELS_BL_DATA + 1, 0xFFFF);
        o = command(&c, ELS_BL_CMD_WRITE);
        check(o.acked && o.result == ELS_BL_ERR_FLASH_VERIFY, "E WRITE 0xFFFFFFFF over programmed flash -> FLASH_VERIFY");
        check(c.stagedOk == 0, "E ...any WRITE invalidates the staged verdict");
        o = command(&c, ELS_BL_CMD_APPLY);
        check(o.acked && o.result == ELS_BL_ERR_NOT_STAGED, "E ...so APPLY is NOT_STAGED until VERIFY runs again");

        /* A flash cell that will not program (stuck bit) and a controller
         * that does not say so: the readback verify is the only thing that
         * can turn that into COPY_FAILED instead of a TRIAL of a corrupt
         * image. The revert then hits the same cell, so the outcome is an
         * IDLE journal, RUN invalid, and no jump -- resident, recoverable
         * over SWD, never running garbage. */
        mock::reset();
        mock::writeImage(ELS_RUN_SLOT_BASE, LEN, 40, 0x5555555u);
        mock::bkp[1] = ELS_BOOT_REQ_STAY;
        blCoreInit(&c); blCoreBoot(&c);
        { uint32_t crc9; auto img9 = hostImage(LEN, 41, 0x6666666u, &crc9);
          command(&c, ELS_BL_CMD_ERASE);
          streamImage(&c, img9.data(), (uint32_t)img9.size(), crc9);
          o = command(&c, ELS_BL_CMD_VERIFY);
          check(o.acked && o.result == ELS_BL_OK, "E9 staged a good image");
          /* Pick a word inside BOTH images where bit 0 must be 0, so the
           * stuck-at-1 cell defeats the copy AND the revert deterministically. */
          for (uint32_t off = 0x400u; off < LEN; off += 4u) {
              uint32_t wNew, wOld;
              memcpy(&wNew, img9.data() + off, 4);
              memcpy(&wOld, blPortMap(ELS_BACKUP_SLOT_BASE + off), 4);   /* BACKUP is erased; use RUN */
              memcpy(&wOld, blPortMap(ELS_RUN_SLOT_BASE + off), 4);
              if ((wNew & 1u) == 0u && (wOld & 1u) == 0u) { mock::stuckAddr = ELS_RUN_SLOT_BASE + off; break; }
          }
          check(mock::stuckAddr != 0, "E9 found a word both images need clear at bit 0");
          o = command(&c, ELS_BL_CMD_APPLY);
          check(o.acked && o.result == ELS_BL_ERR_COPY_FAILED, "E9 APPLY with a stuck bit in RUN -> COPY_FAILED (readback caught it)");
          check(o.status != ELS_BL_STATUS_READY_TO_JUMP && c.regs[ELS_BL_RUN_VALID] == 0, "E9 ...not READY_TO_JUMP, runValid 0");
          check(blStateRead() == ELS_BL_STATE_IDLE, "E9 ...journal settled to IDLE (revert hit the same cell)");
          mock::stuckAddr = 0;
          blCoreInit(&c);
          check(blCoreBoot(&c) == BL_BOOT_STAY, "E9 ...next boot stays resident rather than jumping into the corrupt RUN");
        }

        /* The read-only registers are republished on every read: a host that
         * scribbles on blRunValid / blAttempts without a command (an FC16
         * covering the head does exactly that) must read the truth back. */
        check(writeReg(&c, ELS_BL_BASE + ELS_BL_RUN_VALID, 7) && writeReg(&c, ELS_BL_BASE + ELS_BL_ATTEMPTS, 9),
              "E10 host can write over the RO registers (they are inside the writable window)");
        { auto h = readRegs(&c, ELS_BL_BASE, 16);
          check(h[ELS_BL_RUN_VALID] == 0 && h[ELS_BL_ATTEMPTS] == 0 && h[ELS_BL_ACTIVE_SLOT] == ELS_BL_SLOT_RUN,
                "E10 ...but an FC3 republishes runValid / attempts / activeSlot from the core state"); }

        /* Modbus-level refusals */
        auto r = ask(&c, frame({ 17, 4, 0x08, 0x00, 0, 8 }));
        check(r.size() == 5 && r[1] == 0x84 && r[2] == 1, "E FC4 -> exception 1 (illegal function)");
        r = ask(&c, frame({ 17, 3, 0x08, 0x00, 0, 126 }));
        check(r.size() == 5 && r[1] == 0x83 && r[2] == 3, "E FC3 count 126 -> exception 3");
        r = ask(&c, { 17, 3, 0x08, 0x00, 0, 8, 0x12, 0x34 });
        check(r.empty(), "E bad CRC -> silence");
        r = ask(&c, frame({ 18, 3, 0x08, 0x00, 0, 8 }));
        check(r.empty(), "E other slave address -> silence");
    }

    /* ================= F. blSeq before payload ================= */
    {
        mock::reset();
        mock::writeImage(ELS_RUN_SLOT_BASE, LEN, 30);
        mock::bkp[1] = ELS_BOOT_REQ_STAY;
        blCore_t c; blCoreInit(&c); blCoreBoot(&c);
        check(ELS_BL_SEQ == 1 && ELS_BL_RESULT == 2, "F blSeq at +1, blResult at +2: seq below the payload it counts");

        /* After a command, seq and result have moved TOGETHER from the
         * host's view: one FC3 over the head shows the new seq beside the
         * result it counts and a cleared command. A bad command is used
         * because it touches nothing else. */
        uint16_t seq0 = c.regs[ELS_BL_SEQ];
        c.regs[ELS_BL_COMMAND] = 99;
        blCoreService(&c);
        auto head = readRegs(&c, ELS_BL_BASE, 4);
        check(head[ELS_BL_SEQ] == (uint16_t)(seq0 + 1) && head[ELS_BL_RESULT] == ELS_BL_ERR_BAD_COMMAND,
              "F one FC3 over [status,seq,result,command] shows the new seq with the new result");
        check(head[ELS_BL_COMMAND] == 0, "F ...and the command already cleared");

        /* And the result is REPLACED, not sticky: the next operation's
         * outcome rides the next seq edge. */
        c.regs[ELS_BL_COMMAND] = ELS_BL_CMD_STAY;
        uint16_t seq1 = c.regs[ELS_BL_SEQ];
        blCoreService(&c);
        check(c.regs[ELS_BL_SEQ] == (uint16_t)(seq1 + 1) && c.regs[ELS_BL_RESULT] == ELS_BL_OK,
              "F STAY: seq +1 with result OK (result replaced the BAD_COMMAND before the edge)");
    }

    printf("%s\n", failures ? "FAILURES" : "all passed");
    return failures ? 1 : 0;
}
