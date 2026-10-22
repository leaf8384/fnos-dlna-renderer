# 未来计划

> 记录于 2026-10-22，更新于 2026-10-23。当前版本 **1.1.4**（1.0.0 稳定运行；1.0.1 修中文设备名；1.1.0 网页设置界面；1.1.1 播放控制 + 桌面入口；1.1.2 去重复页头 + 修窗口图标；1.1.3 补安装依赖；1.1.4 修入口配置），**暂无必须做的事**，以下按优先级排列，做到哪算哪。
> 应用化设计的完整方案见 [`docs/app-console-plan.md`](app-console-plan.md)。

## P0 · 待验证（决定后续路线，30 分钟）

- [x] ~~**中文广播名能不能不源码就支持**~~ —— **已实测：能，不用改 C（2026-10-22）**
      `main.c` 用 GLib 的 `G_OPTION_ARG_STRING` 收参数，GLib 按 locale 转 argv，猜对了。
      根因是**进程里根本没有 `LANG`**：飞牛只注入 `TRIM_SYS_LANGUAGE=zh-CN`（glibc 不认这个变量），
      appcenter 拉起的环境里 `LANG/LC_*` 全无（`/proc/PID/environ` 已确认），于是落到 C locale。

      ```
      LANG=C.UTF-8    -f "客厅音箱" → <friendlyName>客厅音箱</friendlyName>   ✔
      LANG=en_US.UTF-8 -f "客厅音箱" → <friendlyName>客厅音箱</friendlyName>   ✔
      LANG=C          -f "客厅音箱" → Failed to initialize: Invalid byte sequence in conversion input  ✘
      ```

      **修法：`cmd/main` 启动前 `export LANG=C.UTF-8 LC_ALL=C.UTF-8`**（systemd unit 同理加 `Environment=`）。
      已应用并在机器上实测：中文名启动成功、`description.xml` 输出正确。
      **结论：应用化里"改广播名"功能就是改配置 + 重启，工作量不翻倍。**
      坑：`--dump-devicedesc` 不是纯本地操作，它仍要初始化 UPnP 抢 49494 端口，测试必须带 `-p <空闲端口>`。

- [ ] **给设备描述补 `dlna:X_DLNADOC`**（改 C 源码 + 重编）
      对照 MiCast-Go 的实现，它写了 `<dlna:X_DLNADOC>DMR-1.50</dlna:X_DLNADOC>`，我们上游没有（已 grep 确认）。这是"某些国产 App 搜不到设备"的可疑原因，补上就能验证。
      **注意：没有 `src/device-desc.xml` 这个文件**（2026-10-23 复核 vendor 源码，`find` 无此文件）。设备描述是 `upnp_device.c` 里 `generate_desc()` **在 C 里拼出来的**，要改两处：① root 元素加 `xmlns:dlna="urn:schemas-dlna-org:device-1-0"`；② device 元素下插 `<dlna:X_DLNADOC>DMR-1.50</dlna:X_DLNADOC>`。然后重编、重打包 fpk。

## P1 · 单页控制台 · **已完成（v1.1.0，2026-10-23）**

一个页面搞定「看 + 改 + 生效」，不分只读/可写两期。设计稿见 [`docs/app-console-plan.md`](app-console-plan.md)。

- [x] **`dlna-console`**（Go 单文件 + `//go:embed` 单页），默认监听 `:8790`
      `CGO_ENABLED=0` 静态二进制 5.9 MB，不依赖目标机 libc → **Debian 12 自带 Go 1.19 即可编译，不需要 Docker**
- [x] **`manifest` / `fnpack.json` 填 `service_port = 8790`**，应用中心出现「打开」按钮
- [x] **页面内容**（同一界面全部搞定）
      - 运行状态 + 版本 + PID + 监听地址 + 当前配置
      - **广播名**（文本框，**中文实测可用**，限 32 字符）
      - **声卡**（下拉，`aplay -l` 枚举成 `ALC887-VD Analog` 这种可读形式，**过滤 `pcsp` 主板蜂鸣器**）
      - **网卡**（下拉，`ip -br link` 枚举，标出哪张走默认路由）
      - **「播放测试音」**（纯 stdlib 合成 2 秒 440Hz WAV 喂给 `aplay`，不依赖 GStreamer；首尾 50ms 淡入淡出防爆音）
      - **「保存并重启」**
