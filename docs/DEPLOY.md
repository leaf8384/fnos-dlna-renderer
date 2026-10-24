# 部署与恢复

## 1.1.12 音量连续反馈

提示音为 120ms，首次请求合并窗口为 60ms。长按或连续点击会保留当前完整提示音，响完后按最新目标平滑调整；渐变期间若还有新请求，则间隔 80ms 进入下一轮提示音，最后一轮响完才落到最终值。到 0 或 100 后，相同目标的重复请求不额外发声。

音量时序回归新增持续 20Hz 输入、最后目标在不同阶段到达、旧零目标与新非零目标交错、满音量补声后改低以及各阶段停止/静音取消。真实音频回归通过 ALSA file→null 捕获独立提示音，检查实际强度、120ms 时长和双音频率，不以事件日志代替音频输出证据。

2026-10-24：已通过 SSH 部署到 NAS，用户实机试听确认「长度和节奏合适」。当前交互以此为准；后续音量反馈变动仍应进行实听确认。

## 1.1.10 验证与维护

- 音量请求先公布目标值，合并 180ms 内的连续操作；保持当前音乐增益播放 70ms 提示音，提示音排空后用 180ms 平滑到目标。取消压低/恢复音乐的 ducking。静音、暂停、停止以及新请求会使过期提示音任务失效。
- `/api/now` 返回 `has_volume`、`has_position`、`has_media_info` 标记部分查询是否有效；设置页保留最后一次有效值，单次只发一个轮询，控制前发出的旧响应会被丢弃。`TrackURI` 与 `CurrentURI` 不一致时不采用进度。
- 写接口要求 `Content-Type: application/json` 和 `X-DLNA-Request: 1`；浏览器还须通过同源校验。命令行调试 POST 也需带上述请求头。
- 发布索引只选 manifest 指定版本的安装包，并检查包内 appname/version/platform；即使改名的旧包也会拒绝。

验证命令（先构建渲染器，Go 与 Node 需在 PATH 中）：

```bash
(cd console && go test -race ./... && go vet ./...)
python3 tests/test_release.py
node tests/test_ui.cjs
python3 tests/test_volume_sequence.py build/gmrender-resurrect-master
python3 tests/test_renderer_integration.py --renderer build/gmrender-resurrect-master/src/gmediarender
```

最后一项使用独立端口 49610、独立 UUID、实时静音 sink 和 ALSA null 提示音，不改安装实例与音量存档。它验证真实 UPnP/GStreamer 的提示音先后顺序、过期任务取消、静音/暂停恢复及三轮 Seek+URI 切换；实际音箱听感仍需人工确认。

这份文档回答一个问题：**换了机器、重装了飞牛、或者升级之后渲染器不见了，怎么最快恢复。**

## 当前生产环境的实际参数

| 项 | 值 |
|---|---|
| 部署方式 | **FPK 应用**（`appcenter-cli install-fpk -v 1`），2026-10-22 实测通过 |
| 二进制 | `/vol1/@appcenter/dlna-renderer/gmediarender` |
| 配置 | `/vol1/@appdata/dlna-renderer/renderer.conf` |
| 音量存档 | `/vol1/@appdata/dlna-renderer/volume.state`（靠 `GMR_VOLUME_STATE` 环境变量重定向） |
| 日志 | `/vol1/@appdata/dlna-renderer/renderer.log` + `control.log` + `install.log` |
| 网卡 | `enp5s0`（`install_init` 自动探测默认路由出口） |
| 声卡 | `plughw:CARD=PCH,DEV=0`（`install_init` 自动取 `aplay -l` 第一块卡，用名字不用编号） |
| 显示名 | `Marshall` |

音箱是 Marshall Acton II，走 3.5mm AUX 接 fnOS 虚拟机的模拟输出。

**旧的部署方式已于 2026-10-23 彻底清理**，机器上现在只有 FPK 一套，不存在抢声卡的风险。
当时删掉的东西（按顺序）：

