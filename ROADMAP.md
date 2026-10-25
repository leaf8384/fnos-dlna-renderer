# 未来计划

> 记录于 2026-10-22，更新于 2026-10-24 23:30。当前版本 **1.1.13**（1.0.0 稳定运行；1.0.1 修中文设备名；1.1.0 网页设置界面；1.1.1 播放控制 + 桌面入口；1.1.2 去重复页头 + 修窗口图标；1.1.3 补安装依赖；1.1.4 修入口配置；1.1.5 修播完不释放声卡；1.1.6 音量确认音；1.1.7 重做确认音，修 1.1.6 放不出声；1.1.8 修 dmix 可用性判断 + 提示音更明确；1.1.9 斜坡 ducking + 和弦式提示音；1.1.10 删 ducking 改「先提示后变音量」+ 控制台加固；1.1.11 修回提示音强度；1.1.12 提示音 120ms + 连按连续反馈；1.1.13 修播完不换下一首），**暂无必须做的事**，以下按优先级排列，做到哪算哪。
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
- **写接口的 CSRF 防护（v1.1.10）**：POST 必须带 `Content-Type: application/json` **且**
  `X-DLNA-Request: 1`，浏览器发起的请求还要通过同源校验。
  命令行调试时别忘带这两个头，否则会收到 4xx —— 这不是接口坏了。
  盯的是「控制台以 root 跑，一个跨站表单就能改配置/重启应用」这个面。

### 轮询与状态一致性（v1.1.10）

`/api/now` 加了 `has_volume` / `has_position` / `has_media_info` 三个**有效性标记**，
前端保留「最后一次有效值」而不是被零值覆盖：

- 前端**同一时刻只发一个轮询**，控制动作发出前尚未返回的旧响应会被丢弃，
  避免「拖完音量条又跳回旧值」。
- `TrackURI` 与 `CurrentURI` 不一致时不采用上一次的进度。
- 拖动音量条期间不被轮询结果回写覆盖（`pointer` 拖动保护）。

> 起因：网易云投屏页不回读远端音量，页面自己又在轮询，两边一抢就出现默认值与实际值闪跳。

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


## P2 · 音量确认音（v1.1.6 → v1.1.12，2026-10-24）

投屏时**手机端看不到渲染器的实际音量**（网易云投屏页不回读远端音量），手机上按音量键之后
「到底调到多少」完全没反馈。所以让渲染器在音量变化时响一声，满档用更高的音。

**演进一句话**：1.1.6 塞进播放管线内部混音（歌放不出来）→ 1.1.7/1.1.8 独立管线 + dmix（能用）
→ 1.1.9 斜坡 ducking（听感差）→ **1.1.10 删掉 ducking，改成「先响提示音、响完再平滑变音量」**
→ 1.1.11 修回提示音强度 → 1.1.12 提示音延长到 120ms + 连按连续反馈。
**现行语义以 1.1.12 为准**；下面「死路 1 / 死路 2」两节是作废方案，留着是为了别再走一遍。

### 死路 1：在播放管线内部混音（v1.1.6，已作废）

1.1.6 把 sink 换成 `audiomixer ! audioconvert ! alsasink` 的自定义 bin，把 `audiotestsrc`
接到 mixer 第二路。**装上后歌直接放不出来**：`audiomixer` 要等**所有**输入 pad 都 preroll
完成才输出，而 `is-live=TRUE` 的测试音源在 PAUSED 下不产数据，于是 mixer 永远 preroll 不完，
`playbin` 卡死在预备阶段。日志特征：

```
Preroll still in flight (3/3); keep waiting
Unexpected state 2 (pending 4, wanted 4)
Restarting playbin (wrong-state) ...
```

真机复现（`playbin` + `audiomixer` + `audiotestsrc is-live=TRUE`）：
`streaming stopped, reason not-linked (-1)` / `pipeline doesn't want to preroll`。
去掉 `is-live` 能让 preroll 通过，但那路常驻源会让 mixer **永远不发 EOS** → 1.1.5 修的
「播完释放声卡」和切歌链路全部失效。**这条路整体不可取，不要回头再试。**

顺带一条教训：控制台的「测试音」是 `aplay` 独立进程，**它响不代表播放链路没坏**。

### 死路 2：ducking（v1.1.9，已被 1.1.10 整个删除）

