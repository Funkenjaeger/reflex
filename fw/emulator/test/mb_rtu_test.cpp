/*
 * mb_rtu_test.cpp -- the Modbus RTU core (modbus_rtu/mb_rtu.c), compiled
 * against the recording test port (test/mb_rtu_port/mb_rtu_port.h).
 *
 * SECTIONS
 *   A. frame handling: the CRC against the specification's worked example;
 *      runts, oversized frames, a foreign address and a wrong length dropped
 *      without a reply and counted as frame drops; a bad CRC dropped and
 *      counted apart; exception replies (1, 2, 3) not counted as drops.
 *   B. FC 3 / 6 / 16 over the main map and the windows: big-endian values,
 *      the FC 6 echo and the FC 16 reply, the identity window read-only, a
 *      range straddling two regions refused.
 *   C. the command hook: called once per write that lands, AFTER the
 *      registers are written and BEFORE any byte of the reply exists; never
 *      for a read, an exception or a dropped frame. beforeRead: once per
 *      read that resolves, never otherwise.
 *   D. ro_firmware rejection over the real map (Ramps_access_generated.h):
 *      every register of rampsSharedData_t written alone with FC 6, then
 *      with a one-register FC 16; a firmware-owned one answers exception 2
 *      and keeps its value, every other one is written. FC 16 blocks that
 *      reach into a firmware-owned field from either side are refused whole
 *      (no partial write), blocks over writable fields land, and with no
 *      table every register is writable as before.
 *   E. the generated table against the COMPILER's layout: offsetof() on the
 *      generated structs places a sample of fields, independently of the
 *      generator's own arithmetic, and their bits must agree with the schema.
 *
 * MUTATIONS (seen red 2026-10-16, each applied alone and reverted). The counts
 * below are as recorded then; calMeasured (6 registers) became host-writable
 * afterwards, so the firmware-owned total is now 160, not 166:
 *   M1 mb_rtu.c without the FC 6 access check -> 166 checks fail, one per
 *      firmware-owned register (D1)
 *   M2 touchesReadOnly testing only the first register of the range -> 13
 *      fail: every D6 block that starts on a writable register below a
 *      firmware-owned field is written
 *   M3 mbRtuPortCommand called after the FC 6 reply is composed -> C3
 *   M4 genregs.py emit_access walking only element [0] of an array member
 *      -> D5 (154 registers, not 166) and E scales[3].position / .speed
 *   M5 the FC 16 access check moved after the registers are written -> 190
 *      fail (D3, D6, D8): the exception is still sent, but over a write
 *      that already landed
 */
#include <cstdio>
#include <cstring>
#include <cstddef>
#include <vector>

extern "C" {
#include "mb_rtu.h"
}
#include "Ramps_generated.h"
#include "Ramps_access_generated.h"

static int failures = 0;
static void check(bool ok, const char *label) {
    if (!ok) { printf("[FAIL] %s\n", label); failures++; }
}
static void checkf(bool ok, const char *fmt, unsigned a, unsigned b = 0) {
    char buf[160];
    snprintf(buf, sizeof buf, fmt, a, b);
    check(ok, buf);
}
static void pass(const char *section) { printf("[DONE] %s\n", section); }

static const uint16_t identity[ELS_ID_SIZE] = ELS_ID_WINDOW_INIT(ELS_ID_STAGE_APP, 12u);

/* ---- a tiny Modbus master ---------------------------------------------- */

static std::vector<uint8_t> frame(std::vector<uint8_t> body) {
    uint16_t crc = mbRtuCrc16(body.data(), (uint32_t)body.size());
    body.push_back((uint8_t)(crc & 0xFF));
    body.push_back((uint8_t)(crc >> 8));
    return body;
}