- [x] **保存流程**：白名单校验 → 原子写 `renderer.conf` → `cmd/main restart-renderer` → **真去拉 `description.xml` 比对 `friendlyName`** → 失败回传 `renderer.log` 末 25 行
      实测保存到生效 **2.75 秒**。这里刻意不用「嗅探 1900 的 NOTIFY」——要抓组播包得开混杂监听，而 HTTP 拉一次描述文件既准确又不需要特权。
- [x] **`cmd/main` 增加 `restart-renderer` 子命令**（**只**重启渲染器，不动控制台）
      `-I / -f / -u / --gstout-*` / `GMR_VOLUME_STATE` **只在 `cmd/main` 里写一份**，控制台通过调它间接生效，不自己 spawn，从根上杜绝两处参数漂移。
- [x] **`cmd/main start` 管两个 pidfile**（`pid` + `console.pid`），一起起落；`status` 仍只看渲染器，控制台挂掉不影响应用中心显示「运行中」
- [x] **`UDN` 独立存储**（`renderer.conf` 的 `UDN=`），改名不产生幽灵设备
- [x] **声卡占用检测**：读 `/proc/asound/cardN/pcmMp/sub0/status` 判 `RUNNING`
      计划里写的是 `fuser /dev/snd/pcmC*D*p`，实测**不可用**——渲染器自己就占着设备，`fuser` 分不清「自己占」和「别人占」；查 PCM 状态字符串才能判断是否真在出声。

### 安全边界（重要）

- **只绑内网 IPv4**，绝不 `0.0.0.0`：飞牛会下发公网 IPv6，控制台以 **root** 跑，暴露出去等于把根权限挂到公网上。启动时先解析网卡 IPv4 再 `DLNA_BIND=`。
- **所有入参白名单校验**：不存在的网卡名、不存在的声卡设备、空名字、`;reboot` 这类注入串一律 `400`；GET 打 POST 接口返 `405`。
- `renderer.conf` 最终会被 shell `.` source，值里的 `$` / 反引号 / 引号 / 反斜杠写入前全部转义。

### 不做：控制台内的开关

开关完全交给应用中心的**启用 / 停用**：

```
停用 → cmd/main stop  → 渲染器 + 控制台一起停
启用 → cmd/main start → 一起起
```

代价要说清楚：**应用停用后连配置页都进不去**，要改配置得先启用。接受这个代价，换来少一个状态要维护（不用再记 `ENABLED=0`，也不用担心应用重启后自己恢复广播）。


## P2 · 播放控制与桌面入口 · **已完成（v1.1.1 / 1.1.2，2026-10-23）**

- [x] **`/api/now`**：一次拿全 `state / title / artist / album / duration / position / volume / has_media / uri`
      - **控制地址不是标准的 `AVTransport1`**：gmediarender 是 `/upnp/control/rendertransport1`、`/upnp/control/rendercontrol1`，连错一律 404
      - **`GetPositionInfo` 不返回 `CurrentTrackMetaData`**（实测响应里整段缺失，不是空值）→ 曲名/艺人只能从 `GetMediaInfo` 的 `CurrentURIMetaData` 取
      - 元数据是**双重转义**的 XML（`&lt;dc:title&gt;` 里面还有实体），要反转义两次再 `tagValue`
      - 网易云只写 `dc:creator`，`upnp:artist` 常为空 → 两级回退
- [x] **`/api/control`**：`play / pause / stop / seek / volume`，前端只传动作名和一个数字，SOAP 参数**全部由后端拼**
      - `seek` 校验 `0..86400`、`volume` 校验 `0..100`，越界与未知动作都回 `400`
- [x] **僵尸态处理**：EOS 会把 URI 清空，客户端随后补发一个 `Play`，渲染器允许 `STOPPED → PLAYING`（URI 仍为空）
      → 后端返回 `has_media`，前端 `has_media=false` 直接显示「未在播放」，不再拿上一首的曲名配零进度