1.1.9 做过「30ms 斜坡把音乐压到 ×0.4（约 -8dB）→ 压到位后再响滴声 → 保持 70ms →
350ms 斜坡回升」，目标是解决「滴声 70ms 太短、被音乐盖住」。**用户实机听感反馈「减弱效果差」**，
1.1.10 就把整套压低/恢复删掉了，改成「先响提示音、响完再平滑落到目标音量」。

复盘为什么差（对照业界 sidechain ducking 的做法）：

- ducking 是给「语音要盖过音乐」设计的，作用时长以**秒**计；用在 70~120ms 的 UI 提示音上，
  压/抬这个动作本身比滴声更抢耳，主观上就是音乐「被抽了一下」（泵动感 / pumping）。
- 压得越深越明显。业界建议 6~12dB、release 250~500ms；我当时压到 -8~-14dB，正好落在
  「听得见在动」的区间里。
- **真正的瓶颈不是「音乐太响」，而是「滴声不够有辨识度」。** 1.1.12 的做法是把滴声做长（120ms）、
  做亮（基音+五度）、并让音量变化本身成为反馈的一部分。

> 教训：想让人听见提示音，先改提示音；别先动音乐。

### 现行方案（v1.1.10 → v1.1.12）：先提示音，再平滑落到新音量

**语义**（`docs/DEPLOY.md` 记着用户实机试听确认「长度和节奏合适」）：

```
SetVolume → 合并窗口 60ms → 用「当前/旧的可听增益」响提示音 120ms
          → 提示音管线排空 → 12 步 × 15ms = 180ms 斜坡落到新目标增益
          → 期间若又来新值：间隔 80ms 再响一轮，最后一轮响完才落到最终值
```

**提示音期间音乐的增益完全不额外压低**，只在提示音结束后做一次 180ms 的平滑过渡。
用户听到的是「这个音量大概多响」→ 然后音量平滑变成新值，而不是「先被抽一下」。

**状态机**（`patches/0007-continuous-volume-feedback.patch`）

```
VOLUME_IDLE → VOLUME_WAIT → VOLUME_BEEP → VOLUME_RAMP →（有新 revision 则回 WAIT）
```

| 参数 | 值 | 含义 |
|---|---|---|
| `VOLUME_SETTLE_MS` | **60** | 首次合并窗口（1.1.9 是 400ms，按一下要等半秒才响） |
| `VOLUME_BEEP_MS` | **120** | 提示音长度（1.1.9 是 70ms，太短容易被盖） |
| `VOLUME_REPEAT_GAP_MS` | **80** | 连按/长按时两轮提示音的间隔 |
| `VOLUME_RAMP_STEPS` × `VOLUME_RAMP_TICK_MS` | **12 × 15ms = 180ms** | 落到新音量的斜坡 |

**双计数器（关键设计，别再简化成一个）**

- `volume_generation_`：交互代次。静音 / 停止 / 暂停 / 新的独立交互会让**过期的提示音任务失效**。
- `volume_revision_`：目标版本。**长按或连续按键只更新它，不打断正在响的那一声**。
  1.1.9 之前每次 SetVolume 都 `g_source_remove`，按住音量键会得到一串被截断的碎音。
- 到 0 或 100 之后，**相同目标**的重复请求不再额外发声。
- 满档时若普通音正在响、此时刚好到 100：**先把普通音放完，再补一声高音**，不硬切。

**回调式 output_beep**（`patches/0006-volume-sequence.patch`）

```c
void output_beep(int which, int duration_ms, float music_gain,
                 int (*valid)(void *), void (*done)(void *), void *data);
```

- `which`：0 = 普通音，非 0 = 满档高音。
- `music_gain`：**当前（旧）的可听增益**；提示音按它的 0.6 倍响。
- `valid`：在 worker 线程里检查（交互是否还有效 / 是否在播 / transport 代次有没有变）。
- `done`：**任何情况都会执行**（成功、失败、取消、设备不可用都一样），固定在 GLib 主循环上跑，
  并且**接管 data 的所有权**。音量斜坡就是从这里启动的 —— 所以「先响完、再变音量」是硬保证，
  不是靠 sleep 猜时间。
- `upnp_transport_generation()`：给 worker 用的原子快照，避免为了读播放状态去抢 transport 的锁
  （锁序倒置会死锁）。

**仍然有效的底座（1.1.7 起，没变）**

- 主播放管线一行不动，提示音走**自己的一次性管线**
  `appsrc → audioconvert → volume → alsasink(device=plug:dmix)`，靠 dmix 与音乐同时出声。
  管线建在 detached `GThread` 里，**绝不能在主循环同步做**——网易云 `GetPositionInfo`
  峰值 19 req/s（约 51ms 一次），阻塞 120ms 会直接丢响应。