| 对象 | 说明 |
|---|---|
| `/etc/systemd/system/gmediarender.service` | 自制 unit，先 `disable` 再删，然后 `daemon-reload` |
| `/usr/local/bin/gmediarender` | 补丁版二进制（md5 `6daa11a0…`） |
| `/usr/local/bin/gmediarender.prev2` | 更早的备份二进制 |
| `/var/lib/gmediarender/` | 音量存档，旧值 70 |
| Debian 包 `gmediarender 0.1-1` | `apt-get purge` 掉的，它自带 `/etc/init.d/gmediarender` + 7 个 `/etc/rc?.d/K01…` 链接 + `/etc/default/gmediarender` + `/usr/bin/gmediarender` + `/usr/share/gmediarender` |

> 那个 Debian 包是最早试水时装的 0.1 版，**不 purge 的话它的 SysV 脚本会一直挂在那里**——
> `systemctl is-enabled gmediarender` 会提示"not a native service, redirecting to systemd-sysv-install"，
> 就是这个包在应答。查残留用 `dpkg -l | grep gmediarender`。

回退需要时，`/vol2/1000/gmrender-backup/` 里仍有二进制、unit 和音量存档的副本，
`restore.sh` 能把 systemd 版装回来（脚本已加防呆：检测到 FPK 版在跑就拒绝执行）。
但这是应急手段，正常恢复请走 FPK。

## 恢复路径

### 路径 A：有 fpk 在手（最快）

飞牛「应用中心 → 设置 → 手动安装」，选 `.fpk`，装完自动起。

配置文件会被重新生成（`renderer.conf`），如果你之前改过 `IFACE` 或显示名，需要再改一次。

### 路径 B：有一台 Linux 构建机

```bash
git clone <repo>
cd fnos-dlna-renderer
sudo apt install -y build-essential autoconf automake libtool pkg-config \
                    libupnp-dev libgstreamer1.0-dev
bash scripts/build.sh
sudo bash scripts/install-standalone.sh
```

`install-standalone.sh` 会自己探测网卡和声卡，生成 `/etc/default/dlna-renderer` 并启动。

### 路径 C：完全从零（连仓库都没有）

那就得靠外部备份了。这份文档存在的意义就是：**别只依赖某一台机器上的文件**。

至少保证下面三样东西另行存一份：

1. 本仓库（推到 GitHub 或私有 Git）
2. 打好的 `.fpk`（扔网盘或 NAS 的另一个盘）
3. `patches/` 目录（万一仓库丢了，这是唯一不可替代的东西）

## 各文件在哪

| 部署方式 | 二进制 | 配置 | 音量 | 日志 |
|---|---|---|---|---|
| fpk | `/volX/@appcenter/dlna-renderer/gmediarender` | `/volX/@appdata/dlna-renderer/renderer.conf` | 同目录 `volume.state` | 同目录 `renderer.log` |
| systemd | `/usr/local/bin/gmediarender` | `/etc/default/dlna-renderer` | `/var/lib/gmediarender/volume.state` | `journalctl -u gmediarender` |

## 升级飞牛要注意什么

**常规小版本更新：不用管。** 飞牛的 OTA 是 apt 升级 `trim-*` 包和内核，不会碰 `/usr/local`、`/etc/systemd/system`、`/var/lib`。

**跨 Debian 大版本（12 → 13）会断。** 二进制链接的是 `libupnp.so.13`，如果新版只有 `libupnp.so.17`，启动会报：

```
error while loading shared libraries: libupnp.so.13: cannot open shared object file
```

处理方式：重新 `bash scripts/build.sh` 编译一版。源码和补丁都在仓库里，不依赖任何外部资源。

**内核升级后声卡编号变化：通常没事。** 因为配置里用的是 `CARD=PCH`（名字），不是 `hw:0`（编号）。万一 `aplay -l` 里连 `PCH` 都没了，那是声卡驱动的问题，不是本项目的。

## 换网卡 / 改网络

渲染器绑在具体网卡上。网卡名一变（比如 `enp5s0` → `enp3s0`），服务照常 `active`，但手机就是搜不到设备——这是最容易踩的坑。

