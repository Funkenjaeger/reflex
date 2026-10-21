/*
 * mb_app_test.cpp -- the application's Modbus slave (Core/Src/mb_app.c): the
 * shared RTU core bound by the app's port (Core/mb_port/mb_rtu_port.h) to the
 * real rampsSharedData_t and the generated access table, driven through
 * mbAppService() over a scripted transport standing in for mb_app_hw.h.
 *
 * mb_rtu_test covers the core against a recording port. This covers what the
 * app's port decides, which is what the host sees:
 *   1. the map: registers 0..N-1 are rampsSharedData_t, read big-endian and
 *      written in place; a read one past the end is exception 2;
 *   2. the identity window at ELS_ID_BASE reads back what RampsStart hands
 *      over, and refuses writes (exception 2);
 *   3. the bootloader control window is NOT served: exception 2 at
 *      ELS_BL_BASE, the "this is the app" signal modbus-flash.py relies on;
 *   4. ro_firmware: FC 6 and FC 16 to a firmware-owned register (commandSeq,
 *      scales[0].position) are refused and write nothing; a host-written
 *      neighbor (command) lands;
 *   4b. host reset: the four firmware-owned registers a host resets
 *      (executionCyclesPeak, stepPulseMinCycles, stepPulseRuntCount,
 *      diagEndReason; `host_reset: true` in the schema) take a 0, by FC 6 per
 *      register and by FC 16 over the whole field, and refuse anything else;
 *      a 0 to a firmware-owned register without the flag is still refused;
 *   5. the reply counter that clears the boot-attempt counter moves on every
 *      reply, exceptions included, and not on a frame dropped for its CRC or
 *      for another slave's address; those land in the receiver counters;
 *   6. no frame is taken while the previous reply is still leaving.
 */
extern "C" {
#include "Ramps.h"
#include "mb_app.h"
#include "mb_app_hw.h"
#include "mb_rtu.h"
#include "els_identity.h"
extern uint16_t mbAppDiag[ELS_BL_DIAG_REGS];
}

#include <cstdio>
#include <cstring>
#include <cstddef>
#include <deque>
#include <vector>

static int failures = 0;
static void check(bool ok, const char *label) {
    if (!ok) { printf("[FAIL] %s\n", label); failures++; }
}

/* ---- the scripted transport -------------------------------------------- */

static std::deque<std::vector<uint8_t>> inbox;   /* frames the "line" will deliver */
static std::vector<uint8_t> lastSent;
static int txBusy = 0;
static uint16_t *hwDiag;

extern "C" {
void mbHwInit(uint16_t *diag) { hwDiag = diag; }
uint32_t mbHwPoll(uint8_t *frame, uint32_t frameMax) {
    if (inbox.empty()) return 0;
    std::vector<uint8_t> f = inbox.front();
    inbox.pop_front();
    if (f.size() > frameMax) return 0;
    memcpy(frame, f.data(), f.size());
    return (uint32_t)f.size();
}
int mbHwTxBusy(void) { return txBusy; }
void mbHwSend(const uint8_t *data, uint32_t len) { lastSent.assign(data, data + len); }
}

/* ---- a tiny Modbus master ---------------------------------------------- */

static std::vector<uint8_t> frame(std::vector<uint8_t> body) {
    uint16_t crc = mbRtuCrc16(body.data(), (uint32_t)body.size());
    body.push_back((uint8_t)(crc & 0xFF));
    body.push_back((uint8_t)(crc >> 8));
    return body;
}
static std::vector<uint8_t> fc3(uint16_t addr, uint16_t count) {
    return frame({ 17, 3, (uint8_t)(addr >> 8), (uint8_t)addr, (uint8_t)(count >> 8), (uint8_t)count });
}
static std::vector<uint8_t> fc6(uint16_t addr, uint16_t value) {
    return frame({ 17, 6, (uint8_t)(addr >> 8), (uint8_t)addr, (uint8_t)(value >> 8), (uint8_t)value });
}
static std::vector<uint8_t> fc16(uint16_t addr, const std::vector<uint16_t> &vals) {
    std::vector<uint8_t> body = { 17, 16, (uint8_t)(addr >> 8), (uint8_t)addr,
                                  (uint8_t)(vals.size() >> 8), (uint8_t)vals.size(), (uint8_t)(vals.size() * 2) };
    for (uint16_t v : vals) { body.push_back((uint8_t)(v >> 8)); body.push_back((uint8_t)v); }
    return frame(body);
}

/* Deliver one frame and service the slave once. Returns the reply, empty if
 * none was sent. */
static std::vector<uint8_t> ask(const std::vector<uint8_t> &req) {
    lastSent.clear();
    inbox.push_back(req);
    (void)mbAppService();
    return lastSent;
}
static uint8_t excOf(const std::vector<uint8_t> &r, uint8_t fc) {
    if (r.size() == 5 && r[0] == 17 && r[1] == (uint8_t)(fc | 0x80)) return r[2];
    return 0;
}
static uint16_t regOf(const std::vector<uint8_t> &r, unsigned i) {
    return (uint16_t)((r[3 + 2 * i] << 8) | r[4 + 2 * i]);
}