- [x] **固定布局**：`html,body{height:100%}` + `overflow:hidden`，两栏 grid，播放卡与设置卡各自 `margin:auto 0` 垂直居中；`max-width:720px` / `max-height:560px` 两档媒体查询兜底
- [x] **`ui/config` 的正确写法（v1.1.4 修正）**：飞牛**不解析花括号占位符**（`title` 会原样显示），
      也没有 `[entry]` 这种小节。访问地址要靠**独立的 `port` 字段**：
      后端拼 URL 的格式串是 `%s://%s:%d/%s`（有 port 时）和 `%s://${host}` + 路径（无 port 时），
      把 `":8790/"` 塞进 `url` 只会被当成路径 → 变成 `http://主机/:8790/` → 404。
      正确写法：`protocol: http` + `port: 8790` + `url: "/"` + 字面 title/desc。
- [x] **桌面入口**：`fpk/app_ui/{config,images}` → 打包进 `app/ui`
      - `config` 里 `"type":"iframe"` 才能内嵌窗口；`icon` 写 `images/icon_{0}.png`（`{0}` 在飞牛自己的资源里是**字面量**，别当占位符）
      - `manifest` 必须同时有 `desktop_uidir = ui` 和 `desktop_applaunchname`
      - **第三方应用永远拿不到 `inbuilt`**（前端 `nativeApp: e.type === 'inbuilt'`，那是内置应用专用）→ 改 `app.native_app` 没用，正路只有 `app_service.type='iframe'` + `ui/`
      - 图标用 Pillow 从 256 原图重采样出 16/24/32/48/64/72/96/128/256 全套
- [x] **窗口标题栏图标（v1.1.2）**：`app_service.icon` 是**空字符串** → 前端拼出
      `/app-center-static/serviceicon/dlna-renderer/` + 空 → 404。这列是安装时从 `ui/config` 灌进库的，
      **手工铺文件补不上**，必须单独 update 成 `ui/images/icon_{0}.png`。
      `{0}` 由后端按 `?size=` 替换，所以每种尺寸都得有文件；解析基准是 `<app.path>/target/`。
      **桌面图标（全部应用里那个）和标题栏图标不是同一份数据**，前者走 `target/ui/images`，后者走库里这列。
- [x] **页面里的标题不再重复（v1.1.2）**：飞牛给的是带标题栏的内嵌窗口，应用名和图标已经在标题栏上，
      页头那套 logo + h1 + 副标题整体删掉，运行状态徽标挪到「设备设置」标题行右侧。
- [x] **应用中心库里补登记**：列表存在 **postgres `appcenter` 库**（`app_service` / `app_open` / `app_poster`），不是扫目录；手工铺文件不会自动写库。重启 `trim_app_center` 会把**所有应用**重启一遍（约 20 秒）


## 安装依赖（v1.1.3 修）

`cmd/install_init` 原来只 apt 补 `gstreamer1.0-*` 四个包，**漏了 `libupnp13` 和 `alsa-utils`**：

- `ldd gmediarender` 明确要 `libupnp.so.13` → 缺了**进程根本起不来**
- `aplay` 来自 `alsa-utils`，设置页枚举声卡和「测试音」都靠它 → 缺了声卡列表是空的

这两个包在本机是早期手工 `apt install` 的，所以走 fpk 装的流程一直没暴露。
**卸载不会回滚这些系统包**（飞牛只删应用目录和库记录，卸载向导里已写明）。

## 重启验证清单

> 目前**唯一必须重启才能验**的是直通 ① 层（`vfio-pci.ids` 至今没生效过，改完后没重启过）；顺带把「开机自启机制未查清」那条一并确认。

**重启前**
- [ ] 确认重要数据有独立备份（阵列无 parity、disk2 已用 93%）
- [ ] 直通四层原件在本地：`Unraid/configs/passthrough-2026-10-23/raw.txt`（U 盘挂了照它恢复）

