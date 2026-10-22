# 应用化方案：带设置界面的 DLNA 音频桥

> 状态：**已实施（v1.1.0，2026-10-23）**。实现在 [`console/`](../console/)，编译产物 `dlna-console`，
> 由 `cmd/main` 与渲染器一起拉起，监听 8790，manifest 的 `service_port` 已填。
> 本文保留为设计依据，实施过程中与设计稿的偏差记在文末「实施备注」。
> 目标：装成飞牛应用后，在**一个页面**里能改**广播名、声卡、网卡**三项，改完即时生效。界面里不做开关——启停交给应用中心的「启用 / 停用」。

---

## 1. 现状与差距

```
现在：应用中心 ──启用/停用──> fpk/cmd/main start|stop ──nohup──> gmediarender
                                                              ↑
                          renderer.conf（手改 or 安装时生成，改完要手动重启）
```

缺三样东西：**能采集硬件列表**、**能改配置**、**能让改动生效**。而这三点都要在 NAS 上以 root 跑命令，所以必须有一个常驻的小服务来做，不能靠静态页。

---

## 2. 方案：应用内置一个控制台

飞牛第三方应用支持 `service_port`：填了之后应用中心会有「打开」按钮，直接指向 `http://<NAS>:<port>`。利用这个入口挂一个极小的 Web 控制台。

```
应用中心 ──启用/停用──> cmd/main start|stop
                          ├─ gmediarender（渲染器本体，照旧）
                          └─ dlna-console （控制台，监听 :8790）

浏览器 ──http://192.168.3.250:8790──> dlna-console
                                        ├─ 读/写 renderer.conf
                                        ├─ aplay -l / ip -br link 枚举
                                        ├─ 播放测试音
                                        └─ 调 cmd/main restart-renderer 让配置生效
```

### 为什么控制台要独立成一个进程

- 控制台崩了不该影响正在播放的音乐；
- 改配置要重启渲染器，控制台得活着才能把新配置写下去并重新拉起；
- 两者由同一条 `cmd/main start|stop` 一起启停，用户只在应用中心点启用/停用。

### 为什么重启走 `cmd/main restart-renderer`，而不是控制台自己 spawn

gmediarender 的启动参数（`-I` / `-f` / `--gstout-audiodevice` / `GMR_VOLUME_STATE` 环境变量）现在只写在 `cmd/main` 里。如果控制台为了重启另写一份，两处参数迟早漂移——改了 `cmd/main` 忘了改控制台，就会出现"应用中心重启正常、界面保存后没声音"这种极难查的问题。**启动参数只允许有一处。**

`cmd/main start` 里按顺序拉起两个进程、两个 pidfile；`stop` 两个一起收。

### 技术选型

| 层 | 选择 | 理由 |
|---|---|---|
| 后端 | **Go 单文件静态编译** | 无运行时依赖，不受 fnOS 系统 Python 升级影响；`os/exec` 调 `aplay`/`ip` 足够 |
| 前端 | 单个 `index.html`，vanilla JS | 就四个控件，不值得上框架；用 `//go:embed` 打进二进制 |
| 通信 | 5 个 JSON 接口 | 见下 |
| 端口 | `8790`（`service_port` 写死在 manifest） | 避免和 49494（UPnP）冲突 |

接口：

```
GET  /api/state      → {running, friendly_name, iface, audiodev, version}
GET  /api/devices    → {cards:[...], ifaces:[...]}   枚举硬件
POST /api/config     → {friendly_name, iface, audiodev}  写配置并重启渲染器
POST /api/test-tone  → 在选定声卡上放 2 秒 440Hz
```

---

## 3. 三项设置的落地细节（外加开关的归属）

### 3.1 广播名（friendlyName）

**这里有个已知坑必须先解决**：现在 `-f` 传中文会启动失败报 `Invalid byte sequence`，所以一直用 `Marshall`。

原因不是 gmediarender 不支持 UTF-8，而是 **GLib 处理命令行参数时会按当前 locale 做编码转换**。`main.c` 里是用 `G_OPTION_ARG_STRING` 收 `--friendly-name` 的，若 `LANG` 是 `POSIX`/`C`（飞牛服务环境下常见），非 ASCII 字节就转换失败。

**修法：启动环境加 `export LANG=C.UTF-8 LC_ALL=C.UTF-8`，不用改源码。** 验证方式：

```bash
LANG=C.UTF-8 gmediarender -f "客厅音箱" ... # 能起来且 description.xml 里是中文
locale                                      # 先看当前是什么
```

补两件事：

- `description.xml` 里 `friendlyName` 必须做 XML 转义（`xmlescape` 已处理，但中文之外的 `& < > "` 也要过一遍）；
- 名字长度限制 32 字符，去掉换行和控制字符——有 App 会截断或直接不显示。

改名后必须**重启渲染器**（friendlyName 写死在设备描述里，libupnp 注册后不会热更新）。控制台要做成「保存 → 停 → 起 → 等 1.5 秒 → 探测 1900 有没有新名字的 NOTIFY」这样一个完整的确认流程，别只回个 200 就完事。

### 3.2 声卡

枚举 `aplay -l`，同时拿 card 名和人类可读描述：

```
card 0: PCH [HDA Intel PCH], device 0: ALC887-VD Analog [ALC887-VD Analog]
card 1: HDMI [HDA Intel HDMI], device 3: HDMI 0 [HDMI 0]
```