- 提示音 PCM 在 C 里生成（1.1.9 起）：**基音 + 五度**（普通 882/1323Hz，满档 1764/2646Hz），
  首尾 4ms 淡入淡出消掉开关爆音，`appsrc` 直通、caps 锁 `S16LE/44100/2ch`，**零重采样**。
- **跟随音量的正确算法**：`playbin` 的音量是 **dB 曲线**，不是线性百分比 ——
  `level 50` 的增益是 `10^(-20/20)=0.1`，不是 0.5（映射 0→-60dB / 50→-20dB / 100→0dB）。
  提示音按这个**同一个线性增益**缩放，而不是 `level/100`。
  1.1.10 曾把系数降到 0.18 倍导致完全听不见，1.1.11 修回 0.6 倍 —— **这个系数动之前先实听**。
- **只在播放中响**：`upnp_transport_is_playing()`；静音/停止/暂停都会取消过期任务，
  宁可不出声，也不要「停了还在滴」。

**音质：dmix 的采样率必须钉在 44100**

ALSA 内置的 `dmix` 默认 `defaults.pcm.dmix.rate = 48000`
（`/usr/share/alsa/alsa.conf:118`），实测 `plug:dmix` 的硬件参数是 **48000 / S32_LE**，
而基线 `plughw:CARD=PCH,DEV=0` 是 **44100 / S16_LE** —— 也就是白插了一次重采样。
所以 `cmd/install_init` 会往 `/etc/asound.conf` 写一段带标记的 `pcm.!dmix` 覆盖：

```
pcm.!dmix { type dmix
    slave { pcm "hw:CARD=PCH,DEV=0"; format S32_LE; rate 44100
            channels 2; period_size 1024; buffer_size 8192 } }
```

- 有一条 `pcm.!dmix` 之后实测硬件参数变成 **44100 / S32_LE**，**不再重采样**。
- 格式用 `S32_LE` 而不是 `S16_LE`：dmix 要把音乐和滴声相加，16bit 相加会削顶；
  32bit 有 16bit 余量。源本身是 16bit，升到 32bit 是精确左移，**无损**。
- 48k 源（个别音源）照样能放，此时才发生一次 48k→44.1k 转换；本机音源基本都是 44.1k。
- 幂等：按 `# >>> dlna-renderer: shared dmix at 44.1 kHz >>>` 标记判断，
  已有别人的 `dmix` 定义时不覆盖、只记日志。
- **卸载不会删这个文件**（和 apt 包一样不回滚），留着也无害：别的程序不用 dmix。

⚠️ **`plug:dmix` 不在 `aplay -L` 的列表里**。`aplay -L` 只有一行 `dmix`（或 `dmix:CARD=...`）；
`plug:dmix` 是我们主动拼的「用 plug 包一层做格式/采样率转换」的组合名，列表里查不到。
1.1.7 就是用 `aplay -L | grep -qx 'plug:dmix'` 判断可用性 —— 永远不匹配，于是安装默认值、
   `detect_audiodev()`、`migrate_audiodev()` 三处全部静默走了「没有 dmix」分支：
   **设备一直停在独占的 plughw，音乐正常，提示音每次都被拒**
  （`beep: Could not open audio device for playback. Device is being used by another application.`）。
   正确写法：`aplay -L | grep -qE '^(plug:)?dmix(:|$)'`。
   教训：**「功能看起来没生效」先翻日志找那句被拒**，比自己听音猜强。

**配置迁移**：`cmd/main` 的 `detect_audiodev()` 优先返回 `plug:dmix`；`load_conf()` 里
`migrate_audiodev()` 把旧值（仅限旧版自动探测出来的那一坨 `plughw:CARD=<第一块卡>,DEV=0` /
`plughw:0,0` / `hw:0,0`）一次性改写成 `plug:dmix`，**用户手工挑的其他设备不动**。
设置页声卡列表第一项固定是「共享输出（推荐，支持提示音）」。

> 为什么必须迁移而不是加个开关：音乐握着 `plughw` 时，dmix 根本打不开 `hw:0,0`，
> 提示音必然失败，而且是**静默失败**——用户只会以为这个功能没做。所以主设备必须是 dmix。

**已知取舍**：提示音跟随主音量 → 音量很低时它也几乎听不见（用户明确选了这个，简单优先）。
另外 1.1.10 之后**不再压低音乐**，所以在音乐很响的段落里，提示音要靠「120ms + 基音五度 + 0.6 倍」
这三点自己站住，而不是靠给音乐让路。

