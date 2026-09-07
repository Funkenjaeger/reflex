/*
 * els_boot_command_test.cpp -- the APP's side of the bootloader hand-off:
 * elsStop.bootCommand / bootSeq (protocolVersion 8) and the register facts
 * scripts/modbus-flash.py hardcodes about them.
 *
 * Links the real Core/Src/Ramps.c with the usual external-stub set, and
 * OVERRIDES the weak emulator no-ops of els_boot.h with strong counters so
 * the reset requests are observable.
 *
 * CONTRACT (Ramps.h, bootCommand)
 *   1. ACCEPT: enable == 0, bootCommand == 1 -> cleared, bootSeq +1, the
 *      stay-and-reset request made exactly once.
 *   2. RESET: bootCommand == 2 -> cleared, bootSeq +1, plain reset once.
 *   3. REFUSE: enable != 0 -> cleared, NO seq edge, NO reset.
 *   4. UNKNOWN: bootCommand == 7 -> cleared, no ack, no reset.
 *   5. IDLE: bootCommand == 0 -> nothing happens.
 *   6. LAYOUT: bootCommand is register 232, bootSeq 233, the struct is 468
 *      bytes, and ELS_PROTOCOL_VERSION is 8 -- the numbers the client's
 *      APP_BOOT_COMMAND_REG table and the UI mirror carry.
 *
 * MUTATIONS (seen red 2026-09-06): drop the `enable != 0` refusal -> case 3
 * fails twice; drop bootSeq++ -> cases 1 and 2; consume in the ISR path
 * instead (not a code change here, a design fact) is pinned by 7: the ISR
 * does not touch bootCommand.
 */
extern "C" {
#include "Ramps.h"
#include "Scales.h"
#include "els_identity.h"
#include "emulator_state.h"
}

#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstddef>

extern "C" {

GPIO_TypeDef    emu_gpioa, emu_gpiob, emu_gpioc;
RCC_TypeDef     emu_rcc;
DWT_Type        emu_dwt;
CoreDebug_Type  emu_coreDebug;
EmulatorHardwareState emu_hw;

void emu_log_trace(const char *fmt, ...) { (void)fmt; }
void emu_log_event(const char *fmt, ...) { (void)fmt; }
void HAL_GPIO_Init(GPIO_TypeDef *p, GPIO_InitTypeDef *i) { (void)p; (void)i; }
void HAL_GPIO_WritePin(GPIO_TypeDef *p, uint16_t pin, GPIO_PinState s) { (void)p; (void)pin; (void)s; }
void HAL_GPIO_TogglePin(GPIO_TypeDef *p, uint16_t pin) { (void)p; (void)pin; }
HAL_StatusTypeDef HAL_TIM_Base_Start_IT(TIM_HandleTypeDef *h) { (void)h; return HAL_OK; }
HAL_StatusTypeDef HAL_TIM_Encoder_Start(TIM_HandleTypeDef *h, uint32_t ch) { (void)h; (void)ch; return HAL_OK; }
HAL_StatusTypeDef initScaleTimer(TIM_HandleTypeDef *h) { (void)h; return HAL_OK; }
void ModbusInit(modbusHandler_t *m)  { (void)m; }
void ModbusStart(modbusHandler_t *m) { (void)m; }
osStatus_t osDelay(uint32_t ticks) { (void)ticks; return osOK; }
osThreadId_t osThreadNew(osThreadFunc_t f, void *a, const osThreadAttr_t *at) { (void)f; (void)a; (void)at; return nullptr; }

extern uint16_t servoCycles;
extern uint16_t servoCyclesCounter;

/* Strong overrides of the weak els_boot.h no-ops: observable. */
static int stayResets = 0, plainResets = 0, attemptsClears = 0, kicks = 0;
void elsBootRequestStayAndReset(void) { stayResets++; }
void elsBootRequestReset(void) { plainResets++; }
void elsBootAttemptsClear(void) { attemptsClears++; }
void elsBootWatchdogKick(void) { kicks++; }

} /* extern "C" */

static int failures = 0;
static void check(bool ok, const char *label) {
    printf("[%s] %s\n", ok ? "PASS" : "FAIL", label);
    if (!ok) failures++;
}

