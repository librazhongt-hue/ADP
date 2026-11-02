# ADP 车端最小包（ROS 场景 A/B/C）

本包是从完整 ADP 仓库裁剪出的**可迁移边端包**，用于在没有完整 ADP 仓库的机器人 Ubuntu 上跑：

- ROS 结合 demo：`examples/ros_robot_live_validation`（A goto / B 意图中枢 / C VLM 寻物）
- 离线 ADP 冒烟：`examples/aquaculture_feeding_loop`（不控车，验证平台可 import）

**不含**：养殖 live MySQL 验证、完整 `docs/` / `reports/`、云端 RDS 依赖。

## 系统要求

| 项 | 要求 |
|---|---|
| OS | 车上 Ubuntu（与现有 `ros_ws` 相同） |
| Python | **≥ 3.11**（与 ADP 一致；若系统是 3.8/3.10，请用符合版本的解释器） |
| ROS 2 | 已能跑 `car_base` + `car_nav2`；C 另需相机与对应 VLM 测试脚本 |
| 网络 | `--real-llm` / C `--image` 需要能访问 `ADP_LLM_BASE_URL` |

## 安装（推荐：系统/ROS 同款 python，避免 venv 丢 rclpy）

```bash
# 1) 解压
cd ~
tar -xzf adp-ros-edge-latest.tar.gz
cd adp-ros-edge-*   # 或解压出的目录名

# 2) 先 source ROS，再用同一 python 装 ADP
source /opt/ros/<foxy|humble>/setup.bash
source ~/ros2_ws/install/setup.bash   # 按车端实际路径
export ROS_DOMAIN_ID=99               # 与现场一致

python3 -m pip install -e ".[dev]" --user
# 需要 PyYAML 读航点：
python3 -m pip install pyyaml --user

# 3) 配置
cp .env.example .env
# 编辑 .env：至少填 ADP_LLM_*；ADP_ROS_WS 指向车上 ros_ws
# 场景 C 文件图 / 真机 VLM：可设 ADP_VLM_MODEL 或 VLM_MODEL
```

若坚持用 venv：请 `python3 -m venv --system-site-packages .venv`，并保证父 shell 已 source ROS；真机 Act 仍建议设置 `ADP_ROS_PYTHON=/usr/bin/python3`。

## 离线冒烟（不控车）

```bash
cd ~/adp-ros-edge-*
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

python3 -m examples.aquaculture_feeding_loop.run_demo
python3 -m examples.ros_robot_live_validation.run_demo \
  --scenario a --utterance "去桌子" --auto-approve
python3 -m examples.ros_robot_live_validation.run_demo \
  --scenario b --utterance "帮我找绿色瓶子" --auto-approve
python3 -m examples.ros_robot_live_validation.run_demo \
  --scenario c --utterance "帮我找红色水杯" --auto-approve
```

可选单测：

```bash
python3 -m pytest -q tests/test_examples_ros_robot_live_validation.py
```

## 真机 goto（场景 A）

```bash
# 终端 1
ros2 launch car_base car_base.launch.py

# 终端 2
ros2 launch car_nav2 car_nav2.launch.py slam:=False
# 车被搬过：RViz 设 2D Pose Estimate

# 终端 3（已 source ROS + 工作空间，ROS_DOMAIN_ID 一致）
cd ~/adp-ros-edge-*
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
export ADP_ROS_PYTHON=/usr/bin/python3

python3 -m examples.ros_robot_live_validation.run_demo \
  --scenario a \
  --utterance "去桌子" \
  --real-llm \
  --live \
  --ros-ws "$HOME/ros2_ws"
# 审批时输入 y（真车不要先用 --auto-approve）
```

## 真机 VLM 寻物（场景 C）

```bash
# P0 静止检测：按现场准备相机 + 能跑通 vlm_detect_once_test.py 的环境
# （通常不需要 lidar wander；与 goto 冲突的 wander 不要开）

python3 -m examples.ros_robot_live_validation.run_demo \
  --scenario c \
  --target "红色水杯" \
  --live \
  --motion once \
  --ros-ws "$HOME/ros2_ws"

# P1 运动搜物：需满足 vlm_object_search_test.py 的 wander|nav 前置
python3 -m examples.ros_robot_live_validation.run_demo \
  --scenario c \
  --target "绿色瓶子" \
  --live \
  --motion wander \
  --ros-ws "$HOME/ros2_ws"
```

离线/文件图（不控车）：`--image /path/to.jpg` + `ADP_LLM_*` + 视觉模型（`ADP_VLM_MODEL` / `VLM_MODEL`）。

说明：

- `--live` 对 A：`goto` → `go_waypoint_test.py`
- `--live` 对 C：`vlm_detect_once` / `vlm_object_search`（按 `--motion`）
- B 的 search/drive/arm 即使 `--live` 也会 skipped（安全门；寻物用 C）
- 本包**不会**自动 launch 底盘/Nav2/相机栈

## 环境变量（`.env`）

见 [`.env.example`](.env.example)。车上最少：

```bash
ADP_LLM_BASE_URL=...
ADP_LLM_API_KEY=...
ADP_LLM_DEFAULT_MODEL=...
ADP_ROS_WS=/home/xxx/ros2_ws
# ADP_ROS_PYTHON=/usr/bin/python3
# ADP_ROS_WAYPOINTS=/home/xxx/ros2_ws/src/car_nav2/map/waypoints.yaml
# ADP_VLM_MODEL=...   # 或 VLM_MODEL=... 场景 C 多模态
```

`DASHSCOPE_*` / `ASR_*` / `TTS_*` 对当前 A/B/C demo **不是必须**（语音仍走 CLI 文本）。

## 目录结构

```text
adp/                              # 平台代码
examples/
  ros_robot_live_validation/      # A/B/C ROS 结合 demo
  aquaculture_feeding_loop/       # 离线冒烟
tests/                            # 对应单测（精简）
pyproject.toml
README.md                         # 本文
.env.example
```

## 重新打包（在开发机）

```bash
cd /path/to/full/ADP
bash scripts/pack_ros_edge_bundle.sh
# 产物：dist/adp-ros-edge-latest.tar.gz
```