static std::vector<uint8_t> ask(mbRtuTestPort_t *t, const std::vector<uint8_t> &req) {
    uint8_t resp[MB_RTU_MAX_FRAME];
    memset(resp, MB_TEST_SENTINEL, sizeof resp);
    t->resp = resp;
    uint32_t n = mbRtuHandle(t, identity, req.data(), (uint32_t)req.size(), resp);
    t->resp = nullptr;
    return std::vector<uint8_t>(resp, resp + n);
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

/* The exception code of a reply, or 0 if it is not an exception. */
static uint8_t excOf(const std::vector<uint8_t> &r, uint8_t fc) {
    if (r.size() == 5 && r[0] == 17 && r[1] == (uint8_t)(fc | 0x80)) return r[2];
    return 0;
}
static bool replyCrcOk(const std::vector<uint8_t> &r) {
    if (r.size() < 4) return false;
    uint16_t crc = mbRtuCrc16(r.data(), (uint32_t)r.size() - 2);
    return r[r.size() - 2] == (uint8_t)crc && r[r.size() - 1] == (uint8_t)(crc >> 8);
}

/* A port over a main map of `size` registers holding 0x1000 + r. */
struct Board {
    std::vector<uint16_t> main;
    mbRtuTestPort_t t;
    explicit Board(uint16_t size, const mbRtuAccess_t *acc = nullptr) : main(size) {
        memset(&t, 0, sizeof t);
        for (uint16_t r = 0; r < size; r++) main[r] = (uint16_t)(0x1000 + r);
        t.mainRegs = main.data();
        t.mainSize = size;
        t.access = acc;
    }
    unsigned drops() const { return t.drops[MB_RTU_DROP_FRAME] + t.drops[MB_RTU_DROP_CRC]; }
};

static bool isRo(uint16_t r) {
    static const uint8_t bits[] = RAMPS_RO_FIRMWARE_BITS_INIT;
    return r < RAMPS_ACCESS_REGISTERS && (bits[r >> 3] & (1u << (r & 7)));
}

/* ---- A. frame handling -------------------------------------------------- */

static void sectionA() {
    /* The specification's example: 01 03 00 00 00 0A -> CRC C5 CD. */
    const uint8_t ex[] = { 0x01, 0x03, 0x00, 0x00, 0x00, 0x0A };
    check(mbRtuCrc16(ex, sizeof ex) == 0xCDC5, "A1 CRC of the worked example is 0xCDC5 (C5 CD on the wire)");

    Board b(32);
    std::vector<uint8_t> ok = fc3(0, 1);

    check(ask(&b.t, { 17, 3, 0 }).empty() && b.t.drops[MB_RTU_DROP_FRAME] == 1, "A2 a 3-byte runt is dropped, counted as a frame drop");
    std::vector<uint8_t> big(MB_RTU_MAX_FRAME + 1, 0); big[0] = 17;
    check(ask(&b.t, big).empty() && b.t.drops[MB_RTU_DROP_FRAME] == 2, "A3 an oversized frame is dropped, counted");
    std::vector<uint8_t> other = frame({ 18, 3, 0, 0, 0, 1 });
    check(ask(&b.t, other).empty() && b.t.drops[MB_RTU_DROP_FRAME] == 3, "A4 another slave's frame is dropped, counted");
    std::vector<uint8_t> bad = ok; bad[3] ^= 1;
    check(ask(&b.t, bad).empty() && b.t.drops[MB_RTU_DROP_CRC] == 1 && b.t.drops[MB_RTU_DROP_FRAME] == 3,
          "A5 a bad CRC is dropped and counted apart from frame drops");
    std::vector<uint8_t> longRead = frame({ 17, 3, 0, 0, 0, 1, 0 });
    check(ask(&b.t, longRead).empty() && b.t.drops[MB_RTU_DROP_FRAME] == 4, "A6 an FC 3 of the wrong length is dropped");
    std::vector<uint8_t> shortWrite = frame({ 17, 16, 0, 0, 0 });
    check(ask(&b.t, shortWrite).empty() && b.t.drops[MB_RTU_DROP_FRAME] == 5, "A7 an FC 16 under 9 bytes is dropped");
    check(ask(&b.t, frame({ 17, 6, 0, 0, 0, 1, 0 })).empty() && b.t.drops[MB_RTU_DROP_FRAME] == 6, "A8 an FC 6 of the wrong length is dropped");

    unsigned before = b.drops();
    auto r1 = ask(&b.t, frame({ 17, 4, 0, 0, 0, 1 }));
    check(excOf(r1, 4) == 1 && replyCrcOk(r1), "A9 an unsupported function answers exception 1");
    auto r2 = ask(&b.t, fc3(0, 0));
    auto r3 = ask(&b.t, fc3(0, 126));
    check(excOf(r2, 3) == 3 && excOf(r3, 3) == 3, "A10 FC 3 of 0 or 126 registers answers exception 3");
    auto r4 = ask(&b.t, fc3(40, 1));
    check(excOf(r4, 3) == 2, "A11 FC 3 past the map answers exception 2");
    std::vector<uint8_t> mism = fc16(0, { 1, 2 }); mism[6] = 2;   /* byte count says 1 register */
    uint16_t c = mbRtuCrc16(mism.data(), (uint32_t)mism.size() - 2);
    mism[mism.size() - 2] = (uint8_t)c; mism[mism.size() - 1] = (uint8_t)(c >> 8);
    check(excOf(ask(&b.t, mism), 16) == 3, "A12 FC 16 with a byte count that disagrees answers exception 3");
    check(excOf(ask(&b.t, fc16(0, {})), 16) == 3, "A13 FC 16 of 0 registers answers exception 3");
    check(b.drops() == before, "A14 exception replies are not counted as drops");
    pass("A frame handling");
}

/* ---- B. FC 3 / 6 / 16 --------------------------------------------------- */

static void sectionB() {
    Board b(32);
    b.main[5] = 0xBEEF; b.main[6] = 0x0102;
    auto r = ask(&b.t, fc3(5, 2));
    check(r.size() == 9 && r[0] == 17 && r[1] == 3 && r[2] == 4 && r[3] == 0xBE && r[4] == 0xEF &&
          r[5] == 0x01 && r[6] == 0x02 && replyCrcOk(r), "B1 FC 3 answers big-endian values with a good CRC");

    auto q = fc6(7, 0x1234);
    check(ask(&b.t, q) == q && b.main[7] == 0x1234, "B2 FC 6 writes and echoes the request");

    auto w = ask(&b.t, fc16(8, { 0xAAAA, 0xBBBB, 0xCCCC }));
    check(w.size() == 8 && w[1] == 16 && w[2] == 0 && w[3] == 8 && w[4] == 0 && w[5] == 3 && replyCrcOk(w) &&
          b.main[8] == 0xAAAA && b.main[9] == 0xBBBB && b.main[10] == 0xCCCC,
          "B3 FC 16 writes in order and replies address + count");

    auto id = ask(&b.t, fc3(ELS_ID_BASE, ELS_ID_SIZE));
    check(id.size() == 5u + 2u * ELS_ID_SIZE && ((id[3] << 8) | id[4]) == identity[0], "B4 the identity window reads");
    check(excOf(ask(&b.t, fc6(ELS_ID_BASE, 1)), 6) == 2, "B5 FC 6 into the identity window answers exception 2");
    check(excOf(ask(&b.t, fc3(ELS_ID_BASE + ELS_ID_SIZE - 1, 2)), 3) == 2, "B6 a read straddling the identity window's end answers exception 2");

    check(ask(&b.t, fc6(MB_TEST_WIN_BASE + 3, 0x5555)) == fc6(MB_TEST_WIN_BASE + 3, 0x5555) && b.t.win[3] == 0x5555,
          "B7 FC 6 into the writable window lands");
    check(excOf(ask(&b.t, fc3(30, 4)), 3) == 2, "B8 a read past the main map's end answers exception 2");
    pass("B FC 3 / 6 / 16");
}

/* ---- C. the command hook and beforeRead -------------------------------- */

static void sectionC() {
    Board b(32);
    ask(&b.t, fc6(MB_TEST_WIN_BASE, 7));
    check(b.t.commands == 1, "C1 an FC 6 write calls the command hook once");
    check(b.t.winAtCommand[0] == 7, "C2 the hook sees the value already written");
    check(b.t.replyUntouchedAtCommand == 1, "C3 the hook runs before any byte of the FC 6 reply is composed");

    ask(&b.t, fc16(MB_TEST_WIN_BASE + 1, { 8, 9 }));
    check(b.t.commands == 2 && b.t.winAtCommand[1] == 8 && b.t.winAtCommand[2] == 9,
          "C4 an FC 16 write calls the hook once, after every register is written");
    check(b.t.replyUntouchedAtCommand == 1, "C5 the hook runs before any byte of the FC 16 reply is composed");

    ask(&b.t, fc3(0, 4));
    ask(&b.t, fc6(ELS_ID_BASE, 1));                        /* exception 2 */
    ask(&b.t, fc16(0, {}));                                /* exception 3 */
    std::vector<uint8_t> bad = fc6(0, 1); bad[2] ^= 1;     /* bad CRC */
    ask(&b.t, bad);
    check(b.t.commands == 2, "C6 no hook for a read, an exception or a dropped frame");

    check(b.t.beforeReads == 1, "C7 beforeRead once per read that resolved");
    ask(&b.t, fc3(40, 1));
    ask(&b.t, fc6(1, 1));
    check(b.t.beforeReads == 1, "C8 no beforeRead for a refused read or for a write");
    pass("C the command hook");
}

/* ---- D. ro_firmware rejection ------------------------------------------ */

static void sectionD() {
    static const uint8_t bits[] = RAMPS_RO_FIRMWARE_BITS_INIT;
    const mbRtuAccess_t acc = { 0, RAMPS_ACCESS_REGISTERS, bits };
    const uint16_t N = RAMPS_ACCESS_REGISTERS;
    check(sizeof bits == (N + 7u) / 8u, "D0 the table covers every register of the map");

    unsigned ro = 0;
    for (uint16_t r = 0; r < N; r++) {
        Board b(N, &acc);
        if (isRo(r)) ro++;
        auto q = fc6(r, 0x7777);
        auto a = ask(&b.t, q);
        if (isRo(r)) {
            checkf(excOf(a, 6) == 2 && b.main[r] == (uint16_t)(0x1000 + r) && b.t.commands == 0,
                   "D1 FC 6 to firmware-owned register %u answers exception 2, writes nothing", r);
        } else {
            checkf(a == q && b.main[r] == 0x7777 && b.t.commands == 1,
                   "D2 FC 6 to host-writable register %u lands as before", r);
        }
        Board m(N, &acc);
        auto a16 = ask(&m.t, fc16(r, { 0x6666 }));
        if (isRo(r)) {
            checkf(excOf(a16, 16) == 2 && m.main[r] == (uint16_t)(0x1000 + r),
                   "D3 FC 16 to firmware-owned register %u answers exception 2, writes nothing", r);
        } else {
            checkf(a16.size() == 8 && m.main[r] == 0x6666, "D4 FC 16 to host-writable register %u lands", r);
        }
    }
    checkf(ro == 160, "D5 160 firmware-owned registers in the map (got %u)", ro);

    /* Blocks that reach into a firmware-owned register from below and from
     * above: a writable register on one side, the firmware-owned one on the
     * other. Refused whole: the writable half is not written either. */
    unsigned edges = 0;
    for (uint16_t r = 1; r < N; r++) {
        if (isRo(r) == isRo((uint16_t)(r - 1))) continue;
        uint16_t lo = (uint16_t)(r - 1);
        Board b(N, &acc);
        auto a = ask(&b.t, fc16(lo, { 0x4444, 0x4444 }));
        checkf(excOf(a, 16) == 2 && b.main[lo] == (uint16_t)(0x1000 + lo) && b.main[r] == (uint16_t)(0x1000 + r),
               "D6 FC 16 over registers %u..%u (one firmware-owned) is refused with nothing written", lo, r);
        edges++;
    }
    checkf(edges > 0, "D7 the map has writable/firmware-owned boundaries to test (%u)", edges);

    {   /* A long block across a whole firmware-owned field, writable on both sides. */
        Board b(N, &acc);
        auto a = ask(&b.t, fc16(166, std::vector<uint16_t>(12, 0x3333)));   /* 166..177 spans bootSeq 169 */
        bool untouched = true;
        for (uint16_t r = 166; r < 178; r++) if (b.main[r] != (uint16_t)(0x1000 + r)) untouched = false;
        check(excOf(a, 16) == 2 && untouched, "D8 FC 16 over 166..177 (bootSeq inside) is refused, nothing written");
    }
    {   /* A block over host-writable fields only lands, as before. */
        Board b(N, &acc);
        auto a = ask(&b.t, fc16(104, { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14 }));   /* enable..backlashSteps */
        bool landed = true;
        for (uint16_t i = 0; i < 14; i++) if (b.main[104 + i] != i + 1) landed = false;
        check(a.size() == 8 && landed && b.t.commands == 1, "D9 FC 16 over elsStop 104..117 (all host-writable) lands");
    }
    {   /* No table: the same write that D8 refuses lands. */
        Board b(N);
        auto a = ask(&b.t, fc16(166, std::vector<uint16_t>(12, 0x3333)));
        check(a.size() == 8 && b.main[170] == 0x3333, "D10 with no access table, a firmware-owned register is written as before");
    }
    {   /* The table only covers its own range: the windows are unaffected. */
        Board b(N, &acc);
        check(ask(&b.t, fc6(MB_TEST_WIN_BASE, 9)) == fc6(MB_TEST_WIN_BASE, 9), "D11 the table does not reach the windows");
    }
    pass("D ro_firmware rejection");
}

/* ---- E. the table against the compiler's layout ------------------------ */

struct Probe { const char *name; unsigned reg; unsigned regs; bool ro; };

static void sectionE() {
    /* Struct bases from the parent (Ramps.h, asserted by the compiler through
     * RAMPS_GENERATED_ASSERT_PARENT_LAYOUT); offsets within each struct from
     * offsetof(), so none of these numbers comes from the generator. */
    const unsigned SERVO = 8, SCALES = 26, SCALE_REGS = sizeof(input_t) / 2, FAST = 74, ELS = 104;
#define AT(base, T, f) (unsigned)((base) + offsetof(T, f) / 2), (unsigned)(sizeof(((T *)0)->f) / 2)
    const Probe probes[] = {
        { "servo.maxSpeed",         AT(SERVO, servo_t, maxSpeed),         false },
        { "servo.currentSpeed",     AT(SERVO, servo_t, currentSpeed),     true  },
        { "servo.stepsToGo",        AT(SERVO, servo_t, stepsToGo),        false },
        { "servo.currentSteps",     AT(SERVO, servo_t, currentSteps),     true  },
        { "servo.desiredSteps",     AT(SERVO, servo_t, desiredSteps),     true  },
        { "scales[0].position",     AT(SCALES, input_t, position),        true  },
        { "scales[3].position",     AT(SCALES + 3 * SCALE_REGS, input_t, position), true },
        { "scales[3].speed",        AT(SCALES + 3 * SCALE_REGS, input_t, speed),    true },
        { "scales[2].syncRatioNum", AT(SCALES + 2 * SCALE_REGS, input_t, syncRatioNum), false },
        { "scales[1].timerHandleSlot", AT(SCALES + SCALE_REGS, input_t, timerHandleSlot), false },
        { "fastData.scaleCurrent",  AT(FAST, fastData_t, scaleCurrent),   true  },
        { "fastData.servoMode",     AT(FAST, fastData_t, servoMode),      false },
        { "elsStop.enable",         AT(ELS, elsStop_t, enable),           false },
        { "elsStop.protocolVersion",AT(ELS, elsStop_t, protocolVersion),  true  },
        { "elsStop.calCommand",     AT(ELS, elsStop_t, calCommand),       false },
        { "elsStop.bootSeq",        AT(ELS, elsStop_t, bootSeq),          true  },
        { "elsStop.calMeasured",    AT(ELS, elsStop_t, calMeasured),      false },
        { "elsStop.calCeilingSteps",AT(ELS, elsStop_t, calCeilingSteps),  false },
        { "elsStop.executionCyclesPeak", AT(ELS, elsStop_t, executionCyclesPeak), true },
        { "elsStop.clearRefCommand",AT(ELS, elsStop_t, clearRefCommand),  false },
        { "elsStop.diagTrace",      AT(ELS, elsStop_t, diagTrace),        true  },
    };
#undef AT
    for (const Probe &p : probes) {
        bool all = true;
        for (unsigned r = p.reg; r < p.reg + p.regs; r++) if (isRo((uint16_t)r) != p.ro) all = false;
        char buf[160];
        snprintf(buf, sizeof buf, "E %s (registers %u..%u) is %s in the table", p.name, p.reg,
                 p.reg + p.regs - 1, p.ro ? "firmware-owned" : "host-writable");
        check(all, buf);
    }
    check(!isRo(0) && !isRo(7), "E registers 0..7, described by no schema, carry no bit");
    pass("E the table against the compiler's layout");
}

int main() {
    sectionA();
    sectionB();
    sectionC();
    sectionD();
    sectionE();
    if (failures) {
        printf("mb_rtu_test: %d check(s) FAILED\n", failures);
        return 1;
    }
    printf("mb_rtu_test: all checks passed\n");
    return 0;
}