int main() {
    setvbuf(stdout, nullptr, _IONBF, 0);
    static rampsHandler_t data;
    static TIM_TypeDef tim[SCALES_COUNT];
    static TIM_HandleTypeDef htim[SCALES_COUNT];
    memset(&data, 0, sizeof data);
    rampsSharedData_t *sh = &data.shared;
    /* The ISR reads encoder counters through ramps_timer_handles; give it
     * real (zeroed) timers, as every other ISR-level test does. */
    for (int i = 0; i < SCALES_COUNT; i++) {
        htim[i].Instance = &tim[i];
        ramps_timer_handles[i] = &htim[i];
        sh->scales[i].timerHandleSlot = (uint32_t)i;
        sh->scales[i].scaleDir = 1;
        sh->scales[i].syncRatioNum = 1;
        sh->scales[i].syncRatioDen = 100;
    }
    sh->servo.servoDir = 1;
    servoCycles = 1;
    servoCyclesCounter = 0;

    /* 6. layout facts */
    check(offsetof(rampsSharedData_t, elsStop.bootCommand) == 464, "bootCommand at byte 464 = register 232");
    check(offsetof(rampsSharedData_t, elsStop.bootSeq) == 466, "bootSeq at byte 466 = register 233");
    check(sizeof(rampsSharedData_t) == 468, "rampsSharedData_t is 468 bytes (464 + the pair)");
    check(ELS_PROTOCOL_VERSION == 8, "ELS_PROTOCOL_VERSION is 8");
    check(sizeof(rampsSharedData_t) % 4 == 0 && offsetof(rampsSharedData_t, elsStop.bootSeq) + 2 == sizeof(rampsSharedData_t),
          "bootSeq is the LAST register: no trailing padding, no phantom register");

    /* 5. idle */
    elsBootCommandTick(sh);
    check(sh->elsStop.bootSeq == 0 && stayResets == 0 && plainResets == 0, "5 bootCommand 0: nothing happens");

    /* 1. accept */
    sh->elsStop.enable = 0;
    sh->elsStop.bootCommand = ELS_BOOT_CMD_BOOTLOADER;
    elsBootCommandTick(sh);
    check(sh->elsStop.bootCommand == 0, "1 bootCommand cleared on consume");
    check(sh->elsStop.bootSeq == 1, "1 bootSeq acks once");
    check(stayResets == 1 && plainResets == 0, "1 stay-and-reset requested exactly once");

    /* 2. plain reset */
    sh->elsStop.bootCommand = ELS_BOOT_CMD_RESET;
    elsBootCommandTick(sh);
    check(sh->elsStop.bootCommand == 0 && sh->elsStop.bootSeq == 2, "2 RESET consumed and acked");
    check(plainResets == 1 && stayResets == 1, "2 plain reset requested once, no stay");

    /* 3. refuse with a live job */
    sh->elsStop.enable = 1;
    sh->elsStop.bootCommand = ELS_BOOT_CMD_BOOTLOADER;
    elsBootCommandTick(sh);
    check(sh->elsStop.bootCommand == 0, "3 refused command is still cleared (no retry storm)");
    check(sh->elsStop.bootSeq == 2, "3 ...with NO seq edge: the absent ack is the refusal");
    check(stayResets == 1 && plainResets == 1, "3 ...and no reset");
    sh->elsStop.enable = 0;

    /* 4. unknown */
    sh->elsStop.bootCommand = 7;
    elsBootCommandTick(sh);
    check(sh->elsStop.bootCommand == 0 && sh->elsStop.bootSeq == 2 && stayResets == 1 && plainResets == 1,
          "4 unknown command consumed, no ack, no reset");

    /* 7. the ISR does not consume it: a pending command survives 50 ticks. */
    sh->elsStop.bootCommand = ELS_BOOT_CMD_BOOTLOADER;
    for (int i = 0; i < 50; i++) SynchroRefreshTimerIsr(&data);
    check(sh->elsStop.bootCommand == ELS_BOOT_CMD_BOOTLOADER && stayResets == 1,
          "7 the ISR leaves bootCommand alone (consumed by the task, not at 100 kHz)");
    sh->elsStop.bootCommand = 0;

    /* 8. RampsStart publishes the version and zeroes the pair. */
    memset(&data, 0x55, sizeof data);
    data.shared.elsStop.bootCommand = 9; data.shared.elsStop.bootSeq = 9;
    for (int i = 0; i < SCALES_COUNT; i++) data.shared.scales[i].timerHandleSlot = (uint32_t)i;
    RampsStart(&data);
    check(data.shared.elsStop.protocolVersion == ELS_PROTOCOL_VERSION, "8 RampsStart publishes ELS_PROTOCOL_VERSION");
    check(data.shared.elsStop.bootCommand == 0 && data.shared.elsStop.bootSeq == 0, "8 RampsStart zeroes bootCommand/bootSeq");
    check(RampsModbusData.windowCount == 1 && RampsModbusData.windows[0].base == ELS_ID_BASE &&
          RampsModbusData.windows[0].size == ELS_ID_SIZE && RampsModbusData.windows[0].readOnly == 1,
          "8 RampsStart registers the identity window, read-only, at ELS_ID_BASE");
    check(RampsModbusData.windows[0].regs[ELS_ID_MAGIC_OFF] == ELS_ID_MAGIC &&
          RampsModbusData.windows[0].regs[ELS_ID_STAGE_OFF] == ELS_ID_STAGE_APP &&
          RampsModbusData.windows[0].regs[ELS_ID_APP_PROTOCOL_OFF] == ELS_PROTOCOL_VERSION,
          "8 ...and it carries magic, stage 2 and the protocol version");

    printf("%s\n", failures ? "FAILURES" : "all passed");
    return failures ? 1 : 0;
}
