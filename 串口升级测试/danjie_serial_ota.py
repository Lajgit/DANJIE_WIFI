#!/usr/bin/env python3
"""弹界球盘串口OTA测试工具。依赖：py -m pip install pyserial"""

import argparse
import binascii
import hashlib
import struct
import sys
import time
from pathlib import Path

try:
    import serial
except ImportError as exc:
    raise SystemExit("缺少pyserial，请执行：py -m pip install pyserial") from exc

APP_ADDR = 0x0800C000
APP_END_ADDR = 0x080A0000
OTA_MAX_IMAGE = 0x080DFF00 - 0x080A0000
APP_MAGIC = 0x424F5441
TOOL_VERSION = "1.2-fixed"

OTA_HELLO = 0x01
OTA_BEGIN = 0x02
OTA_DATA = 0x03
OTA_END = 0x04
OTA_INSTALL = 0x05
OTA_STATUS = 0x06
OTA_ACK = 0x80
OTA_NACK = 0x81

STATE_NAMES = {
    0: "IDLE",
    1: "RECEIVING",
    2: "VERIFIED",
    3: "INSTALLING",
    4: "INSTALLED",
    5: "ERROR",
}

RESULT_NAMES = {
    0x00: "OK",
    0x01: "协议版本错误",
    0x02: "帧CRC32错误",
    0x03: "负载长度错误",
    0x04: "目标错误",
    0x05: "固件大小错误",
    0x06: "缓存擦除失败",
    0x07: "Flash写入失败",
    0x08: "偏移错误",
    0x09: "固件CRC32错误",
    0x0A: "固件向量无效",
    0x0B: "没有有效固件",
    0x0C: "安装失败",
    0x0D: "升级未开始",
    0x0E: "未知命令",
}


class OtaError(RuntimeError):
    pass


class OtaTimeout(OtaError):
    pass


class OtaReject(OtaError):
    pass


def crc16(data):
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xA001 if value & 1 else value >> 1
    return value & 0xFFFF


def format_app_version(version: int) -> str:
    major = (version >> 24) & 0xFF
    minor = (version >> 16) & 0xFF
    patch = (version >> 8) & 0xFF
    build = version & 0xFF

    if build:
        return f"V{major}.{minor}.{patch}.{build}"
    return f"V{major}.{minor}.{patch}"


def app_frame(message_id, command, data=0):
    frame = bytearray(14)
    frame[0] = 0xAA
    frame[2] = message_id & 0xFF
    frame[3] = 0x01
    frame[4] = command
    frame[5:9] = data.to_bytes(4, "big")
    frame[9] = 0x01
    frame[10] = 0x00
    value = crc16(frame[:11])
    frame[11] = value >> 8
    frame[12] = value & 0xFF
    frame[13] = 0x55
    return bytes(frame)


def app_frame_valid(frame):
    return (
        len(frame) == 14
        and frame[0] == 0xAA
        and frame[13] == 0x55
        and ((frame[11] << 8) | frame[12]) == crc16(frame[:11])
    )


def read_exact(port, size, deadline):
    result = bytearray()
    while len(result) < size and time.monotonic() < deadline:
        data = port.read(size - len(result))
        if data:
            result.extend(data)
    return bytes(result)


