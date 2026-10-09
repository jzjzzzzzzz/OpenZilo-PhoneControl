<div align="center">

# OpenZilo-PhoneControl

**戒指 IMU → 本地推理 → iPhone 操作**

[![CI](https://github.com/jzjzzzzzzz/OpenZilo-PhoneControl/actions/workflows/ci.yml/badge.svg)](https://github.com/jzjzzzzzzz/OpenZilo-PhoneControl/actions/workflows/ci.yml)

[操作流程](docs/quickstart.zh-CN.md) · [模型示例](demo/README.md) · [手机连接](docs/wda.md)

[English](README.md) | **简体中文**

</div>

在 Mac 上接收 OpenZilo 戒指的六轴 IMU，运行本地手势模型，通过 USB WebDriverAgent
控制 iPhone 抖音的下一条、上一条。支持 IMU 采集、训练导出、模型导入和手机操作。

## 系统流程

```text
戒指 → BLE → Mac IMU 窗口 → GRU 分类 → 动作门控 → USB WDA → iPhone
                    ↓
             本地采集数据 → 自行训练 → NPZ 导出 → 导入 Mac
```

模型运行在 Mac；手机安装 WDA 接收操作指令，不将模型文件导入抖音。

## 快速开始

```bash
git clone https://github.com/jzjzzzzzzz/OpenZilo-PhoneControl.git
cd OpenZilo-PhoneControl
./setup.sh
./ringphone doctor

git clone https://github.com/jzjzzzzzzz/combodied-motion-lab.git ../combodied-motion-lab
git -C ../combodied-motion-lab checkout 839ecc0bc89fd560e29eb6cc1b41f17afe5a51d7
.venv/bin/python scripts/demo_imu.py --lab ../combodied-motion-lab \
  --output results/imu-demo.json
```

## 已提供的模型与结果

| 文件 | 内容 |
| --- | --- |
| `demo/imu-baseline.npz` | 最小 GRU 示例，1,692 个参数，12,777 字节 |
| `demo/model-card.json` | 训练配置、传感器参数、校验和及评估结果 |
| `demo/imu-windows.json` | 三个合成六轴输入窗口 |
| `demo/predictions.json` | down / idle / up 的导出概率与动作映射 |

示例模型使用合成 IMU 数据训练。**实际佩戴使用请自行采集戒指数据并训练**。
训练方法由 [ComBodied Motion Lab](https://github.com/jzjzzzzzzz/combodied-motion-lab) 提供。

## 手机操作

完成[WDA 安装流程](docs/quickstart.zh-CN.md#2-安装并启动手机-wda)并保持 Xcode 测试运行：

```bash
.venv/bin/python scripts/wda_usb.py
```

另开终端，手机打开抖音：

```bash
./ringphone phone console --enable-output
```

`u` + 回车：下一条；`d` + 回车：上一条；`q` + 回车：退出。

## 自己采集、训练和导入

```bash
./ringphone pair
./ringphone record --label idle --duration 3
./ringphone record --label up --duration 3
./ringphone record --label down --duration 3
```

各动作重复录制多段，输出在本地 `captures/`。进入戒指手势模式后采集。
安装 Motion Lab 的训练依赖，执行：

```bash
python scripts/train_ring_model.py --lab ../combodied-motion-lab \
  --data 'captures/*.jsonl' --output models/my-ring.npz \
  --report results/my-ring-training.json --epochs 60

./ringphone model import models/my-ring.npz --name my-ring \
  --lab ../combodied-motion-lab \
  --sample-rate 100 --accel-range 16 --gyro-range 2000
./ringphone run --source rnn --model my-ring --preset rnn --backend wda --dry-run
./ringphone run --source rnn --model my-ring --preset rnn --backend wda --enable-output
```

导入参数使用采集报告中的实际数值。完整步骤见[中文操作流程](docs/quickstart.zh-CN.md)。

## 验证与文件管理

```bash
COMBODIED_MOTION_LAB_PATH=../combodied-motion-lab \
  .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_release.py
```

示例已验证训练导出、模型导入和推理结果；自动化集成测试覆盖 IMU 分类到 WDA 指令路由。
手机端已检查 USB WDA、抖音应用标识与翻页操作。

真实采集数据、个人模型、设备绑定、WDA 签名材料和运行截图保存在本地。仓库仅包含
指定的合成示例模型。GitHub Topics：**combodied-ai、openzilo**。