下拉里显示 `ALC887-VD Analog（3.5mm 绿孔）` / `HDMI 0（核显）`，值存 `plughw:CARD=PCH,DEV=0`。

要点：

- **存名字不存序号**。`CARD=PCH` 重启后稳定，`hw:0,0` 会漂（HDMI 枚举顺序会变）。
- 显卡直通给 fnOS 的话 HDMI 声卡会出现，用户可能真的想从 HDMI 出声——两个都要列。
- **必须有「测试音」按钮**。选错声卡的症状是「UPnP 报 PLAYING 但没声音」，光看界面看不出来。用 `aplay` 放一段内置的 2 秒正弦波最省事（不依赖 GStreamer）。
- 切换声卡前要检查该设备是否被占用（`fuser /dev/snd/pcmC0D0p`），占用时提示而不是静默失败。

### 3.3 网卡

枚举 `ip -br link`，过滤掉 `lo`、`docker*`、`veth*`、`tailscale*`、`br-*`，每个候选配它的 IPv4 和「是否默认路由」。

默认选中「有默认路由的那张」。这不是随便挑的——**SSDP 依赖组播，选错网卡手机就完全搜不到设备**，而症状是「应用在跑、日志没报错、但设备列表里没有」。

要在界面上直接给警示文案：*切换网卡后手机上可能要 1–2 分钟才刷新出设备，或需要重开音乐 App。*

### 3.4 开关：不做在界面里

启停完全交给应用中心的「启用 / 停用」：

| 操作 | 实际行为 |
|---|---|
| 应用中心「停用」 | `cmd/main stop` → 渲染器 + 控制台一起停 |
| 应用中心「启用」 | `cmd/main start` → 一起起 |

这样不需要在 `renderer.conf` 里记 `ENABLED`，也不用担心"应用重启后自动恢复广播"这种状态不一致。

代价是：**应用停用后配置页也进不去**，想改配置必须先启用。这个取舍是划算的——用户要停播时本来就在应用中心，不会先开浏览器。

---

## 4. 配置文件

保持现在的 shell 可 `source` 的格式（`cmd/main` 直接 `.` 进来用），扩一个字段：

```sh
# 唯一不可重复的标识，决定手机记住的是哪台设备；不要随名字变
UDN="uuid:GMediaRender-1_0-000-000-002"
FRIENDLY_NAME="Marshall"
IFACE="enp5s0"
AUDIODEV="plughw:CARD=PCH,DEV=0"
```

`UDN` 独立存的好处：用户把名字从「Marshall」改成「客厅音箱」，手机里还是同一台设备，不会多出一条幽灵记录。

---

## 5. 生效机制

参数（名字/网卡/声卡）都是 gmediarender 的启动参数，改完只能重启进程。断流窗口约 1~2 秒，要在界面上明确写「保存后音乐会有 1–2 秒中断」，别让人以为坏了。

```
保存 → 校验 → 写 conf（原子写：先写 .tmp 再 mv）
     → cmd/main restart-renderer（内部：停 → 等 pid 真退出，最多 5 秒 → 起）
     → 轮询 status + 嗅探 1900 的 NOTIFY 确认新名字已广播
     → 返回结果（失败时把 renderer.log 最后 20 行带回来显示）
```

最后一步的日志回传很重要——启动失败最常见的原因是声卡被占或网卡名不存在，用户不该被要求去 SSH 翻日志。

---

## 6. 权限与安全

- 控制台以 **root** 跑（要执行 `aplay`、`ip`、启停进程）。这是 `privilege` 里 `run-as: root` 的既定事实。
- 因此**必须限制暴露面**：只监听局域网网卡，不要 `0.0.0.0` 全绑；不要提供任何"执行任意命令"的接口，接口参数一律白名单校验（声卡名必须出现在 `aplay -l` 的结果里，网卡名必须在 `ip -br link` 里）。
- fnOS 有公网 IPv6 下发到内网设备（前缀会漂移），所以**不要只靠绑定 0.0.0.0 + 防火墙**。控制台只绑内网 IPv4，不提供外部访问入口。
- 不做的事：不提供文件浏览、不提供上传、不提供任意重启、不做管理密码体系（只在内网用）。

---

## 7. 分阶段

| 阶段 | 内容 | 能否独立交付 |
|---|---|---|
| P0 | 修中文广播名（`LANG=C.UTF-8`）+ 补 `dlna:X_DLNADOC` | 是，两处小改，先验证 |
| P1 | 单页控制台：展示 + 改三项 + 测试音 + 保存生效 + `service_port` 接进应用中心 | 是，一次做完 |

**不再拆成"只读 / 可写"两期**——界面只有一个，拆开做等于同一个页面写两遍，中间还得维护一个半成品。

P0 独立于应用化，**先做**——如果 `LANG` 那招不成立，整个"中文广播名"的需求都得改方案（退回改 C 源码，工作量翻倍）。

---

## 8. 顺带要改的

- `manifest` 加 `service_port = 8790`，`fnpack.json` 的 `service_port` 同步填上，否则应用中心不出现「打开」按钮。
- `cmd/main` 新增 `restart-renderer` 子命令；`do_start` 拉起两个进程、写两个 pidfile；`do_stop` 两个都收（先渲染器后控制台）。
- `status` 仍只看渲染器：渲染器在跑就 exit 0。控制台意外退出不该让应用中心以为整个应用挂了。
- `README` 增加一节「装好后怎么进设置页」。