def read_app_frame(port, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        first = port.read(1)
        if not first or first[0] != 0xAA:
            continue
        rest = read_exact(port, 13, deadline)
        frame = first + rest
        if app_frame_valid(frame):
            return frame
    return None


def get_version(port, message_id, timeout=2.0):
    port.reset_input_buffer()
    port.write(app_frame(message_id, 0x00))
    port.flush()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = read_app_frame(port, max(0.05, deadline - time.monotonic()))
        if frame is None:
            break
        if frame[3] == 0x00 and frame[4] == 0x00:
            return int.from_bytes(frame[5:9], "big")
    raise OtaError("未收到APP版本应答")


def enter_bootloader(port, message_id):
    frame = app_frame(message_id, 0xF0, APP_MAGIC)
    print("进入Bootloader发送：", frame.hex(" ").upper())
    port.reset_input_buffer()
    port.write(frame)
    port.flush()
    time.sleep(0.3)


def ota_frame(command, sequence, payload=b""):
    header = struct.pack("<BBHH", 1, command, sequence, len(payload))
    crc = binascii.crc32(header + payload) & 0xFFFFFFFF
    return b"\xAA\x5A" + header + payload + struct.pack("<I", crc)


def read_ota_frame(port, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        first = port.read(1)
        if not first or first != b"\xAA":
            continue
        if read_exact(port, 1, deadline) != b"\x5A":
            continue
        header = read_exact(port, 6, deadline)
        if len(header) != 6:
            return None
        version, command, sequence, length = struct.unpack("<BBHH", header)
        if version != 1 or length > 1094:
            continue
        body = read_exact(port, length + 4, deadline)
        if len(body) != length + 4:
            return None
        payload = body[:length]
        received = struct.unpack("<I", body[length:])[0]
        if received != (binascii.crc32(header + payload) & 0xFFFFFFFF):
            continue
        return command, sequence, payload
    return None


class Client:
    def __init__(self, port):
        self.port = port
        self.sequence = 0

    def request(self, command, payload=b"", timeout=2.0, retries=3):
        self.sequence = self.sequence % 0xFFFF + 1
        frame = ota_frame(command, self.sequence, payload)
        for attempt in range(1, retries + 1):
            self.port.write(frame)
            self.port.flush()
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                packet = read_ota_frame(self.port, max(0.05, deadline - time.monotonic()))
                if packet is None:
                    break
                response, sequence, body = packet
                if sequence != self.sequence or response not in (OTA_ACK, OTA_NACK):
                    continue
                if len(body) != 10 or body[0] != command:
                    continue
                result = body[1]
                state = body[2]
                value = struct.unpack_from("<I", body, 4)[0]
                max_data = struct.unpack_from("<H", body, 8)[0]
                if response == OTA_NACK or result != 0:
                    name = RESULT_NAMES.get(result, f"0x{result:02X}")
                    raise OtaReject(
                        f"命令0x{command:02X}失败：{name}，"
                        f"state={STATE_NAMES.get(state, state)}，value=0x{value:08X}"
                    )
                return state, value, max_data
            print(f"命令0x{command:02X}第{attempt}/{retries}次超时")
        raise OtaTimeout(f"命令0x{command:02X}无应答")


def connect_bootloader(port, message_id):
    client = Client(port)

    # 不先向APP发送AA 5A OTA帧。APP仍在运行时，这类非14字节帧可能
    # 留在原通信接收队列中，影响随后0xF0复位命令的帧同步。
    # 无论当前处于APP还是Bootloader，先发送一次APP复位帧都不会破坏OTA状态。
    enter_bootloader(port, message_id)
    port.reset_input_buffer()

    last_error = None
    for _ in range(20):
        try:
            return client, client.request(OTA_HELLO, timeout=0.6, retries=1)
        except OtaError as exc:
            last_error = exc
            time.sleep(0.2)
    raise OtaError(f"无法进入Bootloader：{last_error}")


def check_bin(path):
    data = path.read_bytes()
    if len(data) < 8 or len(data) > OTA_MAX_IMAGE:
        raise OtaError(f"BIN大小无效：{len(data)}，OTA上限{OTA_MAX_IMAGE}")
    msp, reset = struct.unpack_from("<II", data)
    reset_address = reset & ~1
    if not 0x20000000 <= msp < 0x20020000:
        raise OtaError(f"初始MSP无效：0x{msp:08X}")
    if (reset & 1) == 0 or not APP_ADDR <= reset_address < APP_END_ADDR:
        raise OtaError(f"Reset向量无效：0x{reset:08X}，APP必须链接到0x{APP_ADDR:08X}")
    crc = binascii.crc32(data) & 0xFFFFFFFF
    print("BIN检查通过：")
    print("  文件：", path.resolve())
    print(f"  大小：{len(data)} 字节（{len(data) / 1024:.2f} KiB）")
    print(f"  初始MSP：0x{msp:08X}")
    print(f"  Reset向量：0x{reset:08X}")
    print(f"  CRC32：0x{crc:08X}")
    print("  MD5：", hashlib.md5(data).hexdigest())
    return data, crc


def upgrade(port, path, target_version, message_id, chunk_size):
    data, image_crc = check_bin(path)
    try:
        version = get_version(port, message_id, 1.5)
        print(f"当前APP版本：{format_app_version(version)}（0x{version:08X}）")
    except OtaError:
        print("当前未读取到APP版本，将直接尝试Bootloader")
    client, hello = connect_bootloader(port, (message_id + 1) & 0xFF or 1)
    state, boot_version, max_data = hello
    print(
        f"Bootloader连接成功：版本0x{boot_version:08X}，"
        f"状态{STATE_NAMES.get(state, state)}，最大数据块{max_data}字节"
    )
    chunk_size = min(chunk_size, max_data, 1024)
    if chunk_size <= 0 or chunk_size % 4:
        raise OtaError("chunk-size必须是4的倍数且不超过1024")

    print("[1/5] BEGIN")
    payload = b"BOTA" + struct.pack("<III", target_version, len(data), image_crc)
    client.request(OTA_BEGIN, payload, timeout=10, retries=2)

    print("[2/5] DATA")
    offset = 0
    last_percent = -1
    while offset < len(data):
        block = data[offset : offset + chunk_size]
        state, next_offset, _ = client.request(
            OTA_DATA,
            struct.pack("<IH", offset, len(block)) + block,
            timeout=3,
            retries=5,
        )
        if next_offset <= offset or next_offset > len(data):
            raise OtaError(f"非法nextOffset：{next_offset}")
        offset = next_offset
        percent = offset * 100 // len(data)
        if percent != last_percent:
            print(f"  {offset}/{len(data)} bytes ({percent}%)")
            last_percent = percent

    print("[3/5] END")
    state, _, _ = client.request(OTA_END, timeout=8, retries=2)
    if state not in (2, 4):
        state, _, _ = client.request(OTA_STATUS, timeout=2, retries=2)
    if state not in (2, 4):
        raise OtaError(f"END状态异常：{STATE_NAMES.get(state, state)}")

    print("[4/5] INSTALL")
    try:
        client.request(OTA_INSTALL, timeout=35, retries=1)
    except OtaTimeout as exc:
        print("  INSTALL应答超时，设备可能已经复位，继续检查APP版本：", exc)

    print("[5/5] 查询升级后版本")
    deadline = time.monotonic() + 20
    last_error = None
    while time.monotonic() < deadline:
        try:
            version = get_version(port, (message_id + 2) & 0xFF or 1, 1.0)
            print(f"升级后APP版本：{format_app_version(version)}（0x{version:08X}）")
            if target_version and version != target_version:
                raise OtaError(f"版本不一致：期望{target_version}，实际{version}")
            print("升级流程完成")
            return
        except OtaError as exc:
            last_error = exc
            time.sleep(0.4)
    raise OtaError(f"升级后未读取到APP版本：{last_error}")


def open_port(name, baud):
    try:
        port = serial.Serial(
            name,
            baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=3,
        )
    except serial.SerialException as exc:
        raise OtaError(f"无法打开串口{name}：{exc}") from exc
    print(f"串口打开成功：{name}, {baud}, 8N1")
    return port


def add_serial_args(parser):
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--message-id", type=lambda value: int(value, 0), default=0xA7)


def main():
    parser = argparse.ArgumentParser(description="弹界球盘串口OTA测试工具")
    commands = parser.add_subparsers(dest="command", required=True)
    version_cmd = commands.add_parser("version")
    add_serial_args(version_cmd)
    boot_cmd = commands.add_parser("bootloader")
    add_serial_args(boot_cmd)
    upgrade_cmd = commands.add_parser("upgrade")
    add_serial_args(upgrade_cmd)
    upgrade_cmd.add_argument("bin", type=Path)
    upgrade_cmd.add_argument("--target-version", type=lambda value: int(value, 0), default=0)
    upgrade_cmd.add_argument("--chunk-size", type=int, default=1024)
    args = parser.parse_args()
    args.message_id = args.message_id & 0xFF or 1

    try:
        print(f"工具版本：{TOOL_VERSION}")
        with open_port(args.port, args.baud) as port:
            if args.command == "version":
                version = get_version(port, args.message_id)
                print(f"弹界APP版本：{format_app_version(version)}（0x{version:08X}）")
            elif args.command == "bootloader":
                # 使用与默认版本查询不同的ID，避免紧接版本查询时被APP的
                # 250ms重复消息过滤机制当作同一条命令。
                boot_message_id = (args.message_id + 1) & 0xFF or 1
                _, reply = connect_bootloader(port, boot_message_id)
                state, boot_version, max_data = reply
                print(
                    f"Bootloader连接成功：版本0x{boot_version:08X}，"
                    f"状态{STATE_NAMES.get(state, state)}，最大数据块{max_data}字节"
                )
            else:
                upgrade(
                    port,
                    args.bin,
                    args.target_version,
                    args.message_id,
                    args.chunk_size,
                )
        return 0
    except KeyboardInterrupt:
        print("用户取消。", file=sys.stderr)
        return 130
    except (OtaError, OSError, serial.SerialException) as exc:
        print("错误：", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