**重启后**
- [ ] `bash scripts/check-passthrough.sh` → ①b 从 WARN 变 PASS，④ 三个设备仍是 `vfio-pci`
- [ ] fnOS 内 `cat /proc/asound/cards` 仍有 `PCH`；手机投屏列表能搜到「肥牛」
- [ ] 渲染器自动起来了（不确定是谁拉起的，正好这次确认）且音量是记忆值、不是满音量
- [ ] 应用中心显示「运行中」，`http://192.168.3.250:8790` 能打开、播放控制正常
- [ ] 若没自动起来 → 顺着技术债「开机自启机制未查清」查（`trim_app_cgi` 曾显示 `disabled`）

## 技术债 / 遗留

- [x] ~~推 GitHub~~ —— 已上 `github.com/leaf8384/fnos-dlna-renderer`，v1.0.0 已发布，CI 用 `debian:12` 容器编译（40 秒通过）
- [x] ~~本地/远端 git 历史分叉~~ —— 2026-10-22 已解决。原因不是真分叉：用 Git Data API 推送时远端 main 被改写成同内容、不同 sha 的提交（`53b3486`），且漏掉了 `docs/app-console-plan.md`。`git fetch` 后用 `git reset --soft origin/main` 对齐，再把漏的文件重新提交上去。
- [x] ~~fpk 未在飞牛应用中心真装验证~~ —— 2026-10-22 已真装通过，详见 `docs/DEPLOY.md` 的「FPK 真装验证记录」。切歌竞态回归三轮全过。
- [x] ~~`-I` 写死在配置里，换网卡/改枚举后进程 running 但手机搜不到~~ —— 1.1.0 起可在设置界面直接改网卡，「改完重启」由 `cmd/main` 统一处理
- [ ] 跨 Debian 大版本升级后 `libupnp.so.13` SONAME 变化会导致 `error while loading shared libraries`
- [ ] **开机自启机制未查清**：`manifest` 里没有相关字段，`trim_app_cgi` 查 `dlna-renderer` 显示 `disabled`，但官方应用重启后都能自己起来。目前重启后渲染器确实在跑（实测），但**不清楚是谁拉起来的**，将来若出现「重启后不自动广播」需要从这里查。
- [ ] `description.xml` 缺 `dlna:X_DLNADOC`（P0 遗留），要改 `upnp_device.c` 的 `generate_desc()` 并**重新编译**（仓库里没有 `src/device-desc.xml`，别去找）。当前网易云可用，先不动。
- [ ] **应用中心「更新」流程未实测**：`appcenter-cli` 的 `install-fpk` 遇到已装应用直接回 `is installed`、一个文件都不覆盖，`uninstall` 又要求走 Web UI，所以只能从应用中心界面点「更新」验证真实升级路径。另外 `appcenter-cli list` 里的版本号存在飞牛自己的库里，改 `manifest` 不会让它变。

## 未决策

- Marshall Acton II 待机 20 分钟后能否被 AUX/DLNA 唤醒。官方只对 III 代承诺。唤不醒的话要么加静音保活流，要么接受手动开机。
- **「手机切回本机播放，NAS 还在响」要不要兜底**。抓包证明网易云切走时**一个包都不发**（没有 `Stop` 也没有 `Pause`），这是 DLNA 投屏的固有模型——纯音频渲染器没有会话回收机制，只能继续拉到流结束。可选方案：① 什么都不做，用设置页的「停止」按钮手工停（已可用）；② 长时间没收到任何 UPnP 指令且无进度变化就自动 `Stop`。②有误杀风险（用户就是想让它放完），暂未采用。

## 明确不做

- **不重写渲染器**。考虑过 Go / Rust 重写（Rust 的 `symphonia` + `cpal` 能甩掉 GStreamer 和 libupnp 两套依赖），但现有系统稳定、重写零功能增益。只在下述情况重启这个议题：出现修不动的问题，或要做多房间同步 / 逐台 EQ。
- **不接小米音箱**。那是另一个方向（需要米家账密 + 云端下发），有现成的 MiCast / MiCast-Go 可用。
