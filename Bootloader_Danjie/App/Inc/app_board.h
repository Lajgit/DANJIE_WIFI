#ifndef __APP_BOARD_H__
#define __APP_BOARD_H__

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * 中文注释：
 * Board ID保存在APM32F407 OTP区域，由同一份Bootloader在运行时读取。
 * OTP未写入有效Board ID时默认按弹界处理，兼容现有弹界Bootloader行为。
 */
typedef enum
{
    BOOT_BOARD_DANJIE = 1U,
    BOOT_BOARD_PANTAO = 2U,
    BOOT_BOARD_NIUDAN = 3U
} BootBoardId_t;

BootBoardId_t Boot_BoardGetId(void);
const char *Boot_BoardGetFirmwareFileName(void);

#ifdef __cplusplus
}
#endif

#endif
