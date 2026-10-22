# 部署与恢复

这份文档回答一个问题：**换了机器、重装了飞牛、或者升级之后渲染器不见了，怎么最快恢复。**

## 当前生产环境的实际参数

| 项 | 值 |
|---|---|
| 部署方式 | systemd 裸装（`/usr/local/bin/gmediarender` + `gmediarender.service`） |
| 网卡 | `enp5s0`（fnOS 虚拟机唯一 virtio 网卡） |
| 声卡 | `plughw:CARD=PCH,DEV=0`（HDA Intel PCH，Intel 核显 HDMI/模拟输出） |
| 显示名 | `Marshall` |
| 音量存档 | `/var/lib/gmediarender/volume.state` |
| 二进制 md5 | 见 `dist/` 或本机 `md5sum`，构建产物与仓库源码严格对应 |

音箱是 Marshall Acton II，走 3.5mm AUX 接 fnOS 虚拟机的模拟输出。

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

1. 进程在跑：`systemctl is-active gmediarender` → `active`
2. SSDP 有监听：`ss -ulpn | grep 1900`
3. 手机会看到设备：音乐 App 投屏列表里出现 `Marshall`
4. 出声：随便投一首，`aplay -l` 确认声卡没被别的东西占着

## 回退

这个项目没有数据库、没有迁移脚本，回退就是换一个二进制：

```bash
# 从 git 检出上一个版本重新构建
git checkout <上一个 tag>
bash scripts/build.sh
sudo bash scripts/install-standalone.sh
```

配置和音量存档格式没变过，换了二进制不用动它们。
