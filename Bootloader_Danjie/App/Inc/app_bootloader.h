#ifndef __APP_BOOTLOADER_H__
#define __APP_BOOTLOADER_H__

#include "main.h"
#include "app_sd.h"
#include <stdbool.h>
#include <stdint.h>

#define BOOTLOADER_ADDR 0x08000000U
#define BOOTLOADER_MAX_SIZE 0x0000C000U

#define APP_ADDR 0x0800C000U
#define APP_END_ADDR 0x080A0000U
#define APP_MAX_SIZE (APP_END_ADDR - APP_ADDR)

/*
 * 弹界原设置数据位于0x080E0000（Sector 11）。
 * OTA缓存只使用Sector 9、10，避免擦除原设置数据。
 */
#define OTA_CACHE_ADDR 0x080A0000U
#define OTA_META_ADDR 0x080DFF00U
#define OTA_META_VALID_ADDR OTA_META_ADDR
#define OTA_META_INSTALLING_MARKER_ADDR (OTA_META_ADDR + 0x20U)
#define OTA_META_INSTALLED_MARKER_ADDR (OTA_META_ADDR + 0x24U)
#define OTA_CACHE_MAX_SIZE (OTA_META_ADDR - OTA_CACHE_ADDR)

#define OTA_META_MARKER_EMPTY 0xFFFFFFFFU
#define OTA_META_MARKER_SET 0x00000000U

#define OTA_REQUEST_MAGIC 0x424F5441U
#define OTA_METADATA_MAGIC 0x4D41544FU

#define OTA_META_EMPTY 0xFFFFFFFFU
#define OTA_META_VALID 0xFFFFFFFEU
#define OTA_META_INSTALLING 0xFFFFFFFCU
#define OTA_META_INSTALLED 0xFFFFFFF8U

typedef struct
{
    uint32_t magic;
    uint32_t state;
    uint32_t version_code;
    uint32_t image_size;
    uint32_t image_crc32;
    uint32_t header_crc32;
} OtaMetadata_t;

HAL_StatusTypeDef Flash_Program(uint32_t StartAddress, uint8_t *Data, uint32_t Size);
void JumpToApplication(void);
void App_Bootloader(void);

#endif
