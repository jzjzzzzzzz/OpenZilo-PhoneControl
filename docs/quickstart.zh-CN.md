# 从戒指 IMU 到 iPhone 操作

## 1. 准备 Mac 与模型运行时

```bash
./setup.sh
./ringphone doctor
git clone https://github.com/jzjzzzzzzz/combodied-motion-lab.git ../combodied-motion-lab
git -C ../combodied-motion-lab checkout 839ecc0bc89fd560e29eb6cc1b41f17afe5a51d7
.venv/bin/python scripts/demo_imu.py --lab ../combodied-motion-lab \
  --output results/imu-demo.json
```

示例导出结果包含输入标签、160 帧 IMU 窗口、预测类别、概率以及 next/previous 映射。
对应的模型与结果可直接查看仓库 `demo/`。

## 2. 安装并启动手机 WDA

1. 在 Mac 安装支持手机 iOS 的完整 Xcode。
2. 使用 USB 数据线连接 iPhone，在手机确认信任 Mac。
3. 在手机启用开发者模式和 UI 自动化。
4. 将 [WebDriverAgent](https://github.com/appium/WebDriverAgent) 下载到本地 `vendor/`，打开工程。
5. 选择 `WebDriverAgentRunner`，在 Signing & Capabilities 选择自己的 Team，配置唯一 Bundle Identifier。
6. 顶部设备选择 iPhone，按 **⌘U** 启动测试，手机显示 **Automation Running**。

免费 Apple Account 可用于 Personal Team 签名。[官方设备与签名说明](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/device-setup/)。
Xcode、WDA 源码和签名材料都不需要提交到本仓库。

## 3. 连接手机并验证翻页

保持 Xcode 测试运行，在普通 Mac 终端执行：

```bash
.venv/bin/python scripts/wda_usb.py
```

输出 WDA ready 后，本地地址为 `http://127.0.0.1:18100`。保留该终端。
手机打开抖音，另开终端执行：

```bash
./ringphone phone next --enable-output
./ringphone phone previous --enable-output
./ringphone phone console --enable-output
```

控制台支持 `u`、`d`、`q`，输入后回车。检查视频作者/标题的变化。

## 4. 绑定戒指并导出 IMU

```bash
./ringphone scan
./ringphone pair
./ringphone monitor --duration 10
```

切到戒指手势模式，按标签分别采集：

```bash
./ringphone record --label idle --duration 3
./ringphone record --label up --duration 3
./ringphone record --label down --duration 3
```

每次输出一份 `captures/*.jsonl` 和传感器报告。每类录制多段，覆盖不同幅度、佩戴位置和
背景活动；按完整记录段划分训练、验证、测试数据。保持测试记录独立。

数据示例：

```json
{"schema":"ring-imu/v1","session_id":"sample-up-01","label":"up","sample_rate_hz":100,"sequence":0,"timestamp_ms":0,"accel_raw":[0,0,2048],"gyro_raw":[0,0,0],"accel_range_g":16,"gyro_range_dps":2000}
```

## 5. 自行训练并导出模型

在训练环境安装 `combodied-motion-lab/ml/requirements-train.txt`，再从 PhoneControl 根目录运行：

```bash
python scripts/train_ring_model.py --lab ../combodied-motion-lab \
  --data 'captures/*.jsonl' --output models/my-ring.npz \
  --report results/my-ring-training.json \
  --window-seconds 1.6 --stride-seconds 0.2 --target-steps 40 \
  --hidden-size 32 --epochs 60
```

产物为便携 NPZ 和训练报告。入口使用 Motion Lab 的 v3 特征训练流程，与本应用的
六轴输入推理一致。模型训练与数据评估方法见 [Motion Lab 文档](https://github.com/jzjzzzzzzz/combodied-motion-lab/blob/main/docs/training.md)。

## 6. 导入 Mac 并接通手机输出

```bash
./ringphone model import models/my-ring.npz --name my-ring \
  --lab ../combodied-motion-lab \
  --sample-rate 100 --accel-range 16 --gyro-range 2000
./ringphone model inspect my-ring
./ringphone run --source rnn --model my-ring --preset rnn --backend wda --dry-run
./ringphone run --source rnn --model my-ring --preset rnn --backend wda --enable-output
```

采样率和量程使用步骤 4 的报告值。模型在 Mac 推理：up → 下一条，down → 上一条，idle
重新允许下一次动作。`p` + 回车暂停，`r` + 回车恢复，`q` + 回车停止。

手机只安装 WDA；NPZ 导入 Mac 的控制应用，不导入手机或抖音。仓库中的最小示例模型
用于离线导入与结果复现，实际操作使用自行训练的模型。