**怎么验（1.1.10 起有了正经测试，别再只靠耳朵和日志）**

```bash
(cd console && go test -race ./... && go vet ./...)
python3 tests/test_release.py
node tests/test_ui.cjs
python3 tests/test_volume_sequence.py build/gmrender-resurrect-master
python3 tests/test_renderer_integration.py --renderer build/gmrender-resurrect-master/src/gmediarender
```

- `test_volume_sequence.py`：状态机时序回归 —— 持续 20Hz 输入、最后目标在不同阶段到达、
  旧零目标与新非零目标交错、满档补声后再改低、各阶段停止/静音取消。
- `test_renderer_integration.py`：**独立端口 49610 + 独立 UUID + 实时静音 sink**，
  不碰安装实例和音量存档；覆盖提示音先后顺序、过期取消、静音/暂停恢复、三轮 Seek+URI 切换。
- **提示音用 ALSA `file→null` 捕获真实 PCM**，验强度 / 120ms 时长 / 双音频率 ——
  **不拿事件日志当音频证据**。1.1.9 之前我就在这上面误判过：日志没有报错 ≠ 有声音
  （服务被占用的报错也可能被 stdout 全缓冲吞掉，得 `kill -TERM` 刷盘再 grep）。
- 集成测试跑之前先 `cmd/main stop` 腾出 49494；否则测试实例绑不上端口，SOAP 会打到已装应用上，
  「测试日志里没报错」是**假阳性**。

## 安装依赖（v1.1.3 修）

`cmd/install_init` 原来只 apt 补 `gstreamer1.0-*` 四个包，**漏了 `libupnp13` 和 `alsa-utils`**：

- `ldd gmediarender` 明确要 `libupnp.so.13` → 缺了**进程根本起不来**
- `aplay` 来自 `alsa-utils`，设置页枚举声卡和「测试音」都靠它 → 缺了声卡列表是空的

这两个包在本机是早期手工 `apt install` 的，所以走 fpk 装的流程一直没暴露。
**卸载不会回滚这些系统包**（飞牛只删应用目录和库记录，卸载向导里已写明）。

### 构建依赖（v1.1.9 修，`patches/0005-appsrc-link.patch`）

1.1.9 起提示音改用 `appsrc` 喂自生成的 PCM，而 **`gst/app/gstappsrc.h` 在
`libgstreamer-plugins-base1.0-dev` 里，不在 `libgstreamer1.0-dev`**：

- **CI 的 `debian:12` 容器必须同时装** `libgstreamer1.0-dev` **和** `libgstreamer-plugins-base1.0-dev`。
- **fnOS 上仍然不要装这个 -dev 包**（会撞飞牛的 `libgbm1`）；运行时只需要
  `gstreamer1.0-plugins-base`（提供 `libgstreamer-plugins-base1.0-0`），它本来就在 `install_init` 的依赖清单里。
- `configure.ac` 里加了 `PKG_CHECK_MODULES(GSTAPP, gstreamer-app-1.0 >= 1.0)`，
  查不到就 `AC_MSG_ERROR` 直接失败——**宁可编不过，也不要发出一个提示音静默失效的包**。

⚠️ **`PKG_CHECK_MODULES` 的 action 分支不能写单行 `AC_SUBST(a); AC_SUBST(b)`**：
`autoreconf` 生成的 `configure` 会报 `syntax error near unexpected token ';'`，
必须拆成两行独立的 `AC_SUBST`。验证手段：fnOS 上有 `autoconf 2.71`，可直接

