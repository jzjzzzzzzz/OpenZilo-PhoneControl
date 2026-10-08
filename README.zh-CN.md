<div align="center">

# OpenZilo-PhoneControl

**可穿戴输入，本地处理，连接 iPhone 交互。**

基于公开 OpenZilo Python SDK、面向 **ComBodied AI** 的 macOS → iPhone 控制应用。

[快速开始](#快速开始) · [手机配置](docs/setup-iphone.zh-CN.md) · [模型导入](docs/models.md) · [架构](docs/architecture.md)

[English](README.md) | **简体中文**

</div>

---

OpenZilo-PhoneControl 在 Mac 上接收智能戒指的按键、固件手势或实时 IMU 数据，将明确的动作映射成 Apple 切换控制输入。初始应用是控制 **iPhone 抖音的下一条视频**。整个处理链路在本地运行，不需要云服务或额外安装 iPhone App。

本项目负责应用集成，不重复维护硬件 SDK 或训练系统：

- [OpenZilo](https://github.com/ziloai/OpenZilo)：公开设备 SDK、协议解析与设备开发资料。
- [ComBodied Motion Lab](https://github.com/jzjzzzzzzz/combodied-motion-lab)：可选 RNN 的训练方法、数据格式和 NumPy 推理运行时。

这是独立的应用层项目，不代表 OpenZilo 或 Apple 官方发行。

## 功能

- BLE 发现、物理 CPUID 核对、绑定和断线重连。
- 无模型的按键/固件手势路径，以及可选的本地 RNN 路径。
- 事件去重、过期丢弃、冷却、暂停恢复与退出时释放按键。
- NPZ 模型导入、SHA-256 校验、标签及传感器参数检查。
- Dry-run、离线回放、本地诊断及自动化测试。

**仓库不包含实际 RNN 权重、真实采集数据、设备绑定信息或私有 SDK。**

## 系统链路

```text
戒指 → Mac BLE 适配器
     → 固件事件 / 可选 RNN 推理
     → 动作门控 → Mac 切换按键
     → Apple 平台切换 → iPhone 上滑方案 → 抖音下一条
```

Mac 是持续运行的一部分；这不是戒指直连 iPhone 的 HID 固件，也不通过“iPhone 镜像”窗口模拟操作。

## 快速开始

### 1. 安装

需要 macOS、Python 3.11+、Git 和兼容的 OpenZilo 戒指。

```bash
git clone https://github.com/jzjzzzzzzz/OpenZilo-PhoneControl.git
cd OpenZilo-PhoneControl
./setup.sh
./ringphone doctor
./ringphone replay
```

安装脚本在 `.venv` 中安装本项目、固定公开版本的 OpenZilo SDK 和可选 NumPy 依赖，不下载模型、不修改系统辅助功能设置。

### 2. 先配置手机并验证一次上滑

按照[实机配置文档](docs/setup-iphone.zh-CN.md)，将 Mac 和 iPhone 的切换控制连接起来。两台设备需使用同一 Apple 账户、同一 Wi-Fi。完成手机上滑方案后：

```bash
./ringphone test-key --key SPACE --delay 5
```

验收标准是 **iPhone 上实际且恰好滑到下一条**，不是终端显示发送成功。远程开关与方案的组合需要在目标系统版本上实测。

### 3. 绑定与运行

```bash
./ringphone scan
./ringphone pair
./ringphone monitor --duration 30
./ringphone run --dry-run
```

有多个候选设备时，使用 `pair --address DEVICE_UUID` 或 `pair --cpuid DEVICE_CPUID` 指定。导入原有绑定可使用 `pair --profile /path/to/profile.json`，程序仍会重新实机验证。

手机按键测试通过后启用真实输出：

```bash
./ringphone run --enable-output
```

默认映射为 **按键双击 → 下一条**。普通单击保留给固件切换模式。`p` + 回车暂停，`r` + 回车恢复，`q` + 回车或 Ctrl-C 退出。

## 输入方式

| 模式 | 示例 | 操作 |
| --- | --- | --- |
| 按键双击 | `run --dry-run` | 双击戒指实体按钮 |
| 固件手势 | `run --preset gesture --dry-run` | 手势模式下长按、动作、松开 |
| 导入 RNN | `run --source rnn --model gesture-rnn --preset rnn --dry-run` | 手势模式下连续上报 IMU |

固件手势不等于免按键的连续识别。RNN 路径会主动开启 IMU 上报，并核对采样率和量程；事件路径不会开启 IMU 数据流。

## 训练与模型导入

训练方法统一指向 **[combodied-motion-lab](https://github.com/jzjzzzzzzz/combodied-motion-lab)**：

- [训练与评估](https://github.com/jzjzzzzzzz/combodied-motion-lab/blob/main/docs/training.md)
- [数据格式与归一化](https://github.com/jzjzzzzzzz/combodied-motion-lab/blob/main/docs/data-format.md)

本仓库不上传实际 RNN。用户自行训练或获得兼容的 NPZ 后导入：

```bash
./ringphone model import /path/to/gesture-rnn.npz \
  --name gesture-rnn \
  --lab /path/to/combodied-motion-lab \
  --sample-rate 100 --accel-range 16 --gyro-range 2000

./ringphone model inspect gesture-rnn
./ringphone run --source rnn --model gesture-rnn --preset rnn --dry-run
```

示例采样率和量程必须替换为训练时的真实参数。模型和清单仅写入 Git 忽略的 `models/`。默认 RNN 映射为 `up → next`、`down → previous`；必须与模型标签一致，且模型应包含 `idle`。

支持上游 v1–v3 特征；v4 的窗口内 22 通道预处理需要明确选择并验证训练/推理差异。详见[模型集成契约](docs/models.md)。

## 验证与限制

- 自动化测试覆盖代码、模拟 GATT 和合成模型，不替代手机实测。
- `posted_keys` 只代表 Mac 按键投递数，不代表手机已翻页。
- 程序无法读取手机当前前台 App，离开抖音前请暂停。
- 默认只开启“下一条”。只有确认手机能区分两个远程开关后才开启“上一条”。
- RNN 效果取决于模型、佩戴方式和实测数据，不提供未经验证的准确率承诺。

## 开发与发布

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_release.py --tracked
```

可选 RNN 集成测试通过 `COMBODIED_MOTION_LAB_PATH` 指向独立的 Motion Lab 克隆。公开 CI 使用固定的公开源码和临时生成的合成测试模型。

发布 Topics 包含 **`combodied-ai`、`openzilo`**。参见[发布范围](PUBLIC_RELEASE.md)、[贡献指南](CONTRIBUTING.md)、[安全说明](SECURITY.md)和[第三方依赖说明](THIRD_PARTY_NOTICES.md)。

本应用当前快照尚未指定项目级开源许可证；许可证选择由维护者决定。上游 SDK 的许可证不自动覆盖本应用或用户导入的模型。