```bash
ip -br link | grep -v ' lo '          # 看现在的网卡名
sudo vi /etc/default/dlna-renderer    # 改 IFACE
sudo systemctl restart gmediarender
```

fpk 版同理，改 `renderer.conf` 后到应用中心停用再启用。

## 验证装好了没有

1. 进程在跑：`systemctl is-active gmediarender` → `active`（FPK 版用 `appcenter-cli status dlna-renderer`）
2. SSDP 有监听：`ss -ulpn | grep 1900`
3. 手机会看到设备：音乐 App 投屏列表里出现 `Marshall`
4. 出声：随便投一首，`aplay -l` 确认声卡没被别的东西占着

## FPK 真装验证记录（2026-10-22）

不是"应该能跑"，是实际装上跑通了。留档备查。

> 下面这段是 1.0.0 首装时的原始记录。1.0.1 用同样流程验证过：
> 包内 manifest 1.0.1、`cmd/main` 带 locale 修复，铺进安装目录后 `running`、
> `<friendlyName>Marshall</friendlyName>` 正常、切歌回归三轮全过、renderer.log 零 error。

```
$ appcenter-cli install-fpk dist/dlna-renderer_v1.0.0_x86.fpk -v 1
$ appcenter-cli status
dlna-renderer │ DLNA 音频桥 │ 1.0.0 │ running

$ ps -o pid,args -p 27936
/vol1/@appcenter/dlna-renderer/gmediarender -I enp5s0 -f Marshall \
  --gstout-audiosink=alsasink --gstout-audiodevice=plughw:CARD=PCH,DEV=0 --logfile=stdout
```

- `install.log`：`依赖已满足` → `已生成默认配置` → `install_callback 完成`
- 依赖清单（`cmd/install_init` 里缺什么补什么，装过的不重复装）：
  **`libupnp13`、`alsa-utils`、`gstreamer1.0-{alsa,plugins-base,plugins-good,plugins-ugly}`**
  - `libupnp13` 是 `gmediarender` 的硬依赖，`ldd` 能直接看到 `libupnp.so.13`；缺了进程起不来
  - `alsa-utils` 提供 `aplay`：设置页的声卡下拉和「测试音」都靠它，缺了声卡列表是空的
  - **卸载不会回滚这几个包**（飞牛只删应用目录和库记录），卸载向导里已经明说了
- `control.log`：`启动：iface=enp5s0 name=Marshall audio=plughw:CARD=PCH,DEV=0` / `已启动 (pid 27936)`
- `renderer.log` error / fatal / cannot / failed 计数 **0**
- SSDP：`0.0.0.0:1900` 与 `[::]:1900` 均 UNCONN 监听

**切歌竞态回归（`tests/t7.py`，三轮 `Seek(0)` 紧跟 `SetAVTransportURI`）全部存活：**

```
[   5.5s] after seek+set #1 (/b.wav)  upnp=PLAYING  pos=0:00:01  dur=0:00:30  alsa=RUNNING
[   9.5s] after seek+set #2 (/a.wav)  upnp=PLAYING  pos=0:00:01  dur=0:00:30  alsa=RUNNING
[  13.5s] after seek+set #3 (/b.wav)  upnp=PLAYING  pos=0:00:01  dur=0:00:30  alsa=RUNNING
```

对应日志每轮都是 `Switch intent: state=3 want_play=1 (in a burst)` → `Switching to ... (target 4)`，
即 400ms 合并窗口正确吸收了 Seek 与切歌的双重状态变更，`watchdog` / `rebuild` / `stream error` 均为 0 次。

**两个已知非问题：**

- `appcenter-cli start` 偶尔报 `[Error]Something wrong with appcenter: code 10500`，但应用实际已经 `starting → running`，进程正常。属 CLI 误报，不用管。
- `volume.state` 新建时是 `60`，装完播放过后变 `90`（手机端调过），说明 `GMR_VOLUME_STATE` 环境变量重定向生效，存档落在 `@appdata` 而不是 `/var/lib`。

## 升级 / 换版本（有坑）

**`appcenter-cli` 既不能升级也不能卸载已安装的应用**，试过：