```bash
NOCONFIGURE=1 ./autogen.sh && bash -n configure
```

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
- [x] ~~**EOS 自然播完不释放 alsasink（声卡被永久占住）**~~ —— **已修，v1.1.5（2026-10-23）**，
      `patches/0003-eos-release-audio-device.patch`（EOS 无 next uri 分支补 `set_state(READY)`）。
      下面是当初的排查记录，留着备查 ——

      ```c
      } else if (play_trans_callback_) {
          play_trans_callback_(PLAY_STOPPED);      // ← 只回调，没有 set_state(READY)
      }
      ```

      对比上面 `gs_next_uri_ != NULL` 的续播分支：那条有 `set_state(READY)` → 换 uri → `set_state(PLAYING)`，
      所以**换歌会重开设备、`hw_ptr` 归零**；而**播到自然结束**只回了个状态回调，pipeline 停在 EOS 态，
      alsasink 不释放设备，一直以 44.1kHz 往 ALSA 喂静音。

      实测证据（`/proc/asound/.../status`）：API 已报 `STOPPED / has_media=false / uri=""`，
      但 `state=RUNNING`、`owner_pid=47065`（渲染器自己）、`hw_ptr` 每 3 秒稳定 +132468 帧、`delay` 恒定 ~8500。
      **此时 `aplay -D plughw:CARD=PCH,DEV=0` 报 `Device or resource busy`** → 控制台「测试音」在 STOPPED 态必然失败。
      **已经 STOPPED 后再发 SOAP `Stop` 也救不回来**（空操作，实测无效），只能重启渲染器。

      修法（v1.1.5 已落地）：EOS 无 next uri 分支补一行 `gst_element_set_state(player_, GST_STATE_READY);`
      （用 READY 不用 NULL，保 pipeline 少一次重建）。**实际行号要按打过 0001 之后的源码算：`output_gstreamer.c:719-721`**
      （裸源码里在 281 附近，0001 在文件前部插了 27 行，别拿裸源码行号去对）。
      连带：控制台读 pcm 判 `RUNNING` 的「声卡占用」提示在这种状态下属误报，修完 C 才根治。
- [ ] 跨 Debian 大版本升级后 `libupnp.so.13` SONAME 变化会导致 `error while loading shared libraries`
- [ ] **开机自启机制未查清**：`manifest` 里没有相关字段，`trim_app_cgi` 查 `dlna-renderer` 显示 `disabled`，但官方应用重启后都能自己起来。目前重启后渲染器确实在跑（实测），但**不清楚是谁拉起来的**，将来若出现「重启后不自动广播」需要从这里查。
- [ ] `description.xml` 缺 `dlna:X_DLNADOC`（P0 遗留），要改 `upnp_device.c` 的 `generate_desc()` 并**重新编译**（仓库里没有 `src/device-desc.xml`，别去找）。当前网易云可用，先不动。
- [ ] **应用中心「更新」流程未实测**：`appcenter-cli` 的 `install-fpk` 遇到已装应用直接回 `is installed`、一个文件都不覆盖，`uninstall` 又要求走 Web UI，所以只能从应用中心界面点「更新」验证真实升级路径。另外 `appcenter-cli list` 里的版本号存在飞牛自己的库里，改 `manifest` 不会让它变。

## 未决策

- Marshall Acton II 待机 20 分钟后能否被 AUX/DLNA 唤醒。官方只对 III 代承诺。唤不醒的话要么加静音保活流，要么接受手动开机。
- ~~**「手机切回本机播放，NAS 还在响」要不要兜底**~~ —— **已实测结案（2026-10-23 02:00），结论是「不用兜底」**。
  双证据（渲染器日志 + `tcpdump` 抓 49494）确认：切走瞬间客户端**连轮询都停**，不是只停控制指令。
  抓包按秒统计：`01:47:55–01:48:08` 每秒 **18–20 个入向新连接**（全是 `GetPositionInfo`，`Connection: close`
  **不复用连接**），最后一个包 `01:48:09.606`，之后**零请求**；渲染器日志同期只有 2 行 `cmd_obtain_variable`。
  而同一时刻 position 从 0:00:40 一路走到 0:03:55 才 EOS → **切走后确实继续响满剩余 3 分 15 秒**，
  然后自己 `STOPPED`、URI 清空、`CurrentTransportActions: PLAY`，**不续播不循环**。
  所以影响范围 = 本曲剩余时长，不是无限播。**决定：不做「失联自动停」**（误杀风险 > 收益，
  且锁屏/后台时客户端同样会停轮询），沿用设置页的手工「停止」。
  **教训：别拿 `/api/now` 当「客户端是否还活着」的探针** —— 控制台处理该请求时自己会向渲染器发
  `GetVolume`，日志里因此冒出 `cmd_obtain_variable`，会把自己的采样误判成客户端轮询（第一次就是这么翻车的）。
  正确探针是「停掉所有本地请求后看日志行数是否增长」或直接抓包。

## 明确不做

- **不重写渲染器**。考虑过 Go / Rust 重写（Rust 的 `symphonia` + `cpal` 能甩掉 GStreamer 和 libupnp 两套依赖），但现有系统稳定、重写零功能增益。只在下述情况重启这个议题：出现修不动的问题，或要做多房间同步 / 逐台 EQ。
- **不接小米音箱**。那是另一个方向（需要米家账密 + 云端下发），有现成的 MiCast / MiCast-Go 可用。