#define REG(field) ((uint16_t)(offsetof(rampsSharedData_t, field) / sizeof(uint16_t)))

static rampsSharedData_t shared;
static const uint16_t identity[ELS_ID_SIZE] = ELS_ID_WINDOW_INIT(ELS_ID_STAGE_APP, ELS_PROTOCOL_VERSION);

int main() {
    const uint16_t N = (uint16_t)(sizeof(rampsSharedData_t) / sizeof(uint16_t));
    uint16_t *regs = (uint16_t *)&shared;
    for (uint16_t r = 0; r < N; r++) regs[r] = (uint16_t)(0x2000 + r);
    mbAppInit(regs, N, identity);
    check(hwDiag == mbAppDiag, "0 the transport is handed the slave's receiver counters");

    /* 1. The map. */
    {
        std::vector<uint8_t> r = ask(fc3(0, 4));
        check(r.size() == 3 + 8 + 2 && r[2] == 8 && regOf(r, 0) == 0x2000 && regOf(r, 3) == 0x2003,
              "1 FC3 at 0 reads rampsSharedData_t, big-endian");
        r = ask(fc3((uint16_t)(N - 1), 1));
        check(r.size() == 7 && regOf(r, 0) == (uint16_t)(0x2000 + N - 1), "1 the last register is served");
        check(excOf(ask(fc3(N, 1)), 3) == MB_EXC_ILLEGAL_ADDRESS, "1 one past the end is exception 2");
        uint16_t sp = REG(servo.maxSpeed);
        r = ask(fc16(sp, { 0x1234, 0x5678 }));
        check(r.size() == 8 && regs[sp] == 0x1234 && regs[sp + 1] == 0x5678, "1 FC16 to servo.maxSpeed lands");
    }

    /* 2. The identity window. */
    {
        std::vector<uint8_t> r = ask(fc3(ELS_ID_BASE, ELS_ID_SIZE));
        bool same = r.size() == 3 + 2 * ELS_ID_SIZE + 2;
        for (unsigned i = 0; same && i < ELS_ID_SIZE; i++) same = regOf(r, i) == identity[i];
        check(same, "2 FC3 at ELS_ID_BASE reads the identity window handed over");
        check(excOf(ask(fc6(ELS_ID_BASE, 0)), 6) == MB_EXC_ILLEGAL_ADDRESS, "2 the identity window refuses FC6");
        check(excOf(ask(fc16(ELS_ID_BASE, { 0 })), 16) == MB_EXC_ILLEGAL_ADDRESS, "2 ...and FC16");
    }

    /* 3. The bootloader window is the bootloader's. */
    check(excOf(ask(fc3(ELS_BL_BASE, 16)), 3) == MB_EXC_ILLEGAL_ADDRESS,
          "3 FC3 at ELS_BL_BASE is exception 2 on the app");

    /* 4. ro_firmware. */
    {
        uint16_t seq = REG(elsStop.commandSeq);
        regs[seq] = 7;
        check(excOf(ask(fc6(seq, 99)), 6) == MB_EXC_ILLEGAL_ADDRESS && regs[seq] == 7,
              "4 FC6 to elsStop.commandSeq is refused and writes nothing");
        uint16_t pos = REG(scales[0].position);
        regs[pos] = 0x0101; regs[pos + 1] = 0x0202;
        check(excOf(ask(fc16(pos, { 0, 0 })), 16) == MB_EXC_ILLEGAL_ADDRESS &&
              regs[pos] == 0x0101 && regs[pos + 1] == 0x0202,
              "4 FC16 to scales[0].position is refused and writes nothing");
        uint16_t cmd = REG(elsStop.command);
        std::vector<uint8_t> r = ask(fc6(cmd, 1));
        check(r.size() == 8 && regs[cmd] == 1, "4 FC6 to elsStop.command lands");
    }

    /* 4b. Host reset: 0, and only 0. */
    {
        struct { const char *name; uint16_t reg, regs; } resets[] = {
            { "elsStop.executionCyclesPeak", REG(elsStop.executionCyclesPeak), 2 },
            { "elsStop.stepPulseMinCycles",  REG(elsStop.stepPulseMinCycles),  2 },
            { "elsStop.stepPulseRuntCount",  REG(elsStop.stepPulseRuntCount),  2 },
            { "elsStop.diagEndReason",       REG(elsStop.diagEndReason),       1 },
        };
        char label[160];
        for (const auto &f : resets) {
            for (uint16_t k = 0; k < f.regs; k++) {
                uint16_t r = (uint16_t)(f.reg + k);
                regs[r] = 0x1234;
                check(excOf(ask(fc6(r, 1)), 6) == MB_EXC_ILLEGAL_ADDRESS && regs[r] == 0x1234,
                      (snprintf(label, sizeof label, "4b FC6 of 1 to %s word %u is refused", f.name, k), label));
                check(excOf(ask(fc6(r, 0xFFFF)), 6) == MB_EXC_ILLEGAL_ADDRESS && regs[r] == 0x1234,
                      (snprintf(label, sizeof label, "4b FC6 of 0xFFFF to %s word %u is refused", f.name, k), label));
                std::vector<uint8_t> q = fc6(r, 0);
                check(ask(q) == q && regs[r] == 0,
                      (snprintf(label, sizeof label, "4b FC6 of 0 to %s word %u lands", f.name, k), label));
            }
            for (uint16_t k = 0; k < f.regs; k++) regs[f.reg + k] = 0x4321;
            std::vector<uint8_t> r16 = ask(fc16(f.reg, std::vector<uint16_t>(f.regs, 0)));
            bool zeroed = true;
            for (uint16_t k = 0; k < f.regs; k++) zeroed = zeroed && regs[f.reg + k] == 0;
            check(r16.size() == 8 && zeroed,
                  (snprintf(label, sizeof label, "4b FC16 of zeros over %s lands", f.name), label));
            for (uint16_t k = 0; k < f.regs; k++) regs[f.reg + k] = 0x4321;
            std::vector<uint16_t> mixed(f.regs, 0);
            mixed.back() = 5;
            bool kept = excOf(ask(fc16(f.reg, mixed)), 16) == MB_EXC_ILLEGAL_ADDRESS;
            for (uint16_t k = 0; k < f.regs; k++) kept = kept && regs[f.reg + k] == 0x4321;
            check(kept, (snprintf(label, sizeof label,
                                  "4b FC16 over %s with a nonzero word is refused whole", f.name), label));
        }
        uint16_t seq = REG(elsStop.commandSeq);
        regs[seq] = 7;
        check(excOf(ask(fc6(seq, 0)), 6) == MB_EXC_ILLEGAL_ADDRESS && regs[seq] == 7,
              "4b FC6 of 0 to elsStop.commandSeq (no host reset) is still refused");
        uint16_t res = REG(elsStop.diagReserved);
        regs[res] = 8;
        check(excOf(ask(fc6(res, 0)), 6) == MB_EXC_ILLEGAL_ADDRESS && regs[res] == 8,
              "4b FC6 of 0 to elsStop.diagReserved[0], diagEndReason's neighbor, is still refused");
        uint16_t er = REG(elsStop.diagEndReason);
        regs[er] = 9; regs[er - 1] = 3;
        check(excOf(ask(fc16((uint16_t)(er - 1), { 0, 0 })), 16) == MB_EXC_ILLEGAL_ADDRESS &&
              regs[er] == 9 && regs[er - 1] == 3,
              "4b FC16 of zeros reaching from diagCaptureTicks into diagEndReason is refused whole");
    }

    /* 5. The reply counter. */
    {
        uint16_t before = mbAppReplies();
        (void)ask(fc3(0, 1));
        check((uint16_t)(mbAppReplies() - before) == 1, "5 a read reply counts");
        (void)ask(fc3(N, 1));
        check((uint16_t)(mbAppReplies() - before) == 2, "5 an exception reply counts");
        unsigned crcBefore = mbAppDiag[ELS_BL_DG_CRC_ERRORS];
        std::vector<uint8_t> bad = fc3(0, 1);
        bad.back() ^= 0xFF;
        check(ask(bad).empty() && (uint16_t)(mbAppReplies() - before) == 2,
              "5 a bad CRC gets no reply and does not count");
        check(mbAppDiag[ELS_BL_DG_CRC_ERRORS] == crcBefore + 1, "5 ...and is counted as a CRC error");
        unsigned badBefore = mbAppDiag[ELS_BL_DG_BAD_FRAMES];
        std::vector<uint8_t> other = frame({ 18, 3, 0, 0, 0, 1 });
        check(ask(other).empty() && (uint16_t)(mbAppReplies() - before) == 2,
              "5 another slave's frame gets no reply and does not count");
        check(mbAppDiag[ELS_BL_DG_BAD_FRAMES] == badBefore + 1, "5 ...and is counted as a bad frame");
    }

    /* 6. One reply on the wire at a time. */
    {
        txBusy = 1;
        lastSent.clear();
        inbox.push_back(fc3(0, 1));
        check(mbAppService() == 0 && lastSent.empty() && inbox.size() == 1,
              "6 no frame is taken while the reply is still leaving");
        txBusy = 0;
        check(mbAppService() == 1 && lastSent.size() == 7 && inbox.empty(),
              "6 ...and it is served once the transmitter is free");
    }

    printf("%s\n", failures ? "FAILURES" : "all passed");
    return failures ? 1 : 0;
}