```
$ appcenter-cli install-fpk -v 1 dlna-renderer_v1.0.1_x86.fpk
[Info]Application [dlna-renderer] is installed.      # 什么都不做，文件时间戳没变

$ appcenter-cli uninstall dlna-renderer
[Error]Failed to uninstall dlna-renderer, please uninstall it from Web UI.
```

它连 `upgrade` 子命令都没有（只有 install / uninstall / start / stop / check / status / list）。
**升级和卸载只能走 Web UI**（应用中心里点「更新」或「卸载」）。

另外版本号和运行状态**记在飞牛自己的库里，不是读 `/var/apps/<app>/manifest`**：
手工改 manifest 的 `version`，`appcenter-cli list` 仍然报旧版本。所以别指望改文件骗过显示。

不想动 Web UI 时，可以手工把新包内容铺进安装目录（等价于完成升级，但飞牛库里版本号不变，
下次从 Web UI 装新版时它仍会认为需要升级，正好走真正的 upgrade 流程）：

```bash
# 1. 解包
P=/tmp/newpkg; rm -rf $P; mkdir -p $P/app
tar xzf dlna-renderer_vX.Y.Z_x86.fpk -C $P
tar xzf $P/app.tgz -C $P/app

# 2. 停（用 cmd/main，别用 appcenter-cli，避免状态机错乱）
sudo /var/apps/dlna-renderer/cmd/main stop

# 3. 铺文件
sudo cp -a $P/manifest $P/cmd $P/config $P/wizard $P/ICON*.PNG /var/apps/dlna-renderer/
sudo chmod 755 /var/apps/dlna-renderer/cmd/*
sudo cp -a $P/app/gmediarender /vol1/@appcenter/dlna-renderer/gmediarender

# 4. 让应用中心的状态机与实际一致
sudo appcenter-cli stop dlna-renderer
sudo appcenter-cli start dlna-renderer
sudo appcenter-cli status dlna-renderer     # → running
```

### 桌面入口 / 图标的三个必填项（漏一个就出怪现象）

桌面图标和窗口标题栏图标**不是同一份数据**，分别来自两个地方：

| 位置 | 来源 | 缺了会怎样 |
|---|---|---|
| 全部应用里的桌面图标 | `<安装目录>/target/ui/images/icon_*.png`（`ui/config` 里的 `icon`） | 图标空白 |
| 窗口标题栏的小图标 | **`app_service.icon`**（库里的一列，形如 `ui/images/icon_{0}.png`） | 请求 `/app-center-static/serviceicon/<app>/?size=256` **404** |
| 窗口标题 | `app_service.title` | 标题栏显示 appname |

`app_service.icon` **不会被「手工铺文件」补上** —— 它是安装时从 `ui/config` 灌进库的。
手工登记应用时必须一起写，否则窗口标题栏那个小图标永远是坏的：

```bash
sudo -u postgres psql -d appcenter -c \
  "update app_service set icon='ui/images/icon_{0}.png' where app_id=(select id from app where app_name='dlna-renderer')"
```

`{0}` 由后端按请求的 `?size=` 替换（`ui/images/icon_256.png` 这种）。
所以 `ui/images/` 下**每种尺寸都要有文件**，缺某个尺寸时那个按钮位置就是空白。

改完不一定立刻生效，应用中心会把这份列表缓存在内存里；实在不刷新再重启
`trim_app_center`（**注意：它会把所有应用重启一遍**，别在放歌的时候干）。

**卸载会连 `/vol1/@appdata/dlna-renderer`（配置 + 音量存档 + 日志）一起删掉**，
真要走 Web UI 卸载前先 `sudo cp -a /vol1/@appdata/dlna-renderer /vol1/@appdata/dlna-renderer.backup`。

## 回退

这个项目没有数据库、没有迁移脚本，回退就是换一个二进制：

```bash
# 从 git 检出上一个版本重新构建
git checkout <上一个 tag>
bash scripts/build.sh
sudo bash scripts/install-standalone.sh
```

配置和音量存档格式没变过，换了二进制不用动它们。
